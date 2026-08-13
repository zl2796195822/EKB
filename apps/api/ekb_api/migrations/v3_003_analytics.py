"""v3_003_analytics: queryable access-event projection for the analytics board.

The analytics page needs three things the current schema cannot answer:

* a **daily time series** of knowledge access (audit_logs has no day column and
  mixes auth/tenant noise into the same table),
* **per-knowledge-base** access share, and
* **unique visitors** per day.

``audit_logs`` stays the authoritative, append-only compliance record.  This
migration adds ``resource_access_events`` as a *narrow projection* limited to
knowledge resources (KB / DOCUMENT / CONVERSATION), with a pre-computed
``occurred_day`` so grouping never needs a function index.

Backfill is deterministic: every historical audit row whose ``target_type``
maps to a knowledge resource becomes exactly one event, with a uuid5 id derived
from the audit log id so re-running the migration cannot duplicate rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_003_analytics"

V3_003_OWNED_TABLES = ("resource_access_events",)

V3_003_ROLLBACK_POLICY = (
    "retain schema_migrations and audit_logs; resource_access_events is a "
    "projection over audit_logs plus runtime writes and can be dropped, but "
    "runtime-sourced rows are not reconstructable from audit_logs, so rollback "
    "is blocked once source='runtime' rows exist"
)

#: Knowledge resource types tracked by the analytics projection.
_RESOURCE_TYPES = ("KB", "DOCUMENT", "CONVERSATION")

#: audit_logs.target_type -> projection resource_type.  Anything absent here is
#: deliberately skipped (auth sessions, tenants, users, audit reads).
_TARGET_TYPE_MAP = {
    "knowledge_base": "KB",
    "kb": "KB",
    "document": "DOCUMENT",
    "conversation": "CONVERSATION",
}

#: action suffix -> access_kind.  Order matters: first match wins.
_ACCESS_KIND_RULES = (
    ("qa.ask", "ASK"),
    (".upload", "CREATE"),
    (".create", "CREATE"),
    (".purge", "DELETE"),
    (".delete", "DELETE"),
    (".restore", "RESTORE"),
    (".grant", "SHARE"),
    (".revoke", "SHARE"),
    (".update", "UPDATE"),
    (".retry", "UPDATE"),
)

_DEFAULT_ACCESS_KIND = "VIEW"

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS resource_access_events (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      resource_type VARCHAR(32) NOT NULL,
      resource_id VARCHAR(36) NOT NULL,
      actor_id VARCHAR(36),
      access_kind VARCHAR(32) NOT NULL,
      occurred_at VARCHAR(32) NOT NULL,
      occurred_day VARCHAR(10) NOT NULL,
      source VARCHAR(32) NOT NULL,
      source_ref VARCHAR(36),
      trace_id VARCHAR(64),
      metadata JSON,
      created_at VARCHAR(32) NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_resource_access_events_source_ref "
    "ON resource_access_events (source, source_ref)",
    "CREATE INDEX IF NOT EXISTS ix_resource_access_events_tenant_day "
    "ON resource_access_events (tenant_id, occurred_day)",
    "CREATE INDEX IF NOT EXISTS ix_resource_access_events_tenant_type_day "
    "ON resource_access_events (tenant_id, resource_type, occurred_day)",
    "CREATE INDEX IF NOT EXISTS ix_resource_access_events_tenant_resource "
    "ON resource_access_events (tenant_id, resource_type, resource_id)",
    "CREATE INDEX IF NOT EXISTS ix_resource_access_events_tenant_actor_day "
    "ON resource_access_events (tenant_id, actor_id, occurred_day)",
)

_BACKFILL_POLICY = {
    "resource_types": list(_RESOURCE_TYPES),
    "source_table": "audit_logs",
    "target_type_map": dict(sorted(_TARGET_TYPE_MAP.items())),
    "access_kind_rules": [list(rule) for rule in _ACCESS_KIND_RULES],
    "default_access_kind": _DEFAULT_ACCESS_KIND,
    "skipped": "audit rows whose target_type is not a knowledge resource",
    "null_target_id": "skipped; a resource event without resource_id is meaningless",
    "null_tenant": "skipped; projection is tenant-scoped",
    "occurred_day": "substr(audit_logs.created_at, 1, 10)",
    "id_strategy": "uuid5(NAMESPACE_URL, ekb:v3_003_analytics:access:<audit_log_id>)",
    "source_value": "audit_backfill",
}

V3_003_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\n"
        + json.dumps(_BACKFILL_POLICY, sort_keys=True, separators=(",", ":"))
        + "\n"
        + "rollback="
        + V3_003_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
    ).encode()
).hexdigest()

_REQUIRED_COLUMNS = {
    "resource_access_events": {
        "id",
        "tenant_id",
        "resource_type",
        "resource_id",
        "actor_id",
        "access_kind",
        "occurred_at",
        "occurred_day",
        "source",
        "source_ref",
        "trace_id",
        "metadata",
        "created_at",
    }
}

_REQUIRED_INDEXES = (
    "ux_resource_access_events_source_ref",
    "ix_resource_access_events_tenant_day",
    "ix_resource_access_events_tenant_type_day",
    "ix_resource_access_events_tenant_resource",
    "ix_resource_access_events_tenant_actor_day",
)


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple
    indexes: tuple
    backfill_counts: dict


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple
    retained: tuple
    blocked_reason: Optional[str] = None


def access_event_id_for(audit_log_id: str) -> str:
    """Stable projection id so re-running the backfill is idempotent."""
    return str(uuid5(NAMESPACE_URL, ":".join(("ekb", VERSION, "access", audit_log_id))))


def resource_type_for(target_type: Optional[str]) -> Optional[str]:
    """Map an audit target_type onto a knowledge resource type, or None to skip."""
    if not target_type:
        return None
    return _TARGET_TYPE_MAP.get(str(target_type).strip().lower())


def access_kind_for(action: Optional[str]) -> str:
    """Derive the access kind from the audit action name."""
    name = (action or "").strip().lower()
    for needle, kind in _ACCESS_KIND_RULES:
        if name == needle or name.endswith(needle):
            return kind
    return _DEFAULT_ACCESS_KIND


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _ddl_for_connection(connection: Connection) -> tuple:
    """Use JSON text on SQLite and native JSONB on PostgreSQL."""
    if connection.dialect.name == "postgresql":
        return tuple(statement.replace(" JSON ", " JSONB ") for statement in _DDL)
    if connection.dialect.name == "sqlite":
        return _DDL
    raise RuntimeError(
        "{0} unsupported database dialect: {1}".format(VERSION, connection.dialect.name)
    )


def _ledger_checksum(connection: Connection) -> Optional[str]:
    row = connection.execute(
        text("SELECT checksum FROM schema_migrations WHERE version = :version"),
        {"version": VERSION},
    ).first()
    return None if row is None else str(row[0])


def _existing_source_refs(connection: Connection) -> set:
    rows = connection.execute(
        text(
            "SELECT source_ref FROM resource_access_events "
            "WHERE source = 'audit_backfill' AND source_ref IS NOT NULL"
        )
    ).all()
    return {str(row[0]) for row in rows}


def _backfill(connection: Connection) -> dict:
    now = utc_now()
    tables = set(inspect(connection).get_table_names())
    counts = {"KB": 0, "DOCUMENT": 0, "CONVERSATION": 0, "skipped": 0}
    if "audit_logs" not in tables:
        return counts

    seen = _existing_source_refs(connection)
    rows = connection.execute(
        text(
            "SELECT id, tenant_id, actor_id, action, target_type, target_id, "
            "trace_id, created_at FROM audit_logs ORDER BY created_at"
        )
    ).all()

    for row in rows:
        audit_id = str(row[0])
        if audit_id in seen:
            continue
        tenant_id = row[1]
        target_id = row[5]
        resource_type = resource_type_for(row[4])
        if resource_type is None or tenant_id is None or target_id is None:
            counts["skipped"] += 1
            continue
        occurred_at = str(row[7] or now)
        connection.execute(
            text(
                """
                INSERT OR IGNORE INTO resource_access_events (
                  id, tenant_id, resource_type, resource_id, actor_id,
                  access_kind, occurred_at, occurred_day, source, source_ref,
                  trace_id, metadata, created_at
                ) VALUES (
                  :id, :tenant_id, :resource_type, :resource_id, :actor_id,
                  :access_kind, :occurred_at, :occurred_day, 'audit_backfill', :source_ref,
                  :trace_id, :metadata, :created_at
                )
                """
            ),
            {
                "id": access_event_id_for(audit_id),
                "tenant_id": str(tenant_id),
                "resource_type": resource_type,
                "resource_id": str(target_id),
                "actor_id": None if row[2] is None else str(row[2]),
                "access_kind": access_kind_for(row[3]),
                "occurred_at": occurred_at,
                "occurred_day": occurred_at[:10],
                "source_ref": audit_id,
                "trace_id": None if row[6] is None else str(row[6]),
                "metadata": json.dumps(
                    {"backfilled": True, "action": str(row[3] or "")},
                    separators=(",", ":"),
                ),
                "created_at": now,
            },
        )
        seen.add(audit_id)
        counts[resource_type] += 1
    return counts


def _catalog_index_names(connection: Connection) -> set:
    inspector = inspect(connection)
    names = set()
    for table in V3_003_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                names.add(str(name))
    return names


def _verify_catalog(connection: Connection) -> tuple:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing_tables = [table for table in V3_003_OWNED_TABLES if table not in table_names]
    if missing_tables:
        raise RuntimeError(
            "{0} verification failed: missing tables={1}".format(VERSION, ",".join(missing_tables))
        )
    for table, expected in _REQUIRED_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(expected - actual)
        if missing:
            raise RuntimeError(
                "{0} verification failed: {1} missing columns={2}".format(
                    VERSION, table, ",".join(missing)
                )
            )
    index_names = _catalog_index_names(connection)
    missing_indexes = [name for name in _REQUIRED_INDEXES if name not in index_names]
    if missing_indexes:
        raise RuntimeError(
            "{0} verification failed: missing indexes={1}".format(VERSION, ",".join(missing_indexes))
        )
    return tuple(sorted(index_names))


def apply_v3_003(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            raise RuntimeError("{0} requires v3_001_identity to be applied first".format(VERSION))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is not None and applied_checksum != V3_003_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_003_CHECKSUM
                )
            )
        for statement in _ddl_for_connection(connection):
            connection.execute(text(statement))
        if applied_checksum is None:
            backfill_counts = _backfill(connection)
        else:
            backfill_counts = {"KB": 0, "DOCUMENT": 0, "CONVERSATION": 0, "skipped": 0}
        _verify_catalog(connection)
        if applied_checksum is None:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at, checksum) "
                    "VALUES (:version, :applied_at, :checksum)"
                ),
                {"version": VERSION, "applied_at": utc_now(), "checksum": V3_003_CHECKSUM},
            )
            applied = True
        else:
            applied = False
    return MigrationResult(VERSION, V3_003_CHECKSUM, applied, backfill_counts)


def verify_v3_003(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        _set_sqlite_foreign_keys(connection)
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum != V3_003_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_003_CHECKSUM
                )
            )
        index_names = _verify_catalog(connection)
        counts = {
            "resource_access_events": int(
                connection.execute(
                    text("SELECT COUNT(*) FROM resource_access_events")
                ).scalar_one()
            ),
            "distinct_days": int(
                connection.execute(
                    text("SELECT COUNT(DISTINCT occurred_day) FROM resource_access_events")
                ).scalar_one()
            ),
        }
    return VerificationResult(
        status="PASS",
        version=VERSION,
        checksum=V3_003_CHECKSUM,
        tables=V3_003_OWNED_TABLES,
        indexes=index_names,
        backfill_counts=counts,
    )


def rollback_v3_003_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            return RollbackPlan(False, (), ("schema_migrations", "audit_logs"))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is None:
            return RollbackPlan(False, (), ("schema_migrations", "audit_logs"))
        if applied_checksum != V3_003_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_003_CHECKSUM
                )
            )
        runtime_rows = connection.execute(
            text("SELECT 1 FROM resource_access_events WHERE source = 'runtime' LIMIT 1")
        ).first()
        if runtime_rows:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations", "audit_logs"),
                blocked_reason="runtime_events_present",
            )
        return RollbackPlan(
            True,
            tuple(reversed(V3_003_OWNED_TABLES)),
            ("schema_migrations", "audit_logs"),
        )

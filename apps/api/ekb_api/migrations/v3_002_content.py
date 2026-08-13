"""v3_002_content: unified trash projection for soft-deleted resources.

Soft deletion already exists but is scattered across three shapes:

* ``knowledge_bases.deleted_at``
* ``documents.status = 'DELETED'`` (no dedicated timestamp column)
* ``conversations.deleted_at``

The recycle bin needs one queryable surface with retention metadata, so this
migration introduces ``trash_items`` as a projection table and backfills the
already soft-deleted rows.  The original tables stay authoritative: restore
writes back to them and the projection is updated in the same transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_002_content"

V3_002_OWNED_TABLES = ("trash_items",)

V3_002_ROLLBACK_POLICY = (
    "retain schema_migrations; trash_items is a projection over source tables and "
    "can be dropped without data loss once no purge history is required; block "
    "rollback while purged rows exist because purge is not reconstructable"
)

#: Retention window applied to backfilled and newly deleted rows.
TRASH_RETENTION_DAYS = 30

_RESOURCE_TYPES = ("KB", "DOCUMENT", "CONVERSATION")

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS trash_items (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      resource_type VARCHAR(32) NOT NULL,
      resource_id VARCHAR(36) NOT NULL,
      title VARCHAR(512) NOT NULL DEFAULT '',
      parent_id VARCHAR(36),
      parent_title VARCHAR(512),
      deleted_by VARCHAR(36),
      deleted_at VARCHAR(32) NOT NULL,
      expires_at VARCHAR(32) NOT NULL,
      restored_at VARCHAR(32),
      purged_at VARCHAR(32),
      metadata JSON,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_trash_items_resource "
    "ON trash_items (resource_type, resource_id)",
    "CREATE INDEX IF NOT EXISTS ix_trash_items_tenant_deleted "
    "ON trash_items (tenant_id, deleted_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_trash_items_tenant_type "
    "ON trash_items (tenant_id, resource_type, deleted_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_trash_items_tenant_active "
    "ON trash_items (tenant_id, restored_at, purged_at, deleted_at DESC)",
)

_BACKFILL_POLICY = {
    "resource_types": list(_RESOURCE_TYPES),
    "retention_days": TRASH_RETENTION_DAYS,
    "sources": {
        "KB": "knowledge_bases.deleted_at IS NOT NULL",
        "DOCUMENT": "documents.status = 'DELETED'",
        "CONVERSATION": "conversations.deleted_at IS NOT NULL",
    },
    "document_deleted_at": "documents has no deleted_at column; use updated_at",
    "deleted_by": "unknown for backfilled rows; stored as NULL, never guessed",
    "id_strategy": "uuid5(NAMESPACE_URL, ekb:v3_002_content:trash:<type>:<resource_id>)",
}

V3_002_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\n"
        + json.dumps(_BACKFILL_POLICY, sort_keys=True, separators=(",", ":"))
        + "\n"
        + "rollback="
        + V3_002_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
    ).encode()
).hexdigest()

_REQUIRED_COLUMNS = {
    "trash_items": {
        "id",
        "tenant_id",
        "resource_type",
        "resource_id",
        "title",
        "parent_id",
        "parent_title",
        "deleted_by",
        "deleted_at",
        "expires_at",
        "restored_at",
        "purged_at",
        "metadata",
        "created_at",
        "updated_at",
    }
}

_REQUIRED_INDEXES = (
    "ux_trash_items_resource",
    "ix_trash_items_tenant_deleted",
    "ix_trash_items_tenant_type",
    "ix_trash_items_tenant_active",
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


def trash_id_for(resource_type: str, resource_id: str) -> str:
    """Stable projection id so backfill and runtime writes never collide."""
    return str(uuid5(NAMESPACE_URL, ":".join(("ekb", VERSION, "trash", resource_type, resource_id))))


def expires_at_for(deleted_at: str) -> str:
    """Retention deadline derived from the deletion timestamp.

    Timestamps are stored as ISO-8601 ``Z`` strings; adding days by parsing is
    avoided here so malformed legacy values cannot break the migration.  The
    service layer computes precise expiry for new deletions.
    """
    from datetime import datetime, timedelta, timezone

    try:
        parsed = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    moment = (parsed + timedelta(days=TRASH_RETENTION_DAYS)).replace(microsecond=0)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _ddl_for_connection(connection: Connection) -> tuple:
    """Use JSON text on SQLite and native JSONB on PostgreSQL."""
    if connection.dialect.name == "postgresql":
        return tuple(statement.replace(" JSON ", " JSONB ") for statement in _DDL)
    if connection.dialect.name == "sqlite":
        return _DDL
    raise RuntimeError("{0} unsupported database dialect: {1}".format(VERSION, connection.dialect.name))


def _ledger_checksum(connection: Connection) -> Optional[str]:
    row = connection.execute(
        text("SELECT checksum FROM schema_migrations WHERE version = :version"),
        {"version": VERSION},
    ).first()
    return None if row is None else str(row[0])


def _existing_resource_ids(connection: Connection) -> set:
    rows = connection.execute(text("SELECT resource_type, resource_id FROM trash_items")).all()
    return {(str(row[0]), str(row[1])) for row in rows}


def _insert_projection(connection: Connection, payload: dict) -> None:
    connection.execute(
        text(
            """
            INSERT INTO trash_items (
              id, tenant_id, resource_type, resource_id, title,
              parent_id, parent_title, deleted_by, deleted_at, expires_at,
              restored_at, purged_at, metadata, created_at, updated_at
            ) VALUES (
              :id, :tenant_id, :resource_type, :resource_id, :title,
              :parent_id, :parent_title, :deleted_by, :deleted_at, :expires_at,
              NULL, NULL, :metadata, :created_at, :updated_at
            )
            """
        ),
        payload,
    )


def _backfill(connection: Connection) -> dict:
    now = utc_now()
    tables = set(inspect(connection).get_table_names())
    seen = _existing_resource_ids(connection)
    counts = {"KB": 0, "DOCUMENT": 0, "CONVERSATION": 0}

    def stage(resource_type: str, rows) -> None:
        for row in rows:
            resource_id = str(row[0])
            if (resource_type, resource_id) in seen:
                continue
            deleted_at = str(row[2] or now)
            _insert_projection(
                connection,
                {
                    "id": trash_id_for(resource_type, resource_id),
                    "tenant_id": str(row[1]),
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "title": str(row[3] or ""),
                    "parent_id": None if len(row) < 5 else (None if row[4] is None else str(row[4])),
                    "parent_title": None,
                    "deleted_by": None,
                    "deleted_at": deleted_at,
                    "expires_at": expires_at_for(deleted_at),
                    "metadata": json.dumps({"backfilled": True}, separators=(",", ":")),
                    "created_at": now,
                    "updated_at": now,
                },
            )
            seen.add((resource_type, resource_id))
            counts[resource_type] += 1

    if "knowledge_bases" in tables:
        stage(
            "KB",
            connection.execute(
                text(
                    "SELECT id, tenant_id, deleted_at, name FROM knowledge_bases "
                    "WHERE deleted_at IS NOT NULL"
                )
            ).all(),
        )
    if "documents" in tables:
        # documents carries no deleted_at column; updated_at is the closest signal.
        stage(
            "DOCUMENT",
            connection.execute(
                text(
                    "SELECT id, tenant_id, updated_at, title, kb_id FROM documents "
                    "WHERE status = 'DELETED'"
                )
            ).all(),
        )
    if "conversations" in tables:
        stage(
            "CONVERSATION",
            connection.execute(
                text(
                    "SELECT id, tenant_id, deleted_at, title FROM conversations "
                    "WHERE deleted_at IS NOT NULL"
                )
            ).all(),
        )
    return counts


def _catalog_index_names(connection: Connection) -> set:
    inspector = inspect(connection)
    names = set()
    for table in V3_002_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                names.add(str(name))
    return names


def _verify_catalog(connection: Connection) -> tuple:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing_tables = [table for table in V3_002_OWNED_TABLES if table not in table_names]
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
            "{0} verification failed: missing indexes={1}".format(
                VERSION, ",".join(missing_indexes)
            )
        )
    return tuple(sorted(index_names))


def apply_v3_002(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            raise RuntimeError("{0} requires v3_001_identity to be applied first".format(VERSION))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is not None and applied_checksum != V3_002_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_002_CHECKSUM
                )
            )
        for statement in _ddl_for_connection(connection):
            connection.execute(text(statement))
        backfill_counts = _backfill(connection)
        _verify_catalog(connection)
        if applied_checksum is None:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at, checksum) "
                    "VALUES (:version, :applied_at, :checksum)"
                ),
                {"version": VERSION, "applied_at": utc_now(), "checksum": V3_002_CHECKSUM},
            )
            applied = True
        else:
            applied = False
    return MigrationResult(VERSION, V3_002_CHECKSUM, applied, backfill_counts)


def verify_v3_002(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        _set_sqlite_foreign_keys(connection)
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum != V3_002_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_002_CHECKSUM
                )
            )
        index_names = _verify_catalog(connection)
        counts = {
            "trash_items": int(
                connection.execute(text("SELECT COUNT(*) FROM trash_items")).scalar_one()
            ),
            "active": int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM trash_items "
                        "WHERE restored_at IS NULL AND purged_at IS NULL"
                    )
                ).scalar_one()
            ),
        }
    return VerificationResult(
        status="PASS",
        version=VERSION,
        checksum=V3_002_CHECKSUM,
        tables=V3_002_OWNED_TABLES,
        indexes=index_names,
        backfill_counts=counts,
    )


def rollback_v3_002_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            return RollbackPlan(False, (), ("schema_migrations",))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is None:
            return RollbackPlan(False, (), ("schema_migrations",))
        if applied_checksum != V3_002_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_002_CHECKSUM
                )
            )
        purged = connection.execute(
            text("SELECT 1 FROM trash_items WHERE purged_at IS NOT NULL LIMIT 1")
        ).first()
        if purged:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations",),
                blocked_reason="purge_history_present",
            )
        return RollbackPlan(True, tuple(reversed(V3_002_OWNED_TABLES)), ("schema_migrations",))

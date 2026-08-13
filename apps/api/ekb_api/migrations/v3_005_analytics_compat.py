"""v3_005_analytics_compat: compensation migration for the analytics vertical.

Resolves ADR-002 drift against 02_spec.md:

* Adds the missing ``support_feedback`` table (owned by v3_004_analytics in
  the authoritative spec, but absent from the applied v3_003_analytics).
* Adds the two analytic indexes named in §5.4 of the spec
  (``ix_access_events_tenant_time`` and ``ix_access_events_resource``) using
  the locked column names from v3_003_analytics (``occurred_at`` /
  ``occurred_day`` instead of the spec's idealised ``created_at`` grouping).

This version is purely additive — it never touches the already-applied
``v3_003_analytics`` DDL or checksum, so the existing projection and the
running writer stay valid.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_005_analytics_compat"

V3_005_OWNED_TABLES = ("support_feedback",)

V3_005_ROLLBACK_POLICY = (
    "retain schema_migrations, audit_logs and resource_access_events; "
    "support_feedback is a user-reported issues table and may be dropped "
    "with --allow-data-loss only after no OPEN rows exist; analytic "
    "indexes are dropped before the table.  Existing v3_003_analytics "
    "objects are never touched by this compensation."
)

_DDL = (
    # --- support_feedback (authoritative spec §5.2, owned by v3_004_analytics)
    """
    CREATE TABLE IF NOT EXISTS support_feedback (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      page_path VARCHAR(255) NOT NULL,
      category VARCHAR(64) NOT NULL,
      message TEXT NOT NULL,
      request_id VARCHAR(128),
      status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      FOREIGN KEY (tenant_id, user_id)
        REFERENCES tenant_memberships (tenant_id, user_id),
      CHECK (status IN ('OPEN', 'CLOSED')),
      CHECK (message <> '')
    )
    """,
    # --- support_feedback indexes (§5.4)
    "CREATE INDEX IF NOT EXISTS ix_support_feedback_tenant_status "
    "ON support_feedback (tenant_id, status, created_at DESC)",
    # --- analytic indexes on the locked resource_access_events projection.
    #     The spec's idealised columns are (created_at, resource_id) but the
    #     applied v3_003_analytics split those into (occurred_at, occurred_day).
    #     We mirror the §5.4 intent using the actually-available column names,
    #     so EXPLAIN still keeps bounded-range queries off the full table.
    "CREATE INDEX IF NOT EXISTS ix_access_events_tenant_time "
    "ON resource_access_events (tenant_id, occurred_at DESC, resource_type)",
    "CREATE INDEX IF NOT EXISTS ix_access_events_resource "
    "ON resource_access_events (tenant_id, resource_type, resource_id, occurred_at DESC)",
)

_BACKFILL_POLICY = {
    "support_feedback": "no backfill; table is empty until users submit feedback",
    "indexes_on_resource_access_events": "created IF NOT EXISTS on the locked v3_003_analytics projection",
}

V3_005_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\n"
        + json.dumps(_BACKFILL_POLICY, sort_keys=True, separators=(",", ":"))
        + "\n"
        + "rollback="
        + V3_005_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
    ).encode()
).hexdigest()

_REQUIRED_COLUMNS = {
    "support_feedback": {
        "id",
        "tenant_id",
        "user_id",
        "page_path",
        "category",
        "message",
        "request_id",
        "status",
        "created_at",
        "updated_at",
    }
}

_REQUIRED_INDEXES = (
    "ix_support_feedback_tenant_status",
    # Note: the two resource_access_events indexes live on the shared
    # catalog of v3_003_analytics; the verifier checks they exist in the
    # database, not that this migration created them.
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


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _ddl_for_connection(connection: Connection) -> tuple:
    """Use JSONB for PostgreSQL where dialect supports it."""
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


def _catalog_index_names(connection: Connection) -> set:
    inspector = inspect(connection)
    names = set()
    for table in V3_005_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                names.add(str(name))
    # The two resource_access_events indexes are not scoped to owned tables
    # but are part of this migration's intent — verify them via raw catalog.
    if connection.dialect.name == "sqlite":
        ra_indexes = connection.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='resource_access_events' AND name IN "
                "('ix_access_events_tenant_time','ix_access_events_resource')"
            )
        ).all()
    else:
        ra_indexes = connection.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename='resource_access_events' "
                "AND indexname IN ('ix_access_events_tenant_time','ix_access_events_resource')"
            )
        ).all()
    for row in ra_indexes:
        names.add(str(row[0]))
    return names


def _verify_catalog(connection: Connection) -> tuple:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing_tables = [table for table in V3_005_OWNED_TABLES if table not in table_names]
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
            "{0} verification failed: missing owned indexes={1}".format(
                VERSION, ",".join(missing_indexes)
            )
        )
    # The two analytic indexes are part of the spec intent.
    for ra_index in ("ix_access_events_tenant_time", "ix_access_events_resource"):
        if ra_index not in index_names:
            raise RuntimeError(
                "{0} verification failed: missing resource_access_events index={1}".format(
                    VERSION, ra_index
                )
            )
    return tuple(sorted(index_names))


def apply_v3_005(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            raise RuntimeError("{0} requires v3_001_identity to be applied first".format(VERSION))
        if "resource_access_events" not in table_names:
            raise RuntimeError(
                "{0} requires v3_003_analytics to be applied first".format(VERSION)
            )
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is not None and applied_checksum != V3_005_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_005_CHECKSUM
                )
            )
        for statement in _ddl_for_connection(connection):
            connection.execute(text(statement))
        _verify_catalog(connection)
        if applied_checksum is None:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at, checksum) "
                    "VALUES (:version, :applied_at, :checksum)"
                ),
                {"version": VERSION, "applied_at": utc_now(), "checksum": V3_005_CHECKSUM},
            )
            applied = True
        else:
            applied = False
    backfill_counts = {"support_feedback": 0, "indexes": "created IF NOT EXISTS"}
    return MigrationResult(VERSION, V3_005_CHECKSUM, applied, backfill_counts)


def verify_v3_005(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        _set_sqlite_foreign_keys(connection)
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum != V3_005_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_005_CHECKSUM
                )
            )
        index_names = _verify_catalog(connection)
        counts = {
            "support_feedback": int(
                connection.execute(
                    text("SELECT COUNT(*) FROM support_feedback")
                ).scalar_one()
            )
        }
    return VerificationResult(
        status="PASS",
        version=VERSION,
        checksum=V3_005_CHECKSUM,
        tables=V3_005_OWNED_TABLES,
        indexes=index_names,
        backfill_counts=counts,
    )


def rollback_v3_005_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            return RollbackPlan(False, (), ("schema_migrations", "resource_access_events"))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is None:
            return RollbackPlan(False, (), ("schema_migrations", "resource_access_events"))
        if applied_checksum != V3_005_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_005_CHECKSUM
                )
            )
        open_rows = connection.execute(
            text("SELECT 1 FROM support_feedback WHERE status = 'OPEN' LIMIT 1")
        ).first()
        if open_rows:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations", "audit_logs", "resource_access_events"),
                blocked_reason="open_support_feedback_present",
            )
        objects = [
            "DROP INDEX ix_access_events_resource",
            "DROP INDEX ix_access_events_tenant_time",
            "DROP INDEX ix_support_feedback_tenant_status",
            "DROP TABLE support_feedback",
        ]
        return RollbackPlan(
            True,
            tuple(objects),
            ("schema_migrations", "audit_logs", "resource_access_events"),
        )

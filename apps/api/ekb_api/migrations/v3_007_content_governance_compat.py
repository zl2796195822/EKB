"""v3_007_content_governance_compat: compensation migration for favorites (P2 content governance).

Adds the authoritative ``favorites`` projection table so the UI's star buttons
can become real (toggle, list per subject, check favourite status) without
requiring the P1 infrastructure (PostgreSQL/pgvector / S3 / Redis).

Strictly follows ADR-002: never touches any already-applied migration DDL or
checksum.  Append-only: register this as step 7 in the CHAIN runner so old
production databases that only have v3_001..v3_006 applied can add this in a
single ``apply`` call while keeping their existing projection writers valid.

The three resource types (KB, DOCUMENT, CONVERSATION) are deliberately
locked to the same enum used by ``trash_items`` (v3_002_content) and
``resource_access_events`` (v3_003_analytics) so joins against
knowledge_bases / documents / conversations stay consistent with the rest of
the app.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_007_content_governance_compat"

V3_007_OWNED_TABLES = ("favorites",)

V3_007_ROLLBACK_POLICY = (
    "retain schema_migrations, audit_logs, resource_access_events, trash_items; "
    "favorites is a user-curated projection and may be dropped with "
    "--allow-data-loss; rollback is blocked by default because favourited state "
    "is not reconstructable from another projection.  All existing v3_001..v3_006 "
    "objects are never touched by this compensation."
)

_RESOURCE_TYPES = ("KB", "DOCUMENT", "CONVERSATION")

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS favorites (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      subject_id VARCHAR(36) NOT NULL,
      resource_type VARCHAR(32) NOT NULL,
      resource_id VARCHAR(36) NOT NULL,
      favorited_at VARCHAR(32) NOT NULL,
      FOREIGN KEY (tenant_id) REFERENCES tenants (id),
      CHECK (resource_type IN ('KB', 'DOCUMENT', 'CONVERSATION')),
      UNIQUE (tenant_id, subject_id, resource_type, resource_id)
    )
    """,
    # Primary lookup: list favourites per subject, newest first.  The adapter
    # uses a simple LIMIT/OFFSET window so the favourited_at DESC ordering
    # makes EXPLAIN an index-only range scan.
    "CREATE INDEX IF NOT EXISTS ix_favorites_tenant_subject_time "
    "ON favorites (tenant_id, subject_id, favorited_at DESC)",
    # Filtered list (e.g. show only favourited conversations) — covers the
    # resource_type predicate without a second scan.
    "CREATE INDEX IF NOT EXISTS ix_favorites_tenant_subject_type "
    "ON favorites (tenant_id, subject_id, resource_type, favorited_at DESC)",
)

_BACKFILL_POLICY = {
    "resource_types": list(_RESOURCE_TYPES),
    "favorites": "no backfill; table is empty until a subject toggles a star",
    "uniqueness": "composite UNIQUE(tenant_id, subject_id, resource_type, resource_id) is the authoritative identity — a duplicate toggle is a no-op INSERT, resolved on the service side",
}

V3_007_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\n"
        + json.dumps(_BACKFILL_POLICY, sort_keys=True, separators=(",", ":"))
        + "\n"
        + "rollback="
        + V3_007_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
    ).encode()
).hexdigest()

_REQUIRED_COLUMNS = {
    "favorites": {
        "id",
        "tenant_id",
        "subject_id",
        "resource_type",
        "resource_id",
        "favorited_at",
    }
}

_REQUIRED_INDEXES = (
    "ix_favorites_tenant_subject_time",
    "ix_favorites_tenant_subject_type",
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
    """Keep DDL identical for both SQLite and PostgreSQL.

    This migration has no JSON columns, so unlike v3_005 we don't need a
    dialect-based rewrite.  The function still wraps the tuple return so
    callers get a consistent shape with every other compensation migration.
    """
    if connection.dialect.name == "postgresql":
        return _DDL
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
    for table in V3_007_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                names.add(str(name))
    return names


def _verify_catalog(connection: Connection) -> tuple:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing_tables = [table for table in V3_007_OWNED_TABLES if table not in table_names]
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
    # The composite UNIQUE on (tenant_id, subject_id, resource_type, resource_id)
    # is created inline by SQLite/PG; the inspector's get_indexes / get_unique_constraints
    # surfaces it under a db-chosen name — we just confirm the table has it.
    uniq_constraints = list(getattr(inspector, "get_unique_constraints", lambda _t: [])(
        "favorites"
    ) or [])
    column_sets = {
        frozenset(constraint.get("column_names", [])) for constraint in uniq_constraints
    }
    required_uniq = frozenset(("tenant_id", "subject_id", "resource_type", "resource_id"))
    if required_uniq not in column_sets:
        # SQLite sometimes exposes the inline UNIQUE via get_indexes() *and*
        # get_unique_constraints(); fall back to a cross-check of raw index columns.
        covered = False
        for idx in inspector.get_indexes("favorites") or []:
            if frozenset(idx.get("column_names", [])) == required_uniq and idx.get(
                "unique", False
            ):
                covered = True
                break
        if not covered:
            raise RuntimeError(
                "{0} verification failed: missing composite UNIQUE on favorites "
                "(tenant_id, subject_id, resource_type, resource_id)".format(VERSION)
            )
    return tuple(sorted(index_names))


def apply_v3_007(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            raise RuntimeError("{0} requires v3_001_identity to be applied first".format(VERSION))
        if "tenants" not in table_names:
            raise RuntimeError(
                "{0} requires v3_001_identity (tenants table) to be applied first".format(VERSION)
            )
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is not None and applied_checksum != V3_007_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_007_CHECKSUM
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
                {"version": VERSION, "applied_at": utc_now(), "checksum": V3_007_CHECKSUM},
            )
            applied = True
        else:
            applied = False
    backfill_counts = {"favorites": 0, "indexes": "created IF NOT EXISTS"}
    return MigrationResult(VERSION, V3_007_CHECKSUM, applied, backfill_counts)


def verify_v3_007(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        _set_sqlite_foreign_keys(connection)
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum != V3_007_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_007_CHECKSUM
                )
            )
        index_names = _verify_catalog(connection)
        counts = {
            "favorites": int(
                connection.execute(text("SELECT COUNT(*) FROM favorites")).scalar_one()
            )
        }
    return VerificationResult(
        status="PASS",
        version=VERSION,
        checksum=V3_007_CHECKSUM,
        tables=V3_007_OWNED_TABLES,
        indexes=index_names,
        backfill_counts=counts,
    )


def rollback_v3_007_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            return RollbackPlan(
                False,
                (),
                ("schema_migrations", "tenants", "resource_access_events", "trash_items"),
            )
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is None:
            return RollbackPlan(
                False,
                (),
                ("schema_migrations", "tenants", "resource_access_events", "trash_items"),
            )
        if applied_checksum != V3_007_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_007_CHECKSUM
                )
            )
        favorited_rows = connection.execute(
            text("SELECT 1 FROM favorites LIMIT 1")
        ).first()
        if favorited_rows:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations",),
                blocked_reason="favorites_rows_present — use --allow-data-loss to drop non-reconstructable user curation state",
            )
        objects = [
            "DROP INDEX ix_favorites_tenant_subject_type",
            "DROP INDEX ix_favorites_tenant_subject_time",
            "DROP TABLE favorites",
        ]
        return RollbackPlan(
            True,
            tuple(objects),
            ("schema_migrations", "tenants", "resource_access_events", "trash_items"),
        )

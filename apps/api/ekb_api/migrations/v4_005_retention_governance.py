"""Retention governance migration for PH2 (Trash / 30-day purge).

Per spec `10-database-changes.md` section 5 the retention model lives here:

* ``deletion_batches`` records each soft-delete generation and the 30-day
  retention window.
* ``trash_items`` gains deletion generation, purge claim CAS columns and a
  purge state machine.
* ``knowledge_bases`` / ``documents`` / ``document_versions`` / ``conversations``
  gain the deletion-generation / purge columns so the *same path* is reserved
  while a trash item is live.

The legacy migration-managed schema in this repository does **not** include a
``trash_items`` table, so this migration creates it with the full v4 retention
shape when absent and only ALTERs the additive columns when present.  Every
column addition is idempotent so the migration is repeatable and the
verification is dialect-aware (the PostgreSQL ``interval '30 days'`` check is
only asserted on PostgreSQL).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_004_postgres_cutover import V4_004_CHECKSUM

VERSION = "v4_005_retention_governance"
V4_005_OWNED_TABLES = ("deletion_batches",)
V4_005_ROLLBACK_POLICY = (
    "dry-run only; deletion batches and purge claims are retained for audit; "
    "use a forward-fix to evolve the retention contract"
)

_DELETION_BATCHES_DDL = (
    "CREATE TABLE IF NOT EXISTS deletion_batches ("
    " id VARCHAR(128) PRIMARY KEY,"
    " tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),"
    " root_resource_type VARCHAR(64) NOT NULL,"
    " root_resource_id VARCHAR(128) NOT NULL,"
    " deletion_generation INTEGER NOT NULL,"
    " deleted_by VARCHAR(128) NOT NULL REFERENCES users(id),"
    " reason TEXT,"
    " deleted_at __TIME__ NOT NULL,"
    " expires_at __TIME__ NOT NULL"
    " CHECK (expires_at = deleted_at + interval '30 days'),"
    " UNIQUE (tenant_id, root_resource_type, root_resource_id, deletion_generation)"
    ")"
)

_TRASH_ITEMS_CREATE_DDL = (
    "CREATE TABLE IF NOT EXISTS trash_items ("
    " id VARCHAR(128) PRIMARY KEY,"
    " tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),"
    " resource_type VARCHAR(64) NOT NULL,"
    " resource_id VARCHAR(128) NOT NULL,"
    " deleted_by VARCHAR(128) REFERENCES users(id),"
    " deleted_at __TIME__,"
    " restored_at __TIME__,"
    " purged_at __TIME__,"
    " expires_at __TIME__,"
    " deletion_generation INTEGER NOT NULL DEFAULT 1,"
    " deletion_batch_id VARCHAR(128) REFERENCES deletion_batches(id),"
    " purge_state VARCHAR(32) NOT NULL DEFAULT 'ELIGIBLE',"
    " claim_token VARCHAR(128),"
    " claim_fencing_token BIGINT NOT NULL DEFAULT 0,"
    " claim_expires_at __TIME__,"
    " purge_job_id VARCHAR(128) REFERENCES background_jobs(id),"
    " CHECK (purge_state IN ('ELIGIBLE','CLAIMED','PURGING_DB','PURGING_OBJECTS',"
    "'PURGING_INDEX','SUCCEEDED','RETRY_WAIT','DEAD'))"
    ")"
)

_TRASH_ITEMS_NEW_COLUMNS = {
    "deletion_generation": "INTEGER NOT NULL DEFAULT 1",
    "deletion_batch_id": "VARCHAR(128) REFERENCES deletion_batches(id)",
    "purge_state": "VARCHAR(32) NOT NULL DEFAULT 'ELIGIBLE'",
    "claim_token": "VARCHAR(128)",
    "claim_fencing_token": "BIGINT NOT NULL DEFAULT 0",
    "claim_expires_at": "__TIME__",
    "purge_job_id": "VARCHAR(128) REFERENCES background_jobs(id)",
}

_KB_NEW_COLUMNS = {
    "expires_at": "__TIME__",
    "deleted_by": "VARCHAR(128) REFERENCES users(id)",
    "deletion_batch_id": "VARCHAR(128) REFERENCES deletion_batches(id)",
    "deletion_reason": "TEXT",
    "deletion_generation": "INTEGER NOT NULL DEFAULT 0",
    "purged_at": "__TIME__",
}
_DOC_NEW_COLUMNS = {
    "deleted_at": "__TIME__",
    "expires_at": "__TIME__",
    "deleted_by": "VARCHAR(128) REFERENCES users(id)",
    "deletion_batch_id": "VARCHAR(128) REFERENCES deletion_batches(id)",
    "deletion_reason": "TEXT",
    "deletion_generation": "INTEGER NOT NULL DEFAULT 0",
    "purged_at": "__TIME__",
}
_DOC_VERSION_NEW_COLUMNS = {
    "deleted_at": "__TIME__",
    "expires_at": "__TIME__",
    "deleted_by": "VARCHAR(128) REFERENCES users(id)",
    "deletion_batch_id": "VARCHAR(128) REFERENCES deletion_batches(id)",
    "deletion_reason": "TEXT",
    "deletion_generation": "INTEGER NOT NULL DEFAULT 0",
    "purged_at": "__TIME__",
}
_CONV_NEW_COLUMNS = {
    "expires_at": "__TIME__",
    "deleted_by": "VARCHAR(128) REFERENCES users(id)",
    "deletion_batch_id": "VARCHAR(128) REFERENCES deletion_batches(id)",
    "deletion_reason": "TEXT",
    "deletion_generation": "INTEGER NOT NULL DEFAULT 0",
    "purged_at": "__TIME__",
}

_REQUIRED_COLUMNS = {
    "deletion_batches": {
        "id", "tenant_id", "root_resource_type", "root_resource_id",
        "deletion_generation", "deleted_by", "reason", "deleted_at", "expires_at",
    },
    "trash_items": {
        "id", "tenant_id", "resource_type", "resource_id", "deleted_by", "deleted_at",
        "restored_at", "purged_at", "expires_at", "deletion_generation",
        "deletion_batch_id", "purge_state", "claim_token", "claim_fencing_token",
        "claim_expires_at", "purge_job_id",
    },
    "knowledge_bases": {
        "expires_at", "deleted_by", "deletion_batch_id", "deletion_reason",
        "deletion_generation", "purged_at",
    },
    "documents": {
        "deleted_at", "expires_at", "deleted_by", "deletion_batch_id",
        "deletion_reason", "deletion_generation", "purged_at",
    },
    "document_versions": {
        "deleted_at", "expires_at", "deleted_by", "deletion_batch_id",
        "deletion_reason", "deletion_generation", "purged_at",
    },
    "conversations": {
        "expires_at", "deleted_by", "deletion_batch_id", "deletion_reason",
        "deletion_generation", "purged_at",
    },
}

_REQUIRED_INDEXES = {
    "ux_trash_items_resource_generation",
    "ux_trash_claim_token",
    "ix_trash_expiry_claim",
}

V4_005_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + " ".join(_DELETION_BATCHES_DDL.split())
        + "\n"
        + " ".join(_TRASH_ITEMS_CREATE_DDL.split())
        + "\ncolumns="
        + json.dumps(
            {
                "trash": _TRASH_ITEMS_NEW_COLUMNS,
                "kb": _KB_NEW_COLUMNS,
                "doc": _DOC_NEW_COLUMNS,
                "doc_version": _DOC_VERSION_NEW_COLUMNS,
                "conv": _CONV_NEW_COLUMNS,
            },
            sort_keys=True,
        )
        + "\nrollback="
        + V4_005_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB,time=sqlite:VARCHAR,postgresql:TIMESTAMPTZ"
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: Optional[str] = None


def _normalize_ddl(statement: str, dialect: str) -> str:
    if dialect == "postgresql":
        return statement.replace(" JSON", " JSONB").replace("__TIME__", "TIMESTAMPTZ")
    if dialect == "sqlite":
        return statement.replace("__TIME__", "VARCHAR(64)").replace(
            " CHECK (expires_at = deleted_at + interval '30 days')", ""
        )
    raise RuntimeError(f"{VERSION} unsupported database dialect: {dialect}")


def _ensure_column(connection: Connection, table: str, name: str, definition: str) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns(table)}
    if name in existing:
        return
    definition = _normalize_ddl(definition, connection.dialect.name)
    if connection.dialect.name == "postgresql":
        definition = definition.replace(" JSON", " JSONB")
    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def _ensure_trash_items(connection: Connection) -> None:
    tables = set(inspect(connection).get_table_names())
    if "trash_items" not in tables:
        connection.execute(text(_normalize_ddl(_TRASH_ITEMS_CREATE_DDL, connection.dialect.name)))
        return
    for name, definition in _TRASH_ITEMS_NEW_COLUMNS.items():
        _ensure_column(connection, "trash_items", name, definition)


def _ledger(connection: Connection, version: str) -> Any:
    return connection.execute(
        text("SELECT checksum, manifest_position FROM migration_provenance WHERE version=:version"),
        {"version": version},
    ).first()


def _audit(connection: Connection, action: str, outcome: str, detail: dict[str, Any]) -> None:
    expression = "CAST(:detail AS JSONB)" if connection.dialect.name == "postgresql" else ":detail"
    from uuid import uuid4

    connection.execute(
        text(
            "INSERT INTO migration_audit "
            "(id, version, action, outcome, detail, recorded_at) "
            f"VALUES (:id,:version,:action,:outcome,{expression},:recorded_at)"
        ),
        {
            "id": str(uuid4()),
            "version": VERSION,
            "action": action,
            "outcome": outcome,
            "detail": json.dumps(detail, sort_keys=True, separators=(",", ":")),
            "recorded_at": utc_now(),
        },
    )


def _drop_index_if_exists(connection: Connection, name: str) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text(f"DROP INDEX IF EXISTS {name}"))
    else:
        existing = {str(index["name"]) for index in inspect(connection).get_indexes("trash_items")}
        if name in existing:
            connection.execute(text(f"DROP INDEX {name}"))


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table in ("deletion_batches", "trash_items"):
        if table not in tables:
            raise RuntimeError(f"{VERSION} verification failed: missing table={table}")
    for table, required in _REQUIRED_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = required - actual
        if missing:
            raise RuntimeError(
            f"{VERSION} verification failed: {table} missing columns={sorted(missing)}"
        )
    indexes = {
        str(index["name"])
        for table in ("deletion_batches", "trash_items")
        for index in inspector.get_indexes(table)
        if index.get("name")
    }
    missing_indexes = sorted(_REQUIRED_INDEXES - indexes)
    if missing_indexes:
        raise RuntimeError(
            f"{VERSION} verification failed: missing indexes={','.join(missing_indexes)}"
        )
    if connection.dialect.name == "postgresql":
        checks = " ".join(
            str(c.get("sqltext") or "").lower()
            for c in inspector.get_check_constraints("deletion_batches")
        )
        has_30 = "interval '30 days'" in checks
        has_30_normalized = "interval 30 days" in checks.replace("'", "")
        if not has_30 and not has_30_normalized:
            # Require the retention-window check only on PostgreSQL.
            raise RuntimeError(
                f"{VERSION} verification failed: deletion_batches 30-day check missing"
            )
    return tuple(sorted(indexes))


def apply_v4_005(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_004_postgres_cutover")
        if previous is None or str(previous[0]) != V4_004_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_004 postgres cutover")
        connection.execute(text(_normalize_ddl(_DELETION_BATCHES_DDL, connection.dialect.name)))
        _ensure_trash_items(connection)
        for name, definition in _KB_NEW_COLUMNS.items():
            _ensure_column(connection, "knowledge_bases", name, definition)
        for name, definition in _DOC_NEW_COLUMNS.items():
            _ensure_column(connection, "documents", name, definition)
        for name, definition in _DOC_VERSION_NEW_COLUMNS.items():
            _ensure_column(connection, "document_versions", name, definition)
        for name, definition in _CONV_NEW_COLUMNS.items():
            _ensure_column(connection, "conversations", name, definition)
        _drop_index_if_exists(connection, "ux_trash_items_resource")
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_trash_items_resource_generation "
                "ON trash_items (tenant_id, resource_type, resource_id, deletion_generation)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_trash_claim_token "
                "ON trash_items (claim_token) WHERE claim_token IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_trash_expiry_claim "
                "ON trash_items (expires_at, purge_state) "
                "WHERE restored_at IS NULL AND purged_at IS NULL"
            )
        )
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) != V4_005_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={ledger[0]} expected={V4_005_CHECKSUM}"
            )
        _catalog(connection)
        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"retention": "schema_only"})
            return MigrationResult(VERSION, V4_005_CHECKSUM, False, {"retention": "schema_only"})
        position = int(previous[1]) + 1
        now = utc_now()
        expression = (
            "CAST(:provenance AS JSONB)"
            if connection.dialect.name == "postgresql"
            else ":provenance"
        )
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version,owner,checksum,applied_at,verify_status,provenance,"
                "manifest_position,created_at) "
                "VALUES (:version,:owner,:checksum,:applied_at,'VERIFIED',"
                f"{expression},:position,:created_at)"
            ),
            {
                "version": VERSION,
                "owner": "lifecycle",
                "checksum": V4_005_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {
                        "tables": list(V4_005_OWNED_TABLES) + ["trash_items(ensure)"],
                        "trash_create_if_absent": True,
                    },
                    sort_keys=True,
                ),
                "position": position,
                "created_at": now,
            },
        )
        _audit(
            connection,
            "APPLY",
            "PASS",
            {"tables": "deletion_batches+trash_items+4 retention columns"},
        )
    return MigrationResult(
        VERSION,
        V4_005_CHECKSUM,
        True,
        {"tables": "deletion_batches+trash_items+4 retention columns"},
    )


def verify_v4_005(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_005_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {"retention": "schema_only"})
    return VerificationResult(
        "PASS", VERSION, V4_005_CHECKSUM, ("deletion_batches", "trash_items"), indexes, {}
    )


def rollback_v4_005_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), ("deletion_batches", "trash_items"))
        if str(row[0]) != V4_005_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_005_ROLLBACK_POLICY})
    return RollbackPlan(True, (), ("deletion_batches", "trash_items"), V4_005_ROLLBACK_POLICY)

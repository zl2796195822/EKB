"""PostgreSQL cutover ledger and marker schema for PH1.

This migration establishes the *bookkeeping* tables required to perform and
verify the SQLite -> PostgreSQL cutover.  It does **not** move any data: the
actual import, sequence/constraint validation and atomic DSN switch are
operational steps that run against the production ledger (currently BLOCKED in
this environment because there is no managed production connection).

Applying v4_004 is therefore safe and reversible on any prepared legacy
schema; it only records that a cutover ledger exists so later phases can write
an auditable cutover marker once the operational gate is satisfied.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_003_provider_security import V4_003_CHECKSUM

VERSION = "v4_004_postgres_cutover"
V4_004_OWNED_TABLES = ("cutover_ledger",)
V4_004_ROLLBACK_POLICY = (
    "dry-run only; the cutover ledger is retained for audit; use a forward-fix "
    "or an approved cutover-back operation once a production cutover has occurred"
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS cutover_ledger (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) REFERENCES tenants(id),
      marker_type VARCHAR(64) NOT NULL,
      source_engine VARCHAR(32) NOT NULL,
      source_ledger_checksum VARCHAR(128),
      target_engine VARCHAR(32) NOT NULL,
      target_ledger_checksum VARCHAR(128),
      import_status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
      validation_report JSON NOT NULL DEFAULT '{}',
      imported_counts JSON,
      started_at __TIME__,
      completed_at __TIME__,
      cutover_marker_at __TIME__,
      created_at __TIME__ NOT NULL,
      CHECK (import_status IN ('PENDING','IMPORTING','VALIDATED','CUTOVER','ROLLED_BACK'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_cutover_ledger_marker "
    "ON cutover_ledger (marker_type, tenant_id, created_at DESC)",
)

_REQUIRED_COLUMNS = {
    "cutover_ledger": {
        "id", "tenant_id", "marker_type", "source_engine", "source_ledger_checksum",
        "target_engine", "target_ledger_checksum", "import_status", "validation_report",
        "imported_counts", "started_at", "completed_at", "cutover_marker_at", "created_at",
    },
}

V4_004_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\nrollback="
        + V4_004_ROLLBACK_POLICY
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


def _ddl_for(connection: Connection) -> tuple[str, ...]:
    if connection.dialect.name == "postgresql":
        return tuple(
            statement.replace(" JSON", " JSONB").replace("__TIME__", "TIMESTAMPTZ")
            for statement in _DDL
        )
    if connection.dialect.name == "sqlite":
        return tuple(statement.replace("__TIME__", "VARCHAR(64)") for statement in _DDL)
    raise RuntimeError(f"{VERSION} unsupported database dialect: {connection.dialect.name}")


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


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing = sorted(set(V4_004_OWNED_TABLES) - tables)
    if missing:
        raise RuntimeError(f"{VERSION} verification failed: missing tables={','.join(missing)}")
    actual = {column["name"] for column in inspector.get_columns("cutover_ledger")}
    if _REQUIRED_COLUMNS["cutover_ledger"] - actual:
        raise RuntimeError(
            f"{VERSION} verification failed: cutover_ledger missing columns="
            f"{sorted(_REQUIRED_COLUMNS['cutover_ledger'] - actual)}"
        )
    indexes = {
        str(index["name"])
        for table in V4_004_OWNED_TABLES
        for index in inspector.get_indexes(table)
        if index.get("name")
    }
    if "ix_cutover_ledger_marker" not in indexes:
        raise RuntimeError(f"{VERSION} verification failed: cutover marker index missing")
    return tuple(sorted(indexes))


def apply_v4_004(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_003_provider_security")
        if previous is None or str(previous[0]) != V4_003_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_003 provider security")
        for statement in _ddl_for(connection):
            connection.execute(text(statement))
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) != V4_004_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={ledger[0]} expected={V4_004_CHECKSUM}"
            )
        _catalog(connection)
        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"cutover": "schema_only"})
            return MigrationResult(VERSION, V4_004_CHECKSUM, False, {"cutover": "schema_only"})
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
                "owner": "cutover",
                "checksum": V4_004_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {"tables": list(V4_004_OWNED_TABLES), "data_move": "operational_BLOCKED"},
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
            {"tables": len(V4_004_OWNED_TABLES), "data_move": "operational_BLOCKED"},
        )
    return MigrationResult(VERSION, V4_004_CHECKSUM, True, {"tables": len(V4_004_OWNED_TABLES)})


def verify_v4_004(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_004_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {"data_move": "operational_BLOCKED"})
    return VerificationResult("PASS", VERSION, V4_004_CHECKSUM, V4_004_OWNED_TABLES, indexes, {})


def rollback_v4_004_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), tuple(V4_004_OWNED_TABLES))
        if str(row[0]) != V4_004_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_004_ROLLBACK_POLICY})
    return RollbackPlan(True, (), V4_004_OWNED_TABLES, V4_004_ROLLBACK_POLICY)

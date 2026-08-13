"""Additive, tenant-scoped route audit storage for legacy QA turns.

``model_route_events`` predates the legacy ``/qa/ask`` compatibility path and
requires a non-null ``requested_model_id``.  The default route is allowed to
resolve from environment/runtime configuration and therefore may have no
database model row.  This forward-only table keeps nullable foreign keys for
that safe case while retaining names for the route snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_008_attachments import V4_008_CHECKSUM

VERSION = "v4_009_qa_route_audit"
V4_009_OWNED_TABLES = ("qa_route_audits",)
V4_009_ROLLBACK_POLICY = (
    "dry-run only; route audit history is retained; use a forward-fix"
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS qa_route_audits (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      actor_id VARCHAR(128) NOT NULL REFERENCES users(id),
      turn_id VARCHAR(128) NOT NULL REFERENCES qa_turns(turn_id),
      requested_provider_id VARCHAR(128) REFERENCES llm_providers(id),
      requested_model_id VARCHAR(128) REFERENCES llm_models(id),
      requested_provider_name VARCHAR(256),
      requested_model_name VARCHAR(256),
      actual_provider_id VARCHAR(128) REFERENCES llm_providers(id),
      actual_model_id VARCHAR(128) REFERENCES llm_models(id),
      actual_provider_name VARCHAR(256),
      actual_model_name VARCHAR(256),
      fallback_reason VARCHAR(256),
      input_tokens INTEGER,
      output_tokens INTEGER,
      usage_source VARCHAR(32) NOT NULL,
      latency_ms INTEGER NOT NULL,
      status VARCHAR(32) NOT NULL,
      finish_reason VARCHAR(32),
      created_at __TIME__ NOT NULL,
      UNIQUE (turn_id),
      CHECK (usage_source IN ('PROVIDER_USAGE','DETERMINISTIC_ESTIMATE','UNAVAILABLE')),
      CHECK (latency_ms >= 0)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_qa_route_audits_scope "
    "ON qa_route_audits (tenant_id, actor_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_qa_route_audits_turn "
    "ON qa_route_audits (tenant_id, turn_id)",
)

V4_009_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\nrollback="
        + V4_009_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB,time=sqlite:VARCHAR,postgresql:TIMESTAMPTZ"
    ).encode()
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
    blocked_reason: str | None = None


def _ddl_for(connection: Connection) -> tuple[str, ...]:
    if connection.dialect.name == "postgresql":
        return tuple(
            statement.replace("__TIME__", "TIMESTAMPTZ") for statement in _DDL
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
    missing = sorted(set(V4_009_OWNED_TABLES) - tables)
    if missing:
        raise RuntimeError(f"{VERSION} verification failed: missing tables={','.join(missing)}")
    required = {
        "qa_route_audits": {
            "id",
            "tenant_id",
            "actor_id",
            "turn_id",
            "requested_provider_id",
            "requested_model_id",
            "actual_provider_id",
            "actual_model_id",
            "usage_source",
            "latency_ms",
            "status",
        }
    }
    for table, columns in required.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        if columns - actual:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} missing columns={sorted(columns - actual)}"
            )
    indexes = {
        str(index["name"])
        for table in V4_009_OWNED_TABLES
        for index in inspector.get_indexes(table)
        if index.get("name")
    }
    if {"ix_qa_route_audits_scope", "ix_qa_route_audits_turn"} - indexes:
        raise RuntimeError(f"{VERSION} verification failed: required indexes missing")
    return tuple(sorted(indexes))


def apply_v4_009(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_008_attachments")
        if previous is None or str(previous[0]) != V4_008_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_008 attachments")
        for statement in _ddl_for(connection):
            connection.execute(text(statement))
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) != V4_009_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: "
                f"applied={ledger[0]} expected={V4_009_CHECKSUM}"
            )
        _catalog(connection)
        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {})
            return MigrationResult(VERSION, V4_009_CHECKSUM, False, {})
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
                f"VALUES (:version,:owner,:checksum,:applied_at,'VERIFIED',"
                f"{expression},:position,:created_at)"
            ),
            {
                "version": VERSION,
                "owner": "qa-route-audit",
                "checksum": V4_009_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps({"tables": list(V4_009_OWNED_TABLES)}, sort_keys=True),
                "position": position,
                "created_at": now,
            },
        )
        _audit(connection, "APPLY", "PASS", {"tables": len(V4_009_OWNED_TABLES)})
    return MigrationResult(VERSION, V4_009_CHECKSUM, True, {"tables": len(V4_009_OWNED_TABLES)})


def verify_v4_009(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_009_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {})
    return VerificationResult("PASS", VERSION, V4_009_CHECKSUM, V4_009_OWNED_TABLES, indexes, {})


def rollback_v4_009_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), V4_009_OWNED_TABLES)
        if str(row[0]) != V4_009_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_009_ROLLBACK_POLICY})
    return RollbackPlan(True, (), V4_009_OWNED_TABLES, V4_009_ROLLBACK_POLICY)

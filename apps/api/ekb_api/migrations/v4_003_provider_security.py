"""Provider credential boundary for the remote-only model runtime.

This migration deliberately does not copy or encrypt the legacy ``api_key``
column.  The new credential table is the only v4 write path and requires the
dedicated provider master key in the service layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_002_runtime_jobs import V4_002_CHECKSUM

VERSION = "v4_003_provider_security"
V4_003_OWNED_TABLES = (
    "provider_credentials",
    "model_fallback_policies",
    "model_route_events",
)
V4_003_ROLLBACK_POLICY = (
    "dry-run only; provider credential and route history are retained; use a forward-fix"
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS provider_credentials (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      owner_user_id VARCHAR(128) REFERENCES users(id),
      ownership_scope VARCHAR(16) NOT NULL,
      ownership_key VARCHAR(256) NOT NULL,
      ciphertext TEXT NOT NULL,
      key_version VARCHAR(128) NOT NULL,
      secret_last4 VARCHAR(4),
      status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
      created_at __TIME__ NOT NULL,
      rotated_at __TIME__,
      CHECK (ownership_scope IN ('PERSONAL','TEAM')),
      CHECK (status IN ('ACTIVE','ROTATING','REVOKED')),
      CHECK ((ownership_scope = 'PERSONAL' AND owner_user_id IS NOT NULL
              AND ownership_key = 'USER:' || owner_user_id)
             OR (ownership_scope = 'TEAM' AND owner_user_id IS NULL
                 AND ownership_key = 'TEAM')),
      UNIQUE (id, tenant_id, ownership_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_provider_credentials_owner "
    "ON provider_credentials (tenant_id, ownership_scope, ownership_key, status)",
    """
    CREATE TABLE IF NOT EXISTS model_fallback_policies (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      owner_user_id VARCHAR(128) REFERENCES users(id),
      scope VARCHAR(16) NOT NULL,
      ordered_model_ids JSON NOT NULL,
      allowed_error_classes JSON NOT NULL,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      version INTEGER NOT NULL DEFAULT 1,
      CHECK (scope IN ('PERSONAL','TEAM'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_route_events (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      turn_id VARCHAR(128) NOT NULL REFERENCES qa_turns(turn_id),
      requested_model_id VARCHAR(128) NOT NULL REFERENCES llm_models(id),
      actual_model_id VARCHAR(128) REFERENCES llm_models(id),
      actual_provider_id VARCHAR(128) REFERENCES llm_providers(id),
      fallback_reason VARCHAR(256),
      input_tokens INTEGER,
      output_tokens INTEGER,
      latency_ms INTEGER,
      status VARCHAR(32) NOT NULL,
      created_at __TIME__ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_model_route_events_turn "
    "ON model_route_events (tenant_id, turn_id, created_at)",
)

_PROVIDER_COLUMNS = {
    "ownership_scope": "VARCHAR(16) NOT NULL DEFAULT 'PERSONAL'",
    "ownership_key": "VARCHAR(256)",
    "adapter_type": "VARCHAR(64)",
    "credential_id": "VARCHAR(128)",
    "egress_policy": "JSON NOT NULL DEFAULT '{}'",
    "config_version": "INTEGER NOT NULL DEFAULT 1",
    "disabled_at": "__TIME__",
}
_MODEL_COLUMNS = {
    "health_status": "VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'",
    "capability_source": "VARCHAR(32) NOT NULL DEFAULT 'BUILT_IN'",
    "capabilities_verified_at": "__TIME__",
    "config_version": "INTEGER NOT NULL DEFAULT 1",
    "disabled_at": "__TIME__",
}

V4_003_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\ncolumns="
        + json.dumps({"provider": _PROVIDER_COLUMNS, "model": _MODEL_COLUMNS}, sort_keys=True)
        + "\nrollback="
        + V4_003_ROLLBACK_POLICY
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


def _ensure_columns(connection: Connection, table: str, columns: dict[str, str]) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns(table)}
    for name, definition in columns.items():
        if name not in existing:
            definition = definition.replace("__TIME__", "TIMESTAMPTZ" if connection.dialect.name == "postgresql" else "VARCHAR(64)")
            if connection.dialect.name == "postgresql":
                definition = definition.replace(" JSON", " JSONB")
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


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
    missing = sorted(set(V4_003_OWNED_TABLES) - tables)
    if missing:
        raise RuntimeError(f"{VERSION} verification failed: missing tables={','.join(missing)}")
    required = {
        "provider_credentials": {"id", "tenant_id", "owner_user_id", "ownership_scope", "ownership_key", "ciphertext", "key_version", "secret_last4", "status"},
        "model_fallback_policies": {"id", "tenant_id", "scope", "ordered_model_ids", "allowed_error_classes", "enabled", "version"},
        "model_route_events": {"id", "tenant_id", "turn_id", "requested_model_id", "actual_model_id", "actual_provider_id", "status"},
    }
    for table, columns in required.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        if columns - actual:
            raise RuntimeError(f"{VERSION} verification failed: {table} missing columns={sorted(columns - actual)}")
    indexes = {
        str(index["name"])
        for table in V4_003_OWNED_TABLES
        for index in inspector.get_indexes(table)
        if index.get("name")
    }
    if "ix_provider_credentials_owner" not in indexes or "ix_model_route_events_turn" not in indexes:
        raise RuntimeError(f"{VERSION} verification failed: required indexes missing")
    return tuple(sorted(indexes))


def apply_v4_003(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_002_runtime_jobs")
        if previous is None or str(previous[0]) != V4_002_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_002 runtime jobs")
        for statement in _ddl_for(connection):
            connection.execute(text(statement))
        _ensure_columns(connection, "llm_providers", _PROVIDER_COLUMNS)
        _ensure_columns(connection, "llm_models", _MODEL_COLUMNS)
        connection.execute(
            text(
                "UPDATE llm_providers SET ownership_key = "
                "CASE WHEN ownership_scope='TEAM' THEN 'TEAM' ELSE 'USER:' || user_id END "
                "WHERE ownership_key IS NULL"
            )
        )
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) != V4_003_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch: applied={ledger[0]} expected={V4_003_CHECKSUM}")
        _catalog(connection)
        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"legacy_api_key_rows": 0})
            return MigrationResult(VERSION, V4_003_CHECKSUM, False, {"legacy_api_key_rows": 0})
        position = int(previous[1]) + 1
        now = utc_now()
        expression = "CAST(:provenance AS JSONB)" if connection.dialect.name == "postgresql" else ":provenance"
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version,owner,checksum,applied_at,verify_status,provenance,manifest_position,created_at) "
                f"VALUES (:version,:owner,:checksum,:applied_at,'VERIFIED',{expression},:position,:created_at)"
            ),
            {
                "version": VERSION,
                "owner": "llm-security",
                "checksum": V4_003_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps({"tables": list(V4_003_OWNED_TABLES), "remote_only": True}, sort_keys=True),
                "position": position,
                "created_at": now,
            },
        )
        _audit(connection, "APPLY", "PASS", {"tables": len(V4_003_OWNED_TABLES), "remote_only": True})
    return MigrationResult(VERSION, V4_003_CHECKSUM, True, {"tables": len(V4_003_OWNED_TABLES)})


def verify_v4_003(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_003_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {"remote_only": True})
    return VerificationResult("PASS", VERSION, V4_003_CHECKSUM, V4_003_OWNED_TABLES, indexes, {})


def rollback_v4_003_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), tuple(V4_003_OWNED_TABLES))
        if str(row[0]) != V4_003_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_003_ROLLBACK_POLICY})
    return RollbackPlan(True, (), V4_003_OWNED_TABLES, V4_003_ROLLBACK_POLICY)

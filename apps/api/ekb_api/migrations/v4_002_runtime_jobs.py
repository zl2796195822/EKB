"""Durable runtime jobs, attempts, leases, workers and outbox tables."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_001_migration_provenance import V4_001_CHECKSUM

VERSION = "v4_002_runtime_jobs"
V4_002_OWNED_TABLES = (
    "background_jobs",
    "background_job_attempts",
    "worker_heartbeats",
    "scheduler_leases",
    "scheduler_runs",
    "job_outbox",
)
V4_002_ROLLBACK_POLICY = (
    "dry-run only; runtime jobs, attempts, worker heartbeats and outbox are retained; "
    "use a forward-fix or an approved data-retention operation"
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS background_jobs (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      job_type VARCHAR(128) NOT NULL,
      idempotency_key VARCHAR(512) NOT NULL,
      state VARCHAR(32) NOT NULL,
      priority INTEGER NOT NULL DEFAULT 0,
      max_attempts INTEGER NOT NULL,
      payload JSON NOT NULL,
      available_at __TIME__ NOT NULL,
      lease_owner VARCHAR(256),
      lease_expires_at __TIME__,
      heartbeat_at __TIME__,
      error_code VARCHAR(128),
      sanitized_error JSON,
      created_at __TIME__ NOT NULL,
      updated_at __TIME__ NOT NULL,
      UNIQUE (tenant_id, job_type, idempotency_key),
      CHECK (state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED',
                      'CANCEL_REQUESTED','CANCELLED','DEAD')),
      CHECK (max_attempts >= 1),
      CHECK (
        (state IN ('RUNNING','CANCEL_REQUESTED') AND lease_owner IS NOT NULL
         AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)
        OR
        (state NOT IN ('RUNNING','CANCEL_REQUESTED') AND lease_owner IS NULL
         AND lease_expires_at IS NULL AND heartbeat_at IS NULL)
      )
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_jobs_claim "
    "ON background_jobs (state, available_at, priority DESC) "
    "WHERE state IN ('QUEUED','RETRY_WAIT')",
    """
    CREATE TABLE IF NOT EXISTS background_job_attempts (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      job_id VARCHAR(128) NOT NULL,
      attempt_no INTEGER NOT NULL,
      worker_id VARCHAR(256),
      state VARCHAR(32) NOT NULL,
      started_at __TIME__ NOT NULL,
      ended_at __TIME__,
      sanitized_error JSON,
      UNIQUE (job_id, attempt_no),
      FOREIGN KEY (job_id) REFERENCES background_jobs(id) ON DELETE CASCADE,
      CHECK (state IN ('RUNNING','SUCCEEDED','FAILED','CANCELLED','LEASE_EXPIRED')),
      CHECK ((state = 'RUNNING' AND worker_id IS NOT NULL AND ended_at IS NULL) OR
             (state <> 'RUNNING' AND ended_at IS NOT NULL))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_job_attempts_job_time "
    "ON background_job_attempts (job_id, attempt_no DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_attempts_one_running "
    "ON background_job_attempts (job_id) WHERE state = 'RUNNING'",
    """
    CREATE TABLE IF NOT EXISTS worker_heartbeats (
      worker_id VARCHAR(256) PRIMARY KEY,
      worker_type VARCHAR(128) NOT NULL,
      queues JSON NOT NULL,
      version VARCHAR(128) NOT NULL,
      heartbeat_at __TIME__ NOT NULL,
      started_at __TIME__ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduler_leases (
      schedule_name VARCHAR(256) PRIMARY KEY,
      owner_id VARCHAR(256) NOT NULL,
      lease_expires_at __TIME__ NOT NULL,
      fencing_token BIGINT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scheduler_runs (
      id VARCHAR(128) PRIMARY KEY,
      schedule_name VARCHAR(256) NOT NULL,
      scope_type VARCHAR(32) NOT NULL,
      tenant_id VARCHAR(128) REFERENCES tenants(id),
      started_at __TIME__ NOT NULL,
      ended_at __TIME__,
      status VARCHAR(32) NOT NULL,
      counters JSON NOT NULL,
      sanitized_error JSON,
      CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','CANCELLED')),
      CHECK (scope_type IN ('PLATFORM','TENANT')),
      CHECK ((scope_type = 'PLATFORM' AND tenant_id IS NULL) OR
             (scope_type = 'TENANT' AND tenant_id IS NOT NULL))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_outbox (
      id VARCHAR(128) PRIMARY KEY,
      tenant_id VARCHAR(128) NOT NULL REFERENCES tenants(id),
      aggregate_type VARCHAR(128) NOT NULL,
      aggregate_id VARCHAR(128) NOT NULL,
      event_type VARCHAR(128) NOT NULL,
      payload JSON NOT NULL,
      published_at __TIME__,
      created_at __TIME__ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_job_outbox_pending "
    "ON job_outbox (published_at, created_at) WHERE published_at IS NULL",
)

_REQUIRED_COLUMNS = {
    "background_jobs": {
        "id", "tenant_id", "job_type", "idempotency_key", "state", "priority",
        "max_attempts", "payload", "available_at", "lease_owner", "lease_expires_at",
        "heartbeat_at", "error_code", "sanitized_error", "created_at", "updated_at",
    },
    "background_job_attempts": {
        "id", "tenant_id", "job_id", "attempt_no", "worker_id", "state", "started_at",
        "ended_at", "sanitized_error",
    },
    "worker_heartbeats": {
        "worker_id", "worker_type", "queues", "version", "heartbeat_at", "started_at",
    },
    "scheduler_leases": {
        "schedule_name", "owner_id", "lease_expires_at", "fencing_token",
    },
    "scheduler_runs": {
        "id", "schedule_name", "scope_type", "tenant_id", "started_at", "ended_at",
        "status", "counters", "sanitized_error",
    },
    "job_outbox": {
        "id", "tenant_id", "aggregate_type", "aggregate_id", "event_type", "payload",
        "published_at", "created_at",
    },
}

V4_002_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\nrollback="
        + V4_002_ROLLBACK_POLICY
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


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _ddl_for_connection(connection: Connection) -> tuple[str, ...]:
    if connection.dialect.name == "postgresql":
        return tuple(
            statement.replace(" JSON", " JSONB").replace("__TIME__", "TIMESTAMPTZ")
            for statement in _DDL
        )
    if connection.dialect.name == "sqlite":
        return tuple(statement.replace("__TIME__", "VARCHAR(64)") for statement in _DDL)
    raise RuntimeError(f"{VERSION} unsupported database dialect: {connection.dialect.name}")


def _ledger_row(connection: Connection, version: str) -> Any:
    return connection.execute(
        text("SELECT checksum, manifest_position FROM migration_provenance WHERE version=:version"),
        {"version": version},
    ).first()


def _catalog_index_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    names = set()
    for table in V4_002_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            if index.get("name"):
                names.add(str(index["name"]))
    return names


def _constraint_texts(connection: Connection, table: str) -> tuple[str, ...]:
    return tuple(
        str(item.get("sqltext") or "").lower()
        for item in inspect(connection).get_check_constraints(table)
    )


def _foreign_key_facts(connection: Connection) -> set[tuple[str, str, str, str]]:
    facts: set[tuple[str, str, str, str]] = set()
    inspector = inspect(connection)
    for table in V4_002_OWNED_TABLES:
        for foreign_key in inspector.get_foreign_keys(table):
            constrained = tuple(
                str(value) for value in foreign_key.get("constrained_columns") or ()
            )
            referred = tuple(str(value) for value in foreign_key.get("referred_columns") or ())
            if len(constrained) == 1 and len(referred) == 1:
                facts.add(
                    (table, constrained[0], str(foreign_key.get("referred_table")), referred[0])
                )
    return facts


def _index_definition(connection: Connection, name: str) -> str:
    if connection.dialect.name == "sqlite":
        value = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:name"),
            {"name": name},
        ).scalar_one_or_none()
        return str(value or "").lower()
    if connection.dialect.name == "postgresql":
        value = connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND indexname = :name"
            ),
            {"name": name},
        ).scalar_one_or_none()
        return str(value or "").lower()
    raise RuntimeError(f"{VERSION} unsupported database dialect: {connection.dialect.name}")


def _verify_required_constraints(connection: Connection) -> None:
    expected_foreign_keys = {
        ("background_jobs", "tenant_id", "tenants", "id"),
        ("background_job_attempts", "tenant_id", "tenants", "id"),
        ("background_job_attempts", "job_id", "background_jobs", "id"),
        ("scheduler_runs", "tenant_id", "tenants", "id"),
        ("job_outbox", "tenant_id", "tenants", "id"),
    }
    missing_foreign_keys = sorted(expected_foreign_keys - _foreign_key_facts(connection))
    if missing_foreign_keys:
        raise RuntimeError(
            f"{VERSION} verification failed: missing foreign keys={missing_foreign_keys}"
        )

    job_checks = " ".join(_constraint_texts(connection, "background_jobs"))
    required_job_tokens = (
        "queued",
        "running",
        "retry_wait",
        "max_attempts",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
    )
    if not all(token in job_checks for token in required_job_tokens):
        raise RuntimeError(f"{VERSION} verification failed: job state/lease checks missing")

    attempt_checks = " ".join(_constraint_texts(connection, "background_job_attempts"))
    if not all(
        token in attempt_checks
        for token in ("running", "lease_expired", "worker_id", "ended_at")
    ):
        raise RuntimeError(f"{VERSION} verification failed: attempt state checks missing")

    scheduler_checks = " ".join(_constraint_texts(connection, "scheduler_runs"))
    if not all(token in scheduler_checks for token in ("platform", "tenant", "tenant_id")):
        raise RuntimeError(f"{VERSION} verification failed: scheduler scope checks missing")

    claim_definition = _index_definition(connection, "ix_jobs_claim")
    if not all(
        token in claim_definition for token in ("where", "queued", "retry_wait")
    ):
        raise RuntimeError(
            f"{VERSION} verification failed: ix_jobs_claim is not the required partial index"
        )

    running_definition = _index_definition(connection, "uq_job_attempts_one_running")
    if "where" not in running_definition or "running" not in running_definition:
        raise RuntimeError(
            f"{VERSION} verification failed: active attempt uniqueness guard missing"
        )

    pending_definition = _index_definition(connection, "ix_job_outbox_pending")
    if "published_at" not in pending_definition or "where" not in pending_definition:
            raise RuntimeError(f"{VERSION} verification failed: pending outbox index missing")

    unique_constraints = {
        frozenset(constraint.get("column_names") or ())
        for table in ("background_jobs", "background_job_attempts")
        for constraint in inspect(connection).get_unique_constraints(table)
    }
    unique_indexes = {
        frozenset(index.get("column_names") or ())
        for table in ("background_jobs", "background_job_attempts")
        for index in inspect(connection).get_indexes(table)
        if index.get("unique")
    }
    expected_unique = {
        frozenset(("tenant_id", "job_type", "idempotency_key")),
        frozenset(("job_id", "attempt_no")),
    }
    if not expected_unique <= unique_constraints.union(unique_indexes):
        raise RuntimeError(
            f"{VERSION} verification failed: required uniqueness is missing"
        )

    aggregate_unique = {"tenant_id", "event_type", "aggregate_type", "aggregate_id"}
    for constraint in inspect(connection).get_unique_constraints("job_outbox"):
        if set(constraint.get("column_names") or ()) == aggregate_unique:
            raise RuntimeError(
                f"{VERSION} verification failed: outbox aggregate uniqueness is over-constraining"
            )


def _verify_catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    table_names = set(inspect(connection).get_table_names())
    missing_tables = sorted(set(V4_002_OWNED_TABLES) - table_names)
    if missing_tables:
        raise RuntimeError(
            f"{VERSION} verification failed: missing tables={','.join(missing_tables)}"
        )
    for table, required in _REQUIRED_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(required - actual)
        if missing:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} missing columns={','.join(missing)}"
            )
    indexes = _catalog_index_names(connection)
    required_indexes = {
        "ix_jobs_claim",
        "ix_job_attempts_job_time",
        "uq_job_attempts_one_running",
        "ix_job_outbox_pending",
    }
    missing_indexes = sorted(required_indexes - indexes)
    if missing_indexes:
        raise RuntimeError(
            f"{VERSION} verification failed: missing indexes={','.join(missing_indexes)}"
        )
    _verify_required_constraints(connection)
    return tuple(sorted(indexes))


def _record_audit(
    connection: Connection,
    *,
    action: str,
    outcome: str,
    detail: dict[str, Any],
) -> None:
    from uuid import uuid4

    detail_expression = (
        "CAST(:detail AS JSONB)"
        if connection.dialect.name == "postgresql"
        else ":detail"
    )
    connection.execute(
        text(
            "INSERT INTO migration_audit "
            "(id, version, action, outcome, detail, recorded_at) "
            f"VALUES (:id, :version, :action, :outcome, {detail_expression}, :recorded_at)"
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


def _ensure_provenance_row(connection: Connection, now: str) -> bool:
    row = _ledger_row(connection, VERSION)
    if row is not None:
        if str(row[0]) != V4_002_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={row[0]} expected={V4_002_CHECKSUM}"
            )
        return False
    previous = _ledger_row(connection, "v4_001_migration_provenance")
    if previous is None or str(previous[0]) != V4_001_CHECKSUM:
        raise RuntimeError(f"{VERSION} requires verified v4_001 provenance")
    position = int(previous[1]) + 1
    connection.execute(
        text(
            "INSERT INTO migration_provenance "
            "(version, owner, checksum, applied_at, verify_status, provenance, "
            "manifest_position, created_at) VALUES "
            "(:version, :owner, :checksum, :applied_at, :verify_status, :provenance, "
            ":manifest_position, :created_at)"
        ),
        {
            "version": VERSION,
            "owner": "runtime",
            "checksum": V4_002_CHECKSUM,
            "applied_at": now,
            "verify_status": "VERIFIED",
            "provenance": json.dumps(
                {"manifest_position": position, "tables": list(V4_002_OWNED_TABLES)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "manifest_position": position,
            "created_at": now,
        },
    )
    return True


def apply_v4_002(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        if "migration_provenance" not in set(inspect(connection).get_table_names()):
            raise RuntimeError(f"{VERSION} requires v4_001_migration_provenance")
        for statement in _ddl_for_connection(connection):
            connection.execute(text(statement))
        now = utc_now()
        applied = _ensure_provenance_row(connection, now)
        _verify_catalog(connection)
        _record_audit(
            connection,
            action="APPLY",
            outcome="PASS" if applied else "NO_OP",
            detail={"tables": len(V4_002_OWNED_TABLES)},
        )
    return MigrationResult(
        VERSION,
        V4_002_CHECKSUM,
        applied,
        {"tables": len(V4_002_OWNED_TABLES), "objects": "created IF NOT EXISTS"},
    )


def verify_v4_002(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        row = _ledger_row(connection, VERSION)
        if row is None or str(row[0]) != V4_002_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch or missing provenance row")
        previous = _ledger_row(connection, "v4_001_migration_provenance")
        if previous is None or int(row[1]) != int(previous[1]) + 1:
            raise RuntimeError(f"{VERSION} verification failed: manifest append order drift")
        indexes = _verify_catalog(connection)
        _record_audit(
            connection,
            action="VERIFY",
            outcome="PASS",
            detail={"tables": len(V4_002_OWNED_TABLES)},
        )
    return VerificationResult(
        "PASS",
        VERSION,
        V4_002_CHECKSUM,
        V4_002_OWNED_TABLES,
        indexes,
        {"tables": len(V4_002_OWNED_TABLES)},
    )


def rollback_v4_002_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        if _ledger_row(connection, VERSION) is None:
            return RollbackPlan(False, (), ("migration_provenance", "migration_audit"))
        _record_audit(
            connection,
            action="ROLLBACK_DRY_RUN",
            outcome="BLOCKED",
            detail={"reason": "runtime_state_retained"},
        )
    return RollbackPlan(
        True,
        (),
        ("migration_provenance", "migration_audit", *V4_002_OWNED_TABLES),
        blocked_reason="runtime_state_retained",
    )

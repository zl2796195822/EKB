"""Conversation Engine Unification migration (PH0).

Per spec ``docs/specs/ekb-ai-assistant-conversation/03-api-db-sse-contract.md``
section 4, this additive migration owns the durable conversation engine
infrastructure:

* ``turn_attempts`` — one row per generation attempt inside a Turn.  Carries
  the immutable capability snapshot, actual provider/model, state, error code
  and token usage.  Attempt-level state is a simplified vocabulary
  (QUEUED/RUNNING/RETRY_WAIT/COMPLETED/STOPPED/FAILED) distinct from the
  Turn-level state machine in ``qa_turns.status``.
* ``turn_events`` — append-only event log keyed by ``(turn_id, seq)``.  Every
  SSE event is persisted here before broadcast, enabling ``Last-Event-ID``
  cursor replay.  Rows expire after the retention window so replay is bounded.
* ``turn_context_manifests`` — per-attempt context audit manifest.  Stores
  only hashes and IDs, never secrets, attachment plaintext, full prompts or
  image base64.

ALTERs on ``qa_turns`` add the client idempotency key, generation job lease
pointer, active attempt pointer, cancel timestamp and last event seq.

The migration is forward-only and additive: existing tables, columns and
checksums are never modified.  Legacy lowercase turn status has already been
canonicalised by ``v4_007``; this migration audits but does not rewrite it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_009_qa_route_audit import V4_009_CHECKSUM

VERSION = "v4_010_conversation_engine_unification"
V4_010_OWNED_TABLES = (
    "turn_attempts",
    "turn_events",
    "turn_context_manifests",
)
V4_010_ROLLBACK_POLICY = (
    "dry-run only; attempts, events and manifests are conversation facts and "
    "must not be dropped; use a forward-fix to evolve the engine contract"
)

# Attempt-level state vocabulary (simplified vs. the Turn-level state machine).
ATTEMPT_STATES = (
    "QUEUED",
    "RUNNING",
    "RETRY_WAIT",
    "COMPLETED",
    "STOPPED",
    "FAILED",
)
_ATTEMPT_STATE_SQL_LIST = ",".join(f"'{state}'" for state in ATTEMPT_STATES)

# ---- Canonical DDL (dialect-neutral with __TIME__ / json placeholders) ----

_TURN_ATTEMPTS_DDL = (
    "CREATE TABLE IF NOT EXISTS turn_attempts ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,"
    " attempt_no integer NOT NULL CHECK (attempt_no >= 1),"
    " actual_provider_id text,"
    " actual_model_id text,"
    " remote_model_name text,"
    " capability_snapshot json NOT NULL,"
    " sampling_snapshot json NOT NULL DEFAULT '{}',"
    " state text NOT NULL CHECK (state IN ("
    + _ATTEMPT_STATE_SQL_LIST
    + ")),"
    " error_code text,"
    " retryable boolean NOT NULL DEFAULT false,"
    " provider_cancel_supported boolean,"
    " started_at __TIME__,"
    " first_token_at __TIME__,"
    " completed_at __TIME__,"
    " lease_expires_at __TIME__,"
    " input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),"
    " output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),"
    " created_at __TIME__ NOT NULL,"
    " UNIQUE (turn_id, attempt_no)"
    ")"
)

_TURN_EVENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS turn_events ("
    " id bigserial PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,"
    " seq integer NOT NULL CHECK (seq >= 1),"
    " event_type text NOT NULL,"
    " payload json NOT NULL,"
    " created_at __TIME__ NOT NULL,"
    " expires_at __TIME__ NOT NULL,"
    " UNIQUE (turn_id, seq)"
    ")"
)

_TURN_CONTEXT_MANIFESTS_DDL = (
    "CREATE TABLE IF NOT EXISTS turn_context_manifests ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,"
    " attempt_id text NOT NULL REFERENCES turn_attempts(id) ON DELETE CASCADE,"
    " manifest_hash char(64) NOT NULL,"
    " system_prompt_version text NOT NULL,"
    " capability_snapshot_hash char(64) NOT NULL,"
    " available_input_tokens integer NOT NULL CHECK (available_input_tokens >= 0),"
    " used_input_tokens integer NOT NULL CHECK (used_input_tokens >= 0),"
    " reserved_output_tokens integer NOT NULL CHECK (reserved_output_tokens >= 0),"
    " provider_safety_margin integer NOT NULL CHECK (provider_safety_margin >= 0),"
    " component_refs json NOT NULL,"
    " compaction_summary_id text REFERENCES context_summaries(id),"
    " created_at __TIME__ NOT NULL,"
    " UNIQUE (attempt_id),"
    " CHECK (used_input_tokens <= available_input_tokens)"
    ")"
)

# ALTER column additions on qa_turns (additive, nullable / default).
_QA_TURN_COLUMNS = {
    "client_turn_id": "text",
    "generation_job_id": "text",
    "active_attempt_id": "text",
    "cancel_requested_at": "__TIME__",
    "last_event_seq": "integer NOT NULL DEFAULT 0",
}

_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_turn_events_replay"
    " ON turn_events (tenant_id, turn_id, seq)",
    "CREATE INDEX IF NOT EXISTS ix_turn_events_expiry"
    " ON turn_events (expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_turn_attempts_turn"
    " ON turn_attempts (tenant_id, turn_id, attempt_no)",
    "CREATE INDEX IF NOT EXISTS ix_turn_attempts_lease"
    " ON turn_attempts (state, lease_expires_at) WHERE lease_expires_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_turn_context_manifests_turn"
    " ON turn_context_manifests (tenant_id, turn_id)",
    # Idempotency: one Turn per (tenant, actor, conversation, client_turn_id).
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_qa_turns_client_id"
    " ON qa_turns (tenant_id, actor_id, conversation_id, client_turn_id)"
    " WHERE client_turn_id IS NOT NULL",
]

# Canonical DDL used to derive the migration checksum (module-load invariant).
_CANONICAL_DDL = "\n".join(
    [
        _TURN_ATTEMPTS_DDL,
        _TURN_EVENTS_DDL,
        _TURN_CONTEXT_MANIFESTS_DDL,
    ]
    + [
        f"ALTER TABLE qa_turns ADD COLUMN {name} {definition}"
        for name, definition in _QA_TURN_COLUMNS.items()
    ]
    + _INDEX_DDL
)

V4_010_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + " ".join(_CANONICAL_DDL.split())
        + "\nrollback="
        + V4_010_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB,time=sqlite:VARCHAR,postgresql:TIMESTAMPTZ"
        + "\nbigserial=sqlite:INTEGER,postgresql:BIGSERIAL"
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
        return (
            statement.replace(" json ", " JSONB ")
            .replace(" json,", " JSONB,")
            .replace(" json)", " JSONB)")
            .replace(" json NOT NULL", " JSONB NOT NULL")
            .replace(" json DEFAULT", " JSONB DEFAULT")
            .replace(" bigserial", " BIGSERIAL")
            .replace("__TIME__", "TIMESTAMPTZ")
        )
    if dialect == "sqlite":
        return statement.replace(" bigserial", " INTEGER").replace("__TIME__", "VARCHAR(64)")
    raise RuntimeError(f"{VERSION} unsupported database dialect: {dialect}")


def _create_tables_ddl() -> list[str]:
    return [_TURN_ATTEMPTS_DDL, _TURN_EVENTS_DDL, _TURN_CONTEXT_MANIFESTS_DDL]


def _alter_columns() -> list[tuple[str, str, str]]:
    return [("qa_turns", name, definition) for name, definition in _QA_TURN_COLUMNS.items()]


def _ensure_column(connection: Connection, table: str, name: str, definition: str) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns(table)}
    if name in existing:
        return
    connection.execute(
        text(
            f"ALTER TABLE {table} ADD COLUMN {name} "
            f"{_normalize_ddl(definition, connection.dialect.name)}"
        )
    )


def _ledger(connection: Connection, version: str) -> Any:
    return connection.execute(
        text(
            "SELECT checksum, manifest_position FROM migration_provenance "
            "WHERE version=:version"
        ),
        {"version": version},
    ).first()


def _audit(connection: Connection, action: str, outcome: str, detail: dict[str, Any]) -> None:
    from uuid import uuid4

    expression = "CAST(:detail AS JSONB)" if connection.dialect.name == "postgresql" else ":detail"
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
    for table in V4_010_OWNED_TABLES:
        if table not in tables:
            raise RuntimeError(f"{VERSION} verification failed: missing table={table}")

    expected_columns = {
        "turn_attempts": {
            "id",
            "tenant_id",
            "turn_id",
            "attempt_no",
            "capability_snapshot",
            "state",
            "created_at",
            "lease_expires_at",
        },
        "turn_events": {
            "id",
            "tenant_id",
            "turn_id",
            "seq",
            "event_type",
            "payload",
            "created_at",
            "expires_at",
        },
        "turn_context_manifests": {
            "id",
            "tenant_id",
            "turn_id",
            "attempt_id",
            "manifest_hash",
            "available_input_tokens",
            "used_input_tokens",
            "component_refs",
        },
        "qa_turns": set(_QA_TURN_COLUMNS),
    }
    for table, expected in expected_columns.items():
        present = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(expected - present)
        if missing:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} missing columns={','.join(missing)}"
            )

    indexes: set[str] = set()
    for table in V4_010_OWNED_TABLES + ("qa_turns",):
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                indexes.add(str(name))
    expected_indexes = {
        "ix_turn_events_replay",
        "ix_turn_events_expiry",
        "ix_turn_attempts_turn",
        "ix_turn_attempts_lease",
        "ix_turn_context_manifests_turn",
        "uq_qa_turns_client_id",
    }
    missing_indexes = sorted(expected_indexes - indexes)
    if missing_indexes:
        raise RuntimeError(
            f"{VERSION} verification failed: missing indexes={','.join(missing_indexes)}"
        )
    return tuple(sorted(indexes))


def _audit_legacy_disposition(connection: Connection) -> dict[str, int]:
    """Audit legacy rows for ambiguous state; never guess, record disposition.

    v4_007 already canonicalised turn status to uppercase.  Here we only count
    residual issues so the migration audit is honest about the baseline.
    """
    # Turns without an actor_id cannot satisfy the client_turn_id uniqueness
    # constraint target — they are legacy and remain unaffected because the
    # unique index is partial (WHERE client_turn_id IS NOT NULL).
    null_actor = connection.execute(
        text(
            "SELECT COUNT(*) FROM qa_turns WHERE actor_id IS NULL OR actor_id = ''"
        )
    ).scalar_one()
    return {"legacy_null_actor_turns": int(null_actor or 0)}


def apply_v4_010(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_009_qa_route_audit")
        if previous is None or str(previous[0]) != V4_009_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_009 qa_route_audit")
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) == V4_010_CHECKSUM:
            _audit(connection, "APPLY", "NO_OP", {"engine": "already_applied"})
            return MigrationResult(VERSION, V4_010_CHECKSUM, False, {"engine": "already_applied"})

        dialect = connection.dialect.name
        for statement in _create_tables_ddl():
            connection.execute(text(_normalize_ddl(statement, dialect)))
        for table, name, definition in _alter_columns():
            _ensure_column(connection, table, name, definition)
        for statement in _INDEX_DDL:
            connection.execute(text(_normalize_ddl(statement, dialect)))

        counts = _audit_legacy_disposition(connection)
        _catalog(connection)

        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"engine": "schema_refreshed"})
            return MigrationResult(VERSION, V4_010_CHECKSUM, False, {"engine": "schema_refreshed"})

        position = int(previous[1]) + 1
        now = utc_now()
        expression = (
            "CAST(:provenance AS JSONB)" if dialect == "postgresql" else ":provenance"
        )
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version, owner, checksum, applied_at, verify_status, provenance,"
                "manifest_position, created_at) "
                "VALUES (:version, :owner, :checksum, :applied_at, 'VERIFIED',"
                f"{expression}, :position, :created_at)"
            ),
            {
                "version": VERSION,
                "owner": "conversation-engine",
                "checksum": V4_010_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {
                        "tables": list(V4_010_OWNED_TABLES),
                        "audit": {key: value for key, value in counts.items()},
                    },
                    sort_keys=True,
                ),
                "position": position,
                "created_at": now,
            },
        )
        detail: dict[str, object] = {key: value for key, value in counts.items()}
        _audit(connection, "APPLY", "PASS", detail)
    return MigrationResult(VERSION, V4_010_CHECKSUM, True, counts)


def verify_v4_010(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_010_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)

        # Verify no duplicate (tenant, actor, conversation, client_turn_id)
        # can exist — the partial unique index enforces this at the DB level.
        duplicates = connection.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT tenant_id, actor_id, conversation_id, client_turn_id, COUNT(*) AS n "
                "FROM qa_turns WHERE client_turn_id IS NOT NULL "
                "GROUP BY tenant_id, actor_id, conversation_id, client_turn_id "
                "HAVING COUNT(*) > 1) d"
            )
        ).scalar_one()
        if int(duplicates or 0) > 0:
            raise RuntimeError(
                f"{VERSION} verification failed: {duplicates} duplicate client_turn_id groups"
            )

        _audit(connection, "VERIFY", "PASS", {"engine": "attempts+events+manifests"})
    return VerificationResult(
        "PASS", VERSION, V4_010_CHECKSUM, V4_010_OWNED_TABLES, indexes, {}
    )


def rollback_v4_010_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), tuple(V4_010_OWNED_TABLES))
        if str(row[0]) != V4_010_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_010_ROLLBACK_POLICY})
    return RollbackPlan(True, (), tuple(V4_010_OWNED_TABLES), V4_010_ROLLBACK_POLICY)

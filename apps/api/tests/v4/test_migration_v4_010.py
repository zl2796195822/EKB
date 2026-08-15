"""Migration verification for v4_010 Conversation Engine Unification.

Tests cover:
* apply / verify / repeat-idempotency through the full migration chain
* owned tables (turn_attempts, turn_events, turn_context_manifests) exist
* qa_turns additive columns exist
* client_turn_id partial unique index enforces idempotency
* rollback is dry-run only (forward-fix policy)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations import v4_fullstack
from ekb_api.migrations.v4_010_conversation_engine_unification import (
    V4_010_CHECKSUM,
    VERSION,
    apply_v4_010,
    rollback_v4_010_dry_run,
    verify_v4_010,
)


def _apply_full_chain(database: Path) -> int:
    return v4_fullstack.main(
        ["--database-url", f"sqlite:///{database}", "--verify"]
    )


def _seed_tenant_and_user(conn, tenant: str, user: str) -> None:
    """Insert minimal tenant + user rows to satisfy FK constraints."""
    conn.execute(
        text(
            "INSERT INTO tenants (id, name, role, policy_version, model_routing_key,"
            " egress_policy, quota_daily_qa, quota_storage_docs,"
            " quota_storage_bytes_per_file, created_at, updated_at) "
            "VALUES (:id,'tenant','OWNER',1,'default','allow',0,0,0,"
            "'2026-08-14T00:00:00Z','2026-08-14T00:00:00Z')"
        ),
        {"id": tenant},
    )
    conn.execute(
        text(
            "INSERT INTO users (id, name, email, password_hash, tenant_id, role,"
            " created_at, updated_at) "
            "VALUES (:id,'Test User',:email,'x',:tenant,'OWNER',"
            "'2026-08-14T00:00:00Z','2026-08-14T00:00:00Z')"
        ),
        {"id": user, "tenant": tenant, "email": f"{user}@test.local"},
    )


def test_v4_010_schema_has_owned_tables_and_columns(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_schema.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)

    assert _apply_full_chain(database) == 0

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    for table in ("turn_attempts", "turn_events", "turn_context_manifests"):
        assert table in table_names, f"missing owned table: {table}"

    # turn_attempts columns
    attempt_cols = {c["name"] for c in inspector.get_columns("turn_attempts")}
    assert {
        "id",
        "tenant_id",
        "turn_id",
        "attempt_no",
        "actual_provider_id",
        "actual_model_id",
        "capability_snapshot",
        "state",
        "error_code",
        "retryable",
        "provider_cancel_supported",
        "lease_expires_at",
        "input_tokens",
        "output_tokens",
        "created_at",
    } <= attempt_cols

    # turn_events columns
    event_cols = {c["name"] for c in inspector.get_columns("turn_events")}
    assert {
        "id",
        "tenant_id",
        "turn_id",
        "seq",
        "event_type",
        "payload",
        "created_at",
        "expires_at",
    } <= event_cols

    # turn_context_manifests columns
    manifest_cols = {c["name"] for c in inspector.get_columns("turn_context_manifests")}
    assert {
        "id",
        "tenant_id",
        "turn_id",
        "attempt_id",
        "manifest_hash",
        "available_input_tokens",
        "used_input_tokens",
        "reserved_output_tokens",
        "provider_safety_margin",
        "component_refs",
        "compaction_summary_id",
    } <= manifest_cols

    # qa_turns additive columns
    turn_cols = {c["name"] for c in inspector.get_columns("qa_turns")}
    assert {
        "client_turn_id",
        "generation_job_id",
        "active_attempt_id",
        "cancel_requested_at",
        "last_event_seq",
    } <= turn_cols


def test_v4_010_indexes_exist(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_indexes.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_full_chain(database) == 0

    # Query sqlite_master directly — SQLAlchemy's engine-level inspector may
    # not reflect indexes created by a separate engine instance in the same
    # process (the CLI runner builds its own engine).
    with engine.connect() as conn:
        index_names = {
            str(row[0])
            for row in conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name IN ('ix_turn_events_replay','ix_turn_events_expiry',"
                    "'ix_turn_attempts_turn','ix_turn_attempts_lease',"
                    "'ix_turn_context_manifests_turn','uq_qa_turns_client_id')"
                )
            ).all()
        }
    expected = {
        "ix_turn_events_replay",
        "ix_turn_events_expiry",
        "ix_turn_attempts_turn",
        "ix_turn_attempts_lease",
        "ix_turn_context_manifests_turn",
        "uq_qa_turns_client_id",
    }
    assert expected <= index_names, f"missing indexes: {expected - index_names}"


def test_v4_010_client_turn_id_unique_constraint(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_idempotency.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_full_chain(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-ce", "user-ce")
        # Insert a conversation + turn with a client_turn_id
        conn.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
                " updated_at) VALUES ('conv-ce-1', 'tenant-ce', 'user-ce', 'test',"
                " '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                " conversation_id, stream_version, status, last_seq, created_at,"
                " client_turn_id) "
                "VALUES ('turn-ce-1', 'req-1', 'tenant-ce', 'user-ce', 'conv-ce-1',"
                " 'v2', 'QUEUED', 0, '2026-08-14T00:00:00Z', 'client-aaa')"
            )
        )
        # Duplicate client_turn_id for same (tenant, actor, conversation) must fail
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                    " conversation_id, stream_version, status, last_seq, created_at,"
                    " client_turn_id) "
                    "VALUES ('turn-ce-2', 'req-2', 'tenant-ce', 'user-ce', 'conv-ce-1',"
                    " 'v2', 'QUEUED', 0, '2026-08-14T00:00:01Z', 'client-aaa')"
                )
            )

    with engine.begin() as conn:
        # Same client_turn_id for a different conversation is allowed
        conn.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
                " updated_at) VALUES ('conv-ce-2', 'tenant-ce', 'user-ce', 'test2',"
                " '2026-08-14T00:00:02Z', '2026-08-14T00:00:02Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                " conversation_id, stream_version, status, last_seq, created_at,"
                " client_turn_id) "
                "VALUES ('turn-ce-3', 'req-3', 'tenant-ce', 'user-ce', 'conv-ce-2',"
                " 'v2', 'QUEUED', 0, '2026-08-14T00:00:03Z', 'client-aaa')"
            )
        )
        # NULL client_turn_id is not subject to the unique constraint
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                " conversation_id, stream_version, status, last_seq, created_at,"
                " client_turn_id) "
                "VALUES ('turn-ce-4', 'req-4', 'tenant-ce', 'user-ce', 'conv-ce-1',"
                " 'v2', 'QUEUED', 0, '2026-08-14T00:00:04Z', NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                " conversation_id, stream_version, status, last_seq, created_at,"
                " client_turn_id) "
                "VALUES ('turn-ce-5', 'req-5', 'tenant-ce', 'user-ce', 'conv-ce-1',"
                " 'v2', 'QUEUED', 0, '2026-08-14T00:00:05Z', NULL)"
            )
        )


def test_v4_010_turn_events_seq_unique_per_turn(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_events.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_full_chain(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-ev", "user-ev")
        conn.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
                " updated_at) VALUES ('conv-ev-1', 'tenant-ev', 'user-ev', 'test',"
                " '2026-08-14T00:00:00Z', '2026-08-14T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                " conversation_id, stream_version, status, last_seq, created_at) "
                "VALUES ('turn-ev-1', 'req-ev-1', 'tenant-ev', 'user-ev', 'conv-ev-1',"
                " 'v2', 'RUNNING', 0, '2026-08-14T00:00:00Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO turn_events (tenant_id, turn_id, seq, event_type, payload, "
                "created_at, expires_at) "
                "VALUES ('tenant-ev', 'turn-ev-1', 1, 'message.delta', '{}', "
                "'2026-08-14T00:00:01Z', '2026-08-21T00:00:01Z')"
            )
        )
        # Duplicate (turn_id, seq) must fail
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO turn_events (tenant_id, turn_id, seq, event_type, payload, "
                    "created_at, expires_at) "
                    "VALUES ('tenant-ev', 'turn-ev-1', 1, 'message.delta', '{}', "
                    "'2026-08-14T00:00:02Z', '2026-08-21T00:00:02Z')"
                )
            )
        # Same seq for a different turn is allowed
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                " conversation_id, stream_version, status, last_seq, created_at) "
                "VALUES ('turn-ev-2', 'req-ev-2', 'tenant-ev', 'user-ev', 'conv-ev-1',"
                " 'v2', 'RUNNING', 0, '2026-08-14T00:00:03Z')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO turn_events (tenant_id, turn_id, seq, event_type, payload, "
                "created_at, expires_at) "
                "VALUES ('tenant-ev', 'turn-ev-2', 1, 'message.delta', '{}', "
                "'2026-08-14T00:00:04Z', '2026-08-21T00:00:04Z')"
            )
        )


def test_v4_010_apply_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_idempotent.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_full_chain(database) == 0

    # Second apply must not raise
    result = apply_v4_010(engine)
    assert result.applied is False
    assert result.checksum == V4_010_CHECKSUM

    # Verify must pass
    verification = verify_v4_010(engine)
    assert verification.status == "PASS"
    assert verification.version == VERSION


def test_v4_010_rollback_is_dry_run_only(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_rollback.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_full_chain(database) == 0

    plan = rollback_v4_010_dry_run(engine)
    assert plan.applied is True
    assert plan.blocked_reason is not None
    assert "dry-run only" in plan.blocked_reason
    # Tables must be retained (not dropped)
    assert set(plan.retained) == {"turn_attempts", "turn_events", "turn_context_manifests"}

    # Verify tables still exist after dry-run rollback
    inspector = inspect(engine)
    assert "turn_attempts" in inspector.get_table_names()
    assert "turn_events" in inspector.get_table_names()
    assert "turn_context_manifests" in inspector.get_table_names()


def test_v4_010_provenance_ledger_recorded(tmp_path: Path) -> None:
    database = tmp_path / "v4_010_provenance.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_full_chain(database) == 0

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT checksum, verify_status FROM migration_provenance "
                "WHERE version=:version"
            ),
            {"version": VERSION},
        ).first()
    assert row is not None
    assert row[0] == V4_010_CHECKSUM
    assert row[1] == "VERIFIED"

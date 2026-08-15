"""Unified Conversation Engine tests (PH1).

Tests cover:
* ConversationApplicationService.create_turn atomic creation
* client_turn_id idempotency (duplicate returns same turn, no duplicate messages)
* turn_attempts row creation and lease
* TurnEventStore append/replay/seq monotonicity
* Terminal first-terminal-wins CAS
* Cross-tenant isolation
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations import v4_fullstack
from ekb_api.services.conversation_app import ConversationApplicationService
from ekb_api.services.generation_worker import TurnEventStore
from ekb_api.services.turns import (
    COMPLETED,
    QUEUED,
    STREAMING,
    TurnService,
)


def _apply_migrations(database: Path) -> int:
    return v4_fullstack.main(
        ["--database-url", f"sqlite:///{database}", "--verify"]
    )


def _seed_tenant_and_user(conn, tenant: str, user: str) -> None:
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


def _seed_conversation(conn, conv_id: str, tenant: str, user: str) -> None:
    conn.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
            " updated_at) VALUES (:id,:t,:u,'Test','2026-08-14T00:00:00Z',"
            "'2026-08-14T00:00:00Z')"
        ),
        {"id": conv_id, "t": tenant, "u": user},
    )
    # Create root branch (v4_007 backfill does this, but we need it for new convs)
    from ekb_api.migrations.v4_007_chat_graph import branch_id_for_conversation

    branch_id = branch_id_for_conversation(conv_id)
    conn.execute(
        text(
            "INSERT INTO conversation_branches "
            "(id, tenant_id, conversation_id, parent_branch_id, fork_message_id,"
            " label, created_by, created_at) "
            "VALUES (:id,:t,:c,NULL,NULL,'root',:u,'2026-08-14T00:00:00Z')"
        ),
        {"id": branch_id, "t": tenant, "c": conv_id, "u": user},
    )
    conn.execute(
        text(
            "UPDATE conversations SET active_branch_id=:bid WHERE id=:id"
        ),
        {"bid": branch_id, "id": conv_id},
    )


def test_create_turn_atomic_and_idempotent(tmp_path: Path) -> None:
    """Duplicate client_turn_id returns the same Turn; no duplicate messages."""
    database = tmp_path / "ce_unified.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-ce", "user-ce")
        _seed_conversation(conn, "conv-ce", "tenant-ce", "user-ce")

    app = ConversationApplicationService(engine)

    # First create
    result1 = app.create_turn(
        tenant_id="tenant-ce",
        actor_id="user-ce",
        conversation_id="conv-ce",
        client_turn_id="client-001",
        request_id="req-001",
        prompt="Hello, what is EKB?",
    )
    assert result1.reused is False
    assert result1.state == QUEUED
    assert result1.events_url == f"/api/v1/chat/turns/{result1.turn_id}/events"
    assert result1.attempt_id  # turn_attempts row was created

    # Duplicate create — must return same turn
    result2 = app.create_turn(
        tenant_id="tenant-ce",
        actor_id="user-ce",
        conversation_id="conv-ce",
        client_turn_id="client-001",
        request_id="req-001",
        prompt="Hello, what is EKB?",
    )
    assert result2.reused is True
    assert result2.turn_id == result1.turn_id
    assert result2.user_message_id == result1.user_message_id
    assert result2.assistant_message_id == result1.assistant_message_id

    # Verify only 1 user message and 1 assistant message in the conversation
    with engine.connect() as conn:
        msg_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM messages WHERE conversation_id='conv-ce'"
            )
        ).scalar_one()
    assert int(msg_count) == 2  # 1 user + 1 assistant

    # Verify turn_attempts row exists
    with engine.connect() as conn:
        attempt_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM turn_attempts WHERE turn_id=:turn_id"
            ),
            {"turn_id": result1.turn_id},
        ).scalar_one()
    assert int(attempt_count) == 1

    # Verify qa_turns.client_turn_id was written
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT client_turn_id, active_attempt_id FROM qa_turns "
                "WHERE turn_id=:turn_id"
            ),
            {"turn_id": result1.turn_id},
        ).first()
    assert row is not None
    assert row[0] == "client-001"
    assert row[1] == result1.attempt_id


def test_turn_event_store_append_and_replay(tmp_path: Path) -> None:
    """TurnEventStore persists events with monotonic seq and supports replay."""
    database = tmp_path / "ce_events.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-ev", "user-ev")
        _seed_conversation(conn, "conv-ev", "tenant-ev", "user-ev")
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, client_turn_id,"
                " tenant_id, actor_id, conversation_id, stream_version,"
                " status, last_seq, state_version, created_at, updated_at) "
                "VALUES ('turn-ev-1','req','cti','tenant-ev','user-ev',"
                "'conv-ev','v2','QUEUED',0,0,'2026-08-14T00:00:00Z',"
                "'2026-08-14T00:00:00Z')"
            )
        )

    store = TurnEventStore(engine)

    # Append 3 events
    e1 = store.append(
        tenant_id="tenant-ev", turn_id="turn-ev-1",
        event_type="turn.accepted", payload={"state": "QUEUED"},
    )
    e2 = store.append(
        tenant_id="tenant-ev", turn_id="turn-ev-1",
        event_type="message.delta", payload={"delta": "Hello"},
    )
    e3 = store.append(
        tenant_id="tenant-ev", turn_id="turn-ev-1",
        event_type="message.delta", payload={"delta": " world"},
    )

    # Seq is strictly increasing from 1
    assert e1.seq == 1
    assert e2.seq == 2
    assert e3.seq == 3

    # Replay from beginning
    all_events = store.replay(
        tenant_id="tenant-ev", turn_id="turn-ev-1", after_seq=0
    )
    assert len(all_events) == 3
    assert [e.seq for e in all_events] == [1, 2, 3]

    # Replay from cursor (after seq 1)
    partial = store.replay(
        tenant_id="tenant-ev", turn_id="turn-ev-1", after_seq=1
    )
    assert len(partial) == 2
    assert [e.seq for e in partial] == [2, 3]

    # Last seq
    assert store.get_last_seq(tenant_id="tenant-ev", turn_id="turn-ev-1") == 3

    # SSE rendering
    sse = e2.to_sse("turn-ev-1")
    assert "id: turn-ev-1:2" in sse
    assert "event: message.delta" in sse
    assert "Hello" in sse


def test_terminal_first_wins_cas(tmp_path: Path) -> None:
    """Terminal first-terminal-wins: the first finish succeeds, second fails."""
    database = tmp_path / "ce_cas.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-cas", "user-cas")
        _seed_conversation(conn, "conv-cas", "tenant-cas", "user-cas")

    app = ConversationApplicationService(engine)
    turns = TurnService(engine)

    result = app.create_turn(
        tenant_id="tenant-cas",
        actor_id="user-cas",
        conversation_id="conv-cas",
        client_turn_id="client-cas-1",
        request_id="req-cas-1",
        prompt="Test CAS",
    )

    # Advance to STREAMING
    turns.advance(
        tenant_id="tenant-cas", turn_id=result.turn_id,
        expected_state=QUEUED, next_state="RETRIEVING",
        actor_id="user-cas",
    )
    turns.advance(
        tenant_id="tenant-cas", turn_id=result.turn_id,
        expected_state="RETRIEVING", next_state="BUILDING_CONTEXT",
        actor_id="user-cas",
    )
    turns.advance(
        tenant_id="tenant-cas", turn_id=result.turn_id,
        expected_state="BUILDING_CONTEXT", next_state=STREAMING,
        actor_id="user-cas",
    )

    # First finish (COMPLETED) succeeds
    turns.finish(
        tenant_id="tenant-cas", turn_id=result.turn_id,
        expected_state=STREAMING, terminal_state=COMPLETED,
        finish_reason="stop", last_seq=10,
        assistant_content="Test answer",
    )

    # Second finish (COMPLETED again) must fail — already terminal
    from ekb_api.services.turns import TurnTerminalConflict

    with pytest.raises(TurnTerminalConflict):
        turns.finish(
            tenant_id="tenant-cas", turn_id=result.turn_id,
            expected_state=STREAMING, terminal_state=COMPLETED,
            finish_reason="stop", last_seq=11,
            assistant_content="Test answer",
        )

    # Verify turn is COMPLETED
    turn = turns.get(tenant_id="tenant-cas", turn_id=result.turn_id)
    assert turn.state == COMPLETED
    assert turn.terminal_event == "turn.completed"


def test_cross_tenant_isolation(tmp_path: Path) -> None:
    """Turn from tenant-a cannot be accessed by tenant-b."""
    database = tmp_path / "ce_isolation.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-a", "user-a")
        _seed_tenant_and_user(conn, "tenant-b", "user-b")
        _seed_conversation(conn, "conv-a", "tenant-a", "user-a")
        _seed_conversation(conn, "conv-b", "tenant-b", "user-b")

    app = ConversationApplicationService(engine)

    # Tenant-a creates a turn
    result_a = app.create_turn(
        tenant_id="tenant-a",
        actor_id="user-a",
        conversation_id="conv-a",
        client_turn_id="client-a-1",
        request_id="req-a-1",
        prompt="Tenant A question",
    )

    # Tenant-b cannot access tenant-a's turn
    turns = TurnService(engine)
    from ekb_api.services.turns import TurnNotFound

    with pytest.raises(TurnNotFound):
        turns.get(tenant_id="tenant-b", turn_id=result_a.turn_id)

    # Tenant-b cannot cancel tenant-a's turn
    with pytest.raises(TurnNotFound):
        turns.request_cancel(
            tenant_id="tenant-b", turn_id=result_a.turn_id, actor_id="user-b"
        )

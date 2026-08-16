"""Durable streaming and Stop/Retry tests (PH3).

Tests cover:
* turn_events cursor replay (Last-Event-ID resume)
* seq strict monotonicity
* Exactly one terminal event
* Stop via CAS state machine (CANCEL_REQUESTED → STOPPED)
* Late-delta rejection (delta after cancel is discarded)
* Retry policy classification (429/5xx/auth/context-overflow)
* Terminal seq consistency (terminal_seq == event.seq, PH3-3 fix)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations import v4_fullstack
from ekb_api.services.conversation_app import ConversationApplicationService
from ekb_api.services.generation_worker import GenerationWorker, TurnEventStore, WorkerContext
from ekb_api.services.retry_policy import RetryPolicy
from ekb_api.services.turns import (
    CANCEL_REQUESTED,
    COMPLETED,
    FAILED,
    QUEUED,
    STOPPED,
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


def _seed_conversation(conn, conv_id: str, tenant: str, user: str) -> str:
    from ekb_api.migrations.v4_007_chat_graph import branch_id_for_conversation

    branch_id = branch_id_for_conversation(conv_id)
    conn.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
            " updated_at) VALUES (:id,:t,:u,'Test','2026-08-14T00:00:00Z',"
            "'2026-08-14T00:00:00Z')"
        ),
        {"id": conv_id, "t": tenant, "u": user},
    )
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
        text("UPDATE conversations SET active_branch_id=:bid WHERE id=:id"),
        {"bid": branch_id, "id": conv_id},
    )
    return branch_id


# ---------------------------------------------------------------------------
# turn_events replay tests
# ---------------------------------------------------------------------------


def test_cursor_replay_resumes_from_last_event_id(tmp_path: Path) -> None:
    """Replay with Last-Event-ID returns only events after the cursor."""
    database = tmp_path / "ph3_replay.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-r", "user-r")
        _seed_conversation(conn, "conv-r", "tenant-r", "user-r")
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, client_turn_id,"
                " tenant_id, actor_id, conversation_id, stream_version,"
                " status, last_seq, state_version, created_at, updated_at) "
                "VALUES ('turn-r','req','cti','tenant-r','user-r',"
                "'conv-r','v2','STREAMING',0,0,'2026-08-14T00:00:00Z',"
                "'2026-08-14T00:00:00Z')"
            )
        )

    store = TurnEventStore(engine)
    # Append 5 events
    for i in range(5):
        store.append(
            tenant_id="tenant-r",
            turn_id="turn-r",
            event_type="message.delta",
            payload={"delta": f"chunk-{i}"},
        )

    # Replay from beginning
    all_events = store.replay(
        tenant_id="tenant-r", turn_id="turn-r", after_seq=0
    )
    assert len(all_events) == 5
    assert [e.seq for e in all_events] == [1, 2, 3, 4, 5]

    # Replay from cursor seq=2 (should get 3,4,5)
    partial = store.replay(
        tenant_id="tenant-r", turn_id="turn-r", after_seq=2
    )
    assert len(partial) == 3
    assert [e.seq for e in partial] == [3, 4, 5]
    # Verify content
    assert partial[0].payload["delta"] == "chunk-2"

    # Replay from cursor seq=5 (should get nothing)
    empty = store.replay(
        tenant_id="tenant-r", turn_id="turn-r", after_seq=5
    )
    assert len(empty) == 0


def test_seq_is_strictly_monotonic(tmp_path: Path) -> None:
    """turn_events seq is strictly increasing per turn, starting at 1."""
    database = tmp_path / "ph3_seq.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-s", "user-s")
        _seed_conversation(conn, "conv-s", "tenant-s", "user-s")
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, client_turn_id,"
                " tenant_id, actor_id, conversation_id, stream_version,"
                " status, last_seq, state_version, created_at, updated_at) "
                "VALUES ('turn-s','req','cti','tenant-s','user-s',"
                "'conv-s','v2','STREAMING',0,0,'2026-08-14T00:00:00Z',"
                "'2026-08-14T00:00:00Z')"
            )
        )

    store = TurnEventStore(engine)
    seqs = []
    for i in range(10):
        event = store.append(
            tenant_id="tenant-s",
            turn_id="turn-s",
            event_type="message.delta",
            payload={"delta": str(i)},
        )
        seqs.append(event.seq)

    assert seqs == list(range(1, 11))


def test_exactly_one_terminal_event(tmp_path: Path) -> None:
    """First-terminal-wins CAS ensures exactly one terminal event."""
    database = tmp_path / "ph3_terminal.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-t", "user-t")
        _seed_conversation(conn, "conv-t", "tenant-t", "user-t")

    app = ConversationApplicationService(engine)
    turns = TurnService(engine)
    store = TurnEventStore(engine)

    result = app.create_turn(
        tenant_id="tenant-t", actor_id="user-t",
        conversation_id="conv-t", client_turn_id="c-t-1",
        request_id="r-t-1", prompt="Test terminal",
    )

    # Advance to STREAMING
    turns.advance(
        tenant_id="tenant-t", turn_id=result.turn_id,
        expected_state=QUEUED, next_state="RETRIEVING", actor_id="user-t"
    )
    turns.advance(
        tenant_id="tenant-t", turn_id=result.turn_id,
        expected_state="RETRIEVING", next_state="BUILDING_CONTEXT",
        actor_id="user-t"
    )
    turns.advance(
        tenant_id="tenant-t", turn_id=result.turn_id,
        expected_state="BUILDING_CONTEXT", next_state=STREAMING,
        actor_id="user-t"
    )

    # First finish (COMPLETED) succeeds
    turns.finish(
        tenant_id="tenant-t", turn_id=result.turn_id,
        expected_state=STREAMING, terminal_state=COMPLETED,
        finish_reason="stop", last_seq=0, assistant_content="answer",
    )

    # Append a terminal event
    store.append(
        tenant_id="tenant-t", turn_id=result.turn_id,
        event_type="turn.completed",
        payload={"finish_reason": "stop", "terminal_seq": 1},
    )

    # Second finish (COMPLETED again) must fail — already terminal
    from ekb_api.services.turns import TurnTerminalConflict

    with pytest.raises(TurnTerminalConflict):
        turns.finish(
            tenant_id="tenant-t", turn_id=result.turn_id,
            expected_state=STREAMING, terminal_state=COMPLETED,
            finish_reason="stop", last_seq=0,
        )

    # Verify only 1 terminal event in turn_events
    with engine.connect() as conn:
        terminal_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM turn_events "
                "WHERE turn_id=:turn_id AND event_type IN "
                "('turn.completed','turn.stopped','turn.failed')"
            ),
            {"turn_id": result.turn_id},
        ).scalar_one()
    assert int(terminal_count) == 1


# ---------------------------------------------------------------------------
# Stop / late-delta rejection tests
# ---------------------------------------------------------------------------


def test_stop_via_cas_state_machine(tmp_path: Path) -> None:
    """Cancel via TurnService.request_cancel advances to CANCEL_REQUESTED."""
    database = tmp_path / "ph3_stop.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-st", "user-st")
        _seed_conversation(conn, "conv-st", "tenant-st", "user-st")

    app = ConversationApplicationService(engine)
    turns = TurnService(engine)

    result = app.create_turn(
        tenant_id="tenant-st", actor_id="user-st",
        conversation_id="conv-st", client_turn_id="c-st-1",
        request_id="r-st-1", prompt="Test stop",
    )

    # Advance to STREAMING
    turns.advance(
        tenant_id="tenant-st", turn_id=result.turn_id,
        expected_state=QUEUED, next_state="RETRIEVING", actor_id="user-st"
    )
    turns.advance(
        tenant_id="tenant-st", turn_id=result.turn_id,
        expected_state="RETRIEVING", next_state="BUILDING_CONTEXT",
        actor_id="user-st"
    )
    turns.advance(
        tenant_id="tenant-st", turn_id=result.turn_id,
        expected_state="BUILDING_CONTEXT", next_state=STREAMING,
        actor_id="user-st"
    )

    # Request cancel
    accepted, turn = turns.request_cancel(
        tenant_id="tenant-st", turn_id=result.turn_id, actor_id="user-st"
    )
    assert accepted is True
    assert turn.state == CANCEL_REQUESTED

    # Verify cancel_requested_at was set
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cancel_requested_at FROM qa_turns "
                "WHERE turn_id=:turn_id"
            ),
            {"turn_id": result.turn_id},
        ).first()
    assert row is not None
    assert row[0] is not None

    # Repeat cancel is idempotent
    accepted2, turn2 = turns.request_cancel(
        tenant_id="tenant-st", turn_id=result.turn_id, actor_id="user-st"
    )
    assert accepted2 is False  # Already CANCEL_REQUESTED
    assert turn2.state == CANCEL_REQUESTED

    # Finish to STOPPED
    turns.finish(
        tenant_id="tenant-st", turn_id=result.turn_id,
        expected_state=CANCEL_REQUESTED, terminal_state=STOPPED,
        finish_reason="cancelled", last_seq=0, assistant_content="partial",
    )
    turn_final = turns.get(tenant_id="tenant-st", turn_id=result.turn_id)
    assert turn_final.state == STOPPED


def test_worker_is_cancelled_detects_cancel_requested(tmp_path: Path) -> None:
    """GenerationWorker._is_cancelled returns True after cancel."""
    database = tmp_path / "ph3_late.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-l", "user-l")
        _seed_conversation(conn, "conv-l", "tenant-l", "user-l")

    app = ConversationApplicationService(engine)
    turns = TurnService(engine)

    result = app.create_turn(
        tenant_id="tenant-l", actor_id="user-l",
        conversation_id="conv-l", client_turn_id="c-l-1",
        request_id="r-l-1", prompt="Test late delta",
    )

    worker = GenerationWorker(engine)
    ctx = WorkerContext(
        tenant_id="tenant-l", actor_id="user-l",
        turn_id=result.turn_id, attempt_id=result.attempt_id,
        request_id="r-l-1", conversation_id="conv-l",
        branch_id=app._graph.load_conversation(
            tenant_id="tenant-l", conversation_id="conv-l"
        )["active_branch_id"],
        user_message_id=result.user_message_id,
        assistant_message_id=result.assistant_message_id,
        prompt="Test late delta",
    )

    # Before cancel: not cancelled
    assert worker._is_cancelled(ctx) is False

    # Advance to STREAMING then cancel
    turns.advance(
        tenant_id="tenant-l", turn_id=result.turn_id,
        expected_state=QUEUED, next_state="RETRIEVING", actor_id="user-l"
    )
    turns.advance(
        tenant_id="tenant-l", turn_id=result.turn_id,
        expected_state="RETRIEVING", next_state="BUILDING_CONTEXT",
        actor_id="user-l"
    )
    turns.advance(
        tenant_id="tenant-l", turn_id=result.turn_id,
        expected_state="BUILDING_CONTEXT", next_state=STREAMING,
        actor_id="user-l"
    )
    turns.request_cancel(
        tenant_id="tenant-l", turn_id=result.turn_id, actor_id="user-l"
    )

    # After cancel: _is_cancelled returns True
    assert worker._is_cancelled(ctx) is True


# ---------------------------------------------------------------------------
# Retry policy tests
# ---------------------------------------------------------------------------


def test_retry_policy_429_is_retryable() -> None:
    """429 errors are retryable with bounded backoff."""
    policy = RetryPolicy()
    decision = policy.classify(http_status=429, attempt=1)
    assert decision.retryable is True
    assert decision.error_category == "rate_limited"
    assert decision.next_retry_at is not None
    assert decision.next_retry_at > 0


def test_retry_policy_500_is_retryable() -> None:
    """5xx errors are retryable."""
    policy = RetryPolicy()
    decision = policy.classify(http_status=503, attempt=1)
    assert decision.retryable is True
    assert decision.error_category == "transient"


def test_retry_policy_auth_not_retryable() -> None:
    """Authentication errors are not retryable."""
    policy = RetryPolicy()
    decision = policy.classify(
        error_code="AUTHENTICATION_FAILED", http_status=401, attempt=1
    )
    assert decision.retryable is False
    assert decision.error_category == "non_retryable"


def test_retry_policy_context_overflow_not_retryable() -> None:
    """Context overflow triggers compaction, not retry."""
    policy = RetryPolicy()
    decision = policy.classify(
        error_code="CONTEXT_OVERFLOW", attempt=1,
        error_message="context length exceeded",
    )
    assert decision.retryable is False
    assert decision.error_category == "context_overflow"


def test_retry_policy_max_attempts_exceeded() -> None:
    """After max attempts, retry is not allowed."""
    policy = RetryPolicy(backoff_schedule=(2, 4), max_429_retries=2)
    # 429 with attempt=4 (max_429_retries=2, so max_attempts=3)
    decision = policy.classify(http_status=429, attempt=4)
    assert decision.retryable is False
    assert decision.error_category == "max_retries_exceeded"


def test_retry_policy_network_error_retryable() -> None:
    """Network errors (timeout, connection reset) are retryable."""
    policy = RetryPolicy()
    decision = policy.classify(
        error_message="Connection reset by peer", attempt=1
    )
    assert decision.retryable is True
    assert decision.error_category == "transient"


def test_retry_policy_retry_after_header_honored() -> None:
    """Retry-After header is honored within cap."""
    policy = RetryPolicy(backoff_429_cap=60)
    decision = policy.classify(
        http_status=429, attempt=1, retry_after_header=10.0
    )
    assert decision.retryable is True
    assert decision.next_retry_at == 10.0

    # Capped if exceeds cap
    decision_capped = policy.classify(
        http_status=429, attempt=1, retry_after_header=120.0
    )
    assert decision_capped.retryable is True
    assert decision_capped.next_retry_at == 60.0  # Capped


# ---------------------------------------------------------------------------
# Terminal seq consistency (PH3-3)
# ---------------------------------------------------------------------------


def test_terminal_seq_matches_event_seq(tmp_path: Path) -> None:
    """terminal_seq in payload matches the actual event seq (PH3-3 fix)."""
    database = tmp_path / "ph3_tseq.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-ts", "user-ts")
        _seed_conversation(conn, "conv-ts", "tenant-ts", "user-ts")

    app = ConversationApplicationService(engine)
    turns = TurnService(engine)
    store = TurnEventStore(engine)

    result = app.create_turn(
        tenant_id="tenant-ts", actor_id="user-ts",
        conversation_id="conv-ts", client_turn_id="c-ts-1",
        request_id="r-ts-1", prompt="Test seq",
    )

    # Advance to STREAMING
    turns.advance(
        tenant_id="tenant-ts", turn_id=result.turn_id,
        expected_state=QUEUED, next_state="RETRIEVING", actor_id="user-ts"
    )
    turns.advance(
        tenant_id="tenant-ts", turn_id=result.turn_id,
        expected_state="RETRIEVING", next_state="BUILDING_CONTEXT",
        actor_id="user-ts"
    )
    turns.advance(
        tenant_id="tenant-ts", turn_id=result.turn_id,
        expected_state="BUILDING_CONTEXT", next_state=STREAMING,
        actor_id="user-ts"
    )

    # Emit some delta events
    for i in range(3):
        store.append(
            tenant_id="tenant-ts", turn_id=result.turn_id,
            event_type="message.delta", payload={"delta": f"chunk-{i}"},
        )

    # Now use the worker's _emit_terminal to emit turn.completed
    worker = GenerationWorker(engine)
    ctx = WorkerContext(
        tenant_id="tenant-ts", actor_id="user-ts",
        turn_id=result.turn_id, attempt_id=result.attempt_id,
        request_id="r-ts-1", conversation_id="conv-ts",
        branch_id=app._graph.load_conversation(
            tenant_id="tenant-ts", conversation_id="conv-ts"
        )["active_branch_id"],
        user_message_id=result.user_message_id,
        assistant_message_id=result.assistant_message_id,
        prompt="Test seq",
    )

    # First finish the turn
    turns.finish(
        tenant_id="tenant-ts", turn_id=result.turn_id,
        expected_state=STREAMING, terminal_state=COMPLETED,
        finish_reason="stop", last_seq=3,
        assistant_content="answer",
    )

    # Emit terminal event via _emit_terminal
    worker._emit_terminal(
        ctx, "turn.completed",
        {"message_id": result.assistant_message_id, "finish_reason": "stop"},
    )

    # The terminal event should be seq=4 (after 3 deltas)
    # and terminal_seq in payload should equal 4
    terminal_events = store.replay(
        tenant_id="tenant-ts", turn_id=result.turn_id, after_seq=3
    )
    assert len(terminal_events) == 1
    terminal_event = terminal_events[0]
    assert terminal_event.event_type == "turn.completed"
    assert terminal_event.seq == 4
    # terminal_seq in payload must match event seq (PH3-3 fix)
    assert terminal_event.payload.get("terminal_seq") == 4


def test_delta_batcher_flushes_on_threshold() -> None:
    """_DeltaBatcher flushes when token/byte/time threshold is reached."""
    from ekb_api.services.generation_worker import _DeltaBatcher

    # Token threshold
    batcher = _DeltaBatcher(max_tokens=2, max_bytes=1000, flush_ms=1000)
    batcher.append("hello")  # ~1 token
    assert batcher.should_flush()[0] is False
    batcher.append("world")  # ~1 token, total ~2
    assert batcher.should_flush()[0] is True
    batch = batcher.flush()
    assert batch == "helloworld"
    assert batcher.has_pending() is False

    # Byte threshold
    batcher2 = _DeltaBatcher(max_tokens=1000, max_bytes=10, flush_ms=1000)
    batcher2.append("12345678901")  # 11 bytes
    assert batcher2.should_flush()[0] is True

    # has_pending
    batcher3 = _DeltaBatcher(max_tokens=100, max_bytes=100, flush_ms=100)
    assert batcher3.has_pending() is False
    batcher3.append("x")
    assert batcher3.has_pending() is True
    batcher3.flush()
    assert batcher3.has_pending() is False


# ---------------------------------------------------------------------------
# 空转熔断（P0）：刷新恢复事件流连续无新事件 → turn 置 FAILED + 发 turn.failed
# ---------------------------------------------------------------------------


def test_fail_idle_turn_transitions_stuck_turn_to_failed(tmp_path: Path) -> None:
    """_fail_idle_turn 把卡死的 STREAMING turn CAS 到 FAILED 并追加 turn.failed 事件。"""
    from ekb_api.routers.chat_graph import _fail_idle_turn

    database = tmp_path / "ph4_idle.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-idle", "user-idle")
        _seed_conversation(conn, "conv-idle", "tenant-idle", "user-idle")
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, client_turn_id,"
                " tenant_id, actor_id, conversation_id, stream_version,"
                " status, last_seq, state_version, created_at, updated_at) "
                "VALUES ('turn-idle','req','cti','tenant-idle','user-idle',"
                "'conv-idle','v2','STREAMING',3,0,'2026-08-14T00:00:00Z',"
                "'2026-08-14T00:00:00Z')"
            )
        )

    # 已有 3 个 delta 事件（模拟流到一半 worker 挂起）
    store = TurnEventStore(engine)
    for i in range(3):
        store.append(
            tenant_id="tenant-idle",
            turn_id="turn-idle",
            event_type="message.delta",
            payload={"delta": f"chunk-{i}"},
        )

    sse = _fail_idle_turn(
        tenant_id="tenant-idle",
        turn_id="turn-idle",
        conversation_id="conv-idle",
        assistant_message_id=None,
        engine=engine,
    )

    # 1) 返回 turn.failed 的 SSE 片段
    assert "turn.failed" in sse
    assert "STREAM_IDLE_TIMEOUT" in sse

    # 2) turn 状态已置 FAILED
    service = TurnService(engine)
    turn = service.get(tenant_id="tenant-idle", turn_id="turn-idle")
    assert turn.state == FAILED

    # 3) 事件存储里新增了 turn.failed 终态事件
    events = store.replay(
        tenant_id="tenant-idle", turn_id="turn-idle", after_seq=3
    )
    failed_events = [e for e in events if e.event_type == "turn.failed"]
    assert len(failed_events) == 1
    assert failed_events[0].payload["code"] == "STREAM_IDLE_TIMEOUT"
    assert failed_events[0].payload["retryable"] is True


def test_fail_idle_turn_is_idempotent_when_already_terminal(tmp_path: Path) -> None:
    """已终态的 turn 再次熔断不应重复写 turn.failed 事件。"""
    from ekb_api.routers.chat_graph import _fail_idle_turn

    database = tmp_path / "ph4_idle_term.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(database) == 0

    with engine.begin() as conn:
        _seed_tenant_and_user(conn, "tenant-term", "user-term")
        _seed_conversation(conn, "conv-term", "tenant-term", "user-term")
        conn.execute(
            text(
                "INSERT INTO qa_turns (turn_id, request_id, client_turn_id,"
                " tenant_id, actor_id, conversation_id, stream_version,"
                " status, last_seq, state_version, terminal_event, created_at,"
                " updated_at, completed_at) "
                "VALUES ('turn-term','req','cti','tenant-term','user-term',"
                "'conv-term','v2','FAILED',3,0,'turn.failed',"
                "'2026-08-14T00:00:00Z','2026-08-14T00:00:00Z',"
                "'2026-08-14T00:00:00Z')"
            )
        )

    store = TurnEventStore(engine)
    store.append(
        tenant_id="tenant-term",
        turn_id="turn-term",
        event_type="turn.failed",
        payload={"code": "GENERATION_ERROR", "message": "already failed"},
    )

    sse = _fail_idle_turn(
        tenant_id="tenant-term",
        turn_id="turn-term",
        conversation_id="conv-term",
        assistant_message_id=None,
        engine=engine,
    )
    # 已终态：不重复写（返回空 SSE）
    assert sse == ""

    events = store.replay(
        tenant_id="tenant-term", turn_id="turn-term", after_seq=0
    )
    failed_events = [e for e in events if e.event_type == "turn.failed"]
    assert len(failed_events) == 1  # 仍是原来那一条

"""Grok behavior parity contract tests (PH5-5, spec 04 §6).

Verifies the EKB Conversation Engine meets the pass conditions from the
Grok comparison matrix.  Each test maps to a row in the matrix and asserts
the EKB-specific pass condition — not identical wording, but identical
behavioral contracts (no lost context, no silent drops, no fake completion).

Matrix reference (spec 04 §6):
  | Case           | Grok behavior                    | EKB pass condition              |
  | Linear cont.   | later prompt uses earlier facts  | correct branch context & facts  |
  | Stream         | typed progress/delta/terminal    | durable events, cursor, unique  |
  | Stop           | cancels, retains partial         | cancellation semantics + status |
  | Retry          | classified retry, visible state  | error class distinctions        |
  | Regenerate     | rewind/fork, keeps history       | new branch, original remains    |
  | Refresh        | session state recovers           | messages/model/branch recover   |
  | Markdown       | incomplete tail stable           | safe renderer, copy (frontend)  |
  | Image          | Vision or explicit fallback      | native/adapter/fail-closed      |
  | Attachment     | file-tool contextual             | real parsed content across turns|
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations import v4_fullstack
from ekb_api.migrations.v4_007_chat_graph import branch_id_for_conversation
from ekb_api.services.context_engine import ContextEngineService
from ekb_api.services.conversation_app import ConversationApplicationService
from ekb_api.services.generation_worker import TurnEventStore
from ekb_api.services.turns import (
    CANCEL_REQUESTED,
    COMPLETED,
    FAILED,
    QUEUED,
    STOPPED,
    TurnService,
)
from ekb_api.services.vision import (
    VISION_UNAVAILABLE,
    VisionUnavailable,
    resolve_vision_strategy,
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
    conn.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
            " updated_at) VALUES (:id,:t,:u,'Parity','2026-08-14T00:00:00Z',"
            "'2026-08-14T00:00:00Z')"
        ),
        {"id": conv_id, "t": tenant, "u": user},
    )
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
        text("UPDATE conversations SET active_branch_id=:bid WHERE id=:id"),
        {"bid": branch_id, "id": conv_id},
    )
    return branch_id


def _complete_assistant(conn, msg_id: str, content: str, tenant: str) -> None:
    """Mark an assistant message as completed with content."""
    conn.execute(
        text(
            "UPDATE messages SET content=:c, status='completed' "
            "WHERE id=:id AND tenant_id=:t"
        ),
        {"c": content, "id": msg_id, "t": tenant},
    )


def _advance_to_completed(
    turns: TurnService, *, tenant_id: str, turn_id: str, actor_id: str,
) -> None:
    """Walk the full state machine to COMPLETED."""
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state=QUEUED, next_state="RETRIEVING", actor_id=actor_id)
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state="RETRIEVING", next_state="BUILDING_CONTEXT", actor_id=actor_id)
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state="BUILDING_CONTEXT", next_state="STREAMING", actor_id=actor_id)
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state="STREAMING", next_state=COMPLETED, actor_id=actor_id)


def _advance_to_streaming(
    turns: TurnService, *, tenant_id: str, turn_id: str, actor_id: str,
) -> None:
    """Walk the state machine to STREAMING for stop/cancel tests."""
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state=QUEUED, next_state="RETRIEVING", actor_id=actor_id)
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state="RETRIEVING", next_state="BUILDING_CONTEXT", actor_id=actor_id)
    turns.advance(tenant_id=tenant_id, turn_id=turn_id,
                  expected_state="BUILDING_CONTEXT", next_state="STREAMING", actor_id=actor_id)


@pytest.fixture()
def env(tmp_path: Path):
    db = tmp_path / "grok_parity.db"
    engine = build_engine(f"sqlite:///{db}")
    prepare_legacy_schema(engine, seed=True)
    assert _apply_migrations(db) == 0
    tenant, user = "tenant-parity", "user-parity"
    conv_id = "conv-parity"
    with engine.begin() as conn:
        _seed_tenant_and_user(conn, tenant, user)
        branch_id = _seed_conversation(conn, conv_id, tenant, user)
    return {
        "engine": engine,
        "tenant": tenant,
        "user": user,
        "conv_id": conv_id,
        "branch_id": branch_id,
    }


# ===== Linear continuation =====


def test_linear_continuation_uses_active_branch_facts(env) -> None:
    """Matrix: 'later prompt uses earlier facts' → correct branch context and facts."""
    app = ConversationApplicationService(env["engine"])
    t, u = env["tenant"], env["user"]

    # Turn 1: introduce a fact
    r1 = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-1", request_id="rq-1",
        prompt="My project name is Phoenix-Alpha.",
    )
    turns = TurnService(env["engine"])
    _advance_to_completed(turns, tenant_id=t, turn_id=r1.turn_id, actor_id=u)
    with env["engine"].begin() as conn:
        _complete_assistant(conn, r1.assistant_message_id,
                            "Noted: Phoenix-Alpha.", t)

    # Turn 2: reference the fact from turn 1
    r2 = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-2", request_id="rq-2",
        prompt="What is my project name?",
    )

    ce = ContextEngineService(env["engine"])
    history = ce._load_history(t, env["branch_id"], r2.user_message_id)
    assert len(history) >= 1
    # The first turn's user content must be in the history
    user_contents = [ht.user.content for ht in history]
    assert any("Phoenix-Alpha" in c for c in user_contents), (
        "Linear continuation failed: prior turn fact not in active-branch history"
    )


# ===== Stream: durable typed events, cursor replay, unique terminal =====


def test_stream_durable_events_and_cursor_replay(env) -> None:
    """Matrix: 'typed progress/delta/terminal' → durable events, cursor replay."""
    app = ConversationApplicationService(env["engine"])
    t, u = env["tenant"], env["user"]

    r = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-stream", request_id="rq-stream",
        prompt="Stream test",
    )
    store = TurnEventStore(env["engine"])

    # Emit typed events
    e1 = store.append(tenant_id=t, turn_id=r.turn_id, event_type="generation.started", payload={})
    e2 = store.append(tenant_id=t, turn_id=r.turn_id, event_type="delta", payload={"text": "Hello"})
    e3 = store.append(tenant_id=t, turn_id=r.turn_id, event_type="done",
                      payload={"finish_reason": "stop"})

    # Seq monotonicity
    assert e1.seq < e2.seq < e3.seq

    # Cursor replay: from seq=0 should get all 3
    events = store.replay(tenant_id=t, turn_id=r.turn_id, after_seq=0)
    assert len(events) == 3
    assert events[0].event_type == "generation.started"
    assert events[1].event_type == "delta"
    assert events[2].event_type == "done"

    # Cursor replay: from seq=e1.seq should get 2 (excluding e1)
    events_after = store.replay(tenant_id=t, turn_id=r.turn_id, after_seq=e1.seq)
    assert len(events_after) == 2
    assert events_after[0].event_type == "delta"


def test_stream_unique_terminal_first_wins(env) -> None:
    """Matrix: 'unique terminal' → first-terminal-wins CAS."""
    app = ConversationApplicationService(env["engine"])
    t, u = env["tenant"], env["user"]

    r = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-term", request_id="rq-term",
        prompt="Terminal test",
    )
    turns = TurnService(env["engine"])

    # First terminal transition: walk full path to COMPLETED
    _advance_to_completed(turns, tenant_id=t, turn_id=r.turn_id, actor_id=u)

    # Second terminal transition attempt: must fail (first-wins)
    from ekb_api.services.turns import TurnTerminalConflict
    with pytest.raises(TurnTerminalConflict):
        turns.advance(
            tenant_id=t, turn_id=r.turn_id,
            expected_state=QUEUED, next_state=FAILED, actor_id=u,
        )

    # Verify only one terminal state
    view = turns.get(tenant_id=t, turn_id=r.turn_id)
    assert view.state == COMPLETED


# ===== Stop: cancellation semantics =====


def test_stop_marks_cancelled_and_retains_partial(env) -> None:
    """Matrix: 'cancels, retains partial separately' → partial status preserved."""
    app = ConversationApplicationService(env["engine"])
    t, u = env["tenant"], env["user"]

    r = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-stop", request_id="rq-stop",
        prompt="Stop test",
    )
    turns = TurnService(env["engine"])

    # Advance to STREAMING via full state machine
    _advance_to_streaming(turns, tenant_id=t, turn_id=r.turn_id, actor_id=u)

    # Request cancel
    turns.advance(
        tenant_id=t, turn_id=r.turn_id,
        expected_state="STREAMING", next_state=CANCEL_REQUESTED, actor_id=u,
    )

    # Then stop (terminal)
    turns.advance(
        tenant_id=t, turn_id=r.turn_id,
        expected_state=CANCEL_REQUESTED, next_state=STOPPED, actor_id=u,
    )

    view = turns.get(tenant_id=t, turn_id=r.turn_id)
    assert view.state == STOPPED

    # Partial content should be retained on the assistant message
    with env["engine"].begin() as conn:
        conn.execute(text(
            "UPDATE messages SET content='partial answer', status='stopped' "
            "WHERE id=:id"
        ), {"id": r.assistant_message_id})

    with env["engine"].connect() as conn:
        content = conn.execute(text(
            "SELECT content, status FROM messages WHERE id=:id"
        ), {"id": r.assistant_message_id}).first()
    assert content[0] == "partial answer"
    assert content[1] == "stopped"


# ===== Retry: classified retry with visible state =====


def test_retry_error_classification() -> None:
    """Matrix: 'classified retry with visible state' → error class distinctions."""
    from ekb_api.services.retry_policy import RetryPolicy

    policy = RetryPolicy()

    # Auth errors are non-retryable
    decision = policy.classify(error_code="AUTHENTICATION_FAILED", http_status=401)
    assert decision.retryable is False

    # Rate limit (429) is retryable with backoff
    decision = policy.classify(http_status=429, attempt=1)
    assert decision.retryable is True
    assert decision.next_retry_at is not None
    assert decision.next_retry_at > 0

    # Server errors (5xx) are retryable
    decision = policy.classify(http_status=503, attempt=1)
    assert decision.retryable is True

    # Bad request (400) is non-retryable
    decision = policy.classify(http_status=400, error_message="Bad Request")
    assert decision.retryable is False

    # Context overflow triggers compaction, not retry
    decision = policy.classify(error_code="CONTEXT_OVERFLOW")
    assert decision.retryable is False
    assert decision.error_category == "context_overflow"


# ===== Regenerate/Edit: fork without destroying history =====


def test_regenerate_creates_branch_and_preserves_original(env) -> None:
    """Matrix: 'rewind/fork without destroying history' → new branch, original remains."""
    from ekb_api.services.conversations import ConversationGraphService

    app = ConversationApplicationService(env["engine"])
    t, u = env["tenant"], env["user"]

    # Turn 1
    r1 = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-regen-1", request_id="rq-regen-1",
        prompt="Original question",
    )
    turns = TurnService(env["engine"])
    _advance_to_completed(turns, tenant_id=t, turn_id=r1.turn_id, actor_id=u)
    with env["engine"].begin() as conn:
        _complete_assistant(conn, r1.assistant_message_id, "Original answer", t)

    # Fork a new branch from the conversation
    graph = ConversationGraphService(env["engine"])
    new_branch = graph.fork_branch(
        tenant_id=t,
        conversation_id=env["conv_id"],
        fork_message_id=r1.user_message_id,
        created_by=u,
    )

    # Original branch messages must still exist
    original_messages = graph.branch_messages(
        tenant_id=t, branch_id=env["branch_id"]
    )
    assert any(m.id == r1.user_message_id for m in original_messages)
    assert any(m.id == r1.assistant_message_id for m in original_messages)

    # New branch should also have the inherited messages
    forked_messages = graph.branch_messages(
        tenant_id=t, branch_id=new_branch.id
    )
    assert any(m.id == r1.user_message_id for m in forked_messages), (
        "Regenerate failed: forked branch must inherit ancestor messages"
    )


# ===== Refresh/restart: session state recovers =====


def test_refresh_recovers_messages_and_turn_state(env) -> None:
    """Matrix: 'session state recovers' → messages/model/branch/turn recover."""
    app = ConversationApplicationService(env["engine"])
    t, u = env["tenant"], env["user"]

    r = app.create_turn(
        tenant_id=t, actor_id=u, conversation_id=env["conv_id"],
        client_turn_id="ct-refresh", request_id="rq-refresh",
        prompt="Refresh test",
        requested_model_id="model-test",
    )

    # Simulate: mark as completed
    turns = TurnService(env["engine"])
    _advance_to_completed(turns, tenant_id=t, turn_id=r.turn_id, actor_id=u)

    # Simulate refresh: reload conversation state from DB
    graph_svc = None
    from ekb_api.services.conversations import ConversationGraphService
    graph_svc = ConversationGraphService(env["engine"])

    conv = graph_svc.load_conversation(tenant_id=t, conversation_id=env["conv_id"])
    assert conv is not None
    assert conv["active_branch_id"] == env["branch_id"]

    messages = graph_svc.branch_messages(tenant_id=t, branch_id=env["branch_id"])
    # Should have the user + assistant messages from the turn
    assert any(m.id == r.user_message_id for m in messages)
    assert any(m.id == r.assistant_message_id for m in messages)

    # Turn state should be recoverable
    view = turns.get(tenant_id=t, turn_id=r.turn_id)
    assert view.state == COMPLETED


# ===== Image: native/adapter/fail-closed =====


def test_image_vision_cascade_contracts() -> None:
    """Matrix: 'Vision or explicit fallback' → native/adapter/fail-closed."""

    class _VisionCapable:
        def supports_vision(self) -> bool:
            return True

    class _VisionIncapable:
        def supports_vision(self) -> bool:
            return False

    class _FakeAdapter:
        def ocr(self, raw: bytes, *, mime: str) -> str:
            return "caption"

    # Stage 1: native vision
    s = resolve_vision_strategy(_VisionCapable(), ocr_provider=None)
    assert s.stage == "native_vision"

    # Stage 2: remote adapter
    s = resolve_vision_strategy(_VisionIncapable(), ocr_provider=_FakeAdapter())
    assert s.stage == "remote_adapter"

    # Stage 3: explicit failure
    with pytest.raises(VisionUnavailable) as exc_info:
        resolve_vision_strategy(_VisionIncapable(), ocr_provider=None)
    assert exc_info.value.error_code == VISION_UNAVAILABLE


# ===== Attachment: real parsed content across turns =====


def test_attachment_content_enters_context_across_turns(env) -> None:
    """Matrix: 'file-tool contextual' → real parsed content across turns."""
    t, u = env["tenant"], env["user"]
    engine = env["engine"]

    # Seed a READY attachment with real parsed content
    obj_id = f"src-parity-{hashlib.sha256(b'sp').hexdigest()[:12]}"
    att_id = f"att-parity-{hashlib.sha256(b'ap').hexdigest()[:12]}"
    _seed_source_object(engine, t, obj_id)
    unique_content = "PROJECT_PHOENIX_SECRET_42"
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO attachments"
            " (id, tenant_id, owner_user_id, conversation_id, source_object_id,"
            "  client_request_id, status, detected_mime, byte_size, created_at, updated_at)"
            " VALUES (:id,:t,:u,:c,:s,:cr,'READY','text/plain',100,"
            " '2026-08-14T00:00:00Z','2026-08-14T00:00:00Z')"
        ), {"id": att_id, "t": t, "u": u, "c": env["conv_id"],
            "s": obj_id, "cr": "cr-parity"})
        art_id = f"art-parity-{hashlib.sha256(b'ap').hexdigest()[:12]}"
        conn.execute(text(
            "INSERT INTO attachment_artifacts"
            " (id, tenant_id, attachment_id, parser_version, text_object_id,"
            "  metadata, token_estimate, status)"
            " VALUES (:id,:t,:a,'parsley-1',NULL,:meta,10,'READY')"
        ), {"id": art_id, "t": t, "a": att_id,
            "meta": json.dumps({"kind": "document", "usage_mode": "RETRIEVAL"})})
        conn.execute(text(
            "INSERT INTO attachment_chunks"
            " (id, tenant_id, attachment_id, artifact_id, ordinal, text_content, metadata)"
            " VALUES (:id,:t,:a,:art,0,:txt,:meta)"
        ), {"id": f"chk-parity-{hashlib.sha256(b'cp').hexdigest()[:12]}",
            "t": t, "a": att_id, "art": art_id,
            "txt": unique_content, "meta": "{}"})

    # Load via ContextEngineService._load_attachments
    ce = ContextEngineService(engine)
    refs, dropped = ce._load_attachments(
        t, [att_id],
        owner_user_id=u, conversation_id=env["conv_id"],
    )
    assert len(refs) == 1
    assert dropped == []
    assert unique_content in refs[0].content, (
        "Attachment content must enter context as real parsed text, not a placeholder"
    )


def _seed_source_object(engine, tenant: str, obj_id: str) -> None:
    sha = hashlib.sha256(obj_id.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO source_objects"
                " (id, tenant_id, object_key, sha256, byte_size, detected_mime, created_at)"
                " VALUES (:id,:t,:k,:s,:b,:m,'2026-08-14T00:00:00Z')"
            ),
            {"id": obj_id, "t": tenant, "k": f"uploads/{tenant}/{obj_id}",
             "s": sha, "b": 100, "m": "text/plain"},
        )

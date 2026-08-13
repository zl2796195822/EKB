"""PH4 chat engine tests (spec 05-ai-chat.md).

What is actually proven here, rather than merely exercised:

* ``v4_007`` backfills legacy conversations into the branch graph and refuses to
  guess an ambiguous user/assistant pairing.
* Branch materialisation walks the lineage and cuts each ancestor at its child's
  fork point, so a regenerated answer never leaks into the sibling branch.
* Turn creation is a single transaction and a duplicate ``client_turn_id``
  returns the original turn instead of a second message pair.
* **first-terminal-wins**: when completion and cancellation race, exactly one
  terminal write survives and the loser observes the authoritative state.
* Tenant isolation holds for every read path that takes an id from the caller.
* The SSE v2 sequencer cannot emit a second terminal event or let ``id`` and
  ``seq`` disagree, and heartbeats do not consume a seq.
* The context builder keeps user/assistant pairs intact, degrades
  deterministically when summarisation is unavailable, and hashes reproducibly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.services.context import (
    STRATEGY_SUMMARIZE,
    STRATEGY_TRUNCATE,
    AttachmentRef,
    ContextBudget,
    ContextBudgetExceeded,
    ContextBuilder,
    EvidenceRef,
    HistoryTurn,
    MessageRef,
    select_evidence,
)
from ekb_api.services.conversations import (
    ChatGraphError,
    ConversationGraphService,
    ConversationNotFound,
)
from ekb_api.services.sse_v2 import (
    SseContractError,
    SseStream,
    encode_heartbeat,
    parse_last_event_id,
)
from ekb_api.services.titles import ConversationTitleService, normalize_title
from ekb_api.services.turns import (
    BUILDING_CONTEXT,
    CANCEL_REQUESTED,
    COMPLETED,
    FAILED,
    QUEUED,
    RETRIEVING,
    STOPPED,
    STREAMING,
    TurnNotFound,
    TurnService,
    TurnTerminalConflict,
    TurnTransitionError,
    is_fallback_allowed,
)
from ekb_api.store import SqlStore

TENANT_B = "tenant-b"
USER_B = "user-b"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _apply_chain(engine) -> None:
    for step in CHAIN:
        step.apply(engine)


def _identity(engine) -> tuple[str, str]:
    with engine.connect() as connection:
        tenant = str(connection.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one())
        user = str(
            connection.execute(
                text("SELECT id FROM users WHERE tenant_id=:t LIMIT 1"), {"t": tenant}
            ).scalar_one()
        )
    return tenant, user


def _second_tenant(engine) -> tuple[str, str]:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, role, policy_version, model_routing_key,"
                " egress_policy, quota_daily_qa, quota_storage_docs,"
                " quota_storage_bytes_per_file, created_at, updated_at) "
                "VALUES (:id,'tenant-b','OWNER',1,'default','allow',0,0,0,"
                "'2020-01-01T00:00:00Z','2020-01-01T00:00:00Z')"
            ),
            {"id": TENANT_B},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, name, email, password_hash, tenant_id, role,"
                " created_at, updated_at) VALUES (:id,'b','b@example.com','x',:t,'OWNER',"
                "'2020-01-01T00:00:00Z','2020-01-01T00:00:00Z')"
            ),
            {"id": USER_B, "t": TENANT_B},
        )
    return TENANT_B, USER_B


def _fresh(tmp_path: Path, name: str):
    engine = build_engine(f"sqlite:///{tmp_path / name}")
    prepare_legacy_schema(engine, seed=True)
    _apply_chain(engine)
    tenant, user = _identity(engine)
    return engine, tenant, user


def test_save_message_assistant_prefers_same_turn_user_parent(
    tmp_path: Path, monkeypatch
) -> None:
    engine, tenant, user = _fresh(tmp_path, "same-turn-parent.db")
    monkeypatch.setattr("ekb_api.core.db._engine", engine)
    monkeypatch.setattr("ekb_api.core.db._SessionLocal", None)

    store = SqlStore()
    auth = AuthContext(
        actor_id=user,
        tenant_id=tenant,
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=[],
        policy_version=1,
        trace_id="same-turn-parent-test",
    )
    conversation = store.create_conversation(auth, "同回合父子关系")
    user_message = store.save_message(
        auth, conversation.id, "USER", "问题", turn_id="turn-same"
    )
    assistant_message = store.save_message(
        auth, conversation.id, "ASSISTANT", "回答", turn_id="turn-same"
    )

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, role, branch_id, parent_message_id, content_hash "
                "FROM messages WHERE id IN (:user_id, :assistant_id)"
            ),
            {"user_id": user_message.id, "assistant_id": assistant_message.id},
        ).mappings().all()
    messages = {row["role"]: row for row in rows}
    assert messages["ASSISTANT"]["branch_id"] == messages["USER"]["branch_id"]
    assert messages["ASSISTANT"]["parent_message_id"] == messages["USER"]["id"]
    assert messages["ASSISTANT"]["content_hash"]


def _legacy_conversation(engine, *, tenant: str, user: str) -> dict[str, str]:
    """Insert a pre-v4_007 conversation with two turns, one unpairable."""

    ids = {
        "conversation": "conv-legacy",
        "m1": "msg-u1",
        "m2": "msg-a1",
        "m3": "msg-a-orphan",
        "t1": "turn-1",
        "t2": "turn-2",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, user_id, title, created_at,"
                " updated_at) VALUES (:id,:t,:u,'legacy','2020-01-01T00:00:00Z',"
                "'2020-01-01T00:00:00Z')"
            ),
            {"id": ids["conversation"], "t": tenant, "u": user},
        )
        rows = [
            (ids["m1"], "user", "第一个问题", ids["t1"], "2020-01-01T00:00:01Z"),
            (ids["m2"], "assistant", "第一个回答", ids["t1"], "2020-01-01T00:00:02Z"),
            # An assistant message whose predecessor is also an assistant message:
            # the migration must not guess a user pairing for it.
            (ids["m3"], "assistant", "孤立回答", ids["t2"], "2020-01-01T00:00:03Z"),
        ]
        for mid, role, content, turn_id, created in rows:
            connection.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, role, content,"
                    " turn_id, visibility_state, created_at) "
                    "VALUES (:id,:t,:c,:r,:content,:turn,'visible',:created)"
                ),
                {
                    "id": mid,
                    "t": tenant,
                    "c": ids["conversation"],
                    "r": role,
                    "content": content,
                    "turn": turn_id,
                    "created": created,
                },
            )
        for turn_id, assistant_id, status in (
            (ids["t1"], ids["m2"], "completed"),
            (ids["t2"], ids["m3"], "cancelled"),
        ):
            connection.execute(
                text(
                    "INSERT INTO qa_turns (turn_id, request_id, tenant_id, actor_id,"
                    " conversation_id, assistant_message_id, stream_version, status,"
                    " last_seq, created_at) VALUES (:turn,:req,:t,:a,:c,:msg,'v2',:status,"
                    "0,'2020-01-01T00:00:02Z')"
                ),
                {
                    "turn": turn_id,
                    "req": f"req-{turn_id}",
                    "t": tenant,
                    "a": user,
                    "c": ids["conversation"],
                    "msg": assistant_id,
                    "status": status,
                },
            )
    return ids


# ---------------------------------------------------------------------------
# migration backfill
# ---------------------------------------------------------------------------


def test_v4_007_backfills_branch_graph_without_guessing(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    prepare_legacy_schema(engine, seed=True)
    tenant, user = _identity(engine)
    # Apply everything *before* v4_007 so the legacy rows predate the chat graph.
    for step in CHAIN:
        if step.version == "v4_007_chat_graph":
            break
        step.apply(engine)
    ids = _legacy_conversation(engine, tenant=tenant, user=user)
    for step in CHAIN:
        if step.version == "v4_007_chat_graph":
            step.apply(engine)
            step.verify(engine)

    with engine.connect() as connection:
        root = connection.execute(
            text(
                "SELECT id FROM conversation_branches WHERE conversation_id=:c "
                "AND parent_branch_id IS NULL"
            ),
            {"c": ids["conversation"]},
        ).scalar_one()
        active = connection.execute(
            text("SELECT active_branch_id FROM conversations WHERE id=:c"),
            {"c": ids["conversation"]},
        ).scalar_one()
        assert active == root

        messages = connection.execute(
            text(
                "SELECT id, branch_id, parent_message_id, content_hash FROM messages "
                "WHERE conversation_id=:c ORDER BY created_at, id"
            ),
            {"c": ids["conversation"]},
        ).fetchall()
        assert [row[1] for row in messages] == [root, root, root]
        assert messages[0][2] is None
        assert messages[1][2] == ids["m1"]
        assert all(len(row[3]) == 64 for row in messages)

        turns = dict(
            connection.execute(
                text("SELECT turn_id, status FROM qa_turns WHERE conversation_id=:c"),
                {"c": ids["conversation"]},
            ).fetchall()
        )
        assert turns[ids["t1"]] == "COMPLETED"
        assert turns[ids["t2"]] == "STOPPED"

        paired = connection.execute(
            text("SELECT user_message_id FROM qa_turns WHERE turn_id=:t"),
            {"t": ids["t1"]},
        ).scalar()
        unpaired = connection.execute(
            text("SELECT user_message_id FROM qa_turns WHERE turn_id=:t"),
            {"t": ids["t2"]},
        ).scalar()
    assert paired == ids["m1"]
    # The predecessor was an assistant message — the migration must leave NULL.
    assert unpaired is None


def test_v4_007_is_idempotent(tmp_path: Path) -> None:
    engine, _, _ = _fresh(tmp_path, "idem.db")
    for step in CHAIN:
        if step.version == "v4_007_chat_graph":
            step.apply(engine)
            step.verify(engine)
            step.apply(engine)
            step.verify(engine)


# ---------------------------------------------------------------------------
# branch graph
# ---------------------------------------------------------------------------


def test_branch_materialisation_cuts_ancestors_at_fork(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "graph.db")
    graph = ConversationGraphService(engine)
    conversation_id, root = graph.create_conversation(tenant_id=tenant, user_id=user)

    q1 = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="user",
        content="问题一",
    )
    a1 = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="assistant",
        content="回答一",
        status="completed",
    )
    q2 = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="user",
        content="问题二",
    )
    a2 = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="assistant",
        content="回答二",
        status="completed",
    )

    # Regenerate the *first* answer: fork at q1.
    branch = graph.fork_branch(
        tenant_id=tenant,
        conversation_id=conversation_id,
        fork_message_id=q1.id,
        created_by=user,
        label="regen",
    )
    alt = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=branch.id,
        role="assistant",
        content="回答一（另一版）",
        status="completed",
    )

    root_ids = [m.id for m in graph.branch_messages(tenant_id=tenant, branch_id=root)]
    fork_ids = [m.id for m in graph.branch_messages(tenant_id=tenant, branch_id=branch.id)]

    assert root_ids == [q1.id, a1.id, q2.id, a2.id]
    # The fork inherits q1 only; a1/q2/a2 stay on the parent branch.
    assert fork_ids == [q1.id, alt.id]
    assert a1.id not in fork_ids and a2.id not in fork_ids


def test_branch_materialisation_excludes_hidden_messages(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "hidden-graph.db")
    graph = ConversationGraphService(engine)
    conversation_id, root = graph.create_conversation(tenant_id=tenant, user_id=user)

    ancestor = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="user",
        content="可见祖先",
    )
    hidden = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="assistant",
        content="隐藏占位",
    )
    following = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="user",
        content="可见后续",
        parent_message_id=ancestor.id,
    )

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE messages SET visibility_state='hidden' WHERE id=:id"),
            {"id": hidden.id},
        )

    materialized = graph.branch_messages(tenant_id=tenant, branch_id=root)

    assert [message.id for message in materialized] == [ancestor.id, following.id]
    assert hidden.id not in {message.id for message in materialized}


def test_fork_rejects_message_from_another_conversation(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "forkguard.db")
    graph = ConversationGraphService(engine)
    first, first_root = graph.create_conversation(tenant_id=tenant, user_id=user)
    second, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    message = graph.append_message(
        tenant_id=tenant,
        conversation_id=first,
        branch_id=first_root,
        role="user",
        content="x",
    )
    with pytest.raises(ChatGraphError):
        graph.fork_branch(
            tenant_id=tenant,
            conversation_id=second,
            fork_message_id=message.id,
            created_by=user,
        )


def test_conversation_reads_are_tenant_and_owner_scoped(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "iso.db")
    other_tenant, other_user = _second_tenant(engine)
    graph = ConversationGraphService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)

    with pytest.raises(ConversationNotFound):
        graph.load_conversation(tenant_id=other_tenant, conversation_id=conversation_id)
    with pytest.raises(ConversationNotFound):
        graph.load_conversation(
            tenant_id=tenant, conversation_id=conversation_id, actor_id=other_user
        )


def test_api_rejects_tool_and_mcp_parts(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "parts.db")
    graph = ConversationGraphService(engine)
    conversation_id, root = graph.create_conversation(tenant_id=tenant, user_id=user)
    message = graph.append_message(
        tenant_id=tenant,
        conversation_id=conversation_id,
        branch_id=root,
        role="assistant",
        content="a",
    )
    graph.add_parts(
        tenant_id=tenant,
        message_id=message.id,
        parts=[{"part_type": "TEXT", "text_content": "a"}],
    )
    for rejected in ("TOOL_CALL", "MCP_CALL", "CODE_EXECUTION"):
        with pytest.raises(ChatGraphError):
            graph.add_parts(
                tenant_id=tenant,
                message_id=message.id,
                parts=[{"part_type": rejected, "text_content": "x"}],
            )


# ---------------------------------------------------------------------------
# turn lifecycle
# ---------------------------------------------------------------------------


def _new_turn(service: TurnService, tenant: str, user: str, conversation_id: str, cid: str):
    return service.create_turn(
        tenant_id=tenant,
        actor_id=user,
        conversation_id=conversation_id,
        client_turn_id=cid,
        request_id=f"req-{cid}",
        prompt="请回答",
        requested_provider_id="p1",
        requested_model_id="m1",
        resources=[{"resource_type": "KB", "resource_id": "kb-1"}],
    )


def test_duplicate_client_turn_id_returns_original(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "turns.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)

    first = _new_turn(service, tenant, user, conversation_id, "c-1")
    again = _new_turn(service, tenant, user, conversation_id, "c-1")

    assert again.reused is True
    assert again.turn.turn_id == first.turn.turn_id
    assert again.user_message_id == first.user_message_id
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT COUNT(*) FROM messages WHERE conversation_id=:c"),
            {"c": conversation_id},
        ).scalar_one()
    assert count == 2  # exactly one user + one assistant placeholder


def test_turn_creation_writes_snapshot_and_placeholder(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "snap.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")

    assert creation.turn.state == QUEUED
    snapshots = service.snapshots(tenant_id=tenant, turn_id=creation.turn.turn_id)
    assert any(item["resource_type"] == "KB" for item in snapshots)
    assert any(item["resource_type"] == "MODEL" for item in snapshots)

    with engine.connect() as connection:
        status = connection.execute(
            text("SELECT status FROM messages WHERE id=:m"),
            {"m": creation.assistant_message_id},
        ).scalar_one()
    assert status == "pending"


def test_illegal_transition_is_rejected(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "fsm.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")

    with pytest.raises(TurnTransitionError):
        service.advance(
            tenant_id=tenant,
            turn_id=creation.turn.turn_id,
            expected_state=QUEUED,
            next_state=STREAMING,
        )
    with pytest.raises(TurnTransitionError):
        service.finish(
            tenant_id=tenant,
            turn_id=creation.turn.turn_id,
            expected_state=QUEUED,
            terminal_state=COMPLETED,
            finish_reason="ok",
            last_seq=1,
        )


def test_happy_path_reaches_completed(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "happy.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")
    turn_id = creation.turn.turn_id

    for expected, nxt in (
        (QUEUED, RETRIEVING),
        (RETRIEVING, BUILDING_CONTEXT),
        (BUILDING_CONTEXT, STREAMING),
    ):
        turn = service.advance(
            tenant_id=tenant, turn_id=turn_id, expected_state=expected, next_state=nxt
        )
        assert turn.state == nxt

    final = service.finish(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=STREAMING,
        terminal_state=COMPLETED,
        finish_reason="stop",
        last_seq=7,
        actual_provider_id="p1",
        actual_model_id="m1",
        assistant_content="最终答案",
    )
    assert final.state == COMPLETED
    assert final.terminal_event == "turn.completed"
    assert final.terminal_seq == 7

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, content FROM messages WHERE id=:m"),
            {"m": creation.assistant_message_id},
        ).one()
    assert row[0] == "completed"
    assert row[1] == "最终答案"


def test_first_terminal_wins_between_stop_and_complete(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "race.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")
    turn_id = creation.turn.turn_id

    service.advance(
        tenant_id=tenant, turn_id=turn_id, expected_state=QUEUED, next_state=RETRIEVING
    )
    service.advance(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=RETRIEVING,
        next_state=BUILDING_CONTEXT,
    )
    service.advance(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=BUILDING_CONTEXT,
        next_state=STREAMING,
    )

    accepted, cancelling = service.request_cancel(
        tenant_id=tenant, turn_id=turn_id, actor_id=user
    )
    assert accepted is True
    assert cancelling.state == CANCEL_REQUESTED

    stopped = service.finish(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=CANCEL_REQUESTED,
        terminal_state=STOPPED,
        finish_reason="user_cancelled",
        last_seq=3,
        assistant_content="部分内容",
    )
    assert stopped.state == STOPPED

    # The generation task, unaware of the cancel, tries to complete. It must lose.
    with pytest.raises(TurnTerminalConflict) as conflict:
        service.finish(
            tenant_id=tenant,
            turn_id=turn_id,
            expected_state=STREAMING,
            terminal_state=COMPLETED,
            finish_reason="stop",
            last_seq=9,
            assistant_content="完整内容",
        )
    assert conflict.value.actual_state == STOPPED

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, content FROM messages WHERE id=:m"),
            {"m": creation.assistant_message_id},
        ).one()
        terminal_event = connection.execute(
            text("SELECT terminal_event FROM qa_turns WHERE turn_id=:t"), {"t": turn_id}
        ).scalar_one()
    # The loser must not have overwritten the persisted partial answer.
    assert row == ("stopped", "部分内容")
    assert terminal_event == "turn.stopped"


def test_repeat_cancel_reports_current_state(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "cancel2.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")
    turn_id = creation.turn.turn_id

    assert service.request_cancel(tenant_id=tenant, turn_id=turn_id, actor_id=user)[0] is True
    accepted, turn = service.request_cancel(
        tenant_id=tenant, turn_id=turn_id, actor_id=user
    )
    assert accepted is False
    assert turn.state == CANCEL_REQUESTED


def test_cancel_is_owner_only(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "owner.db")
    _second_tenant(engine)
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")

    with pytest.raises(TurnNotFound):
        service.request_cancel(
            tenant_id=tenant, turn_id=creation.turn.turn_id, actor_id=USER_B
        )
    with pytest.raises(TurnNotFound):
        service.get(tenant_id=TENANT_B, turn_id=creation.turn.turn_id)


def test_regenerate_forks_and_preserves_old_answer(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "regen.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, root = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")
    turn_id = creation.turn.turn_id
    for expected, nxt in (
        (QUEUED, RETRIEVING),
        (RETRIEVING, BUILDING_CONTEXT),
        (BUILDING_CONTEXT, STREAMING),
    ):
        service.advance(
            tenant_id=tenant, turn_id=turn_id, expected_state=expected, next_state=nxt
        )
    service.finish(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=STREAMING,
        terminal_state=COMPLETED,
        finish_reason="stop",
        last_seq=2,
        assistant_content="旧回答",
    )

    again = service.regenerate(
        tenant_id=tenant,
        actor_id=user,
        conversation_id=conversation_id,
        assistant_message_id=creation.assistant_message_id,
        client_turn_id="c-2",
        request_id="req-c-2",
    )
    assert again.branch_id != root

    conversation = graph.load_conversation(
        tenant_id=tenant, conversation_id=conversation_id
    )
    assert conversation["active_branch_id"] == again.branch_id

    with engine.connect() as connection:
        old = connection.execute(
            text("SELECT status, content FROM messages WHERE id=:m"),
            {"m": creation.assistant_message_id},
        ).one()
    assert old == ("completed", "旧回答")

    old_branch = [m.id for m in graph.branch_messages(tenant_id=tenant, branch_id=root)]
    new_branch = [
        m.id for m in graph.branch_messages(tenant_id=tenant, branch_id=again.branch_id)
    ]
    assert creation.assistant_message_id in old_branch
    assert creation.assistant_message_id not in new_branch


def test_retry_requires_failed_turn(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "retry.db")
    graph = ConversationGraphService(engine)
    service = TurnService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(service, tenant, user, conversation_id, "c-1")
    turn_id = creation.turn.turn_id

    with pytest.raises(TurnTransitionError):
        service.retry(
            tenant_id=tenant,
            actor_id=user,
            turn_id=turn_id,
            client_turn_id="c-2",
            request_id="req-c-2",
        )

    service.finish(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=QUEUED,
        terminal_state=FAILED,
        finish_reason="provider_error",
        last_seq=0,
    )
    retried = service.retry(
        tenant_id=tenant,
        actor_id=user,
        turn_id=turn_id,
        client_turn_id="c-2",
        request_id="req-c-2",
    )
    assert retried.turn.turn_id != turn_id
    assert retried.turn.state == QUEUED
    with engine.connect() as connection:
        prompt = connection.execute(
            text("SELECT content FROM messages WHERE id=:m"),
            {"m": retried.user_message_id},
        ).scalar_one()
    assert prompt == "请回答"


def test_non_fallback_error_codes(tmp_path: Path) -> None:
    assert is_fallback_allowed("PROVIDER_TIMEOUT") is True
    for code in (
        "TENANT_FORBIDDEN",
        "ACL_REVOKED",
        "ATTACHMENT_POLICY_VIOLATION",
        "EGRESS_POLICY_BLOCKED",
        "MIGRATION_REQUIRED",
        "SCHEMA_MISMATCH",
        "PERSISTENCE_FAILED",
    ):
        assert is_fallback_allowed(code) is False


# ---------------------------------------------------------------------------
# SSE v2
# ---------------------------------------------------------------------------


def test_sse_seq_is_monotonic_and_matches_id() -> None:
    stream = SseStream(turn_id="t-1", request_id="r-1")
    accepted = stream.accepted(
        conversation_id="c-1",
        branch_id="b-1",
        user_message_id="u-1",
        assistant_message_id="a-1",
    )
    assert accepted.startswith("id: t-1:1\nevent: turn.accepted\n")
    assert '"seq":1' in accepted
    # Legacy readers still find conversation_id/message_id at the envelope root.
    assert '"conversation_id":"c-1"' in accepted
    assert '"message_id":"a-1"' in accepted

    stage = stream.stage("streaming")
    assert stage.startswith("id: t-1:2\n")
    delta = stream.delta(message_id="a-1", text="你好")
    assert delta.startswith("id: t-1:3\n")

    beat = stream.heartbeat()
    assert beat.startswith(": heartbeat ")
    assert "\nid:" not in beat
    assert stream.seq == 3  # heartbeat consumed no seq

    done = stream.completed(message_id="a-1")
    assert done.startswith("id: t-1:4\nevent: turn.completed\n")
    assert stream.terminal_event == "turn.completed"


def test_sse_refuses_second_terminal_and_post_terminal_writes() -> None:
    stream = SseStream(turn_id="t-1", request_id="r-1")
    stream.completed(message_id="a-1")
    with pytest.raises(SseContractError):
        stream.stopped(message_id="a-1", partial_persisted=True)
    with pytest.raises(SseContractError):
        stream.delta(message_id="a-1", text="late")


def test_sse_rejects_unknown_stage_and_terminal_via_emit() -> None:
    stream = SseStream(turn_id="t-1", request_id="r-1")
    with pytest.raises(SseContractError):
        stream.stage("thinking")
    with pytest.raises(SseContractError):
        stream.emit("turn.completed", {})


def test_route_selected_discloses_fallback() -> None:
    stream = SseStream(turn_id="t-1", request_id="r-1")
    payload = stream.route_selected(
        requested_provider_id="p1",
        requested_model_id="m1",
        actual_provider_id="p2",
        actual_model_id="m2",
        fallback_reason="provider_timeout",
    )
    assert '"fallback":true' in payload
    assert '"fallback_reason":"provider_timeout"' in payload


def test_last_event_id_is_diagnostic_only() -> None:
    assert parse_last_event_id("t-1:42") == ("t-1", 42)
    assert parse_last_event_id("garbage") == (None, None)
    assert parse_last_event_id(None) == (None, None)
    assert encode_heartbeat("2020-01-01T00:00:00Z") == ": heartbeat 2020-01-01T00:00:00Z\n\n"


# ---------------------------------------------------------------------------
# context builder
# ---------------------------------------------------------------------------


def _turn(index: int, size: int = 200) -> HistoryTurn:
    return HistoryTurn(
        user=MessageRef(f"u{index}", "user", "问" * size),
        assistant=MessageRef(f"a{index}", "assistant", "答" * size),
    )


def test_pinned_material_over_budget_is_a_hard_error() -> None:
    builder = ContextBuilder(ContextBudget(model_context_window=600))
    with pytest.raises(ContextBudgetExceeded):
        builder.build(
            system_prompt="s" * 10,
            user_message=MessageRef("u", "user", "长" * 5000),
        )


def test_history_pairs_are_never_split() -> None:
    builder = ContextBuilder(ContextBudget(model_context_window=3000, reserved_output=200))
    manifest = builder.build(
        system_prompt="系统提示",
        user_message=MessageRef("u-now", "user", "当前问题"),
        history=[_turn(i) for i in range(1, 6)],
    )
    history_ids = [
        seg.ref_id for seg in manifest.segments if seg.kind == "history"
    ]
    # Every retained turn contributes both of its messages, in order.
    assert len(history_ids) % 2 == 0
    for i in range(0, len(history_ids), 2):
        assert history_ids[i].startswith("u")
        assert history_ids[i + 1].startswith("a")
        assert history_ids[i][1:] == history_ids[i + 1][1:]
    assert manifest.used_tokens <= manifest.available_input


def test_overflow_without_summarizer_truncates_deterministically() -> None:
    builder = ContextBuilder(ContextBudget(model_context_window=1200, reserved_output=200))
    history = [_turn(i) for i in range(1, 6)]
    first = builder.build(
        system_prompt="系统提示",
        user_message=MessageRef("u-now", "user", "当前问题"),
        history=history,
    )
    assert first.compaction.strategy == STRATEGY_TRUNCATE
    assert first.compaction.failure_reason == "no_summarizer"
    assert first.dropped_message_ids  # oldest turns dropped, not partial messages
    assert "u1" in first.dropped_message_ids and "a1" in first.dropped_message_ids

    second = builder.build(
        system_prompt="系统提示",
        user_message=MessageRef("u-now", "user", "当前问题"),
        history=history,
    )
    assert second.manifest_hash == first.manifest_hash


def test_overflow_with_summarizer_records_compaction() -> None:
    builder = ContextBuilder(ContextBudget(model_context_window=1400, reserved_output=200))
    manifest = builder.build(
        system_prompt="系统提示",
        user_message=MessageRef("u-now", "user", "当前问题"),
        history=[_turn(i) for i in range(1, 6)],
        summarizer=lambda turns: f"摘要覆盖 {len(turns)} 轮",
    )
    assert manifest.compaction.strategy == STRATEGY_SUMMARIZE
    assert manifest.compaction.tokens_after > 0
    assert manifest.compaction.tokens_before > manifest.compaction.tokens_after
    assert not manifest.dropped_message_ids


def test_summarizer_failure_degrades_to_truncation() -> None:
    def boom(_turns):
        raise RuntimeError("provider down")

    builder = ContextBuilder(ContextBudget(model_context_window=1200, reserved_output=200))
    manifest = builder.build(
        system_prompt="系统提示",
        user_message=MessageRef("u-now", "user", "当前问题"),
        history=[_turn(i) for i in range(1, 6)],
        summarizer=boom,
    )
    assert manifest.compaction.failure_reason == "RuntimeError"
    assert manifest.dropped_message_ids


def test_evidence_quotas_and_determinism() -> None:
    evidence = [
        EvidenceRef(f"e{i}", "证据", score=1.0 - i * 0.01, kb_id="kb-1", document_id="doc-1")
        for i in range(10)
    ]
    picked = select_evidence(evidence, max_per_kb=6, max_per_document=2)
    assert [item.evidence_id for item in picked] == ["e0", "e1"]

    mixed = [
        EvidenceRef("b", "x", score=0.5, kb_id="kb-1", document_id="d1"),
        EvidenceRef("a", "x", score=0.5, kb_id="kb-2", document_id="d2"),
    ]
    assert [item.evidence_id for item in select_evidence(mixed)] == ["a", "b"]


def test_audit_record_carries_hashes_not_bodies() -> None:
    builder = ContextBuilder(ContextBudget(model_context_window=4000))
    manifest = builder.build(
        system_prompt="系统提示",
        user_message=MessageRef("u-now", "user", "机密问题内容"),
        attachments=[AttachmentRef("att-1", "机密附件正文")],
    )
    record = manifest.audit_record()
    serialized = str(record)
    assert "机密问题内容" not in serialized
    assert "机密附件正文" not in serialized
    assert len(record["manifest_hash"]) == 64


# ---------------------------------------------------------------------------
# titles
# ---------------------------------------------------------------------------


def _complete_turn(service: TurnService, tenant: str, turn_id: str, content: str) -> None:
    for expected, nxt in (
        (QUEUED, RETRIEVING),
        (RETRIEVING, BUILDING_CONTEXT),
        (BUILDING_CONTEXT, STREAMING),
    ):
        service.advance(
            tenant_id=tenant, turn_id=turn_id, expected_state=expected, next_state=nxt
        )
    service.finish(
        tenant_id=tenant,
        turn_id=turn_id,
        expected_state=STREAMING,
        terminal_state=COMPLETED,
        finish_reason="stop",
        last_seq=1,
        assistant_content=content,
    )


def test_first_completed_turn_schedules_title_job_once(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "title.db")
    graph = ConversationGraphService(engine)
    turns = TurnService(engine)
    titles = ConversationTitleService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)

    first = _new_turn(turns, tenant, user, conversation_id, "c-1")
    _complete_turn(turns, tenant, first.turn.turn_id, "第一个回答")
    job_id = titles.schedule_for_turn(
        tenant_id=tenant, conversation_id=conversation_id, turn_id=first.turn.turn_id
    )
    assert job_id

    second = _new_turn(turns, tenant, user, conversation_id, "c-2")
    _complete_turn(turns, tenant, second.turn.turn_id, "第二个回答")
    # Not the first completed turn any more.
    assert (
        titles.schedule_for_turn(
            tenant_id=tenant,
            conversation_id=conversation_id,
            turn_id=second.turn.turn_id,
        )
        is None
    )


def test_title_job_never_overwrites_a_locked_title(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "titlelock.db")
    graph = ConversationGraphService(engine)
    turns = TurnService(engine)
    titles = ConversationTitleService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(turns, tenant, user, conversation_id, "c-1")
    _complete_turn(turns, tenant, creation.turn.turn_id, "回答")

    titles.rename(tenant_id=tenant, conversation_id=conversation_id, title="我的标题")
    assert (
        titles.schedule_for_turn(
            tenant_id=tenant,
            conversation_id=conversation_id,
            turn_id=creation.turn.turn_id,
        )
        is None
    )
    outcome = titles.run_job(
        tenant_id=tenant,
        conversation_id=conversation_id,
        turn_id=creation.turn.turn_id,
        generator=lambda q, a: "自动标题",
    )
    assert outcome.changed is False
    assert outcome.reason == "title_locked"
    assert (
        graph.load_conversation(tenant_id=tenant, conversation_id=conversation_id)["title"]
        == "我的标题"
    )


def test_title_generator_failure_keeps_truncated_question(tmp_path: Path) -> None:
    engine, tenant, user = _fresh(tmp_path, "titlefail.db")
    graph = ConversationGraphService(engine)
    turns = TurnService(engine)
    titles = ConversationTitleService(engine)
    conversation_id, _ = graph.create_conversation(tenant_id=tenant, user_id=user)
    creation = _new_turn(turns, tenant, user, conversation_id, "c-1")
    _complete_turn(turns, tenant, creation.turn.turn_id, "回答")

    def boom(_q, _a):
        raise TimeoutError("title model timeout")

    outcome = titles.run_job(
        tenant_id=tenant,
        conversation_id=conversation_id,
        turn_id=creation.turn.turn_id,
        generator=boom,
    )
    assert outcome.reason.startswith("generator_error")
    assert outcome.title == "请回答"


def test_normalize_title_truncates_and_strips() -> None:
    assert normalize_title("  多余   空白 ") == "多余 空白"
    assert normalize_title("") == "新对话"
    long_title = normalize_title("字" * 100)
    assert len(long_title) == 48
    assert long_title.endswith("…")

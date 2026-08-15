"""PH4 chat graph endpoints (spec 05-ai-chat.md sections 2, 3, 4, 7, 13).

Scope is the *graph and the turn lifecycle* — conversations, branches, messages
and turn control (create / cancel / retry / regenerate).  Token streaming stays
in ``routers/qa.py``; this router hands back the ids that stream consumers need.

Two invariants are enforced at the boundary rather than deeper down:

* Tenant and actor always come from the live auth context.  There is no
  caller-supplied tenant or owner parameter anywhere in this file.
* Section 13 removed web search.  The request models reject a ``web_search``
  option with a typed ``FEATURE_REMOVED`` instead of silently ignoring it, so a
  stale client fails loudly during the compatibility window.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_QA_ASK, assert_capability
from ekb_api.core.db import get_engine
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.services.conversations import (
    BranchNotFound,
    ChatGraphError,
    ConversationGraphService,
    ConversationNotFound,
    MessageNotFound,
)
from ekb_api.services.titles import ConversationTitleService, fallback_title
from ekb_api.services.turns import (
    TurnNotFound,
    TurnService,
    TurnTerminalConflict,
    TurnTransitionError,
)

router = APIRouter(prefix="/chat", tags=["chat"])

ANSWER_MODES = ("none", "strict_grounded", "knowledge_enhanced")


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------


class CreateConversationRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SetActiveBranchRequest(BaseModel):
    branch_id: str = Field(min_length=1, max_length=64)


class ResourceRef(BaseModel):
    resource_type: str = Field(pattern="^(KB|DOCUMENT|ATTACHMENT|MODEL|RETRIEVAL)$")
    resource_id: str = Field(min_length=1, max_length=128)
    resource_version_id: Optional[str] = Field(default=None, max_length=128)
    parameters: Optional[dict[str, Any]] = None


class CreateTurnRequest(BaseModel):
    client_turn_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1)
    branch_id: Optional[str] = Field(default=None, max_length=64)
    answer_mode: str = Field(default="knowledge_enhanced")
    requested_provider_id: Optional[str] = Field(default=None, max_length=128)
    requested_model_id: Optional[str] = Field(default=None, max_length=128)
    resources: list[ResourceRef] = Field(default_factory=list, max_length=200)
    # Section 13: accepted only so it can be rejected with a stable code.
    web_search: Optional[bool] = None


class RegenerateRequest(BaseModel):
    client_turn_id: str = Field(min_length=1, max_length=128)
    requested_provider_id: Optional[str] = Field(default=None, max_length=128)
    requested_model_id: Optional[str] = Field(default=None, max_length=128)
    activate: bool = True


class RetryRequest(BaseModel):
    client_turn_id: str = Field(min_length=1, max_length=128)
    requested_provider_id: Optional[str] = Field(default=None, max_length=128)
    requested_model_id: Optional[str] = Field(default=None, max_length=128)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _graph() -> ConversationGraphService:
    return ConversationGraphService(get_engine())


def _turns() -> TurnService:
    return TurnService(get_engine())


def _titles() -> ConversationTitleService:
    return ConversationTitleService(get_engine())


def _dump(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


def _reject_web_search(enabled: Optional[bool]) -> None:
    if enabled is None:
        return
    raise ApiError(
        400,
        "FEATURE_REMOVED",
        "联网搜索能力已下线，请求不再接受 web_search 选项",
        {"option": "web_search"},
    )


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, ConversationNotFound):
        return ApiError(404, "CONVERSATION_NOT_FOUND", "会话不存在或不属于当前用户")
    if isinstance(exc, BranchNotFound):
        return ApiError(404, "BRANCH_NOT_FOUND", "分支不存在或不属于该会话")
    if isinstance(exc, MessageNotFound):
        return ApiError(404, "MESSAGE_NOT_FOUND", "消息不存在或不在当前租户")
    if isinstance(exc, TurnNotFound):
        return ApiError(404, "TURN_NOT_FOUND", "Turn 不存在或不属于当前用户")
    if isinstance(exc, TurnTerminalConflict):
        return ApiError(
            409,
            "TURN_ALREADY_TERMINAL",
            "该 Turn 已进入终态，权威状态以返回值为准",
            {"turn_id": exc.turn_id, "actual_state": exc.actual_state},
        )
    if isinstance(exc, TurnTransitionError):
        return ApiError(422, "TURN_TRANSITION_REJECTED", "非法的 Turn 流转", {"reason": str(exc)})
    if isinstance(exc, ChatGraphError):
        return ApiError(422, "CHAT_GRAPH_REJECTED", "会话图操作被拒绝", {"reason": str(exc)})
    raise exc


def _request_id(auth: AuthContext) -> str:
    """The live auth context carries the per-request trace id."""

    return auth.trace_id


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


@router.post("/conversations")
def create_conversation(
    payload: CreateConversationRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Create a conversation together with its root branch (spec section 2)."""

    assert_capability(auth, CAP_QA_ASK)
    service = _graph()
    conversation_id, branch_id = service.create_conversation(
        tenant_id=auth.tenant_id,
        user_id=auth.actor_id,
        title=fallback_title(payload.title) if payload.title else "",
    )
    if payload.title:
        # An explicit title at creation time is a user rename: lock it.
        service.rename(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            title=fallback_title(payload.title),
            lock=True,
        )
    return {
        "conversation_id": conversation_id,
        "root_branch_id": branch_id,
        "active_branch_id": branch_id,
        "title_locked": bool(payload.title),
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_QA_ASK)
    try:
        return _graph().load_conversation(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            actor_id=auth.actor_id,
        )
    except Exception as exc:
        raise _translate(exc) from exc


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: str,
    payload: RenameConversationRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """User rename — sets ``title_locked=true`` (spec section 3)."""

    assert_capability(auth, CAP_QA_ASK)
    graph = _graph()
    try:
        graph.load_conversation(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            actor_id=auth.actor_id,
        )
        title = _titles().rename(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            title=payload.title,
        )
    except LookupError as exc:
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "会话不存在或不属于当前用户") from exc
    except Exception as exc:
        raise _translate(exc) from exc
    return {"conversation_id": conversation_id, "title": title, "title_locked": True}


# ---------------------------------------------------------------------------
# branches & messages
# ---------------------------------------------------------------------------


@router.get("/conversations/{conversation_id}/branches")
def list_branches(
    conversation_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_QA_ASK)
    graph = _graph()
    try:
        conversation = graph.load_conversation(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            actor_id=auth.actor_id,
        )
        branches = graph.list_branches(
            tenant_id=auth.tenant_id, conversation_id=conversation_id
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "conversation_id": conversation_id,
        "active_branch_id": conversation.get("active_branch_id"),
        "branches": _dump(branches),
    }


@router.post("/conversations/{conversation_id}/active-branch")
def set_active_branch(
    conversation_id: str,
    payload: SetActiveBranchRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_QA_ASK)
    graph = _graph()
    try:
        graph.load_conversation(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            actor_id=auth.actor_id,
        )
        graph.set_active_branch(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            branch_id=payload.branch_id,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {"conversation_id": conversation_id, "active_branch_id": payload.branch_id}


@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    branch_id: Annotated[Optional[str], Query()] = None,
) -> dict:
    """Materialise one branch: ancestors up to each fork point, then the leaf."""

    assert_capability(auth, CAP_QA_ASK)
    graph = _graph()
    try:
        conversation = graph.load_conversation(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            actor_id=auth.actor_id,
        )
        target = branch_id or conversation.get("active_branch_id")
        if not target:
            branch = graph.ensure_root_branch(
                tenant_id=auth.tenant_id,
                conversation_id=conversation_id,
                user_id=auth.actor_id,
            )
            target = branch.id
        messages = graph.branch_messages(
            tenant_id=auth.tenant_id, branch_id=str(target)
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "conversation_id": conversation_id,
        "branch_id": target,
        "messages": _dump(messages),
    }


# ---------------------------------------------------------------------------
# turns
# ---------------------------------------------------------------------------


@router.post("/conversations/{conversation_id}/turns")
def create_turn(
    conversation_id: str,
    payload: CreateTurnRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Single-transaction turn creation; duplicate client_turn_id is a no-op."""

    assert_capability(auth, CAP_QA_ASK)
    _reject_web_search(payload.web_search)
    if payload.answer_mode not in ANSWER_MODES:
        raise ApiError(
            422,
            "ANSWER_MODE_INVALID",
            "不支持的回答模式",
            {"answer_mode": payload.answer_mode, "allowed": list(ANSWER_MODES)},
        )
    try:
        creation = _turns().create_turn(
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            conversation_id=conversation_id,
            client_turn_id=payload.client_turn_id,
            request_id=_request_id(auth),
            prompt=payload.prompt,
            branch_id=payload.branch_id,
            requested_provider_id=payload.requested_provider_id,
            requested_model_id=payload.requested_model_id,
            answer_mode=payload.answer_mode,
            resources=[item.model_dump() for item in payload.resources],
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return _turn_creation_response(creation)


@router.get("/turns/{turn_id}")
def get_turn(
    turn_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_QA_ASK)
    service = _turns()
    try:
        turn = service.get(tenant_id=auth.tenant_id, turn_id=turn_id)
        if turn.actor_id != auth.actor_id:
            raise TurnNotFound(turn_id)
        snapshots = service.snapshots(tenant_id=auth.tenant_id, turn_id=turn_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return {"turn": _dump(turn), "snapshots": snapshots}


@router.get("/turns/{turn_id}/context")
def get_turn_context(
    turn_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Return context manifest for a turn (safe, no sensitive content).

    Spec 02 §11.2 — surfaces token budget, compaction range and component
    counts so the assistant UI can render a context diagnostic panel.  Only
    hashes and IDs are stored in ``turn_context_manifests``; no prompt text,
    attachment plaintext or secrets are ever returned.
    """
    assert_capability(auth, CAP_QA_ASK)
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT manifest_hash, available_input_tokens, used_input_tokens,"
                " reserved_output_tokens, provider_safety_margin, component_refs,"
                " compaction_summary_id FROM turn_context_manifests"
                " WHERE turn_id=:turn_id AND tenant_id=:tenant_id"
            ),
            {"turn_id": turn_id, "tenant_id": auth.tenant_id},
        ).first()
    if row is None:
        return {"available": False}
    raw_refs = row[5]
    if isinstance(raw_refs, dict):
        refs = raw_refs
    elif isinstance(raw_refs, (str, bytes)) and raw_refs:
        refs = json.loads(raw_refs)
    else:
        refs = {}
    return {
        "available": True,
        "manifest_hash": row[0],
        "available_input_tokens": int(row[1]),
        "used_input_tokens": int(row[2]),
        "reserved_output_tokens": int(row[3]),
        "provider_safety_margin": int(row[4]),
        "compaction_summary_id": row[6],
        "included_message_count": len(refs.get("included_message_ids") or []),
        "dropped_message_count": len(refs.get("dropped_message_ids") or []),
        "evidence_count": len(refs.get("evidence_ids") or []),
        "compaction_strategy": refs.get("compaction_strategy", "none"),
    }


@router.post("/turns/{turn_id}/cancel")
def cancel_turn(
    turn_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Owner-only Stop; a repeat call reports the current state (section 7)."""

    assert_capability(auth, CAP_QA_ASK)
    try:
        accepted, turn = _turns().request_cancel(
            tenant_id=auth.tenant_id, turn_id=turn_id, actor_id=auth.actor_id
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {"accepted": accepted, "turn": _dump(turn)}


@router.post("/turns/{turn_id}/retry")
def retry_turn(
    turn_id: str,
    payload: RetryRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_QA_ASK)
    try:
        creation = _turns().retry(
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            turn_id=turn_id,
            client_turn_id=payload.client_turn_id,
            request_id=_request_id(auth),
            requested_provider_id=payload.requested_provider_id,
            requested_model_id=payload.requested_model_id,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return _turn_creation_response(creation)


@router.post("/conversations/{conversation_id}/messages/{message_id}/regenerate")
def regenerate(
    conversation_id: str,
    message_id: str,
    payload: RegenerateRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Fork a new branch at the originating user message; the old answer stays."""

    assert_capability(auth, CAP_QA_ASK)
    graph = _graph()
    try:
        graph.load_conversation(
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            actor_id=auth.actor_id,
        )
        creation = _turns().regenerate(
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            conversation_id=conversation_id,
            assistant_message_id=message_id,
            client_turn_id=payload.client_turn_id,
            request_id=_request_id(auth),
            requested_provider_id=payload.requested_provider_id,
            requested_model_id=payload.requested_model_id,
            activate=payload.activate,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return _turn_creation_response(creation)


def _turn_creation_response(creation) -> dict:
    return {
        "turn_id": creation.turn.turn_id,
        "conversation_id": creation.turn.conversation_id,
        "branch_id": creation.branch_id,
        "user_message_id": creation.user_message_id,
        "assistant_message_id": creation.assistant_message_id,
        "state": creation.turn.state,
        "state_version": creation.turn.state_version,
        "reused": creation.reused,
        "snapshots": list(creation.snapshots),
    }


@router.get("/turns/{turn_id}/events")
def stream_turn_events(
    turn_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Authenticated SSE event stream with cursor replay (spec 03 §1.2, §3).

    Accepts ``Last-Event-ID`` header ({turn_id}:{seq}) to resume from a
    specific position.  Replays all persisted events with seq > cursor,
    then follows live events if the turn is still active.
    """
    assert_capability(auth, CAP_QA_ASK)
    from ekb_api.services.generation_worker import TurnEventStore

    # Validate turn ownership
    service = _turns()
    try:
        turn = service.get(tenant_id=auth.tenant_id, turn_id=turn_id)
        if turn.actor_id != auth.actor_id:
            raise TurnNotFound(turn_id)
    except TurnNotFound as exc:
        raise ApiError(404, "NOT_FOUND", "Turn not found") from exc

    # Parse Last-Event-ID to get cursor seq
    cursor_seq = 0
    if last_event_id:
        parts = last_event_id.rsplit(":", 1)
        if len(parts) == 2:
            try:
                cursor_seq = int(parts[1])
            except ValueError:
                pass

    event_store = TurnEventStore(get_engine())

    def event_generator():
        # Replay persisted events
        events = event_store.replay(
            tenant_id=auth.tenant_id,
            turn_id=turn_id,
            after_seq=cursor_seq,
        )
        for event in events:
            yield event.to_sse(turn_id)

        # If turn is terminal, we're done after replay
        if turn.state in ("COMPLETED", "STOPPED", "FAILED"):
            return

        # Live follow: poll for new events until terminal
        # (PH3 will replace this with a more efficient mechanism)
        import time

        last_seq = events[-1].seq if events else cursor_seq
        max_polls = 7200  # 2 hours at 1s interval
        for _ in range(max_polls):
            time.sleep(1.0)
            new_events = event_store.replay(
                tenant_id=auth.tenant_id,
                turn_id=turn_id,
                after_seq=last_seq,
            )
            if not new_events:
                yield ": heartbeat\n\n"
                continue
            for event in new_events:
                yield event.to_sse(turn_id)
                last_seq = event.seq
                # Check if this was a terminal event
                if event.event_type in (
                    "turn.completed",
                    "turn.stopped",
                    "turn.failed",
                ):
                    return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

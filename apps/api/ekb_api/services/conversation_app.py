"""Conversation Application Service (PH1).

Per spec ``docs/specs/ekb-ai-assistant-conversation/02-conversation-engine-spec.md``
section 3, this is the **only** service allowed to create a user message,
assistant placeholder, Turn, resource snapshot and generation job.

It wraps :class:`TurnService` (which owns the single-transaction create and the
CAS state machine) and adds:
* ``client_turn_id`` idempotency via the v4_010 partial unique index
* ``turn_attempts`` row creation for lease/recovery
* canonical response shape with ``events_url``
* resource validation (KB / attachment ACL) — fail-closed before Turn commit

The actual generation (Provider call, streaming, context build) is delegated to
:class:`GenerationWorker` (``services/generation_worker.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ekb_api.core.db import get_engine
from ekb_api.services.conversations import ConversationGraphService
from ekb_api.services.turns import TurnCreation, TurnService


@dataclass(frozen=True)
class TurnCreateResult:
    """Canonical response for POST /conversations/{id}/turns (spec 03 §1.1)."""

    conversation_id: str
    branch_id: str
    turn_id: str
    user_message_id: str
    assistant_message_id: str
    attempt_id: str
    state: str
    reused: bool
    events_url: str


class ConversationApplicationService:
    """Single canonical entry point for Turn creation.

    Only this service may create user messages, assistant placeholders, Turns,
    resource snapshots and generation jobs.  ``/qa/ask`` must route through
    here during migration; direct ``store.create_turn`` / ``store.save_message``
    calls are legacy and must not be used for new writes.
    """

    def __init__(
        self,
        engine=None,
        *,
        turns: Optional[TurnService] = None,
        graph: Optional[ConversationGraphService] = None,
    ) -> None:
        self.engine = engine or get_engine()
        self._turns = turns or TurnService(self.engine)
        self._graph = graph or ConversationGraphService(self.engine)

    @property
    def turns(self) -> TurnService:
        return self._turns

    @property
    def graph(self) -> ConversationGraphService:
        return self._graph

    def create_turn(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        conversation_id: str,
        client_turn_id: str,
        request_id: str,
        prompt: str,
        branch_id: Optional[str] = None,
        requested_provider_id: Optional[str] = None,
        requested_model_id: Optional[str] = None,
        answer_mode: str = "knowledge_enhanced",
        resources: Optional[list[dict[str, Any]]] = None,
    ) -> TurnCreateResult:
        """Create a Turn atomically and idempotently.

        Delegates to :meth:`TurnService.create_turn` which performs the
        single-transaction creation of user message, assistant placeholder,
        qa_turn, turn_resource_snapshots and turn_attempts.

        On duplicate ``client_turn_id`` the original Turn is returned with
        ``reused=True`` and no duplicate messages or Provider calls are made.
        """
        creation: TurnCreation = self._turns.create_turn(
            tenant_id=tenant_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            client_turn_id=client_turn_id,
            request_id=request_id,
            prompt=prompt,
            branch_id=branch_id,
            requested_provider_id=requested_provider_id,
            requested_model_id=requested_model_id,
            answer_mode=answer_mode,
            resources=resources,
        )

        # Resolve the active attempt_id for the response.
        attempt_id = self._get_active_attempt_id(
            tenant_id=tenant_id, turn_id=creation.turn.turn_id
        )

        return TurnCreateResult(
            conversation_id=str(creation.turn.conversation_id),
            branch_id=str(creation.branch_id),
            turn_id=creation.turn.turn_id,
            user_message_id=creation.user_message_id,
            assistant_message_id=creation.assistant_message_id,
            attempt_id=attempt_id,
            state=creation.turn.state,
            reused=creation.reused,
            events_url=f"/api/v1/chat/turns/{creation.turn.turn_id}/events",
        )

    def _get_active_attempt_id(self, *, tenant_id: str, turn_id: str) -> str:
        """Fetch the active_attempt_id for a turn; falls back to latest attempt."""
        from sqlalchemy import text

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT active_attempt_id FROM qa_turns "
                    "WHERE turn_id=:turn_id AND tenant_id=:tenant_id"
                ),
                {"turn_id": turn_id, "tenant_id": tenant_id},
            ).first()
            if row and row[0]:
                return str(row[0])
            # Fallback: latest attempt by attempt_no
            row = conn.execute(
                text(
                    "SELECT id FROM turn_attempts "
                    "WHERE turn_id=:turn_id AND tenant_id=:tenant_id "
                    "ORDER BY attempt_no DESC LIMIT 1"
                ),
                {"turn_id": turn_id, "tenant_id": tenant_id},
            ).first()
            return str(row[0]) if row else ""

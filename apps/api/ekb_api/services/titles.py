"""PH4 conversation auto-title (spec 05-ai-chat.md section 3).

Three rules from the spec drive every branch in this file:

* A user rename sets ``title_locked=true``; the automatic title must never
  overwrite a locked title, not even if the job was enqueued before the rename.
* The **first completed turn** triggers an asynchronous title job — the turn
  itself must not block on the model.
* If the job fails or times out, the conversation keeps the truncated first
  question.  That fallback is written eagerly at conversation creation time via
  :func:`fallback_title`, so "failure" degrades to "already correct" rather than
  to an empty title.

The generator callback is injected, so the job runner stays testable without a
provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import Engine, text

from ekb_api.domain import utc_now
from ekb_api.services.jobs import get_job_service

JOB_TYPE = "conversation.title"
MAX_TITLE_CHARS = 48
DEFAULT_TITLE = "新对话"

_WHITESPACE = re.compile(r"\s+")
# Strip characters that make a title unusable in a list row.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def normalize_title(raw: Optional[str], *, limit: int = MAX_TITLE_CHARS) -> str:
    if not raw:
        return DEFAULT_TITLE
    cleaned = _CONTROL.sub(" ", raw)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().strip("\"'“”‘’")
    if not cleaned:
        return DEFAULT_TITLE
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def fallback_title(first_question: Optional[str]) -> str:
    """Truncated first question — the durable fallback required by section 3."""

    return normalize_title(first_question)


@dataclass(frozen=True)
class TitleOutcome:
    conversation_id: str
    title: str
    changed: bool
    reason: str


TitleGenerator = Callable[[str, str], Optional[str]]
"""``(question, answer) -> title | None``. Returning None keeps the fallback."""


class ConversationTitleService:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._jobs = get_job_service(engine)

    # -- job scheduling ----------------------------------------------------

    def schedule_for_turn(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        turn_id: str,
        connection=None,
    ) -> Optional[str]:
        """Enqueue the title job iff this is the first completed turn.

        Returns the job id, or ``None`` when the conversation is locked or
        already has an earlier completed turn.  Idempotency is doubly enforced:
        the guard query below, and the job's ``idempotency_key`` keyed on the
        conversation (not the turn) so a race cannot enqueue twice.
        """

        if connection is not None:
            return self._schedule(connection, tenant_id, conversation_id, turn_id)
        with self.engine.begin() as conn:
            return self._schedule(conn, tenant_id, conversation_id, turn_id)

    def _schedule(
        self, connection, tenant_id: str, conversation_id: str, turn_id: str
    ) -> Optional[str]:
        row = connection.execute(
            text(
                "SELECT title_locked FROM conversations "
                "WHERE tenant_id = :tenant_id AND id = :conversation_id"
            ),
            {"tenant_id": tenant_id, "conversation_id": conversation_id},
        ).first()
        if row is None or int(row.title_locked or 0) == 1:
            return None

        earlier = connection.execute(
            text(
                "SELECT COUNT(*) AS n FROM qa_turns "
                "WHERE tenant_id = :tenant_id AND conversation_id = :conversation_id "
                "AND status = 'COMPLETED' AND turn_id <> :turn_id"
            ),
            {
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
            },
        ).scalar()
        if int(earlier or 0) > 0:
            return None

        result = self._jobs.enqueue_in(
            connection,
            tenant_id=tenant_id,
            job_type=JOB_TYPE,
            idempotency_key=f"{JOB_TYPE}:{conversation_id}",
            payload={"conversation_id": conversation_id, "turn_id": turn_id},
            max_attempts=2,
        )
        return result.job.id

    # -- job execution -----------------------------------------------------

    def run_job(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        turn_id: str,
        generator: Optional[TitleGenerator] = None,
    ) -> TitleOutcome:
        """Execute the title job. Never raises for generator failure."""

        with self.engine.begin() as connection:
            conversation = connection.execute(
                text(
                    "SELECT id, title, title_locked FROM conversations "
                    "WHERE tenant_id = :tenant_id AND id = :conversation_id"
                ),
                {"tenant_id": tenant_id, "conversation_id": conversation_id},
            ).first()
            if conversation is None:
                return TitleOutcome(conversation_id, "", False, "conversation_missing")
            current = str(conversation.title or "")
            if int(conversation.title_locked or 0) == 1:
                return TitleOutcome(conversation_id, current, False, "title_locked")

            pair = connection.execute(
                text(
                    "SELECT u.content AS question, a.content AS answer "
                    "FROM qa_turns t "
                    "LEFT JOIN messages u ON u.id = t.user_message_id "
                    "  AND u.tenant_id = t.tenant_id "
                    "LEFT JOIN messages a ON a.id = t.assistant_message_id "
                    "  AND a.tenant_id = t.tenant_id "
                    "WHERE t.tenant_id = :tenant_id AND t.turn_id = :turn_id"
                ),
                {"tenant_id": tenant_id, "turn_id": turn_id},
            ).first()
            question = str(pair.question or "") if pair else ""
            answer = str(pair.answer or "") if pair else ""

            candidate: Optional[str] = None
            reason = "generated"
            if generator is None:
                reason = "no_generator"
            else:
                try:
                    candidate = generator(question, answer)
                except Exception as exc:  # degrade to the fallback, never fail the turn
                    candidate = None
                    reason = f"generator_error:{type(exc).__name__}"

            if candidate:
                title = normalize_title(candidate)
            else:
                title = fallback_title(question)
                if reason == "generated":
                    reason = "generator_empty"

            if title == current:
                return TitleOutcome(conversation_id, current, False, "unchanged")

            connection.execute(
                text(
                    "UPDATE conversations SET title = :title, updated_at = :now "
                    "WHERE tenant_id = :tenant_id AND id = :conversation_id "
                    "AND title_locked = 0"
                ),
                {
                    "title": title,
                    "now": utc_now(),
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                },
            )
            return TitleOutcome(conversation_id, title, True, reason)

    # -- user rename -------------------------------------------------------

    def rename(self, *, tenant_id: str, conversation_id: str, title: str) -> str:
        """User rename: normalises and locks the title (section 3)."""

        normalized = normalize_title(title)
        with self.engine.begin() as connection:
            updated = connection.execute(
                text(
                    "UPDATE conversations SET title = :title, title_locked = 1, "
                    "updated_at = :now "
                    "WHERE tenant_id = :tenant_id AND id = :conversation_id"
                ),
                {
                    "title": normalized,
                    "now": utc_now(),
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                },
            ).rowcount
            if not updated:
                raise LookupError(f"conversation not found: {conversation_id}")
        return normalized

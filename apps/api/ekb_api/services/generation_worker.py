"""Generation Worker (PH1).

Executes Turn generation in the request thread with durable event logging.
The worker is **not** a separate process — it runs synchronously inside the
SSE endpoint — but every event is persisted to ``turn_events`` before
broadcast, enabling ``Last-Event-ID`` cursor replay (PH3) and crash recovery
via lease expiry.

State machine flow (spec 02 §5):
    QUEUED → RETRIEVING → BUILDING_CONTEXT → STREAMING → COMPLETED
    any active → CANCEL_REQUESTED → STOPPED
    any non-terminal → FAILED

Key invariants:
* Every SSE event is persisted to ``turn_events`` (seq strictly increasing)
  **before** being yielded to the client.
* Terminal events use first-terminal-wins CAS via ``TurnService.finish``.
* Late deltas arriving after ``CANCEL_REQUESTED`` are discarded by state check.
* ``last_provider_activity_at`` and ``last_sse_write_at`` are tracked
  separately (spec 00 §6 fix).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from ekb_api.core.config import get_settings
from ekb_api.core.db import get_engine
from ekb_api.domain import utc_now
from ekb_api.services.turns import (
    CANCEL_REQUESTED,
    COMPLETED,
    FAILED,
    QUEUED,
    RETRIEVING,
    STOPPED,
    STREAMING,
    TERMINAL_STATES,
    TurnService,
    TurnTerminalConflict,
    TurnTransitionError,
)

logger = logging.getLogger("ekb.generation_worker")

# Heartbeat interval (seconds) — SSE comment, does not consume seq.
_HEARTBEAT_INTERVAL = 15.0


class _DeltaBatcher:
    """Batches small deltas before flushing to turn_events (PH3-2).

    Reuses the token/byte/time triple-condition flush logic from qa.py's
    DeltaMerger, but simplified for the GenerationWorker context.
    Each flush produces a single ``message.delta`` event, reducing DB
    writes from one-per-token to one-per-batch.
    """

    def __init__(self, *, max_tokens: int, max_bytes: int, flush_ms: int) -> None:
        self.max_tokens = max_tokens
        self.max_bytes = max_bytes
        self.flush_ms = flush_ms
        self._buf: list[str] = []
        self._token_count = 0
        self._byte_count = 0
        self._first_at: float | None = None

    def append(self, token: str) -> None:
        if self._first_at is None:
            self._first_at = time.perf_counter()
        self._buf.append(token)
        # CJK approximation: ~3 chars per token
        self._token_count += max(1, len(token) // 3)
        self._byte_count += len(token.encode("utf-8"))

    def should_flush(self) -> tuple[bool, str]:
        if not self._buf:
            return False, ""
        elapsed_ms = (time.perf_counter() - (self._first_at or 0)) * 1000
        if self._token_count >= self.max_tokens:
            return True, "tokens"
        if self._byte_count >= self.max_bytes:
            return True, "bytes"
        if elapsed_ms >= self.flush_ms:
            return True, "time"
        return False, ""

    def flush(self) -> str:
        batch = "".join(self._buf)
        self._buf = []
        self._token_count = 0
        self._byte_count = 0
        self._first_at = None
        return batch

    def has_pending(self) -> bool:
        return bool(self._buf)


@dataclass
class WorkerContext:
    """Context for a single generation run."""

    tenant_id: str
    actor_id: str
    turn_id: str
    attempt_id: str
    request_id: str
    conversation_id: str
    branch_id: str
    user_message_id: str
    assistant_message_id: str
    prompt: str
    requested_model_id: Optional[str] = None
    knowledge_base_ids: list[str] = field(default_factory=list)
    answer_mode: str = "knowledge_enhanced"
    attachment_ids: list[str] = field(default_factory=list)
    image_attachment_ids: list[str] = field(default_factory=list)
    # PH2: model capability snapshot (filled by GenerationWorker before context build)
    model_context_window: int = 16384
    capability_snapshot: dict[str, Any] = field(default_factory=dict)
    # PH2: built context manifest (filled after BUILDING_CONTEXT stage)
    context_manifest: Any = None  # ContextManifest


@dataclass
class TurnEvent:
    """A single persisted turn event ready for SSE broadcast."""

    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: str

    def to_sse(self, turn_id: str) -> str:
        """Render as SSE v3 envelope (spec 03 §3.1)."""
        envelope = {
            "conversation_id": "",
            "turn_id": turn_id,
            "message_id": "",
            "seq": self.seq,
            "created_at": self.created_at,
            **self.payload,
        }
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        sse_id = f"{turn_id}:{self.seq}"
        return f"id: {sse_id}\nevent: {self.event_type}\ndata: {encoded}\n\n"


class TurnEventStore:
    """Persists and reads turn_events for cursor replay."""

    def __init__(self, engine=None) -> None:
        self.engine = engine or get_engine()
        settings = get_settings()
        self._retention_days = settings.ce_event_retention_days

    def append(
        self,
        *,
        tenant_id: str,
        turn_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> TurnEvent:
        """Append a single event; seq is atomically assigned."""
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expires_dt = now_dt + timedelta(days=self._retention_days)
        expires = expires_dt.isoformat().replace("+00:00", "Z")
        with self.engine.begin() as conn:
            # Atomic seq assignment: SELECT MAX(seq) + 1 within the transaction.
            row = conn.execute(
                text(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                    "FROM turn_events WHERE turn_id=:turn_id"
                ),
                {"turn_id": turn_id},
            ).first()
            seq = int(row[0]) if row else 1

            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "INSERT INTO turn_events "
                        "(tenant_id, turn_id, seq, event_type, payload, created_at, expires_at) "
                        "VALUES (:tenant_id,:turn_id,:seq,:event_type,"
                        "CAST(:payload AS jsonb),:created_at,:expires_at)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "turn_id": turn_id,
                        "seq": seq,
                        "event_type": event_type,
                        "payload": payload_json,
                        "created_at": now,
                        "expires_at": expires,
                    },
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO turn_events "
                        "(tenant_id, turn_id, seq, event_type, payload,"
                        " created_at, expires_at) "
                        "VALUES (:tenant_id,:turn_id,:seq,:event_type,"
                        ":payload,:created_at,:expires_at)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "turn_id": turn_id,
                        "seq": seq,
                        "event_type": event_type,
                        "payload": payload_json,
                        "created_at": now,
                        "expires_at": expires,
                    },
                )
            # Update last_event_seq on qa_turns
            conn.execute(
                text(
                    "UPDATE qa_turns SET last_event_seq=:seq "
                    "WHERE turn_id=:turn_id AND tenant_id=:tenant_id"
                ),
                {"seq": seq, "turn_id": turn_id, "tenant_id": tenant_id},
            )

        return TurnEvent(seq=seq, event_type=event_type, payload=payload, created_at=now)

    def replay(
        self,
        *,
        tenant_id: str,
        turn_id: str,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[TurnEvent]:
        """Read persisted events with seq > after_seq for cursor replay."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT seq, event_type, payload, created_at FROM turn_events "
                    "WHERE tenant_id=:tenant_id AND turn_id=:turn_id "
                    "AND seq > :after_seq "
                    "ORDER BY seq LIMIT :limit"
                ),
                {
                    "tenant_id": tenant_id,
                    "turn_id": turn_id,
                    "after_seq": after_seq,
                    "limit": limit,
                },
            ).all()
        events: list[TurnEvent] = []
        for row in rows:
            payload = json.loads(str(row[2])) if row[2] else {}
            events.append(
                TurnEvent(
                    seq=int(row[0]),
                    event_type=str(row[1]),
                    payload=payload,
                    created_at=str(row[3]),
                )
            )
        return events

    def get_last_seq(self, *, tenant_id: str, turn_id: str) -> int:
        """Get the last persisted seq for a turn."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT MAX(seq) FROM turn_events "
                    "WHERE tenant_id=:tenant_id AND turn_id=:turn_id"
                ),
                {"tenant_id": tenant_id, "turn_id": turn_id},
            ).first()
            return int(row[0]) if row and row[0] else 0


class GenerationWorker:
    """Runs Turn generation in the request thread with durable event logging.

    The worker yields SSE event strings that can be streamed directly to the
    client.  Every event is persisted to ``turn_events`` before yield.
    """

    def __init__(
        self,
        engine=None,
        *,
        turns: Optional[TurnService] = None,
        event_store: Optional[TurnEventStore] = None,
    ) -> None:
        self.engine = engine or get_engine()
        self._turns = turns or TurnService(self.engine)
        self._events = event_store or TurnEventStore(self.engine)
        settings = get_settings()
        self._lease_seconds = settings.ce_generation_lease_seconds

    @property
    def event_store(self) -> TurnEventStore:
        return self._events

    def _lease_attempt(self, ctx: WorkerContext) -> bool:
        """CAS attempt state QUEUED→RUNNING and set lease_expires_at."""
        now = utc_now()
        lease_expires = now + timedelta(seconds=self._lease_seconds)
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE turn_attempts SET state='RUNNING', started_at=:now, "
                    "lease_expires_at=:lease_expires "
                    "WHERE id=:attempt_id AND tenant_id=:tenant_id "
                    "AND turn_id=:turn_id AND state='QUEUED'"
                ),
                {
                    "attempt_id": ctx.attempt_id,
                    "tenant_id": ctx.tenant_id,
                    "turn_id": ctx.turn_id,
                    "now": now,
                    "lease_expires": lease_expires,
                },
            )
            return bool(result.rowcount)

    def _update_attempt_capability(
        self, ctx: WorkerContext, capability_snapshot: dict[str, Any]
    ) -> None:
        """Update the attempt's capability snapshot (PH2 model resolution)."""
        payload = json.dumps(capability_snapshot, ensure_ascii=False, separators=(",", ":"))
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "UPDATE turn_attempts SET capability_snapshot=CAST(:payload AS jsonb) "
                        "WHERE id=:attempt_id AND tenant_id=:tenant_id"
                    ),
                    {"attempt_id": ctx.attempt_id, "tenant_id": ctx.tenant_id, "payload": payload},
                )
            else:
                conn.execute(
                    text(
                        "UPDATE turn_attempts SET capability_snapshot=:payload "
                        "WHERE id=:attempt_id AND tenant_id=:tenant_id"
                    ),
                    {"attempt_id": ctx.attempt_id, "tenant_id": ctx.tenant_id, "payload": payload},
                )

    def _resolve_capability(self, ctx: WorkerContext) -> dict[str, Any]:
        """Resolve the model capability snapshot for this turn (PH2-2).

        Reads the actual model context window from the database model
        configuration (not the global env default). Falls back to the
        global setting if the model is not found or has no window defined.

        The snapshot is immutable for the Turn even if the model is later
        edited or disabled (spec 02 §8.1, §10).
        """
        from ekb_api.core.config import get_settings

        settings = get_settings()
        context_window = settings.llm_context_window
        supports_vision = False
        supports_streaming = True
        max_output_tokens = settings.llm_max_tokens
        tokenizer = "conservative"
        model_name = ""
        provider_id = ""

        # Try to read model capability from the database
        if ctx.requested_model_id:
            try:
                with self.engine.connect() as conn:
                    row = conn.execute(
                        text(
                            "SELECT m.model_id, m.context_window, m.supports_vision,"
                            " m.max_output_tokens, m.tokenizer, m.provider_id,"
                            " p.provider_key "
                            "FROM llm_models m "
                            "LEFT JOIN llm_providers p ON p.id = m.provider_id "
                            "WHERE m.id = :model_id AND m.tenant_id = :tenant_id"
                        ),
                        {
                            "model_id": ctx.requested_model_id,
                            "tenant_id": ctx.tenant_id,
                        },
                    ).first()
                    if row:
                        model_name = str(row[0] or "")
                        if row[1] is not None and int(row[1]) > 0:
                            context_window = int(row[1])
                        supports_vision = bool(row[2])
                        if row[3] is not None and int(row[3]) > 0:
                            max_output_tokens = int(row[3])
                        tokenizer = str(row[4] or "conservative")
                        provider_id = str(row[5] or "")
            except Exception as exc:
                logger.warning(
                    "capability resolve failed, using defaults: %s", exc
                )

        # Update ctx.model_context_window so ContextEngineService uses the
        # actual window, not the global default
        ctx.model_context_window = context_window

        return {
            "context_window": context_window,
            "supports_vision": supports_vision,
            "supports_streaming": supports_streaming,
            "max_output_tokens": max_output_tokens,
            "tokenizer": tokenizer,
            "model_name": model_name,
            "provider_id": provider_id,
            "resolved_at": utc_now(),
        }

    def _is_cancelled(self, ctx: WorkerContext) -> bool:
        """Check if the turn has been cancelled (CANCEL_REQUESTED or terminal)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM qa_turns "
                    "WHERE turn_id=:turn_id AND tenant_id=:tenant_id"
                ),
                {"turn_id": ctx.turn_id, "tenant_id": ctx.tenant_id},
            ).first()
            if row is None:
                return True
            return str(row[0]) in (CANCEL_REQUESTED, *TERMINAL_STATES)

    def _emit(
        self, ctx: WorkerContext, event_type: str, payload: dict[str, Any]
    ) -> str:
        """Persist an event and return the SSE string."""
        event = self._events.append(
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            event_type=event_type,
            payload={
                "conversation_id": ctx.conversation_id,
                "turn_id": ctx.turn_id,
                "message_id": ctx.assistant_message_id,
                **payload,
            },
        )
        return event.to_sse(ctx.turn_id)

    def _emit_terminal(
        self, ctx: WorkerContext, event_type: str, payload: dict[str, Any]
    ) -> str:
        """Emit a terminal event with correct terminal_seq (PH3-3 fix).

        The terminal event's seq is assigned atomically by the DB, then
        we inject it into the payload as ``terminal_seq``. This guarantees
        ``terminal_seq == event.seq`` (spec 00 §6 fix).
        """
        event = self._events.append(
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            event_type=event_type,
            payload={
                "conversation_id": ctx.conversation_id,
                "turn_id": ctx.turn_id,
                "message_id": ctx.assistant_message_id,
                "terminal_seq": 0,  # placeholder, will be overwritten
                **payload,
            },
        )
        # Now we know the assigned seq — update the payload's terminal_seq
        # to match. We need to update the persisted event's payload too.
        self._update_event_payload(
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            seq=event.seq,
            key="terminal_seq",
            value=event.seq,
        )
        # Rebuild the SSE string with the correct terminal_seq
        event.payload["terminal_seq"] = event.seq
        return event.to_sse(ctx.turn_id)

    def _update_event_payload(
        self, *, tenant_id: str, turn_id: str, seq: int, key: str, value: Any
    ) -> None:
        """Update a single key in a persisted turn_events payload (PH3-3)."""
        with self.engine.begin() as conn:
            # Read current payload
            row = conn.execute(
                text(
                    "SELECT payload FROM turn_events "
                    "WHERE tenant_id=:tenant_id AND turn_id=:turn_id AND seq=:seq"
                ),
                {"tenant_id": tenant_id, "turn_id": turn_id, "seq": seq},
            ).first()
            if row is None:
                return
            payload = json.loads(str(row[0])) if row[0] else {}
            payload[key] = value
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "UPDATE turn_events SET payload=CAST(:payload AS jsonb) "
                        "WHERE tenant_id=:tenant_id AND turn_id=:turn_id AND seq=:seq"
                    ),
                    {
                        "payload": payload_json,
                        "tenant_id": tenant_id,
                        "turn_id": turn_id,
                        "seq": seq,
                    },
                )
            else:
                conn.execute(
                    text(
                        "UPDATE turn_events SET payload=:payload "
                        "WHERE tenant_id=:tenant_id AND turn_id=:turn_id AND seq=:seq"
                    ),
                    {
                        "payload": payload_json,
                        "tenant_id": tenant_id,
                        "turn_id": turn_id,
                        "seq": seq,
                    },
                )

    def run(self, ctx: WorkerContext) -> Generator[str, None, None]:
        """Execute generation and yield SSE event strings.

        This is the canonical generation path.  ``/qa/ask`` and
        ``GET /turns/{id}/events`` both consume this generator.

        The worker handles:
        * lease acquisition (fail if attempt already running)
        * state machine advancement (QUEUED→RETRIEVING→BUILDING_CONTEXT→STREAMING)
        * Provider call (delegated to ``_run_generation``)
        * durable event persistence (every event before yield)
        * terminal first-terminal-wins CAS
        * Stop signal checking (CANCEL_REQUESTED)
        """
        # 1. Lease the attempt
        if not self._lease_attempt(ctx):
            # Attempt already running or not QUEUED — check if turn is terminal
            yield self._emit(ctx, "turn.stage", {"state": "already_running"})
            return

        # 2. Emit accepted event
        yield self._emit(
            ctx,
            "turn.accepted",
            {
                "state": QUEUED,
                "turn_id": ctx.turn_id,
                "attempt_id": ctx.attempt_id,
            },
        )

        # 3. Advance to RETRIEVING
        try:
            self._turns.advance(
                tenant_id=ctx.tenant_id,
                turn_id=ctx.turn_id,
                expected_state=QUEUED,
                next_state=RETRIEVING,
                actor_id=ctx.actor_id,
            )
        except (TurnTransitionError, TurnTerminalConflict) as exc:
            yield self._emit(
                ctx,
                "turn.failed",
                {
                    "code": "STATE_TRANSITION_FAILED",
                    "message": str(exc),
                    "retryable": False,
                },
            )
            return

        yield self._emit(ctx, "turn.stage", {"state": RETRIEVING})

        # Check for early cancel
        if self._is_cancelled(ctx):
            yield from self._finish_stopped(ctx, partial_text="")
            return

        # 4. Advance to BUILDING_CONTEXT (PH2 will insert ContextEngine here)
        try:
            self._turns.advance(
                tenant_id=ctx.tenant_id,
                turn_id=ctx.turn_id,
                expected_state=RETRIEVING,
                next_state="BUILDING_CONTEXT",
                actor_id=ctx.actor_id,
            )
        except (TurnTransitionError, TurnTerminalConflict):
            yield from self._finish_stopped(ctx, partial_text="")
            return

        # 4b. PH2: Build context via ContextEngineService
        try:
            from ekb_api.services.context_engine import ContextEngineService

            context_engine = ContextEngineService(self.engine)

            # Resolve model capability and persist snapshot to turn_attempts
            capability_snapshot = self._resolve_capability(ctx)
            self._update_attempt_capability(ctx, capability_snapshot)

            manifest = context_engine.build_context(
                tenant_id=ctx.tenant_id,
                turn_id=ctx.turn_id,
                attempt_id=ctx.attempt_id,
                conversation_id=ctx.conversation_id,
                branch_id=ctx.branch_id,
                user_message_id=ctx.user_message_id,
                prompt=ctx.prompt,
                model_context_window=ctx.model_context_window,
                knowledge_base_ids=ctx.knowledge_base_ids,
                attachment_ids=ctx.attachment_ids,
                actor_id=ctx.actor_id,
            )
            ctx.context_manifest = manifest

            # Emit compaction event if summary was applied
            if manifest.compaction.applied:
                yield self._emit(
                    ctx,
                    "context.compacted",
                    {
                        "summary_id": manifest.compaction.summary_id or "",
                        "covered_count": len(manifest.compaction.covered_message_ids),
                        "before_tokens": manifest.compaction.tokens_before,
                        "after_tokens": manifest.compaction.tokens_after,
                    },
                )
            elif manifest.compaction.strategy == "summarize_failed_truncate":
                yield self._emit(
                    ctx,
                    "context.compaction_degraded",
                    {
                        "reason": manifest.compaction.failure_reason or "summary_failed",
                        "dropped_count": len(manifest.dropped_message_ids),
                    },
                )

            # Emit retrieval completed event if evidence was retrieved
            if manifest.evidence_ids:
                yield self._emit(
                    ctx,
                    "retrieval.completed",
                    {
                        "evidence_count": len(manifest.evidence_ids),
                        "evidence_ids": list(manifest.evidence_ids),
                    },
                )

        except Exception as exc:
            # ContextBudgetExceeded or other context build failure → fail closed
            logger.exception("context_engine build failed turn=%s", ctx.turn_id)
            error_code = "CONTEXT_BUDGET_EXCEEDED"
            if "ContextBudgetExceeded" not in type(exc).__name__:
                error_code = "CONTEXT_BUILD_ERROR"
            yield from self._finish_failed(
                ctx, error_code=error_code, message=str(exc), retryable=False
            )
            return

        # 5. Advance to STREAMING
        try:
            self._turns.advance(
                tenant_id=ctx.tenant_id,
                turn_id=ctx.turn_id,
                expected_state="BUILDING_CONTEXT",
                next_state=STREAMING,
                actor_id=ctx.actor_id,
            )
        except (TurnTransitionError, TurnTerminalConflict):
            yield from self._finish_stopped(ctx, partial_text="")
            return

        # 6. Run generation with batched delta persistence (PH3-2)
        partial_text = ""
        finish_reason = "stop"
        last_heartbeat = time.perf_counter()
        settings = get_settings()
        merger = _DeltaBatcher(
            max_tokens=settings.sse_v2_delta_max_tokens,
            max_bytes=settings.sse_v2_delta_max_bytes,
            flush_ms=settings.sse_v2_delta_flush_ms,
        )

        try:
            for delta in self._run_generation(ctx):
                # Check for cancel before processing delta (PH3-4 late-delta rejection)
                if self._is_cancelled(ctx):
                    # Flush any buffered delta before stopping
                    if merger.has_pending():
                        batch = merger.flush()
                        partial_text += batch
                        yield self._emit(
                            ctx, "message.delta", {"delta": batch}
                        )
                    yield from self._finish_stopped(ctx, partial_text=partial_text)
                    return

                partial_text += delta
                merger.append(delta)

                # Check if batch should flush
                should_flush, _ = merger.should_flush()
                if should_flush:
                    batch = merger.flush()
                    yield self._emit(
                        ctx, "message.delta", {"delta": batch}
                    )

                # Heartbeat check (SSE comment, no seq)
                now = time.perf_counter()
                if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                    last_heartbeat = now
                    yield ": heartbeat\n\n"

            # Flush any remaining buffered delta
            if merger.has_pending():
                batch = merger.flush()
                yield self._emit(
                    ctx, "message.delta", {"delta": batch}
                )

        except Exception as exc:
            logger.exception("generation_worker failed turn=%s", ctx.turn_id)
            # Flush buffered content before failing so partial is preserved
            if merger.has_pending():
                partial_text += merger.flush()
            yield from self._finish_failed(ctx, error_code="GENERATION_ERROR", message=str(exc))
            return

        # 7. Terminal: COMPLETED
        yield from self._finish_completed(ctx, content=partial_text, finish_reason=finish_reason)

    def _run_generation(self, ctx: WorkerContext) -> Generator[str, None, None]:
        """Override in subclass or PH2 to call ContextEngine + Provider.

        Default implementation raises NotImplementedError; the actual generation
        logic is wired in by the qa.py adapter or a concrete subclass.
        """
        raise NotImplementedError(
            "GenerationWorker._run_generation must be overridden by a concrete "
            "implementation that calls the Provider stream."
        )

    def _finish_completed(
        self, ctx: WorkerContext, *, content: str, finish_reason: str
    ) -> Generator[str, None, None]:
        """CAS to COMPLETED and emit terminal event.

        PH3-3 fix: the terminal event's seq is assigned by the DB BEFORE
        we read it for the payload, so terminal_seq always matches the
        actual terminal event seq (spec 00 §6 fix).
        """
        try:
            self._turns.finish(
                tenant_id=ctx.tenant_id,
                turn_id=ctx.turn_id,
                expected_state=STREAMING,
                terminal_state=COMPLETED,
                finish_reason=finish_reason,
                last_seq=self._events.get_last_seq(
                    tenant_id=ctx.tenant_id, turn_id=ctx.turn_id
                ),
                assistant_content=content,
            )
        except (TurnTerminalConflict, TurnTransitionError):
            # Already terminal (e.g. Stop raced) — do not emit a second terminal
            return

        # Update attempt to COMPLETED
        self._update_attempt_state(ctx, state=COMPLETED)

        # Emit terminal event — _emit assigns the seq atomically, then
        # we read the assigned seq for the payload (PH3-3 fix).
        yield self._emit_terminal(
            ctx,
            "turn.completed",
            {
                "message_id": ctx.assistant_message_id,
                "finish_reason": finish_reason,
            },
        )

    def _finish_stopped(
        self, ctx: WorkerContext, *, partial_text: str
    ) -> Generator[str, None, None]:
        """CAS to STOPPED and emit terminal event (PH3-3 seq fix)."""
        last_seq = self._events.get_last_seq(
            tenant_id=ctx.tenant_id, turn_id=ctx.turn_id
        )
        try:
            current = self._get_turn_state(ctx)
            if current == STREAMING:
                self._turns.finish(
                    tenant_id=ctx.tenant_id,
                    turn_id=ctx.turn_id,
                    expected_state=STREAMING,
                    terminal_state=STOPPED,
                    finish_reason="cancelled",
                    last_seq=last_seq,
                    assistant_content=partial_text,
                )
            elif current == CANCEL_REQUESTED:
                self._turns.finish(
                    tenant_id=ctx.tenant_id,
                    turn_id=ctx.turn_id,
                    expected_state=CANCEL_REQUESTED,
                    terminal_state=STOPPED,
                    finish_reason="cancelled",
                    last_seq=last_seq,
                    assistant_content=partial_text,
                )
            else:
                return
        except (TurnTerminalConflict, TurnTransitionError):
            return

        self._update_attempt_state(ctx, state=STOPPED)

        yield self._emit_terminal(
            ctx,
            "turn.stopped",
            {"partial": bool(partial_text)},
        )

    def _finish_failed(
        self, ctx: WorkerContext, *, error_code: str, message: str, retryable: bool = False
    ) -> Generator[str, None, None]:
        """CAS to FAILED and emit terminal event (PH3-3 seq fix)."""
        last_seq = self._events.get_last_seq(
            tenant_id=ctx.tenant_id, turn_id=ctx.turn_id
        )
        current = self._get_turn_state(ctx)
        for expected in (STREAMING, "BUILDING_CONTEXT", RETRIEVING, QUEUED, CANCEL_REQUESTED):
            if current == expected:
                try:
                    self._turns.finish(
                        tenant_id=ctx.tenant_id,
                        turn_id=ctx.turn_id,
                        expected_state=expected,
                        terminal_state=FAILED,
                        finish_reason="error",
                        last_seq=last_seq,
                    )
                    break
                except (TurnTerminalConflict, TurnTransitionError):
                    continue
        else:
            return  # Already terminal

        self._update_attempt_state(ctx, state=FAILED, error_code=error_code, retryable=retryable)

        yield self._emit_terminal(
            ctx,
            "turn.failed",
            {
                "code": error_code,
                "message": message,
                "retryable": retryable,
            },
        )

    def _get_turn_state(self, ctx: WorkerContext) -> str:
        """Get the current turn state from the database."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status FROM qa_turns "
                    "WHERE turn_id=:turn_id AND tenant_id=:tenant_id"
                ),
                {"turn_id": ctx.turn_id, "tenant_id": ctx.tenant_id},
            ).first()
            return str(row[0]) if row else FAILED

    def _update_attempt_state(
        self,
        ctx: WorkerContext,
        *,
        state: str,
        error_code: Optional[str] = None,
        retryable: bool = False,
    ) -> None:
        """Update the attempt's terminal state."""
        now = utc_now()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE turn_attempts SET state=:state, completed_at=:now, "
                    "error_code=:error_code, retryable=:retryable "
                    "WHERE id=:attempt_id AND tenant_id=:tenant_id"
                ),
                {
                    "state": state,
                    "now": now,
                    "error_code": error_code,
                    "retryable": retryable,
                    "attempt_id": ctx.attempt_id,
                    "tenant_id": ctx.tenant_id,
                },
            )
            # Release lease
            conn.execute(
                text(
                    "UPDATE turn_attempts SET lease_expires_at=NULL "
                    "WHERE id=:attempt_id AND tenant_id=:tenant_id"
                ),
                {
                    "attempt_id": ctx.attempt_id,
                    "tenant_id": ctx.tenant_id,
                },
            )

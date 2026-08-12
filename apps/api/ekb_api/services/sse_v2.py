"""PH4 SSE v2 wire contract (spec 05-ai-chat.md section 6).

This module owns the *bytes* of the conversation stream so that routers cannot
drift from the contract by hand-rolling f-strings.  Three rules are enforced
mechanically rather than by review:

1. ``id: <turn_id>:<seq>`` and the JSON ``seq`` are produced from the same
   counter, so they can never disagree.  The first data event is ``seq=1``.
2. Heartbeats are transport comments (``: heartbeat <timestamp>``).  They carry
   no id/event/data lines and do **not** consume a seq.
3. A turn emits exactly one terminal event.  ``turn.completed`` /
   ``turn.stopped`` / ``turn.failed`` all go through :meth:`SseStream.terminal`,
   which refuses a second terminal — the first-terminal-wins rule from section 5
   has to hold on the wire, not just in the database.

Backwards compatibility with the older v2 readers is kept by lifting
``conversation_id`` / ``message_id`` / ``stream_version`` out of the payload onto
the envelope top level, matching ``routers/qa.py::_sse_v2``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

from ekb_api.domain import utc_now

# --- event names ----------------------------------------------------------

TURN_ACCEPTED = "turn.accepted"
TURN_STAGE = "turn.stage"
MESSAGE_DELTA = "message.delta"
CITATION_UPSERT = "citation.upsert"
CONTEXT_COMPACTED = "context.compacted"
ROUTE_SELECTED = "route.selected"
USAGE_UPDATED = "usage.updated"
TURN_COMPLETED = "turn.completed"
TURN_STOPPED = "turn.stopped"
TURN_FAILED = "turn.failed"

TERMINAL_EVENTS = frozenset({TURN_COMPLETED, TURN_STOPPED, TURN_FAILED})

DATA_EVENTS = (
    TURN_ACCEPTED,
    TURN_STAGE,
    MESSAGE_DELTA,
    CITATION_UPSERT,
    CONTEXT_COMPACTED,
    ROUTE_SELECTED,
    USAGE_UPDATED,
    TURN_COMPLETED,
    TURN_STOPPED,
    TURN_FAILED,
)

# Stage values mirror the active states of the turn state machine; the spec
# requires non-random progress, so each stage carries a fixed ordinal.
STAGE_PROGRESS = {
    "retrieving": 0.25,
    "building_context": 0.55,
    "streaming": 0.80,
}

# Envelope keys that stay lifted for legacy readers.
_LIFTED_KEYS = ("conversation_id", "message_id", "stream_version")

STREAM_VERSION = "v2"


class SseContractError(RuntimeError):
    """Raised when a caller would violate the wire contract."""


def encode_event(
    event: str,
    *,
    turn_id: str,
    request_id: str,
    seq: int,
    payload: dict[str, Any],
    timestamp: Optional[str] = None,
) -> str:
    """Render one SSE v2 data event exactly as specified in section 6."""

    if event not in DATA_EVENTS:
        raise SseContractError(f"unknown SSE v2 event: {event}")
    if seq < 1:
        raise SseContractError("SSE v2 seq starts at 1")
    envelope: dict[str, Any] = {
        "turn_id": turn_id,
        "request_id": request_id,
        "seq": seq,
        "timestamp": timestamp or utc_now(),
        "payload": payload,
    }
    for key in _LIFTED_KEYS:
        if key in payload and key not in envelope:
            envelope[key] = payload[key]
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return f"id: {turn_id}:{seq}\nevent: {event}\ndata: {encoded}\n\n"


def encode_heartbeat(timestamp: Optional[str] = None) -> str:
    """Transport comment; never consumes a seq (section 6)."""

    return f": heartbeat {timestamp or utc_now()}\n\n"


def parse_last_event_id(raw: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """Diagnostics only — the first release does not promise replay.

    Returns ``(turn_id, seq)`` when the header is well formed, ``(None, None)``
    otherwise.  Callers must not use this to skip events.
    """

    if not raw or ":" not in raw:
        return None, None
    turn_id, _, tail = raw.rpartition(":")
    if not turn_id or not tail.isdigit():
        return None, None
    return turn_id, int(tail)


@dataclass
class SseStream:
    """Sequencer for one turn's stream.

    The router owns an instance for the lifetime of the response and calls the
    typed helpers below.  ``seq`` is private state: every data event increments
    it, heartbeats do not.
    """

    turn_id: str
    request_id: str
    _seq: int = field(default=0, init=False)
    _terminal: Optional[str] = field(default=None, init=False)

    @property
    def seq(self) -> int:
        """Sequence number of the last emitted data event (0 before the first)."""

        return self._seq

    @property
    def terminal_event(self) -> Optional[str]:
        return self._terminal

    @property
    def closed(self) -> bool:
        return self._terminal is not None

    # -- generic ----------------------------------------------------------

    def emit(self, event: str, payload: dict[str, Any]) -> str:
        if self._terminal is not None:
            raise SseContractError(
                f"turn {self.turn_id} already emitted terminal {self._terminal}"
            )
        if event in TERMINAL_EVENTS:
            raise SseContractError("use SseStream.terminal() for terminal events")
        self._seq += 1
        return encode_event(
            event,
            turn_id=self.turn_id,
            request_id=self.request_id,
            seq=self._seq,
            payload=payload,
        )

    def heartbeat(self) -> str:
        return encode_heartbeat()

    # -- typed events -----------------------------------------------------

    def accepted(
        self,
        *,
        conversation_id: str,
        branch_id: str,
        user_message_id: str,
        assistant_message_id: str,
        snapshot_summary: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.emit(
            TURN_ACCEPTED,
            {
                "conversation_id": conversation_id,
                "branch_id": branch_id,
                "user_message_id": user_message_id,
                "message_id": assistant_message_id,
                "stream_version": STREAM_VERSION,
                "snapshot_summary": snapshot_summary or {},
            },
        )

    def stage(self, stage: str, *, detail: Optional[dict[str, Any]] = None) -> str:
        key = stage.lower()
        if key not in STAGE_PROGRESS:
            raise SseContractError(f"unknown turn stage: {stage}")
        payload: dict[str, Any] = {"stage": key, "progress": STAGE_PROGRESS[key]}
        if detail:
            payload["detail"] = detail
        return self.emit(TURN_STAGE, payload)

    def delta(self, *, message_id: str, text: str) -> str:
        return self.emit(
            MESSAGE_DELTA,
            {"message_id": message_id, "delta": text, "stream_version": STREAM_VERSION},
        )

    def citation(
        self,
        *,
        citation_id: str,
        document_id: Optional[str] = None,
        document_version_id: Optional[str] = None,
        attachment_id: Optional[str] = None,
        locator: Optional[dict[str, Any]] = None,
        display_title: Optional[str] = None,
    ) -> str:
        return self.emit(
            CITATION_UPSERT,
            {
                "citation_id": citation_id,
                "document_id": document_id,
                "document_version_id": document_version_id,
                "attachment_id": attachment_id,
                "locator": locator or {},
                "display_title": display_title,
            },
        )

    def context_compacted(
        self,
        *,
        covered_message_ids: Iterable[str],
        tokens_before: int,
        tokens_after: int,
        strategy: str,
        summary_id: Optional[str] = None,
    ) -> str:
        covered = list(covered_message_ids)
        return self.emit(
            CONTEXT_COMPACTED,
            {
                "summary_id": summary_id,
                "covered_message_ids": covered,
                "covered_count": len(covered),
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "strategy": strategy,
            },
        )

    def route_selected(
        self,
        *,
        requested_provider_id: Optional[str],
        requested_model_id: Optional[str],
        actual_provider_id: Optional[str],
        actual_model_id: Optional[str],
        fallback_reason: Optional[str] = None,
    ) -> str:
        fell_back = (
            requested_provider_id is not None
            and actual_provider_id is not None
            and (
                requested_provider_id != actual_provider_id
                or requested_model_id != actual_model_id
            )
        )
        return self.emit(
            ROUTE_SELECTED,
            {
                "requested": {
                    "provider_id": requested_provider_id,
                    "model_id": requested_model_id,
                },
                "actual": {
                    "provider_id": actual_provider_id,
                    "model_id": actual_model_id,
                },
                "fallback": fell_back,
                "fallback_reason": fallback_reason,
            },
        )

    def usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        estimated: bool = False,
    ) -> str:
        return self.emit(
            USAGE_UPDATED,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "estimated": estimated,
            },
        )

    # -- terminal ---------------------------------------------------------

    def terminal(self, event: str, payload: dict[str, Any]) -> str:
        """Emit the single terminal event for this turn."""

        if event not in TERMINAL_EVENTS:
            raise SseContractError(f"{event} is not a terminal event")
        if self._terminal is not None:
            raise SseContractError(
                f"turn {self.turn_id} already emitted terminal {self._terminal}"
            )
        self._seq += 1
        self._terminal = event
        return encode_event(
            event,
            turn_id=self.turn_id,
            request_id=self.request_id,
            seq=self._seq,
            payload=payload,
        )

    def completed(
        self,
        *,
        message_id: str,
        status: str = "completed",
        usage: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.terminal(
            TURN_COMPLETED,
            {
                "message_id": message_id,
                "status": status,
                "usage": usage or {},
                "stream_version": STREAM_VERSION,
            },
        )

    def stopped(
        self,
        *,
        message_id: str,
        partial_persisted: bool,
        usage: Optional[dict[str, Any]] = None,
    ) -> str:
        return self.terminal(
            TURN_STOPPED,
            {
                "message_id": message_id,
                "status": "stopped",
                "partial_persisted": partial_persisted,
                "usage": usage or {},
                "stream_version": STREAM_VERSION,
            },
        )

    def failed(
        self,
        *,
        code: str,
        retryable: bool,
        message_id: Optional[str] = None,
    ) -> str:
        return self.terminal(
            TURN_FAILED,
            {
                "message_id": message_id,
                "status": "failed",
                "code": code,
                "retryable": retryable,
                "request_id": self.request_id,
                "stream_version": STREAM_VERSION,
            },
        )

    def terminal_for_state(self, state: str, payload: dict[str, Any]) -> str:
        """Map a turn terminal *state* to its wire event."""

        mapping = {
            "COMPLETED": TURN_COMPLETED,
            "STOPPED": TURN_STOPPED,
            "FAILED": TURN_FAILED,
        }
        event = mapping.get(state)
        if event is None:
            raise SseContractError(f"{state} is not a terminal turn state")
        return self.terminal(event, payload)

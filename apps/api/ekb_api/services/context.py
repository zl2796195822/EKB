"""PH4 context assembly and budgeting (spec 05-ai-chat.md section 8).

The builder is deliberately pure: it takes already-authorised material and
returns a manifest.  No database, no provider calls.  That keeps the budget
arithmetic testable and makes the audit trail reproducible — the same inputs
always yield the same ``manifest_hash``.

Budget:

    available_input = model_context_window - reserved_output - provider_safety_margin

Allocation follows the spec's fixed priority.  Steps 1 and 2 are *pinned*: if
the system prompt plus the current user message and its direct attachments do
not fit, that is a hard error, not a silent drop.  Everything after that is
greedy in priority order:

    3. RAG evidence (relevance, with per-KB and per-document quotas)
    4. recent complete turns, newest first, never splitting a user/assistant pair
    5. existing valid summaries
    6. on overflow: a bounded summary of what did not fit; if summarisation is
       unavailable or fails, deterministic truncation of the oldest complete turns

Only hashes are recorded for audit — never message bodies (section 12).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Callable, Optional

from ekb_api.llm import estimate_text_tokens

# Segment kinds, in wire order.
KIND_SYSTEM = "system"
KIND_SUMMARY = "summary"
KIND_HISTORY = "history"
KIND_RAG = "rag_evidence"
KIND_ATTACHMENT = "attachment"
KIND_USER = "user"

WIRE_ORDER = (KIND_SYSTEM, KIND_SUMMARY, KIND_HISTORY, KIND_RAG, KIND_ATTACHMENT, KIND_USER)

STRATEGY_NONE = "none"
STRATEGY_SUMMARIZE = "summarize"
STRATEGY_TRUNCATE = "truncate"
STRATEGY_SUMMARIZE_FAILED = "summarize_failed_truncate"

DEFAULT_RESERVED_OUTPUT = 2048
DEFAULT_SAFETY_MARGIN = 512
DEFAULT_SUMMARY_TOKEN_CAP = 768
DEFAULT_MAX_PER_KB = 6
DEFAULT_MAX_PER_DOCUMENT = 3


class ContextBudgetExceeded(RuntimeError):
    """The pinned material alone does not fit the model window."""

    def __init__(self, required: int, available: int) -> None:
        super().__init__(
            f"pinned context requires {required} tokens but only {available} available"
        )
        self.required = required
        self.available = available


@dataclass(frozen=True)
class ContextBudget:
    model_context_window: int
    reserved_output: int = DEFAULT_RESERVED_OUTPUT
    provider_safety_margin: int = DEFAULT_SAFETY_MARGIN

    @property
    def available_input(self) -> int:
        value = (
            self.model_context_window
            - self.reserved_output
            - self.provider_safety_margin
        )
        return max(0, value)


@dataclass(frozen=True)
class MessageRef:
    """A persisted message that may enter the context."""

    message_id: str
    role: str
    content: str

    @property
    def tokens(self) -> int:
        return estimate_text_tokens(self.content) + 4


@dataclass(frozen=True)
class HistoryTurn:
    """A complete user/assistant pair. Never split (spec step 4)."""

    user: MessageRef
    assistant: MessageRef

    @property
    def tokens(self) -> int:
        return self.user.tokens + self.assistant.tokens

    @property
    def message_ids(self) -> tuple[str, str]:
        return (self.user.message_id, self.assistant.message_id)


@dataclass(frozen=True)
class AttachmentRef:
    attachment_id: str
    content: str
    kind: str = "text"

    @property
    def tokens(self) -> int:
        return estimate_text_tokens(self.content) + 4


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    content: str
    score: float
    kb_id: Optional[str] = None
    document_id: Optional[str] = None
    document_version_id: Optional[str] = None
    display_title: Optional[str] = None

    @property
    def tokens(self) -> int:
        return estimate_text_tokens(self.content) + 4


@dataclass(frozen=True)
class SummaryRef:
    summary_id: str
    content: str
    covered_message_ids: tuple[str, ...]
    valid: bool = True

    @property
    def tokens(self) -> int:
        return estimate_text_tokens(self.content) + 4


@dataclass
class ContextSegment:
    kind: str
    role: str
    content: str
    tokens: int
    ref_id: Optional[str] = None

    def digest(self) -> str:
        body = hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:16]
        return f"{self.kind}:{self.ref_id or '-'}:{self.tokens}:{body}"


@dataclass
class CompactionOutcome:
    strategy: str = STRATEGY_NONE
    covered_message_ids: tuple[str, ...] = ()
    tokens_before: int = 0
    tokens_after: int = 0
    summary_id: Optional[str] = None
    failure_reason: Optional[str] = None

    @property
    def applied(self) -> bool:
        return self.strategy != STRATEGY_NONE


@dataclass
class ContextManifest:
    segments: list[ContextSegment] = field(default_factory=list)
    available_input: int = 0
    used_tokens: int = 0
    compaction: CompactionOutcome = field(default_factory=CompactionOutcome)
    dropped_message_ids: tuple[str, ...] = ()
    included_message_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.available_input - self.used_tokens)

    @property
    def manifest_hash(self) -> str:
        joined = "|".join(segment.digest() for segment in self.segments)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def as_messages(self) -> list[dict[str, str]]:
        """Provider-facing message list, in wire order."""

        ordered: list[ContextSegment] = []
        for kind in WIRE_ORDER:
            ordered.extend(seg for seg in self.segments if seg.kind == kind)
        return [{"role": seg.role, "content": seg.content} for seg in ordered]

    def audit_record(self) -> dict[str, object]:
        """Section 12: ids, counts and hashes only — never bodies."""

        return {
            "manifest_hash": self.manifest_hash,
            "available_input": self.available_input,
            "used_tokens": self.used_tokens,
            "segment_count": len(self.segments),
            "included_message_ids": list(self.included_message_ids),
            "dropped_message_ids": list(self.dropped_message_ids),
            "evidence_count": len(self.evidence_ids),
            "compaction_strategy": self.compaction.strategy,
            "compaction_tokens_before": self.compaction.tokens_before,
            "compaction_tokens_after": self.compaction.tokens_after,
        }


Summarizer = Callable[[Sequence[HistoryTurn]], Optional[str]]


def select_evidence(
    evidence: Iterable[EvidenceRef],
    *,
    max_per_kb: int = DEFAULT_MAX_PER_KB,
    max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
) -> list[EvidenceRef]:
    """Relevance ordering with diversity quotas (spec step 3).

    Ties break on ``evidence_id`` so the selection is deterministic — the audit
    hash must not depend on dictionary ordering.
    """

    ranked = sorted(evidence, key=lambda item: (-item.score, item.evidence_id))
    per_kb: dict[str, int] = {}
    per_doc: dict[str, int] = {}
    chosen: list[EvidenceRef] = []
    for item in ranked:
        kb_key = item.kb_id or "_"
        doc_key = item.document_id or item.evidence_id
        if per_kb.get(kb_key, 0) >= max_per_kb:
            continue
        if per_doc.get(doc_key, 0) >= max_per_document:
            continue
        per_kb[kb_key] = per_kb.get(kb_key, 0) + 1
        per_doc[doc_key] = per_doc.get(doc_key, 0) + 1
        chosen.append(item)
    return chosen


class ContextBuilder:
    def __init__(
        self,
        budget: ContextBudget,
        *,
        summary_token_cap: int = DEFAULT_SUMMARY_TOKEN_CAP,
        max_per_kb: int = DEFAULT_MAX_PER_KB,
        max_per_document: int = DEFAULT_MAX_PER_DOCUMENT,
    ) -> None:
        self.budget = budget
        self.summary_token_cap = summary_token_cap
        self.max_per_kb = max_per_kb
        self.max_per_document = max_per_document

    def build(
        self,
        *,
        system_prompt: str,
        user_message: MessageRef,
        attachments: Sequence[AttachmentRef] = (),
        evidence: Sequence[EvidenceRef] = (),
        history: Sequence[HistoryTurn] = (),
        summaries: Sequence[SummaryRef] = (),
        summarizer: Optional[Summarizer] = None,
    ) -> ContextManifest:
        available = self.budget.available_input
        manifest = ContextManifest(available_input=available)

        # -- steps 1 & 2: pinned ------------------------------------------
        pinned: list[ContextSegment] = [
            ContextSegment(
                kind=KIND_SYSTEM,
                role="system",
                content=system_prompt,
                tokens=estimate_text_tokens(system_prompt) + 4,
                ref_id="system",
            )
        ]
        for attachment in attachments:
            pinned.append(
                ContextSegment(
                    kind=KIND_ATTACHMENT,
                    role="user",
                    content=attachment.content,
                    tokens=attachment.tokens,
                    ref_id=attachment.attachment_id,
                )
            )
        pinned.append(
            ContextSegment(
                kind=KIND_USER,
                role=user_message.role,
                content=user_message.content,
                tokens=user_message.tokens,
                ref_id=user_message.message_id,
            )
        )
        used = sum(seg.tokens for seg in pinned)
        if used > available:
            raise ContextBudgetExceeded(used, available)
        manifest.segments.extend(pinned)

        included_ids: list[str] = [user_message.message_id]
        evidence_ids: list[str] = []

        # -- step 3: RAG evidence -----------------------------------------
        for item in select_evidence(
            evidence,
            max_per_kb=self.max_per_kb,
            max_per_document=self.max_per_document,
        ):
            if used + item.tokens > available:
                continue
            used += item.tokens
            manifest.segments.append(
                ContextSegment(
                    kind=KIND_RAG,
                    role="system",
                    content=item.content,
                    tokens=item.tokens,
                    ref_id=item.evidence_id,
                )
            )
            evidence_ids.append(item.evidence_id)

        # -- step 4: recent complete turns, newest first --------------------
        kept: list[HistoryTurn] = []
        overflow: list[HistoryTurn] = []
        for turn in reversed(list(history)):
            if overflow or used + turn.tokens > available:
                overflow.append(turn)
                continue
            used += turn.tokens
            kept.append(turn)
        kept.reverse()
        overflow.reverse()

        for turn in kept:
            manifest.segments.append(
                ContextSegment(
                    kind=KIND_HISTORY,
                    role=turn.user.role,
                    content=turn.user.content,
                    tokens=turn.user.tokens,
                    ref_id=turn.user.message_id,
                )
            )
            manifest.segments.append(
                ContextSegment(
                    kind=KIND_HISTORY,
                    role=turn.assistant.role,
                    content=turn.assistant.content,
                    tokens=turn.assistant.tokens,
                    ref_id=turn.assistant.message_id,
                )
            )
            included_ids.extend(turn.message_ids)

        # -- step 5: existing valid summaries -------------------------------
        covered_by_summary: set[str] = set()
        for summary in summaries:
            if not summary.valid:
                continue
            if used + summary.tokens > available:
                continue
            used += summary.tokens
            manifest.segments.append(
                ContextSegment(
                    kind=KIND_SUMMARY,
                    role="system",
                    content=summary.content,
                    tokens=summary.tokens,
                    ref_id=summary.summary_id,
                )
            )
            covered_by_summary.update(summary.covered_message_ids)

        # -- step 6: compaction of what did not fit --------------------------
        dropped: list[str] = []
        uncovered = [
            turn
            for turn in overflow
            if not set(turn.message_ids).issubset(covered_by_summary)
        ]
        if uncovered:
            tokens_before = sum(turn.tokens for turn in uncovered)
            covered_ids = tuple(
                mid for turn in uncovered for mid in turn.message_ids
            )
            summary_text = None
            failure_reason = None
            if summarizer is not None:
                try:
                    summary_text = summarizer(tuple(uncovered))
                except Exception as exc:  # bounded: summarisation must not fail the turn
                    failure_reason = type(exc).__name__
                    summary_text = None
            else:
                failure_reason = "no_summarizer"

            fitted = False
            if summary_text:
                tokens = estimate_text_tokens(summary_text) + 4
                if tokens > self.summary_token_cap:
                    failure_reason = "summary_over_cap"
                elif used + tokens > available:
                    failure_reason = "summary_does_not_fit"
                else:
                    used += tokens
                    manifest.segments.append(
                        ContextSegment(
                            kind=KIND_SUMMARY,
                            role="system",
                            content=summary_text,
                            tokens=tokens,
                            ref_id="compaction",
                        )
                    )
                    manifest.compaction = CompactionOutcome(
                        strategy=STRATEGY_SUMMARIZE,
                        covered_message_ids=covered_ids,
                        tokens_before=tokens_before,
                        tokens_after=tokens,
                    )
                    fitted = True

            if not fitted:
                dropped.extend(covered_ids)
                manifest.compaction = CompactionOutcome(
                    strategy=(
                        STRATEGY_TRUNCATE
                        if failure_reason == "no_summarizer"
                        else STRATEGY_SUMMARIZE_FAILED
                    ),
                    covered_message_ids=covered_ids,
                    tokens_before=tokens_before,
                    tokens_after=0,
                    failure_reason=failure_reason,
                )

        manifest.used_tokens = used
        manifest.included_message_ids = tuple(included_ids)
        manifest.dropped_message_ids = tuple(dropped)
        manifest.evidence_ids = tuple(evidence_ids)
        return manifest

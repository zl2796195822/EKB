"""Context Engine Service (PH2).

Bridges the database (canonical messages, KB evidence, attachments, summaries)
with the pure :class:`ContextBuilder`.  This service is called by
:class:`GenerationWorker` during the ``BUILDING_CONTEXT`` stage.

Key responsibilities:
* Load active-branch history as ``HistoryTurn`` pairs
* Execute RAG retrieval (if KBs are selected) → ``EvidenceRef``
* Load attachment parsed text → ``AttachmentRef``
* Load existing valid summaries → ``SummaryRef``
* Build ``ContextBudget`` from the model capability snapshot
* Call ``ContextBuilder.build()`` → ``ContextManifest``
* Persist the manifest to ``turn_context_manifests``
* Persist new summaries to ``context_summaries`` (compaction)
* Return the Provider-facing message list + audit manifest
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from sqlalchemy import text

from ekb_api.core.config import get_settings
from ekb_api.core.db import get_engine
from ekb_api.domain import utc_now
from ekb_api.services.context import (
    DEFAULT_RESERVED_OUTPUT,
    DEFAULT_SAFETY_MARGIN,
    STRATEGY_SUMMARIZE,
    AttachmentRef,
    ContextBudget,
    ContextBudgetExceeded,
    ContextBuilder,
    ContextManifest,
    EvidenceRef,
    HistoryTurn,
    MessageRef,
    SummaryRef,
)

logger = logging.getLogger("ekb.context_engine")


class ContextEngineService:
    """Builds and persists the model context for a Turn attempt."""

    def __init__(self, engine=None) -> None:
        self.engine = engine or get_engine()

    def build_context(
        self,
        *,
        tenant_id: str,
        turn_id: str,
        attempt_id: str,
        conversation_id: str,
        branch_id: str,
        user_message_id: str,
        prompt: str,
        model_context_window: int = 16384,
        knowledge_base_ids: Optional[list[str]] = None,
        attachment_ids: Optional[list[str]] = None,
        system_prompt: str = "",
        actor_id: str = "",
    ) -> ContextManifest:
        """Build the context manifest and persist it.

        Returns the ``ContextManifest`` which contains the Provider-facing
        message list (``manifest.as_messages()``) and the audit record
        (``manifest.audit_record()``).
        """
        # 1. Load history from active branch
        history = self._load_history(tenant_id, branch_id, user_message_id)

        # 2. Load current user message
        user_message = MessageRef(
            message_id=user_message_id,
            role="user",
            content=prompt,
        )

        # 3. Load attachments (current + historical reuse, spec 02 §9)
        current_refs, dropped_current = self._load_attachments(
            tenant_id, attachment_ids or [],
            owner_user_id=actor_id, conversation_id=conversation_id,
        )
        historical_refs, dropped_historical = self._load_historical_attachments(
            tenant_id, branch_id, user_message_id,
            owner_user_id=actor_id, conversation_id=conversation_id,
            exclude_ids=set(attachment_ids or []),
        )
        # Deduplicate: current attachments take priority
        seen_ids = {r.attachment_id for r in current_refs}
        attachments = list(current_refs)
        for ref in historical_refs:
            if ref.attachment_id not in seen_ids:
                attachments.append(ref)
                seen_ids.add(ref.attachment_id)

        all_dropped = dropped_current + dropped_historical
        if all_dropped:
            logger.warning(
                "context_engine attachments dropped turn=%s count=%d ids=%s",
                turn_id, len(all_dropped), all_dropped,
            )

        # 4. Load evidence (RAG)
        evidence = self._load_evidence(
            tenant_id, knowledge_base_ids or [], prompt, history
        )

        # 5. Load existing summaries
        summaries = self._load_summaries(tenant_id, branch_id)

        # 6. Build budget from model capability
        budget = ContextBudget(
            model_context_window=model_context_window,
            reserved_output=DEFAULT_RESERVED_OUTPUT,
            provider_safety_margin=DEFAULT_SAFETY_MARGIN,
        )

        # 7. Build context
        builder = ContextBuilder(budget)
        try:
            manifest = builder.build(
                system_prompt=system_prompt or self._default_system_prompt(),
                user_message=user_message,
                attachments=attachments,
                evidence=evidence,
                history=history,
                summaries=summaries,
            )
        except ContextBudgetExceeded:
            logger.warning(
                "context_budget_exceeded turn=%s window=%d",
                turn_id,
                model_context_window,
            )
            raise

        # 8. Assert budget
        assert manifest.used_tokens <= manifest.available_input, (
            f"Context budget assertion failed: used={manifest.used_tokens} "
            f"> available={manifest.available_input}"
        )

        # 9. Persist manifest
        self._persist_manifest(
            tenant_id=tenant_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            manifest=manifest,
            model_context_window=model_context_window,
        )

        # 10. Persist summary if compaction occurred
        if manifest.compaction.applied and manifest.compaction.summary_id:
            self._persist_summary(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                manifest=manifest,
            )

        return manifest

    def _load_history(
        self, tenant_id: str, branch_id: str, current_user_message_id: str
    ) -> list[HistoryTurn]:
        """Load complete user/assistant pairs from the active branch."""
        from ekb_api.services.conversations import ConversationGraphService

        graph = ConversationGraphService(self.engine)
        messages = graph.branch_messages(tenant_id=tenant_id, branch_id=branch_id)

        # Filter out the current turn's messages and incomplete pairs
        pairs: list[HistoryTurn] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.id == current_user_message_id:
                break  # Stop at current turn
            if msg.role == "user" and i + 1 < len(messages):
                next_msg = messages[i + 1]
                if next_msg.role == "assistant" and next_msg.status == "completed":
                    pairs.append(
                        HistoryTurn(
                            user=MessageRef(
                                message_id=msg.id, role="user", content=msg.content or ""
                            ),
                            assistant=MessageRef(
                                message_id=next_msg.id,
                                role="assistant",
                                content=next_msg.content or "",
                            ),
                        )
                    )
                    i += 2
                    continue
            i += 1

        return pairs

    def _load_attachments(
        self,
        tenant_id: str,
        attachment_ids: list[str],
        *,
        owner_user_id: str = "",
        conversation_id: str = "",
    ) -> tuple[list[AttachmentRef], list[str]]:
        """Load parsed text content of READY/ATTACHED attachments with ACL revalidation.

        Spec 02 §9: every turn revalidates tenant, actor, attachment state,
        retention and object availability.  Revoked/deleted/expired items are
        dropped (fail-closed) and their IDs returned for visible warning.
        """
        if not attachment_ids:
            return [], []
        refs: list[AttachmentRef] = []
        dropped: list[str] = []
        now_iso = utc_now()
        with self.engine.connect() as conn:
            for att_id in dict.fromkeys(attachment_ids):
                row = conn.execute(
                    text(
                        "SELECT a.id, a.detected_mime, a.owner_user_id,"
                        " a.conversation_id, a.status, a.expires_at,"
                        " a.deleted_at, a.purged_at, "
                        "(SELECT ac.text_content FROM attachment_chunks ac "
                        " WHERE ac.attachment_id = a.id AND ac.tenant_id = a.tenant_id"
                        " ORDER BY ac.ordinal LIMIT 1) AS chunk_text "
                        "FROM attachments a "
                        "WHERE a.id = :id AND a.tenant_id = :tenant_id"
                    ),
                    {"id": att_id, "tenant_id": tenant_id},
                ).first()
                if row is None:
                    dropped.append(att_id)
                    continue
                # ACL: owner scope
                if owner_user_id and row[2] != owner_user_id:
                    dropped.append(att_id)
                    continue
                # ACL: conversation scope
                if conversation_id and row[3] not in (None, conversation_id):
                    dropped.append(att_id)
                    continue
                # ACL: state must be READY or ATTACHED
                if row[4] not in ("READY", "ATTACHED"):
                    dropped.append(att_id)
                    continue
                # Retention: expired
                if row[5] and str(row[5]) <= now_iso:
                    dropped.append(att_id)
                    continue
                # Retention: deleted or purged
                if row[6] or row[7]:
                    dropped.append(att_id)
                    continue
                refs.append(
                    AttachmentRef(
                        attachment_id=str(row[0]),
                        content=str(row[8] or ""),
                        kind=str(row[1] or "text"),
                    )
                )
        return refs, dropped

    def _load_historical_attachments(
        self,
        tenant_id: str,
        branch_id: str,
        current_user_message_id: str,
        *,
        owner_user_id: str = "",
        conversation_id: str = "",
        exclude_ids: Optional[set[str]] = None,
    ) -> tuple[list[AttachmentRef], list[str]]:
        """Discover and load attachments bound to prior messages in the active branch.

        Spec 02 §9: later turns may reuse authorized historical attachments
        from the active branch without resubmitting raw content.  Each is
        revalidated for ACL and retention (same as current attachments).
        """
        from ekb_api.services.conversations import ConversationGraphService

        graph = ConversationGraphService(self.engine)
        messages = graph.branch_messages(tenant_id=tenant_id, branch_id=branch_id)

        # Collect message IDs prior to the current user message
        prior_message_ids: list[str] = []
        for msg in messages:
            if msg.id == current_user_message_id:
                break
            prior_message_ids.append(msg.id)

        if not prior_message_ids:
            return [], []

        exclude_ids = exclude_ids or set()

        # Query message_attachments for prior messages
        historical_ids: list[str] = []
        with self.engine.connect() as conn:
            placeholders = ",".join(f":m{i}" for i in range(len(prior_message_ids)))
            params: dict[str, str] = {f"m{i}": mid for i, mid in enumerate(prior_message_ids)}
            params["tenant_id"] = tenant_id
            rows = conn.execute(
                text(
                    f"SELECT DISTINCT ma.attachment_id FROM message_attachments ma "
                    f"JOIN messages m ON m.id = ma.message_id AND m.tenant_id = ma.tenant_id "
                    f"WHERE ma.tenant_id = :tenant_id "
                    f"AND ma.message_id IN ({placeholders})"
                ),
                params,
            ).all()
            for r in rows:
                aid = str(r[0])
                if aid not in exclude_ids:
                    historical_ids.append(aid)

        if not historical_ids:
            return [], []

        return self._load_attachments(
            tenant_id, historical_ids,
            owner_user_id=owner_user_id, conversation_id=conversation_id,
        )

    def _load_evidence(
        self,
        tenant_id: str,
        kb_ids: list[str],
        query: str,
        history: list[HistoryTurn],
    ) -> list[EvidenceRef]:
        """Execute RAG retrieval and return evidence refs."""
        if not kb_ids:
            return []
        try:
            from ekb_api.services.rag import RagService

            rag = RagService(self.engine)
            # Build contextual query (PH2-5: resolve pronouns using recent context)
            contextual_query = self._build_contextual_query(query, history)

            results = rag.retrieve(
                tenant_id=tenant_id,
                kb_ids=kb_ids,
                query=contextual_query,
            )
            return [
                EvidenceRef(
                    evidence_id=str(r.get("chunk_id", r.get("id", ""))),
                    content=str(r.get("text", r.get("content", ""))),
                    score=float(r.get("score", 0.0)),
                    kb_id=str(r.get("kb_id", "")),
                    document_id=str(r.get("document_id", "")),
                    document_version_id=str(r.get("document_version_id", "")),
                    display_title=str(r.get("title", "")),
                )
                for r in results
            ]
        except Exception as exc:
            logger.warning("context_engine evidence load failed: %s", exc)
            return []

    def _build_contextual_query(
        self, query: str, history: list[HistoryTurn]
    ) -> str:
        """Build a contextual retrieval query (PH2-5).

        Uses the last 1-2 turns to resolve pronouns and references.
        """
        if not history:
            return query
        recent = history[-2:] if len(history) >= 2 else history
        context_parts = []
        for turn in recent:
            context_parts.append(f"Q: {turn.user.content[:200]}")
            context_parts.append(f"A: {turn.assistant.content[:200]}")
        context_str = "\n".join(context_parts)
        return f"Context:\n{context_str}\n\nCurrent question: {query}"

    def _load_summaries(
        self, tenant_id: str, branch_id: str
    ) -> list[SummaryRef]:
        """Load existing valid summaries for the branch."""
        refs: list[SummaryRef] = []
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, summary_text, covered_message_ids, quality_state "
                    "FROM context_summaries "
                    "WHERE tenant_id = :tenant_id AND branch_id = :branch_id "
                    "AND quality_state = 'ok' "
                    "ORDER BY created_at DESC LIMIT 5"
                ),
                {"tenant_id": tenant_id, "branch_id": branch_id},
            ).all()
            for row in rows:
                covered = ()
                if row[2]:
                    try:
                        covered = tuple(json.loads(str(row[2])))
                    except (json.JSONDecodeError, TypeError):
                        pass
                refs.append(
                    SummaryRef(
                        summary_id=str(row[0]),
                        content=str(row[1] or ""),
                        covered_message_ids=covered,
                        valid=True,
                    )
                )
        return refs

    def _persist_manifest(
        self,
        *,
        tenant_id: str,
        turn_id: str,
        attempt_id: str,
        manifest: ContextManifest,
        model_context_window: int,
    ) -> None:
        """Persist the context manifest to turn_context_manifests."""
        from ekb_api.domain import new_id

        manifest_id = new_id()
        now = utc_now()
        settings = get_settings()

        # Build component_refs (IDs and hashes only, no content)
        component_refs = {
            "included_message_ids": list(manifest.included_message_ids),
            "dropped_message_ids": list(manifest.dropped_message_ids),
            "evidence_ids": list(manifest.evidence_ids),
            "segment_count": len(manifest.segments),
            "compaction_strategy": manifest.compaction.strategy,
            "compaction_summary_id": manifest.compaction.summary_id,
        }

        # Capability snapshot hash
        cap_hash = hashlib.sha256(
            f"{model_context_window}:{settings.ce_event_retention_days}".encode()
        ).hexdigest()

        refs_json = json.dumps(component_refs, ensure_ascii=False, separators=(",", ":"))
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "INSERT INTO turn_context_manifests "
                        "(id, tenant_id, turn_id, attempt_id, manifest_hash,"
                        " system_prompt_version, capability_snapshot_hash,"
                        " available_input_tokens, used_input_tokens,"
                        " reserved_output_tokens, provider_safety_margin,"
                        " component_refs, created_at) "
                        "VALUES (:id,:tenant_id,:turn_id,:attempt_id,:manifest_hash,"
                        "'v1',:cap_hash,:available,:used,:reserved,:margin,"
                        "CAST(:refs AS jsonb),:created_at)"
                    ),
                    {
                        "id": manifest_id,
                        "tenant_id": tenant_id,
                        "turn_id": turn_id,
                        "attempt_id": attempt_id,
                        "manifest_hash": manifest.manifest_hash,
                        "cap_hash": cap_hash,
                        "available": manifest.available_input,
                        "used": manifest.used_tokens,
                        "reserved": DEFAULT_RESERVED_OUTPUT,
                        "margin": DEFAULT_SAFETY_MARGIN,
                        "refs": refs_json,
                        "created_at": now,
                    },
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO turn_context_manifests "
                        "(id, tenant_id, turn_id, attempt_id, manifest_hash,"
                        " system_prompt_version, capability_snapshot_hash,"
                        " available_input_tokens, used_input_tokens,"
                        " reserved_output_tokens, provider_safety_margin,"
                        " component_refs, created_at) "
                        "VALUES (:id,:tenant_id,:turn_id,:attempt_id,:manifest_hash,"
                        "'v1',:cap_hash,:available,:used,:reserved,:margin,"
                        ":refs,:created_at)"
                    ),
                    {
                        "id": manifest_id,
                        "tenant_id": tenant_id,
                        "turn_id": turn_id,
                        "attempt_id": attempt_id,
                        "manifest_hash": manifest.manifest_hash,
                        "cap_hash": cap_hash,
                        "available": manifest.available_input,
                        "used": manifest.used_tokens,
                        "reserved": DEFAULT_RESERVED_OUTPUT,
                        "margin": DEFAULT_SAFETY_MARGIN,
                        "refs": refs_json,
                        "created_at": now,
                    },
                )

    def _persist_summary(
        self,
        *,
        tenant_id: str,
        conversation_id: str,
        branch_id: str,
        manifest: ContextManifest,
    ) -> None:
        """Persist a new context summary if compaction produced one."""
        if not manifest.compaction.summary_id:
            return

        summary_id = manifest.compaction.summary_id
        now = utc_now()

        # Check if summary already exists
        with self.engine.connect() as conn:
            existing = conn.execute(
                text(
                    "SELECT id FROM context_summaries WHERE id = :id"
                ),
                {"id": summary_id},
            ).first()
        if existing:
            return  # Already persisted

        # Compute source_hash from covered messages
        covered = list(manifest.compaction.covered_message_ids)
        source_hash = hashlib.sha256(
            "|".join(covered).encode()
        ).hexdigest()

        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "INSERT INTO context_summaries "
                        "(id, tenant_id, conversation_id, branch_id,"
                        " first_message_id, last_message_id, source_hash,"
                        " model_id, prompt_version, before_tokens, after_tokens,"
                        " summary_text, covered_message_ids, quality_state, created_at) "
                        "VALUES (:id,:tenant_id,:conversation_id,:branch_id,"
                        ":first_msg,:last_msg,:source_hash,'model','v1',"
                        ":before_tokens,:after_tokens,:summary_text,"
                        "CAST(:covered AS jsonb),:quality,:created_at)"
                    ),
                    {
                        "id": summary_id,
                        "tenant_id": tenant_id,
                        "conversation_id": conversation_id,
                        "branch_id": branch_id,
                        "first_msg": covered[0] if covered else "",
                        "last_msg": covered[-1] if covered else "",
                        "source_hash": source_hash,
                        "before_tokens": manifest.compaction.tokens_before,
                        "after_tokens": manifest.compaction.tokens_after,
                        "summary_text": "",
                        "covered": json.dumps(covered),
                        "quality": (
                            "ok"
                            if manifest.compaction.strategy == STRATEGY_SUMMARIZE
                            else "degraded"
                        ),
                        "created_at": now,
                    },
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO context_summaries "
                        "(id, tenant_id, conversation_id, branch_id,"
                        " first_message_id, last_message_id, source_hash,"
                        " model_id, prompt_version, before_tokens, after_tokens,"
                        " summary_text, covered_message_ids, quality_state, created_at) "
                        "VALUES (:id,:tenant_id,:conversation_id,:branch_id,"
                        ":first_msg,:last_msg,:source_hash,'model','v1',"
                        ":before_tokens,:after_tokens,:summary_text,"
                        ":covered,:quality,:created_at)"
                    ),
                    {
                        "id": summary_id,
                        "tenant_id": tenant_id,
                        "conversation_id": conversation_id,
                        "branch_id": branch_id,
                        "first_msg": covered[0] if covered else "",
                        "last_msg": covered[-1] if covered else "",
                        "source_hash": source_hash,
                        "before_tokens": manifest.compaction.tokens_before,
                        "after_tokens": manifest.compaction.tokens_after,
                        "summary_text": "",
                        "covered": json.dumps(covered),
                        "quality": (
                            "ok"
                            if manifest.compaction.strategy == STRATEGY_SUMMARIZE
                            else "degraded"
                        ),
                        "created_at": now,
                    },
                )

    def _default_system_prompt(self) -> str:
        return (
            "You are EKB AI Assistant. Answer based on the provided context. "
            "If the context is insufficient, state that clearly."
        )

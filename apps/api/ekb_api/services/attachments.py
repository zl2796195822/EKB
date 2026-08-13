"""Chat attachment core service (PH5).

Owns the lifecycle of a chat attachment per ``spec/07-file-and-image.md`` and the
``attachments`` shape created by ``migrations/v4_008_attachments.py``.

An attachment is a **chat resource**, distinct from a knowledge base document:
it has its own tenant/owner, lifecycle, retention fields and an explicit
cross-resource binding (``message_attachments``) that records the resolved
``usage_mode`` (INLINE / RETRIEVAL / VISION / OCR_FALLBACK). Attachment ids must
never be interpreted as KB/document ids.

This module is intentionally free of provider/object-storage calls. Storage and
processing are injected so the service stays testable against an in-memory
engine + fakes. ``AttachmentProcessor`` (``attachment_processor.py``) wires the
real storage/parser/embedder/vision/ocr glue.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import Engine, text

from ekb_api.domain import utc_now


class AttachmentError(Exception):
    """Base class for attachment domain errors."""


class AttachmentNotFound(AttachmentError):
    def __init__(self, attachment_id: str) -> None:
        super().__init__(f"attachment not found: {attachment_id}")
        self.attachment_id = attachment_id


class AttachmentStateConflict(AttachmentError):
    def __init__(self, attachment_id: str, current: str, required: str) -> None:
        super().__init__(
            f"attachment {attachment_id} state conflict: current={current} required={required}"
        )
        self.attachment_id = attachment_id
        self.current = current
        self.required = required


class AttachmentOwnershipError(AttachmentError):
    def __init__(self, attachment_id: str, actor_id: str) -> None:
        super().__init__(f"actor {actor_id} does not own attachment {attachment_id}")
        self.attachment_id = attachment_id
        self.actor_id = actor_id


class AttachmentMessageBindingError(AttachmentError):
    """The target message is not owned by the current actor and tenant."""

    def __init__(self, message_id: str) -> None:
        super().__init__(f"message is not bindable: {message_id}")
        self.message_id = message_id


class DuplicateClientRequest(AttachmentError):
    def __init__(self, attachment_id: str, client_request_id: str) -> None:
        super().__init__(f"duplicate attachment client_request_id={client_request_id}")
        self.attachment_id = attachment_id
        self.client_request_id = client_request_id


@dataclass(frozen=True)
class AttachmentStatus:
    UPLOADING = "UPLOADING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    ATTACHED = "ATTACHED"
    TRASHED = "TRASHED"


# Allowed lifecycle transitions (owner-scoped). Anything else is rejected.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    AttachmentStatus.UPLOADING: {AttachmentStatus.PROCESSING, AttachmentStatus.FAILED},
    AttachmentStatus.PROCESSING: {AttachmentStatus.READY, AttachmentStatus.FAILED},
    AttachmentStatus.READY: {AttachmentStatus.ATTACHED, AttachmentStatus.TRASHED},
    AttachmentStatus.FAILED: {AttachmentStatus.PROCESSING},  # retry
    AttachmentStatus.ATTACHED: {AttachmentStatus.TRASHED},
    AttachmentStatus.TRASHED: {AttachmentStatus.READY},  # restore within retention
}


USAGE_MODES = ("INLINE", "RETRIEVAL", "VISION", "OCR_FALLBACK")


@dataclass
class AttachmentRecord:
    id: str
    tenant_id: str
    owner_user_id: str
    conversation_id: Optional[str]
    source_object_id: str
    client_request_id: str
    status: str
    detected_mime: str
    byte_size: int
    expires_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "owner_user_id": self.owner_user_id,
            "conversation_id": self.conversation_id,
            "source_object_id": self.source_object_id,
            "client_request_id": self.client_request_id,
            "status": self.status,
            "detected_mime": self.detected_mime,
            "byte_size": self.byte_size,
            "expires_at": self.expires_at,
        }


@dataclass
class MessageAttachmentBinding:
    message_id: str
    attachment_id: str
    ordinal: int
    usage_mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "attachment_id": self.attachment_id,
            "ordinal": self.ordinal,
            "usage_mode": self.usage_mode,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prefixed_id(prefix: str) -> str:
    """Build a stable, readable id like ``att-<uuid>`` (domain.new_id is uuid-only)."""
    return f"{prefix}-{uuid4()}"


class AttachmentService:
    """Tenant/owner-scoped attachment registry and lifecycle controller."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---- read -------------------------------------------------------------

    def get(self, *, tenant_id: str, attachment_id: str) -> AttachmentRecord:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, tenant_id, owner_user_id, conversation_id,"
                    " source_object_id, client_request_id, status, detected_mime,"
                    " byte_size, expires_at FROM attachments WHERE id=:id"
                ),
                {"id": attachment_id},
            ).first()
        if row is None or row[1] != tenant_id:
            raise AttachmentNotFound(attachment_id)
        return AttachmentRecord(
            id=row[0], tenant_id=row[1], owner_user_id=row[2], conversation_id=row[3],
            source_object_id=row[4], client_request_id=row[5], status=row[6],
            detected_mime=row[7], byte_size=row[8], expires_at=row[9],
        )

    def list_for_conversation(
        self, *, tenant_id: str, conversation_id: str
    ) -> list[AttachmentRecord]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, tenant_id, owner_user_id, conversation_id,"
                    " source_object_id, client_request_id, status, detected_mime,"
                    " byte_size, expires_at FROM attachments"
                    " WHERE tenant_id=:t AND conversation_id=:c ORDER BY created_at, id"
                ),
                {"t": tenant_id, "c": conversation_id},
            ).all()
        return [
            AttachmentRecord(
                id=r[0], tenant_id=r[1], owner_user_id=r[2], conversation_id=r[3],
                source_object_id=r[4], client_request_id=r[5], status=r[6],
                detected_mime=r[7], byte_size=r[8], expires_at=r[9],
            )
            for r in rows
        ]

    # ---- register ---------------------------------------------------------

    def register(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        source_object_id: str,
        detected_mime: str,
        byte_size: int,
        client_request_id: str,
        conversation_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> AttachmentRecord:
        """Create an UPLOADING attachment.

        Idempotency: (tenant_id, owner_user_id, client_request_id) is UNIQUE.
        Re-registering returns the existing row instead of raising, so a retried
        client upload does not create duplicate rows.
        """
        with self._engine.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT id FROM attachments"
                    " WHERE tenant_id=:t AND owner_user_id=:o AND client_request_id=:c"
                ),
                {"t": tenant_id, "o": owner_user_id, "c": client_request_id},
            ).first()
            if existing is not None:
                attachment_id = existing[0]
                row = conn.execute(
                    text(
                        "SELECT id, tenant_id, owner_user_id, conversation_id,"
                        " source_object_id, client_request_id, status, detected_mime,"
                        " byte_size, expires_at FROM attachments WHERE id=:id"
                    ),
                    {"id": attachment_id},
                ).first()
                return AttachmentRecord(
                    id=row[0], tenant_id=row[1], owner_user_id=row[2], conversation_id=row[3],
                    source_object_id=row[4], client_request_id=row[5], status=row[6],
                    detected_mime=row[7], byte_size=row[8], expires_at=row[9],
                )

            attachment_id = prefixed_id("att")
            now = utc_now()
            conn.execute(
                text(
                    "INSERT INTO attachments"
                    " (id, tenant_id, owner_user_id, conversation_id, source_object_id,"
                    " client_request_id, status, detected_mime, byte_size, expires_at,"
                    " created_at, updated_at)"
                    " VALUES (:id,:t,:o,:c,:s,:cr,'UPLOADING',:m,:b,:e,:now,:now)"
                ),
                {
                    "id": attachment_id, "t": tenant_id, "o": owner_user_id,
                    "c": conversation_id, "s": source_object_id, "cr": client_request_id,
                    "m": detected_mime, "b": byte_size, "e": expires_at, "now": now,
                },
            )
            return AttachmentRecord(
                id=attachment_id, tenant_id=tenant_id, owner_user_id=owner_user_id,
                conversation_id=conversation_id, source_object_id=source_object_id,
                client_request_id=client_request_id, status=AttachmentStatus.UPLOADING,
                detected_mime=detected_mime, byte_size=byte_size, expires_at=expires_at,
            )

    def register_upload(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        client_request_id: str,
        detected_mime: str,
        byte_size: int,
        sha256: str,
        conversation_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> tuple[AttachmentRecord, str]:
        """Create an attachment and its tenant-scoped object identity."""
        if len(sha256) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in sha256):
            raise AttachmentError("sha256 非法")
        if byte_size <= 0:
            raise AttachmentError("附件不能为空")
        with self._engine.begin() as conn:
            if conversation_id:
                conversation = conn.execute(
                    text(
                        "SELECT id FROM conversations WHERE id=:c AND tenant_id=:t "
                        "AND user_id=:u"
                    ),
                    {"c": conversation_id, "t": tenant_id, "u": owner_user_id},
                ).first()
                if conversation is None:
                    raise AttachmentMessageBindingError(conversation_id)
            existing = conn.execute(
                text(
                    "SELECT id, source_object_id FROM attachments WHERE tenant_id=:t "
                    "AND owner_user_id=:o AND client_request_id=:c"
                ),
                {"t": tenant_id, "o": owner_user_id, "c": client_request_id},
            ).first()
            if existing is not None:
                existing_record = self.get(
                    tenant_id=tenant_id, attachment_id=str(existing[0])
                )
                return existing_record, str(existing[1])

            attachment_id = prefixed_id("att")
            source_object = conn.execute(
                text(
                    "SELECT id FROM source_objects WHERE tenant_id=:t "
                    "AND sha256=:sha AND byte_size=:b"
                ),
                {"t": tenant_id, "sha": sha256.lower(), "b": byte_size},
            ).first()
            source_object_id = str(source_object[0]) if source_object is not None else prefixed_id("src")
            now = utc_now()
            if source_object is None:
                conn.execute(
                    text(
                        "INSERT INTO source_objects"
                        " (id, tenant_id, object_key, sha256, byte_size, detected_mime, "
                        "ref_count, created_at)"
                        " VALUES (:id,:t,:k,:sha,:b,:m,1,:now)"
                    ),
                    {
                        "id": source_object_id,
                        "t": tenant_id,
                        "k": f"attachments/{tenant_id}/{attachment_id}",
                        "sha": sha256.lower(),
                        "b": byte_size,
                        "m": detected_mime,
                        "now": now,
                    },
                )
            else:
                conn.execute(
                    text("UPDATE source_objects SET ref_count=ref_count+1 WHERE id=:id"),
                    {"id": source_object_id},
                )
            conn.execute(
                text(
                    "INSERT INTO attachments"
                    " (id, tenant_id, owner_user_id, conversation_id, source_object_id,"
                    " client_request_id, status, detected_mime, byte_size, expires_at,"
                    " created_at, updated_at)"
                    " VALUES (:id,:t,:o,:c,:s,:cr,'UPLOADING',:m,:b,:e,:now,:now)"
                ),
                {
                    "id": attachment_id,
                    "t": tenant_id,
                    "o": owner_user_id,
                    "c": conversation_id,
                    "s": source_object_id,
                    "cr": client_request_id,
                    "m": detected_mime,
                    "b": byte_size,
                    "e": expires_at,
                    "now": now,
                },
            )
        return self.get(tenant_id=tenant_id, attachment_id=attachment_id), source_object_id

    def upload_object_metadata(
        self, *, tenant_id: str, actor_id: str, attachment_id: str
    ) -> tuple[AttachmentRecord, str, str]:
        """Resolve the exact object key and expected digest for a protected PUT."""
        record = self.get(tenant_id=tenant_id, attachment_id=attachment_id)
        if record.owner_user_id != actor_id:
            raise AttachmentOwnershipError(attachment_id, actor_id)
        if record.status != AttachmentStatus.UPLOADING:
            raise AttachmentStateConflict(attachment_id, record.status, AttachmentStatus.UPLOADING)
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT object_key, sha256 FROM source_objects "
                    "WHERE id=:s AND tenant_id=:t"
                ),
                {"s": record.source_object_id, "t": tenant_id},
            ).first()
        if row is None:
            raise AttachmentNotFound(attachment_id)
        return record, str(row[0]), str(row[1])

    def assert_message_bindable(
        self, *, tenant_id: str, actor_id: str, message_id: str
    ) -> None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT m.id FROM messages m JOIN conversations c "
                    "ON c.id=m.conversation_id WHERE m.id=:m AND m.tenant_id=:t "
                    "AND c.tenant_id=:t AND c.user_id=:u"
                ),
                {"m": message_id, "t": tenant_id, "u": actor_id},
            ).first()
        if row is None:
            raise AttachmentMessageBindingError(message_id)

    # ---- lifecycle transitions -------------------------------------------

    def transition(
        self, *, tenant_id: str, actor_id: str, attachment_id: str, to: str
    ) -> AttachmentRecord:
        current = self.get(tenant_id=tenant_id, attachment_id=attachment_id)
        if current.owner_user_id != actor_id:
            raise AttachmentOwnershipError(attachment_id, actor_id)
        allowed = _ALLOWED_TRANSITIONS.get(current.status, set())
        if to not in allowed:
            raise AttachmentStateConflict(attachment_id, current.status, to)
        with self._engine.begin() as conn:
            conn.execute(
                text("UPDATE attachments SET status=:s, updated_at=:now WHERE id=:id"),
                {"s": to, "now": utc_now(), "id": attachment_id},
            )
        return self.get(tenant_id=tenant_id, attachment_id=attachment_id)

    def mark_processing(
        self, *, tenant_id: str, actor_id: str, attachment_id: str
    ) -> AttachmentRecord:
        return self.transition(
            tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id,
            to=AttachmentStatus.PROCESSING,
        )

    def mark_ready(
        self, *, tenant_id: str, actor_id: str, attachment_id: str
    ) -> AttachmentRecord:
        return self.transition(
            tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id,
            to=AttachmentStatus.READY,
        )

    def mark_failed(
        self, *, tenant_id: str, actor_id: str, attachment_id: str
    ) -> AttachmentRecord:
        return self.transition(
            tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id,
            to=AttachmentStatus.FAILED,
        )

    def trash(
        self, *, tenant_id: str, actor_id: str, attachment_id: str
    ) -> AttachmentRecord:
        return self.transition(
            tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id,
            to=AttachmentStatus.TRASHED,
        )

    def restore(self, *, tenant_id: str, actor_id: str, attachment_id: str) -> AttachmentRecord:
        return self.transition(
            tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id,
            to=AttachmentStatus.READY,
        )

    # ---- binding to a message --------------------------------------------

    def bind(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        message_id: str,
        attachment_ids: list[str],
    ) -> list[MessageAttachmentBinding]:
        """Bind attachments to a message within a single transaction.

        Enforces: every attachment must be READY, tenant-scoped, and owned by
        ``actor_id``. Each attachment's resolved ``usage_mode`` is taken from its
        prepared artifact (written by the processor). If no artifact exists the
        default is INLINE for images with vision and RETRIEVAL otherwise.
        """
        if not attachment_ids:
            return []
        self.assert_message_bindable(
            tenant_id=tenant_id, actor_id=actor_id, message_id=message_id
        )
        bindings: list[MessageAttachmentBinding] = []
        with self._engine.begin() as conn:
            for ordinal, attachment_id in enumerate(attachment_ids):
                rec = conn.execute(
                    text(
                        "SELECT id, tenant_id, owner_user_id, status"
                        " FROM attachments WHERE id=:id"
                    ),
                    {"id": attachment_id},
                ).first()
                if rec is None or rec[1] != tenant_id:
                    raise AttachmentNotFound(attachment_id)
                if rec[2] != actor_id:
                    raise AttachmentOwnershipError(attachment_id, actor_id)
                if rec[3] not in (AttachmentStatus.READY, AttachmentStatus.ATTACHED):
                    raise AttachmentStateConflict(attachment_id, rec[3], AttachmentStatus.READY)

                usage_mode = self._resolve_usage_mode(conn, attachment_id)
                conn.execute(
                    text(
                        "INSERT INTO message_attachments"
                        " (tenant_id, message_id, attachment_id, ordinal, usage_mode)"
                        " VALUES (:t,:m,:a,:o,:u)"
                        " ON CONFLICT (message_id, attachment_id) DO UPDATE SET"
                        " ordinal=EXCLUDED.ordinal, usage_mode=EXCLUDED.usage_mode"
                    ),
                    {
                        "t": tenant_id, "m": message_id, "a": attachment_id,
                        "o": ordinal, "u": usage_mode,
                    },
                )
                # Binding is the ATTACHED transition for the attachment.
                conn.execute(
                    text("UPDATE attachments SET status='ATTACHED', updated_at=:now WHERE id=:id"),
                    {"now": utc_now(), "id": attachment_id},
                )
                bindings.append(
                    MessageAttachmentBinding(
                        message_id=message_id, attachment_id=attachment_id,
                        ordinal=ordinal, usage_mode=usage_mode,
                    )
                )
        return bindings

    def load_context(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        attachment_ids: list[str],
        conversation_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Load only owner-scoped, processed attachment text for an LLM context."""
        if not attachment_ids:
            return []
        contexts: list[dict[str, Any]] = []
        with self._engine.connect() as conn:
            for attachment_id in dict.fromkeys(attachment_ids):
                row = conn.execute(
                    text(
                        "SELECT id, owner_user_id, conversation_id, status, detected_mime "
                        "FROM attachments WHERE id=:a AND tenant_id=:t"
                    ),
                    {"a": attachment_id, "t": tenant_id},
                ).first()
                if row is None:
                    raise AttachmentNotFound(attachment_id)
                if row[1] != owner_user_id:
                    raise AttachmentOwnershipError(attachment_id, owner_user_id)
                if conversation_id and row[2] not in (None, conversation_id):
                    raise AttachmentOwnershipError(attachment_id, owner_user_id)
                if row[3] not in (AttachmentStatus.READY, AttachmentStatus.ATTACHED):
                    raise AttachmentStateConflict(attachment_id, row[3], AttachmentStatus.READY)
                artifacts = conn.execute(
                    text(
                        "SELECT metadata FROM attachment_artifacts "
                        "WHERE attachment_id=:a AND tenant_id=:t ORDER BY id"
                    ),
                    {"a": attachment_id, "t": tenant_id},
                ).all()
                chunks = conn.execute(
                    text(
                        "SELECT id, ordinal, text_content FROM attachment_chunks "
                        "WHERE attachment_id=:a AND tenant_id=:t ORDER BY ordinal, id"
                    ),
                    {"a": attachment_id, "t": tenant_id},
                ).all()
                texts: list[str] = []
                for artifact in artifacts:
                    metadata = artifact[0]
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except json.JSONDecodeError:
                            metadata = {}
                    if isinstance(metadata, dict) and metadata.get("ocr_text"):
                        texts.append(str(metadata["ocr_text"]))
                texts.extend(str(chunk[2]) for chunk in chunks if str(chunk[2]).strip())
                contexts.append(
                    {
                        "attachment_id": str(row[0]),
                        "title": f"附件 {str(row[0])[:12]}",
                        "mime": str(row[4]),
                        "chunks": [
                            {
                                "id": str(chunk[0]),
                                "ordinal": int(chunk[1]),
                                "text": str(chunk[2]),
                            }
                            for chunk in chunks
                        ],
                        "text": "\n\n".join(texts)[:100_000],
                    }
                )
        return contexts

    @staticmethod
    def _resolve_usage_mode(conn: Any, attachment_id: str) -> str:
        # usage_mode is stored inside attachment_artifacts.metadata JSON.
        row = conn.execute(
            text("SELECT metadata FROM attachment_artifacts WHERE attachment_id=:a LIMIT 1"),
            {"a": attachment_id},
        ).first()
        if row is not None:
            try:
                meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                mode = meta.get("usage_mode") if isinstance(meta, dict) else None
                if mode in USAGE_MODES:
                    return mode
            except (ValueError, TypeError):
                pass
        img = conn.execute(
            text("SELECT id FROM image_artifacts WHERE attachment_id=:a LIMIT 1"),
            {"a": attachment_id},
        ).first()
        return "VISION" if img is not None else "RETRIEVAL"

    # ---- artifact / chunk persistence (used by the processor) ------------

    def write_artifact(
        self,
        *,
        tenant_id: str,
        attachment_id: str,
        parser_version: str,
        text_object_id: Optional[str],
        metadata: dict[str, Any],
        token_estimate: Optional[int],
        status: str,
        usage_mode: str,
    ) -> str:
        artifact_id = prefixed_id("art")
        stored = {**metadata, "usage_mode": usage_mode}
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO attachment_artifacts"
                    " (id, tenant_id, attachment_id, parser_version, text_object_id,"
                    " metadata, token_estimate, status)"
                    " VALUES (:id,:t,:a,:pv,:to,:m,:te,:s)"
                ),
                {
                    "id": artifact_id, "t": tenant_id, "a": attachment_id, "pv": parser_version,
                    "to": text_object_id, "m": json.dumps(stored, sort_keys=True),
                    "te": token_estimate, "s": status,
                },
            )
        return artifact_id

    def write_chunks(
        self,
        *,
        tenant_id: str,
        attachment_id: str,
        artifact_id: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        count = 0
        with self._engine.begin() as conn:
            for idx, chunk in enumerate(chunks):
                conn.execute(
                    text(
                        "INSERT INTO attachment_chunks"
                        " (id, tenant_id, attachment_id, artifact_id, ordinal, text_content,"
                        " metadata, embedding_profile_id)"
                        " VALUES (:id,:t,:a,:art,:o,:tc,:m,:ep)"
                    ),
                    {
                        "id": prefixed_id("ach"), "t": tenant_id, "a": attachment_id,
                        "art": artifact_id, "o": idx, "tc": chunk["text"],
                        "m": json.dumps(chunk.get("metadata", {}), sort_keys=True),
                        "ep": chunk.get("embedding_profile_id"),
                    },
                )
                count += 1
        return count

    def write_image_artifact(
        self,
        *,
        tenant_id: str,
        attachment_id: str,
        width: int,
        height: int,
        method: str,
        ocr_text_object_id: Optional[str] = None,
        caption: Optional[str] = None,
        confidence: Optional[float] = None,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> str:
        artifact_id = prefixed_id("img")
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO image_artifacts"
                    " (id, tenant_id, attachment_id, derived_object_id, width, height,"
                    " method, ocr_text_object_id, caption, confidence, provider_id, model_id)"
                    " VALUES (:id,:t,:a,NULL,:w,:h,:m,:ot,:c,:cf,:p,:mi)"
                ),
                {
                    "id": artifact_id, "t": tenant_id, "a": attachment_id, "w": width,
                    "h": height, "m": method, "ot": ocr_text_object_id, "c": caption,
                    "cf": confidence, "p": provider_id, "mi": model_id,
                },
            )
        return artifact_id

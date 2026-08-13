"""Promote a chat attachment into a knowledge-base ingest job (PH5).

Promotion is an explicit cross-resource command.  The attachment remains a
chat resource; the target KB is resolved independently and the PH3 ingestion
writer receives the attachment's existing ``source_object_id``.  No object
bytes are read by this service and a queued job is never reported as success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from ekb_api.domain import utc_now
from ekb_api.services.attachments import (
    AttachmentError,
    AttachmentNotFound,
    AttachmentOwnershipError,
    AttachmentStateConflict,
)
from ekb_api.services.ingestion import create_version_for_item
from ekb_api.services.jobs import get_job_service
from ekb_api.services.storage import PathValidationError, normalize_relative_path


class PromotionError(AttachmentError):
    """Base class for promotion command failures."""

    status_code = 422
    code = "ATTACHMENT_PROMOTION_INVALID"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}


class PromotionTargetNotFound(PromotionError):
    status_code = 404
    code = "TARGET_KB_NOT_FOUND"


class PromotionForbidden(PromotionError):
    status_code = 403
    code = "KB_WRITE_FORBIDDEN"


class PromotionPathConflict(PromotionError):
    status_code = 409
    code = "PATH_CONFLICT"


class PromotionPathReservedByTrash(PromotionError):
    status_code = 409
    code = "PATH_RESERVED_BY_TRASH"


class PromotionUnavailable(PromotionError):
    status_code = 409
    code = "ATTACHMENT_PROMOTION_UNAVAILABLE"


class PromotionSourceInvalid(PromotionError):
    status_code = 422
    code = "ATTACHMENT_SOURCE_INVALID"


@dataclass(frozen=True)
class PromotionResult:
    promotion_id: str
    attachment_id: str
    target_knowledge_base_id: str
    normalized_relative_path: str
    client_request_id: str
    document_id: str
    document_version_id: str
    ingest_job_id: str
    status: str
    request_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "promotion_id": self.promotion_id,
            "attachment_id": self.attachment_id,
            "target_knowledge_base_id": self.target_knowledge_base_id,
            "normalized_relative_path": self.normalized_relative_path,
            "client_request_id": self.client_request_id,
            "document_id": self.document_id,
            "document_version_id": self.document_version_id,
            "ingest_job_id": self.ingest_job_id,
            "status": self.status,
            "request_id": self.request_id,
        }


def _promotion_from_row(row: Any, *, request_id: str) -> PromotionResult:
    return PromotionResult(
        promotion_id=str(row.id),
        attachment_id=str(row.attachment_id),
        target_knowledge_base_id=str(row.target_knowledge_base_id),
        normalized_relative_path=str(row.normalized_relative_path),
        client_request_id=str(row.client_request_id),
        document_id="" if row.document_id is None else str(row.document_id),
        document_version_id="" if row.document_version_id is None else str(row.document_version_id),
        ingest_job_id="" if row.ingest_job_id is None else str(row.ingest_job_id),
        status=str(row.status),
        request_id=request_id,
    )


class AttachmentPromotionService:
    """Tenant/owner/KB-editor scoped promotion writer."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def promote(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        attachment_id: str,
        target_knowledge_base_id: str,
        relative_path: str,
        client_request_id: str,
        request_id: str,
    ) -> PromotionResult:
        """Create or return a promotion, translating concurrent uniqueness races."""
        try:
            return self._promote_once(
                tenant_id=tenant_id,
                actor_id=actor_id,
                attachment_id=attachment_id,
                target_knowledge_base_id=target_knowledge_base_id,
                relative_path=relative_path,
                client_request_id=client_request_id,
                request_id=request_id,
            )
        except IntegrityError:
            # The transaction has already rolled back.  Re-read committed state
            # before deciding whether a concurrent writer reserved the path or
            # completed this exact idempotent command.
            existing = self._read_existing(
                tenant_id=tenant_id,
                attachment_id=attachment_id,
                target_knowledge_base_id=target_knowledge_base_id,
                client_request_id=client_request_id,
            )
            if existing is not None:
                return _promotion_from_row(existing, request_id=request_id)
            normalized_path = normalize_relative_path(relative_path)
            path_state = self._read_path_state(
                tenant_id=tenant_id,
                target_knowledge_base_id=target_knowledge_base_id,
                normalized_path=normalized_path,
                attachment_id=attachment_id,
            )
            if path_state == "TRASH_RESERVED":
                raise PromotionPathReservedByTrash("目标路径仍被回收站文档占用") from None
            if path_state == "CONFLICT":
                raise PromotionPathConflict("目标知识库已存在同路径文档") from None
            raise

    def _promote_once(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        attachment_id: str,
        target_knowledge_base_id: str,
        relative_path: str,
        client_request_id: str,
        request_id: str,
    ) -> PromotionResult:
        if not isinstance(client_request_id, str) or not client_request_id.strip():
            raise PromotionError("client_request_id 不能为空")
        if len(client_request_id) > 128:
            raise PromotionError("client_request_id 超出长度限制")
        try:
            normalized_path = normalize_relative_path(relative_path)
        except PathValidationError as exc:
            raise PromotionError(exc.message, code=exc.code) from exc

        with self.engine.begin() as connection:
            attachment = connection.execute(
                text(
                    "SELECT a.id, a.owner_user_id, a.status, a.source_object_id, "
                    "a.detected_mime, a.byte_size, a.expires_at, a.deleted_at, a.purged_at, "
                    "so.sha256, so.byte_size AS source_byte_size, so.detected_mime AS source_mime "
                    "FROM attachments a LEFT JOIN source_objects so "
                    "ON so.id=a.source_object_id AND so.tenant_id=a.tenant_id "
                    "WHERE a.id=:attachment AND a.tenant_id=:tenant"
                ),
                {"attachment": attachment_id, "tenant": tenant_id},
            ).first()
            if attachment is None:
                raise AttachmentNotFound(attachment_id)
            if str(attachment.owner_user_id) != actor_id:
                raise AttachmentOwnershipError(attachment_id, actor_id)
            if str(attachment.status) not in ("READY", "ATTACHED"):
                raise AttachmentStateConflict(attachment_id, str(attachment.status), "READY")
            if attachment.deleted_at is not None or attachment.purged_at is not None:
                raise PromotionUnavailable("附件已删除或已清理")
            if attachment.expires_at is not None and str(attachment.expires_at) <= utc_now():
                raise PromotionUnavailable("附件已过期")
            if attachment.source_byte_size is None or attachment.sha256 is None:
                raise PromotionSourceInvalid("附件缺少可复用的源对象")
            if int(attachment.source_byte_size) != int(attachment.byte_size):
                raise PromotionSourceInvalid("附件与源对象大小不一致")

            target = connection.execute(
                text(
                    "SELECT kb.id, kb.deleted_at, kb.purged_at, tm.role AS member_role, "
                    "live_role.slug AS live_tenant_role "
                    "FROM knowledge_bases kb "
                    "JOIN tenant_memberships live_membership "
                    "ON live_membership.tenant_id=kb.tenant_id "
                    "AND live_membership.user_id=:actor AND live_membership.status='ACTIVE' "
                    "JOIN tenant_roles live_role "
                    "ON live_role.tenant_id=live_membership.tenant_id "
                    "AND live_role.id=live_membership.role_id "
                    "LEFT JOIN kb_memberships tm ON tm.kb_id=kb.id "
                    "AND tm.tenant_id=kb.tenant_id AND tm.user_id=:actor "
                    "WHERE kb.id=:kb AND kb.tenant_id=:tenant"
                ),
                {"kb": target_knowledge_base_id, "tenant": tenant_id, "actor": actor_id},
            ).first()
            if target is None or target.deleted_at is not None or target.purged_at is not None:
                raise PromotionTargetNotFound("目标知识库不存在或不在当前租户")
            member_role = str(target.member_role or "").upper()
            tenant_role = str(target.live_tenant_role or "").lower()
            if member_role not in {"OWNER", "ADMIN", "EDITOR"} and tenant_role not in {
                "owner",
                "admin",
            }:
                raise PromotionForbidden("当前账号没有目标知识库写入权限")

            # Idempotency is only safe after the current actor has passed the
            # attachment owner/lifecycle and live KB ACL checks above.
            existing = connection.execute(
                text(
                    "SELECT p.id, p.attachment_id, p.target_knowledge_base_id, "
                    "p.normalized_relative_path, p.client_request_id, "
                    "p.document_version_id, p.ingest_job_id, p.status, "
                    "d.id AS document_id "
                    "FROM attachment_promotions p "
                    "LEFT JOIN document_versions dv ON dv.id=p.document_version_id "
                    "LEFT JOIN documents d ON d.id=dv.doc_id "
                    "WHERE p.tenant_id=:tenant AND p.attachment_id=:attachment "
                    "AND p.target_knowledge_base_id=:kb AND p.client_request_id=:request"
                ),
                {
                    "tenant": tenant_id,
                    "attachment": attachment_id,
                    "kb": target_knowledge_base_id,
                    "request": client_request_id,
                },
            ).first()
            if existing is not None:
                return _promotion_from_row(existing, request_id=request_id)

            path_row = connection.execute(
                text(
                    "SELECT id, status, deleted_at, purged_at FROM documents "
                    "WHERE tenant_id=:tenant AND kb_id=:kb "
                    "AND normalized_relative_path=:path AND purged_at IS NULL"
                ),
                {"tenant": tenant_id, "kb": target_knowledge_base_id, "path": normalized_path},
            ).first()
            if path_row is not None:
                if path_row.deleted_at is not None or str(path_row.status).upper() == "DELETED":
                    raise PromotionPathReservedByTrash("目标路径仍被回收站文档占用")
                raise PromotionPathConflict("目标知识库已存在同路径文档")

            promotion_path = connection.execute(
                text(
                    "SELECT id FROM attachment_promotions "
                    "WHERE tenant_id=:tenant AND attachment_id=:attachment "
                    "AND target_knowledge_base_id=:kb AND normalized_relative_path=:path"
                ),
                {
                    "tenant": tenant_id,
                    "attachment": attachment_id,
                    "kb": target_knowledge_base_id,
                    "path": normalized_path,
                },
            ).first()
            if promotion_path is not None:
                raise PromotionPathConflict("该附件已向目标知识库提交同一路径提升")

            promotion_id = f"att-promo-{uuid4()}"
            now = utc_now()
            connection.execute(
                text(
                    "INSERT INTO attachment_promotions "
                    "(id, tenant_id, attachment_id, target_knowledge_base_id, "
                    "normalized_relative_path, client_request_id, status) "
                    "VALUES (:id,:tenant,:attachment,:kb,:path,:request,'QUEUED')"
                ),
                {
                    "id": promotion_id,
                    "tenant": tenant_id,
                    "attachment": attachment_id,
                    "kb": target_knowledge_base_id,
                    "path": normalized_path,
                    "request": client_request_id,
                },
            )

            outcome = create_version_for_item(
                connection,
                tenant_id=tenant_id,
                kb_id=target_knowledge_base_id,
                normalized_relative_path=normalized_path,
                display_path=relative_path,
                sha256=str(attachment.sha256),
                byte_size=int(attachment.source_byte_size),
                detected_mime=str(attachment.source_mime or attachment.detected_mime),
                source_object_id=str(attachment.source_object_id),
                job_service=get_job_service(self.engine),
                owner_user_id=actor_id,
            )
            if not outcome["is_new"]:
                raise PromotionPathConflict("目标知识库已存在同路径文档")

            version_source = connection.execute(
                text(
                    "SELECT source_object_id FROM document_versions "
                    "WHERE id=:version AND tenant_id=:tenant"
                ),
                {"version": outcome["version_id"], "tenant": tenant_id},
            ).scalar_one_or_none()
            if str(version_source) != str(attachment.source_object_id):
                raise PromotionSourceInvalid("摄取版本未复用附件源对象")

            connection.execute(
                text(
                    "UPDATE attachment_promotions SET document_version_id=:version, "
                    "ingest_job_id=:job WHERE id=:id AND tenant_id=:tenant"
                ),
                {
                    "version": outcome["version_id"],
                    "job": outcome["ingest_job_id"],
                    "id": promotion_id,
                    "tenant": tenant_id,
                },
            )
            self._write_audit(
                connection,
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=request_id,
                promotion_id=promotion_id,
                attachment_id=attachment_id,
                target_knowledge_base_id=target_knowledge_base_id,
                normalized_path=normalized_path,
                document_id=outcome["document_id"],
                document_version_id=outcome["version_id"],
                ingest_job_id=outcome["ingest_job_id"],
                now=now,
            )
            row = connection.execute(
                text(
                    "SELECT p.id, p.attachment_id, p.target_knowledge_base_id, "
                    "p.normalized_relative_path, p.client_request_id, "
                    "p.document_version_id, p.ingest_job_id, p.status, d.id AS document_id "
                    "FROM attachment_promotions p "
                    "LEFT JOIN document_versions dv ON dv.id=p.document_version_id "
                    "LEFT JOIN documents d ON d.id=dv.doc_id "
                    "WHERE p.id=:id AND p.tenant_id=:tenant"
                ),
                {"id": promotion_id, "tenant": tenant_id},
            ).first()
            if row is None:  # pragma: no cover - protected by the same transaction
                raise PromotionSourceInvalid("promotion 记录未持久化")
            return _promotion_from_row(row, request_id=request_id)

    def _read_existing(
        self,
        *,
        tenant_id: str,
        attachment_id: str,
        target_knowledge_base_id: str,
        client_request_id: str,
    ) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT p.id, p.attachment_id, p.target_knowledge_base_id, "
                    "p.normalized_relative_path, p.client_request_id, "
                    "p.document_version_id, p.ingest_job_id, p.status, "
                    "d.id AS document_id "
                    "FROM attachment_promotions p "
                    "LEFT JOIN document_versions dv ON dv.id=p.document_version_id "
                    "LEFT JOIN documents d ON d.id=dv.doc_id "
                    "WHERE p.tenant_id=:tenant AND p.attachment_id=:attachment "
                    "AND p.target_knowledge_base_id=:kb AND p.client_request_id=:request"
                ),
                {
                    "tenant": tenant_id,
                    "attachment": attachment_id,
                    "kb": target_knowledge_base_id,
                    "request": client_request_id,
                },
            ).first()

    def _read_path_state(
        self,
        *,
        tenant_id: str,
        target_knowledge_base_id: str,
        normalized_path: str,
        attachment_id: str,
    ) -> str | None:
        with self.engine.connect() as connection:
            document = connection.execute(
                text(
                    "SELECT status, deleted_at FROM documents "
                    "WHERE tenant_id=:tenant AND kb_id=:kb "
                    "AND normalized_relative_path=:path AND purged_at IS NULL"
                ),
                {
                    "tenant": tenant_id,
                    "kb": target_knowledge_base_id,
                    "path": normalized_path,
                },
            ).first()
            if document is not None:
                if document.deleted_at is not None or str(document.status).upper() == "DELETED":
                    return "TRASH_RESERVED"
                return "CONFLICT"
            promotion = connection.execute(
                text(
                    "SELECT id FROM attachment_promotions "
                    "WHERE tenant_id=:tenant AND attachment_id=:attachment "
                    "AND target_knowledge_base_id=:kb AND normalized_relative_path=:path"
                ),
                {
                    "tenant": tenant_id,
                    "attachment": attachment_id,
                    "kb": target_knowledge_base_id,
                    "path": normalized_path,
                },
            ).first()
            return "CONFLICT" if promotion is not None else None

    @staticmethod
    def _write_audit(
        connection: Any,
        *,
        tenant_id: str,
        actor_id: str,
        request_id: str,
        promotion_id: str,
        attachment_id: str,
        target_knowledge_base_id: str,
        normalized_path: str,
        document_id: str,
        document_version_id: str,
        ingest_job_id: str,
        now: str,
    ) -> None:
        metadata = json.dumps(
            {
                "attachment_id": attachment_id,
                "target_knowledge_base_id": target_knowledge_base_id,
                "normalized_relative_path": normalized_path,
                "document_id": document_id,
                "document_version_id": document_version_id,
                "ingest_job_id": ingest_job_id,
                "status": "QUEUED",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expression = (
            "CAST(:metadata AS JSONB)"
            if connection.dialect.name == "postgresql"
            else ":metadata"
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, tenant_id, actor_id, action, target_type, target_id, result, "
                "trace_id, metadata_redacted, created_at) "
                f"VALUES (:id,:tenant,:actor,'attachment.promote','attachment_promotion',"
                f":target,'SUCCESS',:trace,{expression},:created)"
            ),
            {
                "id": str(uuid4()),
                "tenant": tenant_id,
                "actor": actor_id,
                "target": promotion_id,
                "trace": request_id,
                "metadata": metadata,
                "created": now,
            },
        )


__all__ = [
    "AttachmentPromotionService",
    "PromotionError",
    "PromotionForbidden",
    "PromotionPathConflict",
    "PromotionPathReservedByTrash",
    "PromotionResult",
    "PromotionSourceInvalid",
    "PromotionTargetNotFound",
    "PromotionUnavailable",
]

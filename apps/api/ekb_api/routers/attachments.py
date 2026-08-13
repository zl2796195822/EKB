"""Chat attachment HTTP endpoints (PH5).

Prefix: ``/api/v1/attachments``. Attachments are chat-scoped resources (per
``spec/07-file-and-image.md``); every endpoint enforces tenant + owner scope via
``AuthContext``. The upload itself is the client putting bytes into object
storage (or an injected local store for tests); this router registers the
metadata row, runs the synchronous prepare pipeline, and binds attachments to a
message.

Design notes:
* ``register`` is idempotent on (tenant, owner, client_request_id) so a retried
  client upload does not create duplicate rows.
* ``process`` runs the fault-tolerant pipeline; failures transition the
  attachment to FAILED with a diagnostic, never raising to the caller.
* ``bind`` requires every attachment be READY, tenant-scoped and owner-matched,
  and records the resolved ``usage_mode``.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine, text

from ekb_api.chunking import split_sections as _split_sections
from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_WRITE, CAP_QA_ASK, assert_capability
from ekb_api.core.config import get_runtime_chat_providers
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.embedding import embed_batch
from ekb_api.parsing import parse as _parse
from ekb_api.services.attachment_processor import ProcessingConfig, process_attachment
from ekb_api.services.attachment_promotions import (
    AttachmentPromotionService,
    PromotionError,
)
from ekb_api.services.attachments import (
    AttachmentError,
    AttachmentMessageBindingError,
    AttachmentNotFound,
    AttachmentOwnershipError,
    AttachmentService,
    AttachmentStateConflict,
    DuplicateClientRequest,
)
from ekb_api.services.vision import RuntimeVisionCapability

router = APIRouter(prefix="/attachments", tags=["attachments"])

# Optional storage override for tests (injected LocalBytesStore). Production uses
# object storage via _storage_reader().
_STORAGE_OVERRIDE: Any = None


def set_storage_reader(reader: Any) -> None:
    global _STORAGE_OVERRIDE
    _STORAGE_OVERRIDE = reader


def _engine() -> Engine:
    from ekb_api.core.db import get_engine

    return get_engine()


def _service() -> AttachmentService:
    return AttachmentService(_engine())


def _storage_reader(tenant_id: str) -> Any:
    """Return a StorageReader. Production uses object storage; tests inject one.

    Missing object storage is a real unavailable condition. Tests override via
    ``set_storage_reader`` and never use this production boundary.
    """
    from ekb_api.services.storage import build_storage_client

    client = build_storage_client()

    class _ClientReader:
        def get_bytes(self, *, object_id: str) -> bytes:
            with _engine().connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT object_key, sha256, byte_size FROM source_objects "
                        "WHERE id=:id AND tenant_id=:tenant"
                    ),
                    {"id": object_id, "tenant": tenant_id},
                ).first()
            if row is None:
                raise AttachmentNotFound(object_id)
            data = client.get_object_bytes(tenant_id=tenant_id, object_key=str(row[0]))
            actual_sha = hashlib.sha256(data).hexdigest()
            if len(data) != int(row[2]) or actual_sha.lower() != str(row[1]).lower():
                raise AttachmentError("对象完整性校验失败")
            return data

    return _ClientReader()


def _request_id(auth: AuthContext) -> str:
    return auth.trace_id


def _translate(exc: Exception) -> ApiError:
    if isinstance(exc, AttachmentNotFound):
        return ApiError(404, "ATTACHMENT_NOT_FOUND", str(exc), {"attachment_id": exc.attachment_id})
    if isinstance(exc, AttachmentOwnershipError):
        return ApiError(403, "ATTACHMENT_FORBIDDEN", str(exc), {"attachment_id": exc.attachment_id})
    if isinstance(exc, AttachmentStateConflict):
        return ApiError(
            409, "ATTACHMENT_STATE_CONFLICT", str(exc),
            {"attachment_id": exc.attachment_id, "current": exc.current, "required": exc.required},
        )
    if isinstance(exc, AttachmentMessageBindingError):
        return ApiError(404, "MESSAGE_NOT_FOUND", "消息不存在或不在当前授权范围")
    if isinstance(exc, DuplicateClientRequest):
        return ApiError(
            409, "ATTACHMENT_DUPLICATE", str(exc), {"client_request_id": exc.client_request_id}
        )
    if isinstance(exc, AttachmentError):
        return ApiError(422, "ATTACHMENT_INVALID", str(exc))
    return ApiError(500, "INTERNAL_ERROR", "附件处理失败", {"detail": str(exc)})


# ---- request / response models (kept inline per PH3/PH4 convention) --------


class RegisterRequest:
    conversation_id: Optional[str]
    client_request_id: str
    detected_mime: str
    byte_size: int
    source_object_id: str


class AttachmentSessionRequest(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=128)
    detected_mime: str = Field(min_length=1, max_length=255)
    byte_size: int = Field(gt=0, le=100 * 1024 * 1024)
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    conversation_id: Optional[str] = Field(default=None, max_length=128)


class AttachmentPromotionRequest(BaseModel):
    target_knowledge_base_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=4096)
    client_request_id: str = Field(min_length=1, max_length=128)


def _register_payload(body: dict[str, Any]) -> dict[str, Any]:
    required = ("client_request_id", "detected_mime", "byte_size", "source_object_id")
    missing = [k for k in required if k not in body]
    if missing:
        raise ApiError(422, "VALIDATION_ERROR", "缺少字段", {"missing": missing})
    if not isinstance(body["byte_size"], int) or body["byte_size"] < 0:
        raise ApiError(
            422, "VALIDATION_ERROR", "byte_size 非法", {"byte_size": body.get("byte_size")}
        )
    return body


@router.post("")
def register_attachment(
    payload: Annotated[dict, Body()],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_capability(auth, CAP_QA_ASK)
    body = _register_payload(payload)
    try:
        rec = _service().register(
            tenant_id=auth.tenant_id, owner_user_id=auth.actor_id,
            source_object_id=body["source_object_id"], detected_mime=body["detected_mime"],
            byte_size=body["byte_size"], client_request_id=body["client_request_id"],
            conversation_id=body.get("conversation_id"),
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return rec.as_dict()


@router.post("/sessions")
def create_attachment_session(
    payload: AttachmentSessionRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    """Create a real tenant-scoped attachment upload session."""
    assert_capability(auth, CAP_QA_ASK)
    try:
        rec, _source_object_id = _service().register_upload(
            tenant_id=auth.tenant_id,
            owner_user_id=auth.actor_id,
            client_request_id=payload.client_request_id,
            detected_mime=payload.detected_mime,
            byte_size=payload.byte_size,
            sha256=payload.sha256,
            conversation_id=payload.conversation_id,
        )
        _record, object_key, _expected_sha = _service().upload_object_metadata(
            tenant_id=auth.tenant_id, actor_id=auth.actor_id, attachment_id=rec.id
        )
    except Exception as exc:
        raise _translate(exc) from exc
    from ekb_api.services.storage import LocalFilesystemStorageClient, build_storage_client

    try:
        storage = build_storage_client()
        if isinstance(storage, LocalFilesystemStorageClient):
            upload_url = f"/api/v1/attachments/objects?attachment_id={rec.id}"
        else:
            presigned = storage.put_presigned(
                tenant_id=auth.tenant_id,
                object_key=object_key,
                byte_size=rec.byte_size,
                method="SINGLE",
                part_size=None,
                expires_in_seconds=3600,
            )
            upload_url = presigned.upload_urls[0]
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "attachment": rec.as_dict(),
        "upload_url": upload_url,
        "object_key": object_key,
    }


@router.put("/objects")
async def put_attachment_object(
    request: Request,
    attachment_id: Annotated[str, Query(min_length=1, max_length=128)],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    """Protected local object upload; bytes never go to an arbitrary path."""
    assert_capability(auth, CAP_QA_ASK)
    try:
        record, object_key, expected_sha = _service().upload_object_metadata(
            tenant_id=auth.tenant_id, actor_id=auth.actor_id, attachment_id=attachment_id
        )
        from ekb_api.services.storage import LocalFilesystemStorageClient, build_storage_client

        client = build_storage_client()
        if not isinstance(client, LocalFilesystemStorageClient):
            raise ApiError(503, "OBJECT_STORAGE_UNAVAILABLE", "当前开发附件上传端点不可用")
        data = await request.body()
        actual_sha = hashlib.sha256(data).hexdigest()
        if len(data) != record.byte_size:
            raise ApiError(
                422,
                "OBJECT_SIZE_MISMATCH",
                "上传对象大小与预检不一致",
                {"expected": record.byte_size, "actual": len(data)},
            )
        if actual_sha.lower() != expected_sha.lower():
            raise ApiError(422, "OBJECT_CHECKSUM_MISMATCH", "上传对象校验和不一致")
        head = client.write_object(tenant_id=auth.tenant_id, object_key=object_key, data=data)
    except ApiError:
        raise
    except Exception as exc:
        raise _translate(exc) from exc
    return {
        "attachment_id": attachment_id,
        "object_key": object_key,
        "byte_size": head.byte_size,
        "sha256": actual_sha,
    }


@router.get("/{attachment_id}")
def get_attachment(
    attachment_id: Annotated[str, Path()],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_capability(auth, CAP_QA_ASK)
    try:
        rec = _service().get(tenant_id=auth.tenant_id, attachment_id=attachment_id)
    except Exception as exc:
        raise _translate(exc) from exc
    return rec.as_dict()


@router.post(
    "/{attachment_id}/promotions",
    status_code=status.HTTP_202_ACCEPTED,
)
def promote_attachment(
    attachment_id: Annotated[str, Path()],
    payload: AttachmentPromotionRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    """Queue a real PH3 ingest for the attachment's existing source object."""
    assert_capability(auth, CAP_KB_WRITE)
    try:
        result = AttachmentPromotionService(_engine()).promote(
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            attachment_id=attachment_id,
            target_knowledge_base_id=payload.target_knowledge_base_id,
            relative_path=payload.relative_path,
            client_request_id=payload.client_request_id,
            request_id=_request_id(auth),
        )
    except PromotionError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message, exc.details) from exc
    except Exception as exc:
        raise _translate(exc) from exc
    return result.as_dict()


@router.get("")
def list_attachments(
    conversation_id: Annotated[Optional[str], Query()] = None,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)] = None,
) -> dict[str, Any]:
    assert_capability(auth, CAP_QA_ASK)
    svc = _service()
    if conversation_id:
        items = svc.list_for_conversation(tenant_id=auth.tenant_id, conversation_id=conversation_id)
    else:
        items = []
    return {"items": [r.as_dict() for r in items], "total": len(items)}


@router.post("/{attachment_id}/process")
def process_attachment_endpoint(
    attachment_id: Annotated[str, Path()],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_capability(auth, CAP_QA_ASK)
    try:
        config = ProcessingConfig(
            parser=_parse, chunker=_split_sections,
            embed=lambda texts: embed_batch(texts, tenant_id=auth.tenant_id, user_id=auth.actor_id),
            vision=RuntimeVisionCapability(
                get_runtime_chat_providers(tenant_id=auth.tenant_id, user_id=auth.actor_id)
            ),
            ocr=None,
        )
        result = process_attachment(
            _service(), tenant_id=auth.tenant_id, actor_id=auth.actor_id,
            attachment_id=attachment_id,
            storage=_STORAGE_OVERRIDE or _storage_reader(auth.tenant_id), config=config,
        )
    except AttachmentError as exc:
        raise _translate(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return result


@router.post("/bind")
def bind_attachments(
    payload: Annotated[dict, Body()],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_capability(auth, CAP_KB_WRITE)
    message_id = payload.get("message_id")
    attachment_ids = payload.get("attachment_ids") or []
    if not message_id:
        raise ApiError(422, "VALIDATION_ERROR", "message_id 必填")
    if not isinstance(attachment_ids, list) or not attachment_ids:
        raise ApiError(422, "VALIDATION_ERROR", "attachment_ids 必为非空数组")
    try:
        bindings = _service().bind(
            tenant_id=auth.tenant_id, actor_id=auth.actor_id,
            message_id=message_id, attachment_ids=list(attachment_ids),
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return {"bindings": [b.as_dict() for b in bindings], "total": len(bindings)}


@router.post("/{attachment_id}/restore")
def restore_attachment(
    attachment_id: Annotated[str, Path()],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_capability(auth, CAP_KB_WRITE)
    try:
        rec = _service().restore(
            tenant_id=auth.tenant_id, actor_id=auth.actor_id, attachment_id=attachment_id
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return rec.as_dict()


@router.post("/{attachment_id}/trash")
def trash_attachment(
    attachment_id: Annotated[str, Path()],
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, Any]:
    assert_capability(auth, CAP_KB_WRITE)
    try:
        rec = _service().trash(
            tenant_id=auth.tenant_id, actor_id=auth.actor_id,
            attachment_id=attachment_id,
        )
    except Exception as exc:
        raise _translate(exc) from exc
    return rec.as_dict()

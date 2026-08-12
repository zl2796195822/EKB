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

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy import Engine

from ekb_api.chunking import split_sections as _split_sections
from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_WRITE, CAP_QA_ASK, assert_capability
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.embedding import embed_batch
from ekb_api.parsing import parse as _parse
from ekb_api.services.attachment_processor import (
    LocalBytesStore,
    ProcessingConfig,
    process_attachment,
)
from ekb_api.services.attachments import (
    AttachmentError,
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


def _storage_reader() -> Any:
    """Return a StorageReader. Production uses object storage; tests inject one.

    Falls back to a no-op local store when object storage is unconfigured so the
    code path is always importable. Tests override via _service injection at the
    processor layer.
    """
    try:
        from ekb_api.core.object_storage import build_storage_client

        client = build_storage_client()

        class _ClientReader:
            def get_bytes(self, *, object_id: str) -> bytes:
                return client.get_object_bytes(tenant_id="*", object_key=object_id)

        return _ClientReader()
    except Exception:  # noqa: BLE001
        return LocalBytesStore()


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
            parser=_parse, chunker=_split_sections, embed=embed_batch,
            vision=RuntimeVisionCapability(), ocr=None,
        )
        result = process_attachment(
            _service(), tenant_id=auth.tenant_id, actor_id=auth.actor_id,
            attachment_id=attachment_id,
            storage=_STORAGE_OVERRIDE or _storage_reader(), config=config,
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

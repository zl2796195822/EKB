"""PH3 upload + ingestion endpoints (spec 05-upload.md / 06-ingestion.md).

Two concerns live here:

* **Upload orchestration** — preflight, batch creation, session open/complete/abort
  and the batch projection the web client polls. These need a configured object
  store; when none is configured the router fails closed with a typed 503
  (``OBJECT_STORAGE_UNAVAILABLE``) instead of accepting bytes it cannot durably
  store.
* **Ingestion control** — the read-only job projection plus retry/cancel.  These
  work without object storage because they only touch the ingest ledger, so
  operators keep visibility even while the store is down.

Tenant scoping comes from the live auth context and is bound inside the service
layer on every statement. There is no caller-supplied tenant parameter.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_READ, CAP_KB_WRITE, assert_capability
from ekb_api.core.db import get_engine
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, utc_now
from ekb_api.services.ingestion import (
    IngestNotFound,
    IngestService,
    IngestStateConflict,
    IngestTransitionError,
)
from ekb_api.services.storage import (
    LocalFilesystemStorageClient,
    ObjectStorageUnavailable,
    PathValidationError,
    UploadService,
)

router = APIRouter(prefix="/kb", tags=["upload"])

MAX_ITEMS_PER_REQUEST = 1000


class UploadItemRequest(BaseModel):
    client_item_id: str = Field(min_length=1, max_length=128)
    relative_path: str = Field(min_length=1, max_length=4096)
    byte_size: int = Field(ge=0)
    browser_mime: Optional[str] = Field(default=None, max_length=255)
    sha256: Optional[str] = Field(default=None, min_length=64, max_length=64)


class CreateBatchRequest(BaseModel):
    mode: str = Field(pattern="^(FILE|MULTI_FILE|DIRECTORY)$")
    client_request_id: str = Field(min_length=1, max_length=128)
    items: list[UploadItemRequest] = Field(min_length=1, max_length=MAX_ITEMS_PER_REQUEST)


class OpenSessionRequest(BaseModel):
    method: str = Field(default="SINGLE", pattern="^(SINGLE|MULTIPART|API_STREAM)$")
    part_size: Optional[int] = Field(default=None, ge=5 * 1024 * 1024)


class CompleteUploadRequest(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    detected_mime: Optional[str] = Field(default=None, max_length=255)


def _local_object_key_parts(object_key: str, tenant_id: str) -> tuple[str, str, str]:
    parts = object_key.split("/")
    if len(parts) != 4 or parts[0] != "uploads" or parts[1] != tenant_id:
        raise ApiError(404, "OBJECT_NOT_FOUND", "对象不存在")
    return parts[1], parts[2], parts[3]


@router.put("/uploads/objects")
async def put_local_object(
    request: Request,
    object_key: str = Query(min_length=1, max_length=1024),
    auth: Annotated[AuthContext, Depends(get_live_auth_context)] = None,
) -> dict[str, Any]:
    """Receive a local-development upload URL without exposing filesystem paths.

    The tenant and item are resolved from the authenticated session. The
    caller cannot choose another tenant, KB, or arbitrary local path.
    """

    assert_capability(auth, CAP_KB_WRITE)
    tenant_id, kb_id, item_id = _local_object_key_parts(object_key, auth.tenant_id)
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT ui.byte_size FROM upload_items ui "
                "JOIN upload_batches ub ON ub.id=ui.batch_id "
                "JOIN upload_sessions us ON us.upload_item_id=ui.id "
                "WHERE ui.id=:item AND ub.tenant_id=:tenant AND "
                "ub.knowledge_base_id=:kb AND us.state='ACTIVE' AND us.expires_at > :now"
            ),
            {"item": item_id, "tenant": tenant_id, "kb": kb_id, "now": utc_now()},
        ).first()
    if row is None:
        raise ApiError(404, "OBJECT_NOT_FOUND", "上传会话不存在或已失效")
    data = await request.body()
    if len(data) != int(row.byte_size):
        raise ApiError(
            422,
            "OBJECT_SIZE_MISMATCH",
            "上传对象大小与预检不一致",
            {"expected": int(row.byte_size), "actual": len(data)},
        )
    try:
        client = _uploads().storage
    except ApiError:
        raise
    if not isinstance(client, LocalFilesystemStorageClient):
        raise ApiError(503, "OBJECT_STORAGE_UNAVAILABLE", "当前开发上传端点不可用")
    head = client.write_object(tenant_id=tenant_id, object_key=object_key, data=data)
    return {"object_key": object_key, "byte_size": head.byte_size, "sha256": head.sha256}


def _uploads() -> UploadService:
    """Build the upload service, translating a missing store into a typed 503."""

    try:
        return UploadService()
    except ObjectStorageUnavailable as exc:
        raise ApiError(
            503,
            "OBJECT_STORAGE_UNAVAILABLE",
            "对象存储未配置，无法接收上传",
            {"reason": str(exc)},
        ) from exc


def _ingest() -> IngestService:
    return IngestService(get_engine())


def _translate_ingest_error(exc: Exception) -> ApiError:
    if isinstance(exc, IngestNotFound):
        return ApiError(404, "INGEST_JOB_NOT_FOUND", "摄取任务不存在或不在当前租户")
    if isinstance(exc, IngestStateConflict):
        return ApiError(
            409, "INGEST_STATE_CONFLICT", "摄取任务状态已变更，请重试", {"reason": str(exc)}
        )
    return ApiError(422, "INGEST_TRANSITION_REJECTED", "非法的摄取状态流转", {"reason": str(exc)})


# ---- Upload orchestration -------------------------------------------------


@router.post("/{kb_id}/uploads/batches")
def create_batch(
    kb_id: str,
    payload: CreateBatchRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Preflight and register an upload batch (single file, multi-file or directory)."""

    assert_capability(auth, CAP_KB_WRITE)
    service = _uploads()
    try:
        result = service.create_batch(
            tenant_id=auth.tenant_id,
            created_by=auth.actor_id,
            kb_id=kb_id,
            mode=payload.mode,
            client_request_id=payload.client_request_id,
            items=[item.model_dump() for item in payload.items],
        )
    except PathValidationError as exc:
        raise ApiError(422, exc.code, exc.message) from exc
    return _as_dict(result)


@router.post("/uploads/items/{item_id}/session")
def open_session(
    item_id: str,
    payload: OpenSessionRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Open (or resume) the upload session for one accepted item."""

    assert_capability(auth, CAP_KB_WRITE)
    service = _uploads()
    return _as_dict(
        service.open_session(
            tenant_id=auth.tenant_id,
            item_id=item_id,
            method=payload.method,
            part_size=payload.part_size,
        )
    )


@router.post("/uploads/items/{item_id}/complete")
def complete_upload(
    item_id: str,
    payload: CompleteUploadRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Verify the stored object, create the version and queue ingestion."""

    assert_capability(auth, CAP_KB_WRITE)
    service = _uploads()
    result = service.complete_upload(
        tenant_id=auth.tenant_id,
        item_id=item_id,
        sha256=payload.sha256,
        detected_mime=payload.detected_mime,
    )
    return _as_dict(result)


@router.post("/uploads/items/{item_id}/abort")
def abort_upload(
    item_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    service = _uploads()
    service.abort_session(tenant_id=auth.tenant_id, item_id=item_id)
    return {"success": True, "item_id": item_id, "status": "ABORTED"}


@router.get("/uploads/batches/{batch_id}")
def read_batch(
    batch_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Batch projection the client polls for per-item progress."""

    assert_capability(auth, CAP_KB_READ)
    service = _uploads()
    return _as_dict(service.get_batch(tenant_id=auth.tenant_id, batch_id=batch_id))


@router.get("/uploads/batches")
def list_batches(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    limit: int = Query(default=30, ge=1, le=100),
) -> dict:
    """List recent upload batches for the authenticated tenant."""

    assert_capability(auth, CAP_KB_READ)
    service = _uploads()
    projections = service.list_batches(tenant_id=auth.tenant_id, limit=limit)
    return {"items": [_as_dict(projection) for projection in projections], "limit": limit}


# ---- Ingestion control ----------------------------------------------------


@router.get("/ingest-jobs/{job_id}")
def read_ingest_job(
    job_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Read-only ingest projection: status, attempt and per-stage ledger."""

    assert_capability(auth, CAP_KB_READ)
    try:
        projection = _ingest().projection(tenant_id=auth.tenant_id, ingest_job_id=job_id)
    except (IngestNotFound, IngestStateConflict, IngestTransitionError) as exc:
        raise _translate_ingest_error(exc) from exc
    return _projection_payload(projection)


@router.post("/ingest-jobs/{job_id}/retry")
def retry_ingest_job(
    job_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    lease_owner: str = Query(default="api-retry", max_length=128),
) -> dict:
    """Start a fresh attempt for a queued/failed job (budget-bounded)."""

    assert_capability(auth, CAP_KB_WRITE)
    service = _ingest()
    try:
        attempt = service.retry(
            tenant_id=auth.tenant_id, ingest_job_id=job_id, lease_owner=lease_owner
        )
    except (IngestNotFound, IngestStateConflict, IngestTransitionError) as exc:
        raise _translate_ingest_error(exc) from exc
    return {"success": True, "attempt_id": attempt.id, "attempt_no": attempt.attempt_no}


@router.post("/ingest-jobs/{job_id}/cancel")
def cancel_ingest_job(
    job_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    service = _ingest()
    try:
        projection = service.cancel(tenant_id=auth.tenant_id, ingest_job_id=job_id)
    except (IngestNotFound, IngestStateConflict, IngestTransitionError) as exc:
        raise _translate_ingest_error(exc) from exc
    return _projection_payload(projection)


def _projection_payload(projection: Any) -> dict:
    attempt = projection.active_attempt
    return {
        "ingest_job_id": projection.ingest_job_id,
        "document_id": projection.document_id,
        "document_version_id": projection.document_version_id,
        "status": projection.status,
        "attempts": projection.attempts,
        "max_attempts": projection.max_attempts,
        "state_version": projection.state_version,
        "active_attempt": None if attempt is None else _as_dict(attempt),
        "stages": projection.stages,
    }


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(vars(value))


__all__ = ["router"]

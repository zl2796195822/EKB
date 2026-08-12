"""Tenant-scoped background job center endpoints (PH1 · T03).

Exposes operational visibility into the v4 ``background_jobs`` queue plus
tenant-initiated enqueue and cancel.  Every query is strictly tenant-scoped
via the live auth context; the service layer always binds ``tenant_id``, so
cross-tenant job access is impossible by construction.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_AUDIT_READ, CAP_KB_WRITE, assert_capability
from ekb_api.domain import AuthContext
from ekb_api.services.jobs import (
    JobNotFound,
    JobService,
    JobStateConflict,
    JobView,
    get_job_service,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


class EnqueueJobRequest(BaseModel):
    job_type: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=25)
    priority: int = Field(default=0, ge=-100, le=100)


class JobViewResponse(BaseModel):
    id: str
    tenant_id: str
    job_type: str
    idempotency_key: str
    state: str
    priority: int
    max_attempts: int
    payload: dict[str, Any]
    available_at: str
    lease_owner: Optional[str]
    lease_expires_at: Optional[str]
    heartbeat_at: Optional[str]
    error_code: Optional[str]
    sanitized_error: Optional[dict[str, Any]]
    created_at: str
    updated_at: str

    @classmethod
    def from_view(cls, view: JobView) -> JobViewResponse:
        return cls(
            id=view.id,
            tenant_id=view.tenant_id,
            job_type=view.job_type,
            idempotency_key=view.idempotency_key,
            state=view.state,
            priority=view.priority,
            max_attempts=view.max_attempts,
            payload=view.payload,
            available_at=view.available_at,
            lease_owner=view.lease_owner,
            lease_expires_at=view.lease_expires_at,
            heartbeat_at=view.heartbeat_at,
            error_code=view.error_code,
            sanitized_error=view.sanitized_error,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


def _service() -> JobService:
    return get_job_service()


def _job_or_404(service: JobService, tenant_id: str, job_id: str) -> JobView:
    try:
        return service.get(tenant_id=tenant_id, job_id=job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "JOB_NOT_FOUND", "message": f"job {job_id} not found"},
        ) from exc


@router.get("")
def list_jobs(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    states: Annotated[list[str], Query(default_factory=list)],
    job_type: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """List background jobs for the caller's tenant (Jobs Center)."""
    assert_capability(auth, CAP_AUDIT_READ)
    service = _service()
    rows = service.list_for_tenant(
        tenant_id=auth.tenant_id, states=states, job_type=job_type, limit=limit
    )
    items = [JobViewResponse.from_view(row).model_dump() for row in rows]
    return {"items": items, "count": len(items)}


@router.get("/cleanup")
def cleanup_projection(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    recent_limit: int = Query(default=10, ge=1, le=100),
) -> dict:
    """Read-only Jobs Center projection of cleanup/purge jobs (PH2 · T05).

    Aggregates the caller's ``retention_purge`` jobs by state and returns the
    most recent ones.  Strictly tenant-scoped; cross-tenant jobs are invisible
    by construction (the service binds ``tenant_id`` on every query).
    """
    assert_capability(auth, CAP_AUDIT_READ)
    service = _service()
    proj = service.cleanup_projection(
        tenant_id=auth.tenant_id, recent_limit=recent_limit
    )
    return {
        "job_type": proj.job_type,
        "total": proj.total,
        "by_state": proj.by_state,
        "recent": [JobViewResponse.from_view(v).model_dump() for v in proj.recent],
    }


@router.get("/{job_id}")
def read_job(
    job_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_AUDIT_READ)
    service = _service()
    return JobViewResponse.from_view(
        _job_or_404(service, auth.tenant_id, job_id)
    ).model_dump()


@router.post("")
def enqueue_job(
    payload: EnqueueJobRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Enqueue a tenant-scoped background job (e.g. an ingestion request)."""
    assert_capability(auth, CAP_KB_WRITE)
    service = _service()
    try:
        result = service.enqueue(
            tenant_id=auth.tenant_id,
            job_type=payload.job_type,
            idempotency_key=payload.idempotency_key,
            payload=dict(payload.payload),
            max_attempts=payload.max_attempts,
            priority=payload.priority,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "JOB_ENQUEUE_REJECTED", "message": str(exc)},
        ) from exc
    return {
        "success": True,
        "job_id": result.job.id,
        "state": result.job.state,
        "created": result.created,
    }


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    """Tenant-admin cancel of a non-terminal job."""
    assert_capability(auth, CAP_KB_WRITE)
    service = _service()
    _job_or_404(service, auth.tenant_id, job_id)
    try:
        view = service.cancel(
            tenant_id=auth.tenant_id, job_id=job_id, actor_id=auth.actor_id
        )
    except JobStateConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "JOB_CANCEL_CONFLICT", "message": str(exc)},
        ) from exc
    return JobViewResponse.from_view(view).model_dump()


__all__ = ["router"]

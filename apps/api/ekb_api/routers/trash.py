"""Recycle bin endpoints backed by the ``trash_items`` projection."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from starlette import status

from ekb_api.core.audit import RESULT_SUCCESS, redact_metadata
from ekb_api.core.auth import get_live_auth_context, get_store
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.services.v3_trash import (
    RESOURCE_TYPES,
    list_trash,
    purge_all,
    purge_item,
    restore_item,
)
from ekb_api.store import SqlStore

router = APIRouter(prefix="/trash", tags=["trash"])

#: Emptying the bin is destructive, so it is gated on the write capability
#: rather than plain read access.
_PURGE_CAPABILITY = "kb:write"


def _require_purge(auth: AuthContext) -> None:
    if _PURGE_CAPABILITY not in auth.capabilities:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "PERMISSION_DENIED",
            "当前账号无权永久删除回收站内容",
        )


def _serialize(item) -> dict:
    return {
        "id": item.id,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "title": item.title,
        "parent_id": item.parent_id,
        "parent_title": item.parent_title,
        "deleted_by": item.deleted_by,
        "deleted_at": item.deleted_at,
        "expires_at": item.expires_at,
        "restorable": item.restorable,
        "blocked_reason": item.blocked_reason,
    }


@router.get("")
def get_trash(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    resource_type: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    page = list_trash(
        auth.tenant_id,
        resource_type=resource_type,
        query=q,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_serialize(item) for item in page.items],
        "total": page.total,
        "counts": page.counts,
        "resource_types": list(RESOURCE_TYPES),
        "limit": limit,
        "offset": offset,
    }


@router.post("/{item_id}/restore")
def post_restore(
    item_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict:
    _require_purge(auth)
    item = restore_item(auth.tenant_id, item_id)
    store.write_audit_log(
        action="trash.restore",
        target_type=item.resource_type.lower(),
        target_id=item.resource_id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata({"trash_item_id": item_id}),
    )
    return {"status": "ok", "item": _serialize(item)}


@router.delete("/{item_id}")
def delete_item(
    item_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict:
    _require_purge(auth)
    result = purge_item(auth.tenant_id, item_id)
    store.write_audit_log(
        action="trash.purge",
        target_type=str(result["resource_type"]).lower(),
        target_id=str(result["resource_id"]),
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata({"trash_item_id": item_id}),
    )
    return {"status": "ok", **result}


@router.delete("")
def delete_all(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    resource_type: Optional[str] = Query(default=None),
) -> dict:
    _require_purge(auth)
    result = purge_all(auth.tenant_id, resource_type=resource_type)
    store.write_audit_log(
        action="trash.clear",
        target_type="trash",
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(
            {"resource_type": resource_type or "ALL", "purged": result["purged"]}
        ),
    )
    return {"status": "ok", **result}

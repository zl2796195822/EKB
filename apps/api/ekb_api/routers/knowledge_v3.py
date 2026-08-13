"""Folder CRUD endpoints for the Knowledge page."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette import status

from ekb_api.core.audit import RESULT_SUCCESS, redact_metadata
from ekb_api.core.auth import get_live_auth_context, get_store
from ekb_api.core.authorization import CAP_KB_WRITE, assert_capability
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.services.v3_folders import (
    _UNSET,
    create_folder,
    delete_folder,
    list_folders,
    update_folder,
)
from ekb_api.store import SqlStore

router = APIRouter(tags=["knowledge-v3"])


class FolderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    parent_id: Optional[str] = Field(default=None, max_length=64)


class FolderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    parent_id: Optional[str] = Field(default=None, max_length=64)


def _serialize(folder) -> dict:
    return {
        "id": folder.id,
        "tenant_id": folder.tenant_id,
        "kb_id": folder.kb_id,
        "parent_id": folder.parent_id,
        "name": folder.name,
        "deleted_at": folder.deleted_at,
        "created_by": folder.created_by,
        "created_at": folder.created_at,
        "updated_at": folder.updated_at,
        "child_count": folder.child_count,
        "document_count": folder.document_count,
    }


@router.get("/knowledge-bases/{kb_id}/folders")
def get_folders(
    kb_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    parent_id: Optional[str] = Query(default=None),
    include_deleted: bool = Query(default=False),
) -> dict:
    if "kb:read" not in auth.capabilities:
        raise ApiError(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "当前账号无权读取文件夹")
    items = list_folders(
        auth.tenant_id, kb_id, parent_id=parent_id, include_deleted=include_deleted
    )
    return {"items": [_serialize(item) for item in items], "parent_id": parent_id, "kb_id": kb_id}


@router.post("/knowledge-bases/{kb_id}/folders", status_code=status.HTTP_201_CREATED)
def post_folder(
    kb_id: str,
    payload: FolderCreate,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    folder = create_folder(auth.tenant_id, auth.actor_id, kb_id, payload.name, payload.parent_id)
    store.write_audit_log(
        action="folder.create",
        target_type="folder",
        target_id=folder.id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata({"kb_id": kb_id, "parent_id": payload.parent_id}),
    )
    return _serialize(folder)


@router.patch("/folders/{folder_id}")
def patch_folder(
    folder_id: str,
    payload: FolderUpdate,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    fields_set = getattr(payload, "model_fields_set", payload.__fields_set__)
    requested_parent = payload.parent_id if "parent_id" in fields_set else _UNSET
    return _serialize(
        update_folder(
            auth.tenant_id,
            folder_id,
            name=payload.name,
            parent_id=requested_parent,
        )
    )


@router.delete("/folders/{folder_id}")
def remove_folder(
    folder_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict:
    assert_capability(auth, CAP_KB_WRITE)
    folder = delete_folder(auth.tenant_id, auth.actor_id, folder_id)
    store.write_audit_log(
        action="folder.delete",
        target_type="folder",
        target_id=folder.id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata({"kb_id": folder.kb_id}),
    )
    return {"status": "deleted", "folder": _serialize(folder)}

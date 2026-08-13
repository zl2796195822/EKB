from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request

from ekb_api.core.audit import RESULT_DENIED, RESULT_SUCCESS, redact_metadata
from ekb_api.core.auth import get_live_auth_context, get_store
from ekb_api.core.errors import ApiError, request_id_from
from ekb_api.domain import AuthContext
from ekb_api.services.v3_identity import (
    list_tenant_users,
    redacted_user_list_metadata,
)
from ekb_api.store import SqlStore

router = APIRouter(tags=["identity-v3"])


@router.get("/tenants/{tenant_id}/users")
def list_users(
    tenant_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    query: Annotated[Optional[str], Query(max_length=255)] = None,
    role: Annotated[Optional[str], Query(max_length=64)] = None,
    status_filter: Annotated[Optional[str], Query(alias="status", max_length=32)] = None,
    sort: Annotated[str, Query()] = "updated_at_desc",
    cursor: Annotated[Optional[str], Query(max_length=4096)] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    try:
        page = list_tenant_users(
            auth,
            tenant_id,
            query=query,
            role=role,
            status_filter=status_filter,
            sort=sort,
            cursor=cursor,
            page_size=page_size,
        )
    except ApiError as exc:
        store.write_audit_log(
            action="team.users.list",
            target_type="tenant_users",
            result=RESULT_DENIED,
            trace_id=request_id_from(request),
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            metadata_redacted=redact_metadata(
                redacted_user_list_metadata(
                    query=query,
                    role=role,
                    status_filter=status_filter,
                    sort=sort,
                    page_size=page_size,
                    returned_count=0,
                    requested_tenant_id=tenant_id,
                    error_code=exc.code,
                    cursor_present=cursor is not None,
                )
            ),
        )
        raise
    store.write_audit_log(
        action="team.users.list",
        target_type="tenant_users",
        result=RESULT_SUCCESS,
        trace_id=request_id_from(request),
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(
            redacted_user_list_metadata(
                query=query,
                role=role,
                status_filter=status_filter,
                sort=sort,
                page_size=page.page_size,
                returned_count=len(page.items),
                requested_tenant_id=tenant_id,
                cursor_present=cursor is not None,
            )
        ),
    )
    return {
        "items": page.items,
        "next_cursor": page.next_cursor,
        "page_size": page.page_size,
    }

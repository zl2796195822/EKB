from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.domain import AuthContext
from ekb_api.schemas import MeResponse, TenantSummary, UserSummary
from ekb_api.store import SqlStore

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeResponse)
def me(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> MeResponse:
    tenant = store.tenant
    return MeResponse(
        user=UserSummary(id=store.user.id, name=store.user.name, email=store.user.email),
        tenants=[TenantSummary(id=tenant.id, name=tenant.name, role=tenant.role.value)],
        capabilities=auth.capabilities,
        policy_version=auth.policy_version,
    )

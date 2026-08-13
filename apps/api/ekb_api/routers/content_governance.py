"""Favorites endpoints (content governance · v4 P2).

Additive only — none of these routes exist in the pre-Phase-6 API surface, so
the mount cannot collide with legacy callers.  Three REST endpoints:

* ``POST /favorites/toggle``   — idempotent star toggle (JSON body).
* ``GET  /favorites``          — paginated per-subject list with titles.
* ``GET  /favorites/check``    — cheap existence probe for star icons.

Authentication is the standard live JWT context; favorites are private to
the subject (a subject never sees another user's starred rows).
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from ekb_api.core.auth import get_live_auth_context
from ekb_api.domain import AuthContext
from ekb_api.services.v3_content_governance import (
    RESOURCE_TYPES,
    check_favorite,
    list_favorites,
    toggle_favorite,
)

router = APIRouter(prefix="/favorites", tags=["favorites"])


# ---- Request shapes --------------------------------------------------------

class ToggleFavoriteRequest(BaseModel):
    resource_type: str = Field(..., description="One of KB / DOCUMENT / CONVERSATION")
    resource_id: str = Field(..., min_length=1, max_length=64)


# ---- Serialisers -----------------------------------------------------------

def _serialize_item(item) -> dict:
    return {
        "id": item.id,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "title": item.title,
        "parent_id": item.parent_id,
        "parent_title": item.parent_title,
        "favorited_at": item.favorited_at,
    }


# ---- Endpoints -------------------------------------------------------------

@router.get("")
def get_favorites(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    resource_type: Optional[str] = Query(default=None, description="Optional filter: KB/DOCUMENT/CONVERSATION"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    page = list_favorites(
        auth.tenant_id,
        auth.actor_id,
        resource_type=resource_type,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_serialize_item(item) for item in page.items],
        "total": page.total,
        "counts": page.counts,
        "resource_types": list(RESOURCE_TYPES),
        "limit": limit,
        "offset": offset,
    }


@router.get("/check")
def get_favorites_check(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    resource_type: str = Query(..., description="KB / DOCUMENT / CONVERSATION"),
    resource_id: str = Query(..., min_length=1, max_length=64),
) -> dict:
    return check_favorite(
        auth.tenant_id,
        auth.actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )


@router.post("/toggle")
def post_favorites_toggle(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    body: Annotated[ToggleFavoriteRequest, Body(...)],
) -> dict:
    result = toggle_favorite(
        auth.tenant_id,
        auth.actor_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
    )
    return {
        "resource_type": result.resource_type,
        "resource_id": result.resource_id,
        "favorited": result.favorited,
        "favorited_at": result.favorited_at,
    }

"""Knowledge analytics endpoints backed by ``resource_access_events`` (v3_003).

Split into four narrow endpoints instead of one fat payload so each card on the
analytics page can load, fail, and retry independently — a slow distribution
query must not block the trend chart.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_READ, assert_capability
from ekb_api.domain import AuthContext
from ekb_api.services.v3_analytics import (
    MAX_ACTIVITY,
    MAX_DISTRIBUTION,
    get_access_trend,
    get_kb_distribution,
    get_overview,
    get_recent_activity,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def read_overview(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    assert_capability(auth, CAP_KB_READ)
    overview = get_overview(auth.tenant_id, days)
    return {
        "days": overview.days,
        "since": overview.since,
        "accesses": overview.accesses,
        "visitors": overview.visitors,
        "qa_volume": overview.qa_volume,
        "kb_count": overview.kb_count,
        "doc_count": overview.doc_count,
        "member_count": overview.member_count,
        "accesses_today": overview.accesses_today,
        "qa_today": overview.qa_today,
    }


@router.get("/trend")
def read_trend(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    days: int = Query(default=14, ge=1, le=90),
) -> dict:
    assert_capability(auth, CAP_KB_READ)
    points = get_access_trend(auth.tenant_id, days)
    return {
        "days": days,
        "points": [
            {"day": point.day, "accesses": point.accesses, "visitors": point.visitors}
            for point in points
        ],
    }


@router.get("/distribution")
def read_distribution(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=MAX_DISTRIBUTION, ge=1, le=MAX_DISTRIBUTION),
) -> dict:
    assert_capability(auth, CAP_KB_READ)
    slices = get_kb_distribution(auth.tenant_id, days, limit=limit)
    return {
        "days": days,
        "items": [
            {
                "kb_id": item.kb_id,
                "name": item.name,
                "documents": item.documents,
                "accesses": item.accesses,
            }
            for item in slices
        ],
    }


@router.get("/activity")
def read_activity(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    limit: int = Query(default=10, ge=1, le=MAX_ACTIVITY),
) -> dict:
    assert_capability(auth, CAP_KB_READ)
    entries = get_recent_activity(auth.tenant_id, limit=limit)
    return {
        "items": [
            {
                "id": entry.id,
                "resource_type": entry.resource_type,
                "resource_id": entry.resource_id,
                "title": entry.title,
                "access_kind": entry.access_kind,
                "actor_id": entry.actor_id,
                "actor_name": entry.actor_name,
                "occurred_at": entry.occurred_at,
            }
            for entry in entries
        ],
        "limit": limit,
    }

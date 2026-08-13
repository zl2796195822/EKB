from __future__ import annotations

from typing_extensions import Annotated
from typing import Optional

from fastapi import APIRouter, Depends, Query

from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.authorization import CAP_AUDIT_READ, assert_capability
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.schemas import (
    FeedbackListResponse,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackStatusUpdate,
)
from ekb_api.store import SqlStore

router = APIRouter(prefix="/qa/messages", tags=["feedback"])


@router.post("/{message_id}/feedback")
def create_feedback(
    message_id: str,
    payload: FeedbackRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict[str, str]:
    message = store.get_message(auth, message_id)
    if not message:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    store.record_feedback(auth, message_id, payload.rating, payload.reason, payload.comment)
    return {"status": "recorded", "message_id": message_id}


# ---- M3-2 反馈闭环：列表 + 标注 ----


@router.get("/feedback", response_model=FeedbackListResponse)
def list_feedback(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    status: Annotated[Optional[str], Query()] = None,
    rating: Annotated[Optional[str], Query()] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[Optional[str], Query()] = None,
) -> FeedbackListResponse:
    """列出反馈记录（含状态与标注），按时间倒序分页。"""
    assert_capability(auth, CAP_AUDIT_READ)
    items, next_cursor = store.list_feedback(
        auth, status=status, rating=rating, page_size=page_size, cursor=cursor
    )
    return FeedbackListResponse(
        results=[FeedbackResponse(**item) for item in items],
        next_cursor=next_cursor,
    )


@router.patch("/feedback/{feedback_id}", response_model=FeedbackResponse)
def update_feedback_status(
    feedback_id: str,
    payload: FeedbackStatusUpdate,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> FeedbackResponse:
    """标注反馈状态（PENDING → REVIEWED → RESOLVED），不自动覆盖生产知识。"""
    assert_capability(auth, CAP_AUDIT_READ)
    result = store.update_feedback_status(auth, feedback_id, payload.status, payload.annotation)
    if result is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    return FeedbackResponse(**result)

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.config import get_settings
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.schemas import SearchRequest, SearchResponse, SearchResult
from ekb_api.store import SqlStore

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> SearchResponse:
    requested_kb_ids = payload.kb_ids
    if requested_kb_ids:
        visible_ids = {kb.id for kb in store.list_knowledge_bases(auth)}
        if not set(requested_kb_ids).issubset(visible_ids):
            raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")

    # M4-7：检索超时保护，避免大候选池或慢查询阻塞事件循环。
    # store.search 是同步阻塞调用，用 to_thread 包装 + wait_for 超时。
    try:
        chunks = await asyncio.wait_for(
            asyncio.to_thread(
                store.search, auth, payload.query, requested_kb_ids, payload.top_k
            ),
            timeout=get_settings().search_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise ApiError(504, "SEARCH_TIMEOUT", "检索超时，请缩小范围或稍后重试") from exc
    return SearchResponse(
        results=[
            SearchResult(
                chunk_id=chunk.id,
                doc_id=chunk.doc_id,
                kb_id=chunk.kb_id,
                title=chunk.title,
                section_path=chunk.section_path,
                snippet=chunk.content[:240],
                score=chunk.score,
                updated_at=chunk.updated_at,
            )
            for chunk in chunks
        ],
        trace_id=auth.trace_id,
    )

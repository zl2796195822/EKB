from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.schemas import SearchRequest, SearchResponse, SearchResult
from ekb_api.store import SqlStore

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
def search(
    payload: SearchRequest,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> SearchResponse:
    requested_kb_ids = payload.kb_ids
    if requested_kb_ids:
        visible_ids = {kb.id for kb in store.list_knowledge_bases(auth)}
        if not set(requested_kb_ids).issubset(visible_ids):
            raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")

    chunks = store.search(auth, payload.query, requested_kb_ids, payload.top_k)
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

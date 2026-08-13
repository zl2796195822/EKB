"""PH6 RAG：检索范围 ACL、严格证据门禁、引用元数据与负向检索过滤。

对应验收：
  - FR-050  0..N KB server ACL，不信任伪造 ID
  - FR-051  Strict 拒答 / Enhanced 常识分隔
  - FR-052  检索携带 version/page/sheet/paragraph/path
  - FR-053  citation 持久且指向生成时版本（持久化在 qa 路由层调用 store.save_message_citations）
  - FR-054  删除/撤权/重建/不兼容内容不检索（store 已按 READY 过滤，本模块提供 RAG 层防御性二次过滤）
"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from ekb_api.core.errors import ApiError
from ekb_api.domain import Chunk

AnswerMode = Literal["STRICT", "ENHANCED"]
_STRICT = "STRICT"


def validate_kb_scope(store: Any, auth: Any, kb_ids: list[str]) -> list[str]:
    """校验用户显式选择的 kb_ids 全部在其授权可见范围内（FR-050，防伪造 ID 越权检索）。

    返回通过校验的 kb_ids 列表；任一不在可见集合内直接抛 ``ApiError(404)``，
    不泄露资源是否存在（与既有 qa.py 行为一致）。``kb_ids`` 为空时直接返回 ``[]``。
    """
    if not kb_ids:
        return []
    visible_ids = {kb.id for kb in store.list_knowledge_bases(auth)}
    only_user_supplied = set(kb_ids)
    if only_user_supplied and not only_user_supplied.issubset(visible_ids):
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    return list(kb_ids)


def has_evidence(chunks: Iterable[Chunk]) -> bool:
    """检索结果中是否存在任何可用证据（非空 content）。"""
    return any((getattr(c, "content", "") or "").strip() for c in chunks)


def needs_strict_refusal(
    answer_mode: AnswerMode,
    kb_ids: list[str],
    chunks: Iterable[Chunk],
) -> bool:
    """STRICT 模式下，选中了 KB 但检索不到任何证据 → 必须拒答（FR-051）。

    - 非 STRICT（ENHANCED）模式永不触发此门禁；
    - 未选中任何 KB（开放问答）不触发（此时本来就没有检索证据要求）。
    """
    if answer_mode != _STRICT:
        return False
    if not kb_ids:
        return False
    return not has_evidence(chunks)


def filter_retrievable(
    chunks: list[Chunk],
    *,
    revoked_doc_ids: Iterable[str] | None = None,
    expired_doc_ids: Iterable[str] | None = None,
) -> list[Chunk]:
    """负向检索过滤（FR-054）：剔除已撤权 / 过期的文档 chunk，绝不进入生成上下文。

    store 层已按 ``DocumentStatus.READY`` 过滤，本函数为 RAG 层防御性二次过滤，
    便于在运行时按显式的撤权/过期文档集合再剔除一次（例如文档被软删除但 chunk 尚未清理时）。
    """
    revoked = frozenset(revoked_doc_ids or ())
    expired = frozenset(expired_doc_ids or ())
    if not revoked and not expired:
        return chunks
    out: list[Chunk] = []
    for c in chunks:
        if c.doc_id in revoked or c.doc_id in expired:
            continue
        out.append(c)
    return out


def build_citations(
    kb_chunks: list[Chunk],
    web_results: list[Any],
    max_citations: int,
) -> list[dict]:
    """构造 SSE ``citations`` 事件的 payload，携带完整元数据（FR-052）。

    结构向后兼容现有前端渲染字段（citation_id/index/type/title/section_path/
    doc_id/chunk_id/url/published_date），并补充：
      - ``version``   ← Chunk.doc_version（生成时的文档版本）
      - ``page`` / ``sheet`` / ``paragraph`` / ``source_path`` ← 文档内定位
      - ``updated_at`` ← Chunk.updated_at（生成时版本时间戳）
      - ``score``     ← 检索得分

    顺序约定：先知识库后联网搜索，超 ``max_citations`` 时优先保留知识库。
    """
    items: list[dict] = []
    budget = max(int(max_citations), 1)
    idx = 0

    for chunk in kb_chunks:
        if idx >= budget:
            break
        body = (getattr(chunk, "content", "") or "").strip()
        if not body:
            continue
        idx += 1
        sp = getattr(chunk, "section_path", None) or []
        if not isinstance(sp, list):
            sp = [str(sp)]
        items.append(
            {
                "citation_id": f"kb-{idx}",
                "index": idx,
                "type": "kb",
                "title": str(getattr(chunk, "title", None) or "知识库文档").strip(),
                "section_path": [str(p) for p in sp if p is not None],
                "version": int(getattr(chunk, "doc_version", 1) or 1),
                "page": getattr(chunk, "page", None),
                "sheet": getattr(chunk, "sheet", None),
                "paragraph": getattr(chunk, "paragraph", None),
                "source_path": getattr(chunk, "source_path", None),
                "updated_at": str(getattr(chunk, "updated_at", "") or ""),
                "score": float(getattr(chunk, "score", 0.0) or 0.0),
                "doc_id": str(getattr(chunk, "doc_id", "") or ""),
                "chunk_id": f"{getattr(chunk, 'doc_id', '')}:{getattr(chunk, 'id', '')}",
                "url": None,
                "published_date": None,
            }
        )

    web_budget = max(budget - idx, 0)
    for wr in web_results:
        if idx >= budget or web_budget <= 0:
            break
        web_budget -= 1
        idx += 1
        items.append(
            {
                "citation_id": f"web-{idx}",
                "index": idx,
                "type": "web",
                "title": str(getattr(wr, "title", None) or getattr(wr, "url", "") or "联网结果"),
                "section_path": [],
                "version": 1,
                "page": None,
                "sheet": None,
                "paragraph": None,
                "source_path": None,
                "updated_at": str(getattr(wr, "published_date", "") or ""),
                "score": 0.0,
                "doc_id": None,
                "chunk_id": f"web-{idx}",
                "url": str(getattr(wr, "url", "") or ""),
                "published_date": getattr(wr, "published_date", None),
            }
        )

    return items

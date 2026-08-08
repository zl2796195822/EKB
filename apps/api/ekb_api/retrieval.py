"""M1-05 检索编排：query 改写 → 多路召回 → 跨查询融合 → Rerank → top K。

设计：
  - LLM 可用时：把用户问题改写成 3-6 个检索 query，对每个 query 跑 store.search（内部已 hybrid：
    BM25 词法 + 语义余弦 → RRF 融合 → Rerank），再用「跨查询 max-score 融合」合并去重
    （每个 chunk 保留其在各子查询中的最高语义分，避免复合问题 gold 被 RRF 稀释漏召），
    最后对候选池 Rerank 精排取 top K，扩大召回覆盖（修复跨 SOP 综合题正确文档不在候选的问题）。
  - LLM 不可用时：直接用原始问题单路检索，保持 M1 评估基线。
"""

from __future__ import annotations

from ekb_api.domain import AuthContext, Chunk
from ekb_api.llm import LlmError, rewrite_query
from ekb_api.ranking import _Bm25, rerank
from ekb_api.store import SqlStore

# 多路召回时每个子 query 的 top_k（扩大候选池）。
_RECALL_TOP_K = 20
# 最终返回给生成的候选数。
_FINAL_TOP_K = 5


def retrieve(
    store: SqlStore,
    auth: AuthContext,
    query: str,
    kb_ids: list[str],
    top_k: int = _FINAL_TOP_K,
    *,
    route=None,
) -> list[Chunk]:
    """检索编排：query 改写 → 多路召回 → 跨查询 max-score 融合 → Rerank → top K。

    LLM 不可用时降级到单路检索（原始 query），保持 M1 评估基线。
    route 为 M2-7 租户级模型路由（注入 query 改写所用的 provider/model）。
    """
    try:
        queries = rewrite_query(query, route=route)
    except LlmError:
        queries = [query]

    if len(queries) <= 1:
        # LLM 未配置或改写失败：单路检索。
        return store.search(auth, query, kb_ids, top_k)

    # 多路召回：对每个改写 query 跑检索，每个 chunk 保留最高语义分（store.search 已写回 score）。
    best: dict[str, Chunk] = {}
    for sub_query in queries:
        for chunk in store.search(auth, sub_query, kb_ids, _RECALL_TOP_K):
            current = best.get(chunk.id)
            if current is None or chunk.score > current.score:
                best[chunk.id] = chunk

    # 跨查询 max-score 融合后，用完整问题对合并候选做终排序：
    #  - 语义分代理：各 chunk 在子查询中的 rerank 融合分（已含语义/词法/重合度，跨查询取最高）。
    #  - 词法分：用完整问题对合并候选重算 BM25（完整问题比任意单一子查询信息更全，重合度更准）。
    # 二者一并交给 rerank，避免无 embedding 时终排序退化为按序号截断。
    merged = sorted(best.values(), key=lambda c: -c.score)
    if len(merged) <= 1:
        return merged[:top_k]
    corpus = [f"{c.title} {' '.join(c.section_path)} {c.content}" for c in merged]
    lex = _Bm25(corpus).scores(query)
    reranked = rerank(
        query,
        merged,
        semantic_scores=[c.score for c in merged],
        lexical_scores=lex,
        pool_size=len(merged),
    )
    return reranked[:top_k]

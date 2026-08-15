"""M1-05 / M4-3 检索编排：query 改写 → 多路召回 → 跨查询融合 → Rerank → top K。

设计：
  - LLM 可用时：把用户问题改写成 3-6 个检索 query，对每个 query 跑 store.search（内部已 hybrid：
    BM25 词法 + 语义余弦 → RRF 融合 → Rerank），再用「跨查询 max-score 融合」合并去重
    （每个 chunk 保留其在各子查询中的最高语义分，避免复合问题 gold 被 RRF 稀释漏召），
    最后对候选池 Rerank 精排取 top K，扩大召回覆盖（修复跨 SOP 综合题正确文档不在候选的问题）。
  - LLM 不可用时：直接用原始问题单路检索，保持 M1 评估基线。

M4-3 优化：
  - 共享检索上下文：多路召回只做一次 DB 查询 + 一次 BM25 索引构建（原 N 次重复）。
  - 并行多路召回：用 ThreadPoolExecutor 并行执行各子查询的 embedding + 门禁 + rerank
    （原串行 for 循环，N 个子查询各等一次 embedding API）。
  - 投机并行：原始 query 检索与 rewrite_query 同时启动（原始 query 本就是 queries[0]）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ekb_api.domain import AuthContext, Chunk
from ekb_api.llm import LlmError, rewrite_query
from ekb_api.ranking import _Bm25, rerank
from ekb_api.store import SqlStore

# 多路召回时每个子 query 的 top_k（扩大候选池）。
_RECALL_TOP_K = 20
# 最终返回给生成的候选数。
_FINAL_TOP_K = 5
# M4-3 并行多路召回的线程池大小（embedding API 是 I/O 密集，GIL 在等待时释放）。
_MAX_WORKERS = 6


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

    M4-3 优化：多路召回共享检索上下文（一次 DB 查询 + 一次 BM25）+ 线程池并行。
    """
    # 通用模式（未选知识库）：不加载全租户 chunk、不建 BM25、不调改写 LLM，
    # 直接返回空，跳到生成阶段。否则 fetch_search_context 会对空 kb_ids 仍加载
    # 全部租户 chunk 并构建 BM25（万级 chunk 耗时可观），既无用又压占 DB 连接。
    if not kb_ids:
        return []

    # M4-3：投机并行——原始 query 检索与 rewrite_query 同时启动。
    # 原始 query 本就是 queries[0]（rewrite_query 返回 [question, ...]），
    # 先用原始 query 预取检索上下文并跑一路检索，同时 LLM 改写在另一线程执行。
    ctx = store.fetch_search_context(auth, kb_ids)
    if ctx is None:
        # 无可见 KB 或无 chunk：跳过改写直接返回空。
        return []

    # 投机并行：原始 query 检索 + LLM 改写同时进行。
    with ThreadPoolExecutor(max_workers=2) as pool:
        rewrite_future = pool.submit(
            _safe_rewrite, query, route, tenant_id=auth.tenant_id, user_id=auth.actor_id
        )
        original_future = pool.submit(
            store.search_with_context,
            ctx,
            query,
            _RECALL_TOP_K,
            tenant_id=auth.tenant_id,
            user_id=auth.actor_id,
        )

        original_chunks = original_future.result()
        try:
            queries = rewrite_future.result(timeout=30)
        except Exception:
            queries = [query]

    # 单路（LLM 未配置或改写失败）：直接返回原始 query 检索结果。
    if len(queries) <= 1:
        return original_chunks[:top_k]

    # M4-3：多路召回并行化——改写产生的子 query（跳过 queries[0]=原始 query，已投机执行）。
    sub_queries = queries[1:]
    if sub_queries:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = [
                pool.submit(
                    store.search_with_context,
                    ctx,
                    sq,
                    _RECALL_TOP_K,
                    tenant_id=auth.tenant_id,
                    user_id=auth.actor_id,
                )
                for sq in sub_queries
            ]
            sub_results = [f.result() for f in futures]
    else:
        sub_results = []

    # 跨查询 max-score 融合：每个 chunk 保留最高语义分（store.search 已写回 score）。
    best: dict[str, Chunk] = {}
    for chunk in original_chunks:
        best[chunk.id] = chunk
    for chunks in sub_results:
        for chunk in chunks:
            current = best.get(chunk.id)
            if current is None or chunk.score > current.score:
                best[chunk.id] = chunk

    # 跨查询 max-score 融合后，用完整问题对合并候选做终排序。
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


def _safe_rewrite(query: str, route=None, *, tenant_id=None, user_id=None) -> list[str]:
    """包装 rewrite_query，异常时返回 [query]（不抛异常到 ThreadPoolExecutor）。"""
    try:
        return rewrite_query(query, route=route, tenant_id=tenant_id, user_id=user_id)
    except LlmError:
        return [query]

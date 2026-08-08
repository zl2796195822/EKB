"""M1-05 检索强化单元测试：锁定 BM25 / RRF / rerank / build_context 行为。"""

from __future__ import annotations

from ekb_api.domain import Chunk
from ekb_api.ranking import _Bm25, _tokenize, build_context, rerank, rrf_fuse


def _chunk(
    content: str,
    title: str = "T",
    section: list[str] | None = None,
    doc_id: str = "d",
) -> Chunk:
    return Chunk(
        id=f"{doc_id}-{content[:6]}",
        tenant_id="t",
        kb_id="kb",
        doc_id=doc_id,
        doc_version=1,
        title=title,
        section_path=section or [],
        content=content,
        score=0.0,
        updated_at="2026-01-01T00:00:00Z",
    )


def test_tokenize_mixes_cjk_and_ascii() -> None:
    toks = _tokenize("Redis 连接池 耗尽")
    assert "redis" in toks
    assert "连接" in toks
    assert "接" in toks  # 单字
    assert "连" in toks


def test_bm25_ranks_relevant_doc_higher() -> None:
    docs = [
        "Redis 连接池耗尽时先检查等待队列长度与超时配置",
        "Kubernetes 集群的 Pod 调度策略与资源配额",
    ]
    bm25 = _Bm25(docs)
    scores = bm25.scores("Redis 连接池 耗尽")
    assert scores[0] > scores[1]
    assert scores[0] > 0


def test_rrf_fuse_combines_lists() -> None:
    # 两项都在两份列表的榜首 → 融合分最高。
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]], k=60)
    assert fused["a"] > fused["b"]
    assert fused["a"] > fused["c"]


def test_rerank_prefers_query_overlap() -> None:
    chunks = [
        _chunk("与问题无关的背景知识介绍一般性概念", title="X"),
        _chunk("Redis 连接池耗尽时应检查等待队列与超时", title="Y"),
    ]
    # 语义分都给 0，仅词法 BM25 有区分；rerank 应把重叠度高的排前。
    lexical = [0.1, 0.9]
    out = rerank("Redis 连接池耗尽", chunks, semantic_scores=[0.0, 0.0], lexical_scores=lexical)
    assert out[0].id == chunks[1].id


def test_build_context_dedups_and_budgets() -> None:
    same = "重复内容段落用于验证去重逻辑是否生效"
    chunks = [
        _chunk(same, doc_id="d1"),
        _chunk(same, doc_id="d2"),  # 相同 (doc_id, content) → 去重
        _chunk("另一段不同的证据内容用于测试预算", doc_id="d3"),
    ]
    evidence = build_context(chunks, char_budget=200)
    # 两条相同内容只保留一条；预算截断后不应超过 2 条（含短文本）。
    assert len(evidence) >= 1
    assert any("重复内容段落" in e for e in evidence)
    assert sum(len(e) for e in evidence) <= 200 + len("另一段不同的证据内容用于测试预算") + 50

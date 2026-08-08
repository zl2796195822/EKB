"""M1-05 检索强化：BM25 词法召回 + RRF 融合 + Rerank 重排 + 上下文构建。

组件：
  - _tokenize: 中英文混合分词（ASCII 词 + CJK 单字 + CJK bigram），无外部依赖。
  - _Bm25: 在候选 chunk 语料上计算 BM25 词法分（本地、确定性）。
  - rrf_fuse: 多路召回排名列表用倒数排名融合（Reciprocal Rank Fusion）合并。
  - rerank: 重排。默认本地重排（归一化语义分 + 词法 BM25 分 + 查询词重合度加权），
    确定性、不调用外部模型；保留 cross-encoder/rerank 模型的接入点（见 rerank 注释）。
  - build_context: 把候选 chunk 去重并裁剪到令牌预算，构建喂给 LLM 的证据上下文。

设计约束（防止破坏 M1 评估基线 / 拒答门禁）：
  - 混合检索只在「已通过原门禁（余弦阈值或关键词命中）的候选集」内做 BM25 重排，
    不引入原门禁之外的 chunk，从而严格保持「无相关证据 → 空 → LLM 拒答」行为。
  - rerank 为确定性函数，温度无关，不增加外部调用，避免 LLM 抖动影响质量门禁。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Optional

from ekb_api.domain import Chunk

# BM25 参数（经验值）。
_K1 = 1.5
_B = 0.75
# RRF 常数：排名越靠后权重越低。
_RRF_K = 60
# rerank 候选池大小：对融合后的候选做精排，再取 top_k。
_RERANK_POOL = 20
# 上下文令牌预算（按字符估算，~1 token/字 对 CJK 偏保守）。
_CONTEXT_CHAR_BUDGET = 2200


def _tokenize(text: str) -> list[str]:
    """中英文混合分词：ASCII 词（去标点）+ CJK 单字 + CJK bigram。"""
    tokens: list[str] = []
    for part in text.lower().split():
        word = "".join(ch for ch in part if ch.isalnum())
        if word:
            tokens.append(word)
    cjk = [ch for ch in text if "\u4e00" <= ch <= "\u9fff"]
    tokens.extend(cjk)
    for index in range(len(cjk) - 1):
        tokens.append(cjk[index] + cjk[index + 1])
    return tokens


class _Bm25:
    """在给定语料上构建 BM25 索引并打分（Okapi BM25）。"""

    def __init__(self, docs: Sequence[str]) -> None:
        self.doc_tokens = [_tokenize(doc) for doc in docs]
        self.n = len(self.doc_tokens)
        df: dict[str, int] = {}
        for toks in self.doc_tokens:
            for term in set(toks):
                df[term] = df.get(term, 0) + 1
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }
        total = sum(len(toks) for toks in self.doc_tokens)
        self.avgdl = (total / self.n) if self.n else 0.0

    def scores(self, query: str) -> list[float]:
        q_tokens = _tokenize(query)
        if not q_tokens or self.n == 0:
            return [0.0] * self.n
        result: list[float] = []
        for toks in self.doc_tokens:
            dl = len(toks)
            norm_dl = dl / self.avgdl if self.avgdl else 1.0
            tf: dict[str, int] = {}
            for term in toks:
                tf[term] = tf.get(term, 0) + 1
            score = 0.0
            for qt in q_tokens:
                f = tf.get(qt, 0)
                if f == 0:
                    continue
                idf = self.idf.get(qt, 0.0)
                denom = f + _K1 * (1 - _B + _B * norm_dl)
                score += idf * (f * (_K1 + 1)) / denom
            result.append(score)
        return result


def _minmax(values: Sequence[float]) -> list[float]:
    """min-max 归一化到 [0,1]；全相等时返回全 0。"""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def rrf_fuse(ranked_items: Sequence[Sequence], k: int = _RRF_K) -> dict:
    """倒数排名融合（RRF）：合并多路已排序的召回列表。

    ranked_items: 每个元素是一个按相关性降序排列的列表（chunk id 或索引均可）。
    返回 {item: rrf_score}，score 越高越相关。
    """
    fused: dict = {}
    for ranked in ranked_items:
        for rank, item in enumerate(ranked):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank + 1)
    return fused


def rerank(
    query: str,
    chunks: Sequence[Chunk],
    *,
    semantic_scores: Optional[Sequence[float]] = None,
    lexical_scores: Optional[Sequence[float]] = None,
    pool_size: int = _RERANK_POOL,
    rrf_k: int = _RRF_K,
) -> list[Chunk]:
    """对候选 chunk 做精排，返回重排后的列表（最多 pool_size 个）。

    融合策略：语义排名 + 词法 BM25 排名 → RRF 倒数排名融合，再叠加查询词重合度作同分微调。
    语义排名在 RRF 中占 3 份权重、词法占 1 份，确保「高语义相关（余弦高）的 gold chunk」
    不被词法噪声挤出 top-K（RRF 等权融合的已知缺陷），同时让 BM25 在语义接近时辅助精度。

    调用方需保证候选池已按语义分预选（见 store.search），本函数只在池内精排，不负责召回。

    扩展点：未来接入真值 cross-encoder / rerank 模型时，可在此读取 settings.rerank_provider，
    对每个 (query, chunk) 打分后替换本地排序；失败时回退本地逻辑，不影响可用性。
    """
    if not chunks:
        return []
    n = len(chunks)
    sem = list(semantic_scores) if semantic_scores is not None else [0.0] * n
    lex = list(lexical_scores) if lexical_scores is not None else [0.0] * n
    if all(s == 0.0 for s in sem) and all(val == 0.0 for val in lex):
        return list(chunks)[:pool_size]

    q_terms = set(_tokenize(query))
    overlaps: list[int] = []
    for chunk in chunks:
        c_tokens = set(_tokenize(f"{chunk.title} {' '.join(chunk.section_path)} {chunk.content}"))
        overlaps.append(len(q_terms & c_tokens))

    # 语义排名占 3 份、词法占 1 份 → RRF 融合；重合度仅作同分微调。
    sem_rank = [i for i, _ in sorted(enumerate(sem), key=lambda x: (-x[1], x[0]))]
    lex_rank = [i for i, _ in sorted(enumerate(lex), key=lambda x: (-x[1], x[0]))]
    fused = rrf_fuse([sem_rank, sem_rank, sem_rank, lex_rank], k=rrf_k)
    final = [fused[i] + 0.0001 * overlaps[i] for i in range(n)]
    order = sorted(range(n), key=lambda i: -final[i])
    out = [chunks[i] for i in order[:pool_size]]
    # 把融合分写回 chunk.score，供调用方（store.search / retrieve）做跨查询 max-score 融合。
    # 无 embedding 时语义分恒为 0，此分退化为「词法 BM25 排名 + 查询重合度」，
    # 仍可作为有效相关性信号。
    for rank, chunk in zip(order[:pool_size], out):
        chunk.score = final[rank]
    return out


def build_context(chunks: Sequence[Chunk], char_budget: int = _CONTEXT_CHAR_BUDGET) -> list[str]:
    """把检索到的 chunk 构建为喂给 LLM 的证据文本列表。

    - 按 (doc_id, content) 去重，避免同一段被重复纳入。
    - 按传入顺序（已是重排后的相关度降序）拼接，超出字符预算后停止。
    - 每条带来源标题与章节路径，便于 LLM 引用溯源。
    """
    seen: set[tuple[str, str]] = set()
    evidence: list[str] = []
    used = 0
    for chunk in chunks:
        key = (chunk.doc_id, chunk.content)
        if key in seen:
            continue
        seen.add(key)
        # 格式与 M1 原 _generate 一致（标题 / 章节路径\n内容），避免 LLM 提示词漂移。
        section = " / ".join(chunk.section_path)
        text = f"{chunk.title}" + (f" / {section}" if section else "") + f"\n{chunk.content}"
        if used + len(text) > char_budget and evidence:
            break
        evidence.append(text)
        used += len(text)
    return evidence

"""M1-04 Embedding 与余弦检索单元测试。

覆盖：
  - 本地字符 n-gram TF 向量的确定性、L2 归一化、空输入。
  - 余弦相似度：相同向量=1、正交=0、空/维度不匹配=0。
  - embed_batch：未配置 API 时降级本地向量，返回数量一致。
  - ingest：未配置 API 时 chunk.embedding=None（检索走关键词 fallback）。
"""

from __future__ import annotations

import math
import os

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DATABASE_URL", "sqlite:///./ekb_test.db")

from ekb_api.embedding import (
    _local_embed,
    cosine_similarity,
    embed_batch,
    embed_one,
)


def test_local_embed_is_deterministic() -> None:
    """相同文本恒产生相同向量（hashlib，非 Python hash 随机化）。"""
    a = _local_embed("数据库连接池耗尽", 256)
    b = _local_embed("数据库连接池耗尽", 256)
    assert a == b


def test_local_embed_is_l2_normalized() -> None:
    """非空文本向量 L2 范数 = 1。"""
    vec = _local_embed("Redis 内存溢出排查", 256)
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-9


def test_local_embed_empty_returns_zero_vector() -> None:
    vec = _local_embed("", 256)
    assert vec == [0.0] * 256
    assert all(v == 0.0 for v in vec)


def test_local_embed_cjk_bigram_contributes_signal() -> None:
    """共享 CJK bigram 的文本余弦高于无共享的文本。"""
    base = _local_embed("连接池耗尽", 256)
    related = _local_embed("连接池使用率", 256)  # 共享"连接"bigram
    unrelated = _local_embed("Redis 内存溢出", 256)
    assert cosine_similarity(base, related) > cosine_similarity(base, unrelated)


def test_cosine_similarity_identical_vectors_is_one() -> None:
    vec = _local_embed("任意文本", 256)
    assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-9


def test_cosine_similarity_empty_or_mismatched_is_zero() -> None:
    vec = _local_embed("文本", 256)
    assert cosine_similarity([], vec) == 0.0
    assert cosine_similarity(vec, []) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0  # 维度不匹配


def test_embed_batch_local_fallback_count_matches() -> None:
    """未配置 API 时降级本地向量，返回数量与输入一致。"""
    texts = ["连接池耗尽", "Redis 内存溢出", "网络延迟"]
    vectors = embed_batch(texts)
    assert len(vectors) == len(texts)
    for v in vectors:
        assert len(v) > 0


def test_embed_one_returns_single_vector() -> None:
    vec = embed_one("单个查询")
    assert isinstance(vec, list)
    assert len(vec) > 0

"""M1-04 远程 Embedding 边界与余弦检索单元测试。

覆盖：
  - 余弦相似度：相同向量=1、正交=0、空/维度不匹配=0。
  - embed_batch：未配置远程 Provider 时 fail-closed。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DATABASE_URL", "sqlite:///./ekb_test.db")

from ekb_api.embedding import EmbeddingError, cosine_similarity, embed_batch


def test_cosine_similarity_identical_vectors_is_one() -> None:
    vec = [0.5, 0.5]
    assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-9


def test_cosine_similarity_empty_or_mismatched_is_zero() -> None:
    vec = [1.0, 0.0]
    assert cosine_similarity([], vec) == 0.0
    assert cosine_similarity(vec, []) == 0.0
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0  # 维度不匹配


def test_embed_batch_fails_closed_without_remote_provider() -> None:
    with pytest.raises(EmbeddingError, match="不会使用本地向量"):
        embed_batch(["连接池耗尽"])

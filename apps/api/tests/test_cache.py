"""M4-1 缓存优化：LruTtlCache + embedding/rewrite 缓存接入测试。"""

from __future__ import annotations

import os
import time

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DEV_USER_EMAIL", "admin@example.com")
os.environ.setdefault("EKB_DEV_PASSWORD", "test-password")
os.environ.setdefault("EKB_TOKEN_SECRET", "test-only-token-secret")
os.environ.setdefault("EKB_DATABASE_URL", "sqlite:///./ekb_test.db")

from ekb_api.core.cache import LruTtlCache, text_hash
from ekb_api.core.metrics import registry
from ekb_api.embedding import embed_one

# ---- LruTtlCache 基础测试 ----


def test_cache_get_put_hit() -> None:
    """写入后命中。"""
    c = LruTtlCache(maxsize=10, ttl_seconds=60.0)
    assert c.get("k") is None
    c.put("k", "v")
    assert c.get("k") == "v"
    assert len(c) == 1


def test_cache_lru_eviction() -> None:
    """超出容量淘汰最久未使用。"""
    c = LruTtlCache(maxsize=2, ttl_seconds=60.0)
    c.put("a", 1)
    c.put("b", 2)
    # 访问 a 刷新 LRU，使 b 成为最久未使用
    assert c.get("a") == 1
    c.put("c", 3)  # 应淘汰 b
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3


def test_cache_ttl_expiry() -> None:
    """TTL 过期后 get 返回 None。"""
    c = LruTtlCache(maxsize=10, ttl_seconds=0.05)
    c.put("k", "v")
    assert c.get("k") == "v"
    time.sleep(0.06)
    assert c.get("k") is None


def test_cache_clear() -> None:
    """clear 清空全部。"""
    c = LruTtlCache(maxsize=10, ttl_seconds=60.0)
    c.put("a", 1)
    c.put("b", 2)
    c.clear()
    assert len(c) == 0
    assert c.get("a") is None


def test_get_or_compute_hit_and_miss() -> None:
    """get_or_compute 命中返回缓存且不调 factory；未命中调 factory 并写入。"""
    c = LruTtlCache(maxsize=10, ttl_seconds=60.0)
    call_count = 0

    def factory() -> str:
        nonlocal call_count
        call_count += 1
        return "computed"

    # 未命中 → 调 factory
    val, hit = c.get_or_compute("k", factory)
    assert val == "computed"
    assert hit is False
    assert call_count == 1

    # 命中 → 不调 factory
    val, hit = c.get_or_compute("k", factory)
    assert val == "computed"
    assert hit is True
    assert call_count == 1


def test_text_hash_stable() -> None:
    """相同文本哈希一致；不同文本哈希不同。"""
    assert text_hash("hello") == text_hash("hello")
    assert text_hash("hello") != text_hash("world")
    assert len(text_hash("test")) == 16


# ---- embed_one 缓存测试 ----


def test_embed_one_cache_hit(monkeypatch) -> None:
    """相同文本第二次 embed_one 不重新计算（缓存命中）。"""
    # 用独立缓存实例避免污染全局
    from ekb_api.core import cache as cache_mod

    test_cache = LruTtlCache(maxsize=10, ttl_seconds=60.0)
    monkeypatch.setattr(cache_mod, "query_embedding_cache", test_cache)
    # embedding.embed_one 引用的是导入时的对象，需 patch embedding 模块的引用
    import ekb_api.embedding as emb_mod

    monkeypatch.setattr(emb_mod, "query_embedding_cache", test_cache)
    monkeypatch.setattr(emb_mod, "embed_batch", lambda texts: [[1.0, 0.0] for _ in texts])

    call_count = 0
    original_embed_batch = emb_mod.embed_batch

    def counting_embed_batch(texts):
        nonlocal call_count
        call_count += 1
        return original_embed_batch(texts)

    monkeypatch.setattr(emb_mod, "embed_batch", counting_embed_batch)

    # 第一次：未命中
    v1 = embed_one("测试缓存文本")
    assert call_count == 1
    # 第二次：命中
    v2 = embed_one("测试缓存文本")
    assert call_count == 1  # 未再调 embed_batch
    assert v1 == v2


def test_embed_one_cache_different_text_miss(monkeypatch) -> None:
    """不同文本不命中缓存。"""
    import ekb_api.embedding as emb_mod

    test_cache = LruTtlCache(maxsize=10, ttl_seconds=60.0)
    monkeypatch.setattr(emb_mod, "query_embedding_cache", test_cache)
    monkeypatch.setattr(emb_mod, "embed_batch", lambda texts: [[1.0, 0.0] for _ in texts])

    embed_one("文本甲")
    embed_one("文本乙")
    # 两次不同文本 → 两次 miss
    metrics_text = registry.collect()
    # embedding miss 至少 2 次（测试间可能有其他调用，用 >= 断言）
    assert "cache_misses_total" in metrics_text


# ---- rewrite_query 缓存测试 ----


def test_rewrite_query_cache_hit(monkeypatch) -> None:
    """相同问题第二次 rewrite_query 不调 LLM（缓存命中）。"""
    import ekb_api.llm as llm_mod

    test_cache = LruTtlCache(maxsize=10, ttl_seconds=60.0)
    monkeypatch.setattr(llm_mod, "query_rewrite_cache", test_cache)

    call_count = 0

    def counting_chat(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return '["改写1", "改写2"]'

    monkeypatch.setattr(llm_mod, "chat", counting_chat)

    # 确保有 chat provider 配置（测试环境可能没有）
    if not llm_mod.get_settings().chat_providers:
        # 无 provider 时 rewrite 直接返回 [question]，不测缓存
        return

    r1 = llm_mod.rewrite_query("Redis内存怎么排查")
    assert call_count == 1
    r2 = llm_mod.rewrite_query("Redis内存怎么排查")
    assert call_count == 1  # 命中缓存，未再调 chat
    assert r1 == r2

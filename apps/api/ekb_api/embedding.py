"""M1-04 Embedding 生成：仅使用远程 Provider 的 OpenAI-compatible 端点。

设计：
  - settings.embedding_providers 为登记的全部启用 embedding 提供商；逐个尝试，
    首个成功即用，失败自动 fallback 下一个。
  - 未配置 Provider 或全部 Provider 失败时抛出 EmbeddingError。
  - 业务层可以保留无向量的词法索引，但绝不生成本地向量冒充语义索引。
"""

from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.request

from ekb_api.core.cache import query_embedding_cache, text_hash
from ekb_api.core.config import ModelProvider, get_settings
from ekb_api.core.metrics import CACHE_HITS, CACHE_MISSES

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """远程 Embedding provider 调用失败。"""


def _embed_with_provider(provider: ModelProvider, texts: list[str]) -> list[list[float]]:
    """调用单个 provider 的 embeddings 端点（OpenAI 兼容 POST base_url）。失败抛 EmbeddingError。"""
    results: list[list[float]] = []
    batch_size = max(1, get_settings().embedding_batch_size)
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        payload = json.dumps({"input": batch, "model": provider.model}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        request = urllib.request.Request(
            provider.base_url, data=payload, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=provider.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            raise EmbeddingError(f"[{provider.name}] 调用失败: {exc}") from exc
        for item in body["data"]:
            results.append([float(v) for v in item["embedding"]])
    return results


def embed_batch(
    texts: list[str], *, tenant_id: str | None = None, user_id: str | None = None
) -> list[list[float]]:
    """批量生成 embedding；无远程 Provider 时显式失败，不使用本地模型。"""
    settings = get_settings()
    if tenant_id and user_id:
        from ekb_api.core.config import get_runtime_embedding_providers

        providers = get_runtime_embedding_providers(tenant_id=tenant_id, user_id=user_id)
    else:
        providers = settings.embedding_providers
    if not providers:
        raise EmbeddingError("未配置远程 embedding provider；不会使用本地向量替代")

    last_exc: EmbeddingError | None = None
    for provider in providers:
        try:
            return _embed_with_provider(provider, texts)
        except EmbeddingError as exc:
            last_exc = exc
            logger.warning("embedding provider %s 失败，尝试下一个: %s", provider.name, exc)
            continue

    raise EmbeddingError(f"所有远程 embedding provider 均失败: {last_exc}")


def embed_one(text: str) -> list[float]:
    """单条 embedding，便于检索时对 query 向量化。

    M4-1：接入 query embedding 缓存。Spec 5.4 明确允许缓存 query embedding，
    且 embedding 是纯函数（文本→向量），跨租户安全。键含模型版本，
    换 embedding 模型时旧向量自动失效。
    """
    settings = get_settings()
    providers = settings.embedding_providers
    model_version = providers[0].model if providers else "unconfigured"
    cache_key = (model_version, text_hash(text))

    cached = query_embedding_cache.get(cache_key)
    if cached is not None:
        CACHE_HITS.inc(type="embedding")
        return cached

    CACHE_MISSES.inc(type="embedding")
    vec = embed_batch([text])[0]
    query_embedding_cache.put(cache_key, vec)
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（输入需已 L2 归一化，等价于点积；此处仍做安全归一化）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)

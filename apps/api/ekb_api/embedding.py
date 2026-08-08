"""M1-04 Embedding 生成：多 Provider 抽象（任意 OpenAI 兼容 embeddings 端点）+ 本地 n-gram 降级。

设计：
  - settings.embedding_providers 为登记的全部启用 embedding 提供商；逐个尝试，
    首个成功即用，失败自动 fallback 下一个。
  - 全部失败 / 无 provider 时降级到本地字符 n-gram TF 向量（hashing trick + L2 归一化）：
      * CJK 单字 + bigram、ASCII 词 token，经 hashlib 哈希到固定维度桶。
      * 确定性：相同文本恒产生相同向量（hashlib，非 Python 内置 hash）。
      * 无真实语义，但保留词法重叠信号，余弦相似度可反映词汇重合度，
        对跨 SOP 综合题的召回优于纯 bigram 整词命中（不会硬过滤零命中 chunk）。
  - 真实语义检索需配置外部 embedding provider（如 OpenAI / 硅基流动 BGE-M3）；
    未配置或调用失败时本地降级保证离线/测试流程可跑通且可复现，入库不会因 Embedding 失败而 FAILED。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import urllib.error
import urllib.request

from ekb_api.core.cache import query_embedding_cache, text_hash
from ekb_api.core.config import ModelProvider, get_settings
from ekb_api.core.metrics import CACHE_HITS, CACHE_MISSES

logger = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """单个 Embedding provider 调用失败（由 embed_batch 捕获并 fallback / 降级）。"""


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _hash_bucket(token: str, dim: int) -> int:
    """确定性哈希：hashlib.md5 → int → mod dim。避免 Python hash 随机化。"""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % dim


def _local_embed(text: str, dim: int) -> list[float]:
    """本地字符 n-gram TF 向量（hashing trick + L2 归一化）。

    仅用 CJK bigram + ASCII 词 token（≥2 字符），不含单字：
      - 单字（不/在/的 等）过于常见，哈希碰撞导致无依据问题误命中走拒答失败；
      - bigram 具备区分度，对齐阈值判定的 bigram 重叠率。
    """
    vec = [0.0] * dim
    if not text:
        return vec

    normalized = text.lower()
    # CJK bigram（不含单字，避免常见字碰撞）
    cjk_chars = [c for c in normalized if _is_cjk(c)]
    for i in range(len(cjk_chars) - 1):
        vec[_hash_bucket("".join(cjk_chars[i : i + 2]), dim)] += 1.0
    # ASCII 词 token（含中划线/数字技术词，如 allkeys-lru、pcap）
    for token in re.findall(r"[a-z0-9]+", normalized):
        if len(token) >= 2:
            vec[_hash_bucket(token, dim)] += 1.0

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


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


def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量生成 embedding：遍历 embedding provider，首个成功即用；全失败/无 provider 降级本地向量。

    降级保证入库不崩、不 FAILED，仅失去真实语义（退化为词法重合）。
    """
    settings = get_settings()
    providers = settings.embedding_providers
    if not providers:
        return [_local_embed(text, settings.embedding_dim) for text in texts]

    last_exc: EmbeddingError | None = None
    for provider in providers:
        try:
            return _embed_with_provider(provider, texts)
        except EmbeddingError as exc:
            last_exc = exc
            logger.warning("embedding provider %s 失败，尝试下一个: %s", provider.name, exc)
            continue

    logger.warning("所有 embedding provider 均失败，降级本地 n-gram 向量: %s", last_exc)
    return [_local_embed(text, settings.embedding_dim) for text in texts]


def embed_one(text: str) -> list[float]:
    """单条 embedding，便于检索时对 query 向量化。

    M4-1：接入 query embedding 缓存。Spec 5.4 明确允许缓存 query embedding，
    且 embedding 是纯函数（文本→向量），跨租户安全。键含模型版本，
    换 embedding 模型时旧向量自动失效。
    """
    settings = get_settings()
    model_version = settings.embedding_model or "local"
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

"""M4-1 缓存优化：轻量 LRU + TTL 缓存（纯 stdlib）。

设计：
  - 基于 collections.OrderedDict 实现 LRU 淘汰 + TTL 过期。
  - 线程安全：Lock 保护读写，适配 SQLite 跨线程复用连接的模式。
  - 不引入 Redis（Spec 4.2：并发和可靠投递要求超出单体能力时才拆分）；
    进程内缓存重启即清空，作为自然 TTL 的兜底失效机制。
  - 缓存键设计遵循 Spec 5.4 + 6.1 + FR-AC-03：
      * query embedding：纯函数（文本→向量），跨租户安全，键含模型版本
        （换 embedding 模型时旧向量自动失效）。
      * query 改写：语言操作（问题→检索词），无授权数据，键含 LLM 模型版本。
      * 答案缓存默认不启用（Spec 5.4 要求绑定 tenant_id+权限摘要+知识版本+
        模型/Prompt/策略版本，复杂度高，M4-1 暂不做）。
  - 失效策略：policy_version / 模型版本纳入键 → 变更时自动失效；
    进程重启清空；TTL 兜底防陈旧。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Callable, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")

# 默认容量与 TTL（query embedding / 改写结果属"公开版本稳定片段"，可较长缓存）。
_DEFAULT_MAXSIZE = 512
_DEFAULT_TTL_SECONDS = 3600.0


class LruTtlCache(Generic[K, V]):
    """线程安全 LRU + TTL 缓存。

    get 命中时刷新 LRU 顺序；过期条目在 get/put 时惰性清除。
    """

    def __init__(
        self,
        maxsize: int = _DEFAULT_MAXSIZE,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        """命中返回值并刷新 LRU；未命中或过期返回 None。"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() >= expires_at:
                # 过期惰性清除。
                del self._data[key]
                return None
            # 刷新 LRU 顺序。
            self._data.move_to_end(key)
            return value

    def put(self, key: K, value: V) -> None:
        """写入缓存；超出容量时淘汰最久未使用条目。"""
        with self._lock:
            now = time.monotonic()
            self._data[key] = (value, now + self._ttl)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        """清空全部缓存（测试用 / 手动失效）。"""
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def get_or_compute(self, key: K, factory: Callable[[], V]) -> tuple[V, bool]:
        """命中缓存返回 (value, True)；未命中调用 factory 计算并写入，返回 (value, False)。"""
        cached = self.get(key)
        if cached is not None:
            return cached, True
        value = factory()
        self.put(key, value)
        return value, False


def text_hash(text: str) -> str:
    """对文本做稳定哈希（sha256 前 16 字符），用于缓存键。

    用 sha256 而非 Python hash()，避免进程间随机化导致缓存键不一致。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---- 全局缓存实例 ----
# query embedding 缓存：键 = (model_version, text_hash)。
query_embedding_cache: LruTtlCache[tuple[str, str], list[float]] = LruTtlCache(
    maxsize=512, ttl_seconds=3600.0
)
# query 改写缓存：键 = (llm_model, query_hash)。
query_rewrite_cache: LruTtlCache[tuple[str, str], list[str]] = LruTtlCache(
    maxsize=256, ttl_seconds=1800.0
)

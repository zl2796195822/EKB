"""M4-4 可观测性：轻量指标采集（纯 stdlib，无外部依赖）。

设计：
  - Counter（只增）/ Histogram（桶 + count + sum），带 label 维度。
  - 全局 Registry 单例，collect() 输出 Prometheus exposition text。
  - 线程安全：SQLite 跨线程复用连接，指标写入用 Lock 保护。
  - 不引入 prometheus_client，保持依赖精简；输出格式兼容 Prometheus 抓取。

采集点：
  - HTTP 中间件：每请求 method/path/status → requests_total + duration histogram。
  - QA 流：finish_reason 分布、检索/生成延迟、降级计数。
  - LLM 调用：provider/status 计数 + 延迟（由 llm.py 记录）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable

# Histogram 默认桶（秒），覆盖检索 <50ms 到问答 P95 <3s 的 NFR 区间。
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _format_labels(labels: dict[str, str]) -> str:
    """把 label dict 格式化为 Prometheus 标签段（key 排序，值转义）。"""
    if not labels:
        return ""
    parts = []
    for key in sorted(labels):
        val = str(labels[key]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{key}="{val}"')
    return "{" + ",".join(parts) + "}"


class _Counter:
    """单调递增计数器，按 label 组合分桶。"""

    def __init__(self, name: str, help_text: str, label_names: Iterable[str] = ()):
        self.name = name
        self.help = help_text
        self.label_names = tuple(label_names)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def _key(self, labels: dict[str, str]) -> tuple[str, ...]:
        return tuple(labels.get(name, "") for name in self.label_names)

    def expose(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            items = list(self._values.items())
        if not items:
            return lines
        for key, val in items:
            labels = dict(zip(self.label_names, key))
            lines.append(f"{self.name}{_format_labels(labels)} {val}")
        return lines


class _Histogram:
    """直方图：按桶累计计数 + 总 count + 总 sum，按 label 组合分桶。"""

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Iterable[str] = (),
        buckets: Iterable[float] = _DEFAULT_BUCKETS,
    ):
        self.name = name
        self.help = help_text
        self.label_names = tuple(label_names)
        self.buckets = tuple(sorted(set(buckets)))
        self._data: dict[tuple[str, ...], dict[str, float]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            bucket = self._data.setdefault(
                key,
                {"count": 0.0, "sum": 0.0, **{f"b_{b}": 0.0 for b in self.buckets}},
            )
            bucket["count"] += 1.0
            bucket["sum"] += value
            for b in self.buckets:
                if value <= b:
                    bucket[f"b_{b}"] += 1.0

    def _key(self, labels: dict[str, str]) -> tuple[str, ...]:
        return tuple(labels.get(name, "") for name in self.label_names)

    def expose(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self._lock:
            items = list(self._data.items())
        if not items:
            return lines
        for key, data in items:
            labels = dict(zip(self.label_names, key))
            for b in self.buckets:
                bucket_labels = {**labels, "le": str(b)}
                lines.append(f"{self.name}_bucket{_format_labels(bucket_labels)} {data[f'b_{b}']}")
            inf_labels = {**labels, "le": "+Inf"}
            lines.append(f"{self.name}_bucket{_format_labels(inf_labels)} {data['count']}")
            lines.append(f"{self.name}_count{_format_labels(labels)} {data['count']}")
            lines.append(f"{self.name}_sum{_format_labels(labels)} {data['sum']}")
        return lines


class _Registry:
    """全局指标注册表单例。"""

    def __init__(self) -> None:
        self._metrics: list[_Counter | _Histogram] = []
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str, label_names: Iterable[str] = ()) -> _Counter:
        with self._lock:
            for m in self._metrics:
                if m.name == name:
                    return m  # type: ignore[return-value]
            c = _Counter(name, help_text, label_names)
            self._metrics.append(c)
            return c

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: Iterable[str] = (),
        buckets: Iterable[float] = _DEFAULT_BUCKETS,
    ) -> _Histogram:
        with self._lock:
            for m in self._metrics:
                if m.name == name:
                    return m  # type: ignore[return-value]
            h = _Histogram(name, help_text, label_names, buckets)
            self._metrics.append(h)
            return h

    def collect(self) -> str:
        """输出 Prometheus exposition 格式文本。"""
        chunks: list[str] = []
        with self._lock:
            metrics = list(self._metrics)
        for m in metrics:
            chunks.extend(m.expose())
            chunks.append("")
        return "\n".join(chunks).rstrip() + "\n"

    def reset(self) -> None:
        """清空所有指标（仅测试用）。"""
        with self._lock:
            self._metrics.clear()


# 全局单例。
registry = _Registry()


# ---- 预定义指标（HTTP + QA + LLM）----
HTTP_REQUESTS = registry.counter(
    "http_requests_total",
    "Total HTTP requests by method/path/status",
    ("method", "path", "status"),
)
HTTP_DURATION = registry.histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("method", "path"),
)
QA_REQUESTS = registry.counter(
    "qa_requests_total",
    "Total QA requests by finish_reason",
    ("finish_reason",),
)
QA_RETRIEVAL_DURATION = registry.histogram(
    "qa_retrieval_duration_seconds",
    "QA retrieval (query rewrite + multi-path search) duration",
)
QA_GENERATION_DURATION = registry.histogram(
    "qa_generation_duration_seconds",
    "QA LLM generation duration",
)
QA_DEGRADATIONS = registry.counter(
    "qa_degradations_total",
    "QA degradation events by reason (llm_unavailable, llm_failed, timeout)",
    ("reason",),
)
# --- SSE v2 Conversation Stream 可观测性指标 ---
# 协议层错误：seq 乱序/turn_id 不匹配/JSON 解码失败/envelope 缺失字段
QA_PROTOCOL_ERRORS = registry.counter(
    "qa_protocol_errors_total",
    "QA SSE protocol errors by type (seq_mismatch, stale_turn, bad_envelope, json_decode)",
    ("type",),
)
# 显式取消计数：区分成功命中 running / 已完成 / 不存在
QA_CANCEL_REQUESTS = registry.counter(
    "qa_cancel_requests_total",
    "QA explicit cancel requests by result (cancelled, already_completed, not_found)",
    ("result",),
)
# QA TTFB（首 token 延迟）：区分 v1/v2 协议
QA_TTFB_DURATION = registry.histogram(
    "qa_ttfb_duration_seconds",
    "QA time-to-first-token (retrieval_started -> first content delta)",
    ("stream_version",),
)
# QA 全回合延迟（request -> done 事件）
QA_TURN_DURATION = registry.histogram(
    "qa_turn_duration_seconds",
    "QA full turn duration (request event -> done event)",
    ("stream_version", "finish_reason"),
)
# QA 端到端 seq/事件丢失告警：done.last_seq vs 实际收到的最大 seq
QA_SEQ_GAPS = registry.counter(
    "qa_seq_gaps_total",
    "QA SSE v2 sequence number gaps detected at turn completion",
)
# QA 心跳发送计数（检测代理/中间层断连频率）
QA_HEARTBEATS_SENT = registry.counter(
    "qa_heartbeats_sent_total",
    "QA SSE v2 heartbeat events sent per turn",
    ("stream_version",),
)
# QA delta 合并 flush 原因统计（tokens/bytes/time）
QA_DELTA_FLUSHES = registry.counter(
    "qa_delta_flushes_total",
    "QA SSE v2 delta merge flushes by trigger (tokens, bytes, time)",
    ("trigger",),
)
# QA 分段空闲超时命中计数
QA_IDLE_TIMEOUTS = registry.counter(
    "qa_idle_timeouts_total",
    "QA SSE v2 segmented idle timeouts hit by phase (retrieval, generation)",
    ("phase",),
)
LLM_CALLS = registry.counter(
    "llm_calls_total",
    "Total LLM calls by provider and status",
    ("provider", "status"),
)
LLM_DURATION = registry.histogram(
    "llm_call_duration_seconds",
    "LLM call duration by provider",
    ("provider",),
)
CACHE_HITS = registry.counter(
    "cache_hits_total",
    "Cache hits by type (embedding, rewrite)",
    ("type",),
)
CACHE_MISSES = registry.counter(
    "cache_misses_total",
    "Cache misses by type (embedding, rewrite)",
    ("type",),
)


class Timer:
    """上下文管理器 / 计时器，observe 到指定 histogram。"""

    def __init__(self, histogram: _Histogram, **labels: str):
        self._histogram = histogram
        self._labels = labels
        self._start = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        elapsed = time.perf_counter() - self._start
        self._histogram.observe(elapsed, **self._labels)

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._start

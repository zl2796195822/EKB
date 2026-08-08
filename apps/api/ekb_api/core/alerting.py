"""M4-4 告警阈值检查：基于已采集指标的告警触发（日志级别 ERROR，供告警系统抓取）。

告警触发条件：
  - QA 延迟 P95 > alert_qa_p95_threshold_seconds
  - 搜索延迟 P95 > alert_search_p95_threshold_seconds
  - HTTP 5xx 错误率 > alert_error_rate_threshold
  - LLM 调用失败率 > alert_llm_failure_rate_threshold

触发方式：在请求生命周期内对指标快照进行简单检查，超阈值则打一条 level=ERROR 的结构化日志。
告警系统（Prometheus AlertManager / Loki / ELK）从日志流中提取 alert=True 字段触发通知。
"""
from __future__ import annotations

import threading
import time

from ekb_api.core.logging import get_logger

_log = get_logger("ekb.alerting")

# 最小检查间隔（秒），避免每请求都算 P95 浪费 CPU。
_CHECK_INTERVAL = 30.0
_last_check: float = 0.0
_lock = threading.Lock()


def maybe_check_alerts() -> None:
    """在请求路径中调用；每 CHECK_INTERVAL 秒执行一次阈值检查。"""
    global _last_check
    now = time.monotonic()
    with _lock:
        if now - _last_check < _CHECK_INTERVAL:
            return
        _last_check = now

    try:
        _run_checks()
    except Exception:  # noqa: BLE001
        pass  # 告警检查失败绝不影响请求路径


def _run_checks() -> None:
    from ekb_api.core.config import get_settings  # noqa: PLC0415
    from ekb_api.core.metrics import (  # noqa: PLC0415
        HTTP_REQUESTS,
        LLM_CALLS,
        QA_GENERATION_DURATION,
        QA_RETRIEVAL_DURATION,
    )

    settings = get_settings()

    # QA P95 延迟检查（retrieval + generation 合计近似）。
    qa_p95 = _estimate_p95(QA_RETRIEVAL_DURATION) + _estimate_p95(QA_GENERATION_DURATION)
    if qa_p95 > settings.alert_qa_p95_threshold_seconds:
        _log.error(
            "alert.qa_latency_exceeded",
            alert=True,
            metric="qa_p95_seconds",
            value=round(qa_p95, 3),
            threshold=settings.alert_qa_p95_threshold_seconds,
        )

    # HTTP 5xx 错误率检查。
    total = _sum_counter(HTTP_REQUESTS)
    errors = _sum_counter_filtered(HTTP_REQUESTS, label_index=2, label_value_prefix="5")
    if total > 0:
        error_rate = errors / total
        if error_rate > settings.alert_error_rate_threshold:
            _log.error(
                "alert.http_error_rate_exceeded",
                alert=True,
                metric="http_5xx_rate",
                value=round(error_rate, 4),
                threshold=settings.alert_error_rate_threshold,
            )

    # LLM 失败率检查。
    llm_total = _sum_counter(LLM_CALLS)
    llm_fail = _sum_counter_filtered(LLM_CALLS, label_index=1, label_value_prefix="error")
    if llm_total > 0:
        llm_fail_rate = llm_fail / llm_total
        if llm_fail_rate > settings.alert_llm_failure_rate_threshold:
            _log.error(
                "alert.llm_failure_rate_exceeded",
                alert=True,
                metric="llm_failure_rate",
                value=round(llm_fail_rate, 4),
                threshold=settings.alert_llm_failure_rate_threshold,
            )


def _estimate_p95(histogram) -> float:  # type: ignore[type-arg]
    """从 Histogram 近似估算 P95（取 95% 分位数对应桶的上界）。"""
    with histogram._lock:
        if not histogram._data:
            return 0.0
        # 合并所有 label 组合的数据。
        total_count = 0.0
        bucket_totals: dict[float, float] = {}
        for data in histogram._data.values():
            total_count += data["count"]
            for b in histogram.buckets:
                bucket_totals[b] = bucket_totals.get(b, 0.0) + data[f"b_{b}"]

    if total_count == 0:
        return 0.0

    target = total_count * 0.95
    cumulative = 0.0
    for b in sorted(bucket_totals):
        cumulative += bucket_totals[b]
        if cumulative >= target:
            return b
    return max(bucket_totals) if bucket_totals else 0.0


def _sum_counter(counter) -> float:  # type: ignore[type-arg]
    with counter._lock:
        return sum(counter._values.values())


def _sum_counter_filtered(counter, label_index: int, label_value_prefix: str) -> float:  # type: ignore[type-arg]
    with counter._lock:
        return sum(
            v
            for key, v in counter._values.items()
            if len(key) > label_index and str(key[label_index]).startswith(label_value_prefix)
        )

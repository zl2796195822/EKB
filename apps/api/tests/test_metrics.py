"""M4-4 可观测性：指标采集与 /metrics 端点测试。"""

from __future__ import annotations

import os

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DEV_USER_EMAIL", "admin@example.com")
os.environ.setdefault("EKB_DEV_PASSWORD", "test-password")
os.environ.setdefault("EKB_TOKEN_SECRET", "test-only-token-secret")
os.environ.setdefault("EKB_DATABASE_URL", "sqlite:///./ekb_test.db")

from fastapi.testclient import TestClient

from ekb_api.core.metrics import _Histogram, registry
from ekb_api.main import _normalize_path, app

client = TestClient(app)


def test_counter_inc_and_expose() -> None:
    """Counter 累加 + Prometheus 文本格式（含 HELP/TYPE/label）。"""
    c = registry.counter(
        "test_counter_xyz",
        "test counter",
        ("method",),
    )
    c.inc(method="GET")
    c.inc(2.0, method="GET")
    lines = c.expose()
    assert "# HELP test_counter_xyz test counter" in lines
    assert "# TYPE test_counter_xyz counter" in lines
    # 累加为 3.0
    assert any('test_counter_xyz{method="GET"} 3.0' in line for line in lines)


def test_histogram_observe_and_expose() -> None:
    """Histogram 桶累计 + count/sum + +Inf 桶。"""
    h = registry.histogram(
        "test_hist_xyz",
        "test histogram",
        buckets=(0.1, 1.0),
    )
    h.observe(0.05)
    h.observe(0.5)
    h.observe(2.0)
    lines = h.expose()
    text = "\n".join(lines)
    # le=0.1 命中 1 个（0.05），le=1.0 命中 2 个，+Inf 命中 3 个
    assert 'test_hist_xyz_bucket{le="0.1"} 1.0' in text
    assert 'test_hist_xyz_bucket{le="1.0"} 2.0' in text
    assert 'test_hist_xyz_bucket{le="+Inf"} 3.0' in text
    assert "test_hist_xyz_count 3.0" in text
    # sum = 0.05 + 0.5 + 2.0 = 2.55
    assert "test_hist_xyz_sum 2.55" in text


def test_histogram_direct_class() -> None:
    """_Histogram 直接实例化（无 label）也能 expose。"""
    h = _Histogram("test_direct_hist", "direct", buckets=(0.5,))
    h.observe(0.3)
    text = "\n".join(h.expose())
    assert 'test_direct_hist_bucket{le="0.5"} 1.0' in text
    assert "test_direct_hist_count 1.0" in text


def test_metrics_endpoint_returns_prometheus_text() -> None:
    """/metrics 返回 200 + text/plain + 含 TYPE 声明。"""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    # 至少有一个指标类型声明
    assert "# TYPE" in body
    # 预定义指标存在
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


def test_http_middleware_records_request() -> None:
    """发请求后 http_requests_total 对应 path 计数增长。"""
    before = client.get("/metrics").text
    # 触发一次 /healthz 请求
    client.get("/healthz")
    after = client.get("/metrics").text

    # 解析 /healthz path 的计数：after 应严格大于 before（至少 +1 来自 healthz 本身）
    def _healthz_count(text: str) -> int:
        total = 0
        for line in text.splitlines():
            if "http_requests_total" in line and 'path="/healthz"' in line:
                # 末尾数字是计数值
                total += float(line.rsplit(" ", 1)[-1])
        return int(total)

    assert _healthz_count(after) > _healthz_count(before)


def test_normalize_path_replaces_hex_ids() -> None:
    """路径中的 hex id 段（≥12 字符）归一化为 :id。"""
    assert _normalize_path("/api/v1/kb") == "/api/v1/kb"
    assert _normalize_path("/api/v1/kb/abc123def456abc123def456abc123de/docs") == (
        "/api/v1/kb/:id/docs"
    )
    # 短段不归一化
    assert _normalize_path("/api/v1/kb/abc") == "/api/v1/kb/abc"


def test_registry_collect_has_all_predefined() -> None:
    """registry.collect() 输出包含全部预定义指标。"""
    text = registry.collect()
    for name in (
        "http_requests_total",
        "http_request_duration_seconds",
        "qa_requests_total",
        "qa_retrieval_duration_seconds",
        "qa_generation_duration_seconds",
        "qa_degradations_total",
        "llm_calls_total",
        "llm_call_duration_seconds",
        "cache_hits_total",
        "cache_misses_total",
    ):
        assert name in text, f"missing metric: {name}"

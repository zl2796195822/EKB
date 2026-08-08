#!/usr/bin/env python3
"""M4-7 容量压测脚本：验证 Spec 第 10 章 NFR 冻结指标。

用法:
    .venv/bin/python apps/api/scripts/load_test.py \
        --base-url http://127.0.0.1:8000 \
        --email admin@example.com --password test-password \
        --duration 60 --concurrency 10

NFR 目标（Spec 第 10 章）:
    - 全新问答 P95 < 3s
    - 高频命中 < 0.5s
    - 单节点检索 < 50ms
    - 目标并发 ≥ 50 QPS（横向扩展，非单机能力）

输出指标:
    - P50/P95/P99 延迟（首事件 + 完整回答）
    - 吞吐（QPS）
    - 错误率（非 200 响应比例）
    - 降级率（finish_reason != stop 比例）
    - 检索延迟 P95

压测策略:
    - 并发用户模拟：asyncio + httpx.AsyncClient
    - 混合负载：70% QA（/qa/ask SSE）+ 30% 检索（/search）
    - 持续时长可配置，默认 60s
    - 结果输出 JSON + Markdown 报告
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class RequestResult:
    """单次请求结果。"""

    endpoint: str  # "qa" 或 "search"
    status_code: int
    latency_ms: float
    first_event_ms: float | None
    finish_reason: str | None
    error: str | None = None


@dataclass
class LoadTestReport:
    """压测报告。"""

    duration_seconds: float
    concurrency: int
    total_requests: int
    results: list[RequestResult] = field(default_factory=list)

    @property
    def qa_results(self) -> list[RequestResult]:
        return [r for r in self.results if r.endpoint == "qa"]

    @property
    def search_results(self) -> list[RequestResult]:
        return [r for r in self.results if r.endpoint == "search"]

    def _percentile(self, values: list[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * p / 100)
        idx = min(idx, len(sorted_vals) - 1)
        return sorted_vals[idx]

    def metrics(self) -> dict:
        qa = self.qa_results
        search = self.search_results
        all_latencies = [r.latency_ms for r in self.results]
        qa_latencies = [r.latency_ms for r in qa]
        search_latencies = [r.latency_ms for r in search]
        qa_first_events = [r.first_event_ms for r in qa if r.first_event_ms is not None]

        total = len(self.results)
        errors = sum(1 for r in self.results if r.status_code != 200)
        # 降级：finish_reason 不是 stop（timeout/refusal/cancelled/error）
        degraded = sum(1 for r in qa if r.finish_reason and r.finish_reason not in ("stop",))

        qps = total / self.duration_seconds if self.duration_seconds > 0 else 0

        return {
            "总请求数": total,
            "持续时长_s": round(self.duration_seconds, 1),
            "并发数": self.concurrency,
            "吞吐_QPS": round(qps, 1),
            "错误率_%": round(errors / total * 100, 2) if total else 0,
            "降级率_%": round(degraded / len(qa) * 100, 2) if qa else 0,
            "QA_P50_ms": round(self._percentile(qa_latencies, 50), 1),
            "QA_P95_ms": round(self._percentile(qa_latencies, 95), 1),
            "QA_P99_ms": round(self._percentile(qa_latencies, 99), 1),
            "QA首事件_P50_ms": round(self._percentile(qa_first_events, 50), 1),
            "QA首事件_P95_ms": round(self._percentile(qa_first_events, 95), 1),
            "Search_P50_ms": round(self._percentile(search_latencies, 50), 1),
            "Search_P95_ms": round(self._percentile(search_latencies, 95), 1),
            "全量_P95_ms": round(self._percentile(all_latencies, 95), 1),
        }


def login(base_url: str, email: str, password: str) -> str:
    resp = httpx.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def list_knowledge_bases(base_url: str, token: str) -> list[dict]:
    resp = httpx.get(
        f"{base_url}/api/v1/kb",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# 脱敏问题池（覆盖 seed-v1 各 SOP 场景）
QUESTION_POOL = [
    "数据库连接池耗尽时应该先检查哪些指标？",
    "Redis 内存溢出时先用什么命令排查？",
    "网络延迟突增时用什么工具分段定位？",
    "发布后什么情况下应立即触发回滚？",
    "确认主机被入侵后第一步做什么？",
    "连接池持续耗尽时如何恢复？",
    "Redis 淘汰策略 allkeys-lru 能起什么作用？",
    "延迟只在首次请求出现应排查什么？",
    "回滚执行时先做什么再做什么？",
    "安全事件取证的采集顺序是什么？",
]

SEARCH_POOL = [
    "连接池使用率",
    "Redis info memory",
    "mtr 延迟定位",
    "回滚判定 5 分钟",
    "内存镜像 磁盘镜像",
    "trace_id 复盘",
    "allkeys-lru 短期缓解",
    "DNS resolver 缓存命中",
    "forward-compatibility",
    "检测规则 访问策略",
]


async def run_qa_request(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    kb_id: str,
    question: str,
) -> RequestResult:
    """发起一次 QA SSE 请求，解析事件并计时。"""
    start = time.perf_counter()
    first_event_ts: float | None = None
    finish_reason: str | None = None
    status_code = 200
    error: str | None = None

    try:
        async with client.stream(
            "POST",
            f"{base_url}/api/v1/qa/ask",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={"question": question, "kb_ids": [kb_id]},
            timeout=30,
        ) as response:
            status_code = response.status_code
            event_type = ""
            async for line in response.aiter_lines():
                if not line:
                    event_type = ""
                    continue
                if line.startswith("event:"):
                    event_type = line[len("event:") :].strip()
                    if first_event_ts is None:
                        first_event_ts = time.perf_counter()
                    continue
                if line.startswith("data:") and event_type == "done":
                    payload = json.loads(line[len("data:") :])
                    finish_reason = payload.get("finish_reason")
    except Exception as exc:
        error = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000
    first_event_ms = (
        (first_event_ts - start) * 1000 if first_event_ts is not None else None
    )
    return RequestResult(
        endpoint="qa",
        status_code=status_code,
        latency_ms=latency_ms,
        first_event_ms=first_event_ms,
        finish_reason=finish_reason,
        error=error,
    )


async def run_search_request(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    query: str,
) -> RequestResult:
    """发起一次检索请求并计时。"""
    start = time.perf_counter()
    status_code = 200
    error: str | None = None

    try:
        resp = await client.post(
            f"{base_url}/api/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "top_k": 5},
            timeout=15,
        )
        status_code = resp.status_code
    except Exception as exc:
        error = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000
    return RequestResult(
        endpoint="search",
        status_code=status_code,
        latency_ms=latency_ms,
        first_event_ms=None,
        finish_reason=None,
        error=error,
    )


async def worker(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    kb_id: str,
    duration: float,
    stop_time: float,
    result_collector: list[RequestResult],
    worker_id: int,
) -> None:
    """单个并发 worker，持续发起混合请求直到时长耗尽。"""
    import random

    counter = 0
    while time.perf_counter() < stop_time:
        # 70% QA + 30% search
        if random.random() < 0.7:
            question = QUESTION_POOL[counter % len(QUESTION_POOL)]
            result = await run_qa_request(client, base_url, token, kb_id, question)
        else:
            query = SEARCH_POOL[counter % len(SEARCH_POOL)]
            result = await run_search_request(client, base_url, token, query)
        result_collector.append(result)
        counter += 1


async def run_load_test(
    base_url: str,
    token: str,
    kb_id: str,
    duration: int,
    concurrency: int,
) -> LoadTestReport:
    """执行压测。"""
    results: list[RequestResult] = []
    start = time.perf_counter()
    stop_time = start + duration

    async with httpx.AsyncClient(trust_env=False) as client:
        tasks = [
            worker(client, base_url, token, kb_id, duration, stop_time, results, i)
            for i in range(concurrency)
        ]
        await asyncio.gather(*tasks)

    actual_duration = time.perf_counter() - start
    return LoadTestReport(
        duration_seconds=actual_duration,
        concurrency=concurrency,
        total_requests=len(results),
        results=results,
    )


def render_markdown(report: LoadTestReport, nfr_targets: dict) -> str:
    """渲染 Markdown 压测报告。"""
    m = report.metrics()
    lines = [
        "# EKB M4-7 容量压测报告",
        "",
        f"- 持续时长: {m['持续时长_s']}s",
        f"- 并发数: {m['并发数']}",
        f"- 总请求数: {m['总请求数']}",
        "",
        "## NFR 指标（Spec 第 10 章冻结目标）",
        "",
        "| 指标 | 实测值 | NFR 目标 | 判定 |",
        "|---|---|---|---|",
    ]

    checks = [
        ("QA P95 延迟", m["QA_P95_ms"], nfr_targets.get("qa_p95_ms", 3000), "ms", "<"),
        ("QA 首事件 P95", m["QA首事件_P95_ms"], nfr_targets.get("qa_first_p95_ms", 500), "ms", "<"),
        ("Search P95 延迟", m["Search_P95_ms"], nfr_targets.get("search_p95_ms", 50), "ms", "<"),
        ("吞吐 QPS", m["吞吐_QPS"], nfr_targets.get("qps", 50), "QPS", ">="),
        ("错误率", m["错误率_%"], nfr_targets.get("error_rate_pct", 1.0), "%", "<"),
    ]

    for name, actual, target, unit, op in checks:
        if op == "<":
            passed = actual < target
        else:
            passed = actual >= target
        judge = "PASS" if passed else "FAIL"
        lines.append(
            f"| {name} | {actual} {unit} | {op} {target} {unit} | {judge} |"
        )

    lines.extend([
        "",
        "## 详细指标",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
    ])
    for k, v in m.items():
        lines.append(f"| {k} | {v} |")

    # NFR 整体判定
    all_pass = all(
        (actual < target if op == "<" else actual >= target)
        for _, actual, target, _, op in checks
    )
    lines.extend([
        "",
        f"## 整体判定: {'PASS' if all_pass else 'FAIL'}",
        "",
    ])

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="EKB M4-7 容量压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="test-password")
    parser.add_argument("--duration", type=int, default=60, help="压测持续时长（秒）")
    parser.add_argument("--concurrency", type=int, default=10, help="并发用户数")
    parser.add_argument("--out-dir", default="eval/reports", help="报告输出目录")
    args = parser.parse_args()

    # 登录 + 获取 KB
    token = login(args.base_url, args.email, args.password)
    kbs = list_knowledge_bases(args.base_url, token)
    if not kbs:
        print("ERROR: 没有可用知识库，请先种子化数据")
        return
    kb_id = kbs[0]["id"]
    print(f"压测目标: {args.base_url}, KB={kb_id}")
    print(f"参数: 并发={args.concurrency}, 时长={args.duration}s")

    # 执行压测
    report = asyncio.run(
        run_load_test(args.base_url, token, kb_id, args.duration, args.concurrency)
    )

    # NFR 目标（Spec 第 10 章）
    nfr_targets = {
        "qa_p95_ms": 3000,
        "qa_first_p95_ms": 500,
        "search_p95_ms": 50,
        "qps": 50,
        "error_rate_pct": 1.0,
    }

    # 输出结果
    m = report.metrics()
    print("\n=== 压测结果 ===")
    for k, v in m.items():
        print(f"  {k}: {v}")

    # 保存报告
    from pathlib import Path

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    json_path = out_dir / f"loadtest_{timestamp}.json"
    json_path.write_text(
        json.dumps({"metrics": m, "nfr_targets": nfr_targets}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_path = out_dir / f"M4-7_容量压测报告_{timestamp}.md"
    md_path.write_text(render_markdown(report, nfr_targets), encoding="utf-8")

    print("\n报告已保存:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")


if __name__ == "__main__":
    main()

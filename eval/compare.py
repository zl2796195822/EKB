#!/usr/bin/env python3
"""M4-5 版本对比工具：对比两份评估结果 JSON，检测回归和提升。

用法:
    python -m eval.compare --baseline eval/reports/eval_result_old.json \
        --candidate eval/reports/eval_result_new.json

输出:
    - 指标 diff（提升/回归/持平）
    - 题目级 diff（新通过/新失败/状态变化）
    - 回归告警（准确率/拒答率/引用正确率回退）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _fmt_value(v) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float) and v <= 1:
        return f"{v * 100:.1f}%"
    return str(v)


def _delta(old, new) -> float | None:
    """计算提升幅度（百分点）。None 表示无法比较。"""
    if old is None or new is None:
        return None
    if isinstance(old, (int, float)) and isinstance(new, (int, float)) and old <= 1 and new <= 1:
        return round((new - old) * 100, 1)
    return None


def compare_metrics(baseline: dict, candidate: dict) -> list[dict]:
    """对比指标，返回每个指标的变化。"""
    old_m = baseline.get("metrics", {})
    new_m = candidate.get("metrics", {})
    all_keys = sorted(set(old_m) | set(new_m))

    results = []
    for key in all_keys:
        old_v = old_m.get(key)
        new_v = new_m.get(key)
        delta = _delta(old_v, new_v)

        if delta is None:
            status = "incomparable"
        elif delta > 0:
            status = "improved"
        elif delta < 0:
            status = "regressed"
        else:
            status = "unchanged"

        results.append({
            "metric": key,
            "baseline": old_v,
            "candidate": new_v,
            "delta_pp": delta,
            "status": status,
        })
    return results


def compare_details(baseline: dict, candidate: dict) -> dict:
    """对比题目级结果，返回新通过/新失败/状态变化。"""
    old_details = {d["id"]: d for d in baseline.get("details", [])}
    new_details = {d["id"]: d for d in candidate.get("details", [])}

    # 也支持 golden_details（golden_no_answer 格式）
    if not old_details and "golden_details" in baseline:
        old_details = {d["id"]: d for d in baseline.get("golden_details", [])}
    if not new_details and "golden_details" in candidate:
        new_details = {d["id"]: d for d in candidate.get("golden_details", [])}

    all_ids = sorted(set(old_details) | set(new_details))

    newly_passed = []
    newly_failed = []
    changed = []

    for qid in all_ids:
        old = old_details.get(qid, {})
        new = new_details.get(qid, {})

        old_pass = _get_pass_status(old)
        new_pass = _get_pass_status(new)

        if old_pass is None or new_pass is None:
            continue

        if new_pass and not old_pass:
            newly_passed.append(qid)
        elif not new_pass and old_pass:
            newly_failed.append(qid)
        elif old_pass != new_pass:
            changed.append({"id": qid, "old": old_pass, "new": new_pass})

    return {
        "newly_passed": newly_passed,
        "newly_failed": newly_failed,
        "changed": changed,
    }


def _get_pass_status(detail: dict) -> bool | None:
    """从 detail 提取通过状态。支持 golden/permission/freshness/adversarial 格式。"""
    if "answer_correct" in detail:
        return detail["answer_correct"]
    if "passed" in detail:
        return detail["passed"]
    if "refused" in detail:
        return detail["refused"]
    return None


def detect_regressions(metric_comparison: list[dict], thresholds: dict | None = None) -> list[str]:
    """检测关键指标回归。"""
    if thresholds is None:
        thresholds = {
            "首答准确率": -2.0,  # 回退超 2 个百分点告警
            "引用正确率": -1.0,
            "拒答率": -5.0,
            "越权拦截率": -5.0,
            "注入拦截率": -5.0,
        }

    alerts = []
    for m in metric_comparison:
        key = m["metric"]
        delta = m["delta_pp"]
        threshold = thresholds.get(key)
        if threshold is not None and delta is not None and delta < threshold:
            alerts.append(
                f"⚠️ 回归告警: {key} 回退 {abs(delta)} 个百分点 "
                f"(基线 {_fmt_value(m['baseline'])} → 候选 {_fmt_value(m['candidate'])})"
            )
    return alerts


def render_report(baseline: dict, candidate: dict, metric_comparison: list[dict],
                  detail_comparison: dict, alerts: list[str]) -> str:
    """渲染对比报告 Markdown。"""
    lines = [
        "# EKB M4-5 评估版本对比报告",
        "",
        f"- 基线评估集版本: {baseline.get('dataset_version', 'N/A')}",
        f"- 候选评估集版本: {candidate.get('dataset_version', 'N/A')}",
        f"- 基线类别: {baseline.get('category', 'golden_no_answer')}",
        f"- 候选类别: {candidate.get('category', 'golden_no_answer')}",
        "",
        "## 1. 指标对比",
        "",
        "| 指标 | 基线 | 候选 | 变化(百分点) | 状态 |",
        "|---|---|---|---|---|",
    ]
    for m in metric_comparison:
        delta_str = f"{m['delta_pp']:+.1f}" if m["delta_pp"] is not None else "N/A"
        status_icon = {
            "improved": "📈",
            "regressed": "📉",
            "unchanged": "➡️",
            "incomparable": "❓",
        }.get(m["status"], "")
        lines.append(
            f"| {m['metric']} | {_fmt_value(m['baseline'])} | "
            f"{_fmt_value(m['candidate'])} | {delta_str} | {status_icon} {m['status']} |"
        )

    lines.extend(["", "## 2. 题目级变化", ""])
    lines.append(f"- 新通过: {len(detail_comparison['newly_passed'])} 题")
    if detail_comparison["newly_passed"]:
        lines.append(f"  - IDs: {', '.join(detail_comparison['newly_passed'])}")
    lines.append(f"- 新失败: {len(detail_comparison['newly_failed'])} 题")
    if detail_comparison["newly_failed"]:
        lines.append(f"  - IDs: {', '.join(detail_comparison['newly_failed'])}")
    lines.append(f"- 状态变化: {len(detail_comparison['changed'])} 题")

    lines.extend(["", "## 3. 回归告警", ""])
    if alerts:
        for alert in alerts:
            lines.append(alert)
    else:
        lines.append("✅ 无回归告警，所有关键指标未超阈值回退。")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="EKB 评估版本对比工具")
    parser.add_argument("--baseline", type=Path, required=True, help="基线评估结果 JSON")
    parser.add_argument("--candidate", type=Path, required=True, help="候选评估结果 JSON")
    parser.add_argument("--out", type=Path, default=None, help="输出 Markdown 报告路径")
    args = parser.parse_args()

    baseline = _load(args.baseline)
    candidate = _load(args.candidate)

    metric_comparison = compare_metrics(baseline, candidate)
    detail_comparison = compare_details(baseline, candidate)
    alerts = detect_regressions(metric_comparison)

    report = render_report(baseline, candidate, metric_comparison, detail_comparison, alerts)

    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"对比报告: {args.out}")
    else:
        print(report)

    # 有回归告警时返回非零退出码
    if alerts:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

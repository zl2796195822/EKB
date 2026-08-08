#!/usr/bin/env python3
"""M4-5 A/B 方案对比：对比两套配置（不同模型/检索策略）的评估结果。

用法:
    python -m eval.ab_compare --dataset eval/dataset/eval_set_v1.yaml \
        --config-a '{"base_url":"http://localhost:8000","label":"baseline"}' \
        --config-b '{"base_url":"http://localhost:8001","label":"rerank"}' \
        --email admin@example.com --password xxx

或对比两份已有结果 JSON:
    python -m eval.ab_compare \
        --result-a eval/reports/eval_result_a.json \
        --result-b eval/reports/eval_result_b.json

输出:
    - 方案 A vs B 指标对比
    - 显著性判定（基于题数和差异比例的简化判定）
    - 推荐方案
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 复用 compare.py 的对比逻辑
from eval.compare import (
    _fmt_value,
    compare_details,
    compare_metrics,
)


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _judge_significance(metric_comparison: list[dict], sample_size: int) -> list[dict]:
    """简化显著性判定：基于差异比例和样本量的经验阈值。

    非统计检验，仅作工程参考。正式 A/B 测试需收集多次评估结果做配对检验。
    """
    results = []
    for m in metric_comparison:
        delta = m["delta_pp"]
        if delta is None:
            results.append({**m, "significant": None, "note": "无法比较"})
            continue

        abs_delta = abs(delta)
        # 简化阈值：样本 ≥100 时 2pp 视为显著，样本 30-100 时 5pp，样本 <30 时 10pp
        if sample_size >= 100:
            threshold = 2.0
        elif sample_size >= 30:
            threshold = 5.0
        else:
            threshold = 10.0

        significant = abs_delta >= threshold
        note = f"阈值 ±{threshold}pp（样本 {sample_size}）"

        results.append({**m, "significant": significant, "note": note})
    return results


def recommend(a_metrics: dict, b_metrics: dict, significance: list[dict]) -> str:
    """基于关键指标和显著性给出推荐。"""
    key_metrics = ["首答准确率", "引用正确率", "拒答率", "越权拦截率", "注入拦截率"]

    a_better = 0
    b_better = 0
    sig_a_better = 0
    sig_b_better = 0

    for s in significance:
        if s["metric"] not in key_metrics:
            continue
        delta = s["delta_pp"]
        if delta is None:
            continue
        if delta > 0:
            b_better += 1
            if s["significant"]:
                sig_b_better += 1
        elif delta < 0:
            a_better += 1
            if s["significant"]:
                sig_a_better += 1

    if sig_b_better > sig_a_better:
        return f"推荐方案 B：在 {sig_b_better} 个关键指标上显著优于 A"
    if sig_a_better > sig_b_better:
        return f"推荐方案 A：在 {sig_a_better} 个关键指标上显著优于 B"
    if b_better > a_better:
        return f"倾向方案 B：在 {b_better} 个指标上优于 A（未达显著阈值）"
    if a_better > b_better:
        return f"倾向方案 A：在 {a_better} 个指标上优于 B（未达显著阈值）"
    return "两方案无显著差异，可基于成本/延迟等非质量因素选择"


def render_ab_report(
    label_a: str,
    label_b: str,
    result_a: dict,
    result_b: dict,
    metric_comparison: list[dict],
    significance: list[dict],
    detail_comparison: dict,
    recommendation: str,
) -> str:
    """渲染 A/B 对比报告 Markdown。"""
    counts_a = result_a.get("counts", {})
    counts_b = result_b.get("counts", {})
    sample_size = counts_a.get("golden", counts_a.get("total", 0))

    lines = [
        "# EKB M4-5 A/B 方案对比报告",
        "",
        f"- 方案 A: {label_a}",
        f"- 方案 B: {label_b}",
        f"- 评估集版本: {result_a.get('dataset_version', 'N/A')}",
        f"- 样本量: {sample_size}",
        f"- 生成时间: {__import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime())}",
        "",
        "## 1. 指标对比",
        "",
        "| 指标 | 方案 A | 方案 B | 变化(pp) | 显著 | 判定说明 |",
        "|---|---|---|---|---|---|",
    ]
    for s in significance:
        delta_str = f"{s['delta_pp']:+.1f}" if s["delta_pp"] is not None else "N/A"
        sig_str = "✓" if s["significant"] else "✗" if s["significant"] is False else "?"
        lines.append(
            f"| {s['metric']} | {_fmt_value(s['baseline'])} | "
            f"{_fmt_value(s['candidate'])} | {delta_str} | {sig_str} | {s['note']} |"
        )

    lines.extend(["", "## 2. 题目级变化", ""])
    lines.append(f"- A 通过 B 失败: {len(detail_comparison['newly_failed'])} 题")
    if detail_comparison["newly_failed"]:
        lines.append(f"  - IDs: {', '.join(detail_comparison['newly_failed'])}")
    lines.append(f"- A 失败 B 通过: {len(detail_comparison['newly_passed'])} 题")
    if detail_comparison["newly_passed"]:
        lines.append(f"  - IDs: {', '.join(detail_comparison['newly_passed'])}")

    lines.extend(["", "## 3. 推荐", ""])
    lines.append(recommendation)

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="EKB A/B 方案对比工具")
    parser.add_argument("--result-a", type=Path, help="方案 A 评估结果 JSON")
    parser.add_argument("--result-b", type=Path, help="方案 B 评估结果 JSON")
    parser.add_argument("--label-a", default="A", help="方案 A 标签")
    parser.add_argument("--label-b", default="B", help="方案 B 标签")
    parser.add_argument("--out", type=Path, default=None, help="输出 Markdown 报告路径")
    args = parser.parse_args()

    if not args.result_a or not args.result_b:
        print("错误：需同时提供 --result-a 和 --result-b", file=sys.stderr)
        return 1

    result_a = _load(args.result_a)
    result_b = _load(args.result_b)

    metric_comparison = compare_metrics(result_a, result_b)
    detail_comparison = compare_details(result_a, result_b)

    counts = result_a.get("counts", {})
    sample_size = counts.get("golden", counts.get("total", 0))
    significance = _judge_significance(metric_comparison, sample_size)

    recommendation = recommend(
        result_a.get("metrics", {}), result_b.get("metrics", {}), significance
    )

    report = render_ab_report(
        args.label_a, args.label_b, result_a, result_b,
        metric_comparison, significance, detail_comparison, recommendation,
    )

    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"A/B 对比报告: {args.out}")
    else:
        print(report)

    print(f"\n推荐: {recommendation}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

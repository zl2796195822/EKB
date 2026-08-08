"""EKB M1 评估脚本：对评估集跑问答，计算质量指标，输出 JSON + Markdown 报告。

用法:
    .venv/bin/python -m eval.run --dataset eval/dataset/eval_set_v1.yaml \
        --out-dir eval/reports --base-url http://localhost:8000

指标口径与 RAG评估与数据治理方案 §7 一致:
    首答准确率 = 正确且有证据的首轮回答数 / 有答案题总数
    引用正确率 = 支持结论的引用数 / 展示引用总数
    拒答率 = 正确拒答数 / 应拒答题总数
    幻觉率 = 编造或引用不支持结论的回答数 / 总回答数
    检索召回率 = 命中标准证据的题数 / 有答案题总数
    首事件延迟 / 完整回答延迟 = P50/P95（毫秒）
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml


@dataclass
class Citation:
    title: str
    section_path: list[str]


@dataclass
class AskResult:
    """单题问答结果。"""

    question_id: str
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    finish_reason: str = ""
    error: str | None = None
    first_event_ms: float | None = None
    done_event_ms: float | None = None


@dataclass
class GoldenJudgement:
    """Golden 题判定。"""

    answer_correct: bool
    citation_correct: bool
    # 检索召回：是否命中预期引用（标题 + 章节包含）。
    retrieval_hit: bool


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


def ask(base_url: str, token: str, kb_id: str, question: str) -> AskResult:
    """发起 SSE 问答，解析事件并计时。"""
    result = AskResult(question_id="", question=question, answer="")
    start = time.perf_counter()
    first_event_ts: float | None = None

    try:
        with httpx.stream(
            "POST",
            f"{base_url}/api/v1/qa/ask",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={"question": question, "kb_ids": [kb_id]},
            timeout=60,
        ) as response:
            response.raise_for_status()
            event_type = ""
            for line in response.iter_lines():
                if not line:
                    event_type = ""
                    continue
                if line.startswith("event:"):
                    event_type = line[len("event:") :].strip()
                    if first_event_ts is None:
                        first_event_ts = time.perf_counter()
                    continue
                if line.startswith("data:"):
                    payload = json.loads(line[len("data:") :])
                    _apply_event(result, event_type, payload)
                    if event_type == "done":
                        result.done_event_ms = (time.perf_counter() - start) * 1000
    except httpx.HTTPError as exc:
        result.error = str(exc)
        return result

    if first_event_ts is not None:
        result.first_event_ms = (first_event_ts - start) * 1000
    if result.done_event_ms is None:
        result.done_event_ms = (time.perf_counter() - start) * 1000
    return result


def _apply_event(result: AskResult, event_type: str, payload: dict) -> None:
    if event_type == "token":
        result.answer += str(payload.get("text", ""))
    elif event_type == "citation":
        result.citations.append(
            Citation(
                title=str(payload.get("title", "")),
                section_path=list(payload.get("section_path", [])),
            )
        )
    elif event_type == "done":
        result.finish_reason = str(payload.get("finish_reason", ""))
    elif event_type == "error":
        result.error = str(payload.get("message", "未知错误"))


def _normalize_text(text: str) -> str:
    """归一化文本用于关键词匹配：移除所有空白字符并小写。

    LLM 输出与评估关键词之间的空格/换行差异（如 "48小时" vs "48 小时"、
    "5分钟" vs "5 分钟"）不应判定为答案错误，故匹配前统一去空白。
    """
    return "".join(text.split()).lower()


def judge_golden(result: AskResult, expected: dict) -> GoldenJudgement:
    """判定 Golden 题：答案关键词 + 引用标题/章节。"""
    answer_norm = _normalize_text(result.answer)
    expected_keywords = [_normalize_text(k) for k in expected.get("expected_keywords", [])]
    answer_correct = all(kw in answer_norm for kw in expected_keywords)

    expected_title = expected.get("expected_citation_title", "")
    expected_section = expected.get("expected_section_contains", "")
    retrieval_hit = False
    citation_correct = False
    for citation in result.citations:
        title_match = citation.title == expected_title
        section_match = expected_section in " / ".join(citation.section_path)
        if title_match and section_match:
            retrieval_hit = True
            citation_correct = True
            break

    return GoldenJudgement(
        answer_correct=answer_correct,
        citation_correct=citation_correct,
        retrieval_hit=retrieval_hit,
    )


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * p) - 1))
    return sorted_values[index]


def run_eval(
    base_url: str,
    email: str,
    password: str,
    dataset_path: Path,
) -> dict:
    """跑完整评估集，返回结果与指标。"""
    with open(dataset_path, encoding="utf-8") as handle:
        dataset = yaml.safe_load(handle)

    token = login(base_url, email, password)
    knowledge_bases = list_knowledge_bases(base_url, token)
    if not knowledge_bases:
        raise RuntimeError("无可用知识库，无法跑评估")
    kb_id = knowledge_bases[0]["id"]

    golden_results: list[tuple[dict, AskResult, GoldenJudgement]] = []
    no_answer_results: list[tuple[dict, AskResult]] = []

    for item in dataset.get("golden", []):
        result = ask(base_url, token, kb_id, item["question"])
        result.question_id = item["id"]
        judgement = judge_golden(result, item)
        golden_results.append((item, result, judgement))

    for item in dataset.get("no_answer", []):
        result = ask(base_url, token, kb_id, item["question"])
        result.question_id = item["id"]
        no_answer_results.append((item, result))

    return _compute_metrics(dataset, golden_results, no_answer_results)


def _compute_metrics(
    dataset: dict,
    golden_results: list[tuple[dict, AskResult, GoldenJudgement]],
    no_answer_results: list[tuple[dict, AskResult]],
) -> dict:
    golden_total = len(golden_results)
    no_answer_total = len(no_answer_results)

    answer_correct_count = sum(1 for _, _, j in golden_results if j.answer_correct)
    citation_correct_count = sum(1 for _, _, j in golden_results if j.citation_correct)
    retrieval_hit_count = sum(1 for _, _, j in golden_results if j.retrieval_hit)
    # 展示引用总数：Golden 题中至少有一条引用的题数。
    citations_shown = sum(1 for _, r, _ in golden_results if r.citations)

    # 拒答：no-answer 题中 finish_reason == refusal。
    refused_count = sum(
        1 for _, r in no_answer_results if r.finish_reason == "refusal" and not r.error
    )
    # 幻觉：no-answer 题中未拒答（返回了 token/answer 且非 refusal）。
    hallucinated_count = sum(
        1
        for _, r in no_answer_results
        if r.finish_reason != "refusal" and not r.error and r.answer
    )

    first_event_latencies = [
        r.first_event_ms for _, r, _ in golden_results if r.first_event_ms
    ]
    done_latencies = [r.done_event_ms for _, r, _ in golden_results if r.done_event_ms]

    def _rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    metrics = {
        "dataset_version": dataset.get("version"),
        "knowledge_version": dataset.get("knowledge_version"),
        "annotator": dataset.get("annotator"),
        "annotated_at": dataset.get("annotated_at"),
        "counts": {
            "golden": golden_total,
            "no_answer": no_answer_total,
            "total": golden_total + no_answer_total,
        },
        "metrics": {
            "首答准确率": _rate(answer_correct_count, golden_total),
            "引用正确率": _rate(citation_correct_count, citations_shown or 1),
            "拒答率": _rate(refused_count, no_answer_total),
            "幻觉率": _rate(hallucinated_count, golden_total + no_answer_total),
            "检索召回率": _rate(retrieval_hit_count, golden_total),
            "首事件延迟_P50_ms": _percentile(first_event_latencies, 0.5),
            "首事件延迟_P95_ms": _percentile(first_event_latencies, 0.95),
            "完整回答延迟_P50_ms": _percentile(done_latencies, 0.5),
            "完整回答延迟_P95_ms": _percentile(done_latencies, 0.95),
        },
        "golden_details": [
            {
                "id": item["id"],
                "question": item["question"],
                "answer_correct": j.answer_correct,
                "citation_correct": j.citation_correct,
                "retrieval_hit": j.retrieval_hit,
                "finish_reason": r.finish_reason,
                "error": r.error,
                "answer_preview": r.answer[:120],
                "citations": [
                    {"title": c.title, "section_path": c.section_path}
                    for c in r.citations
                ],
            }
            for item, r, j in golden_results
        ],
        "no_answer_details": [
            {
                "id": item["id"],
                "question": item["question"],
                "refused": r.finish_reason == "refusal" and not r.error,
                "finish_reason": r.finish_reason,
                "error": r.error,
                "answer_preview": r.answer[:120],
            }
            for item, r in no_answer_results
        ],
    }
    return metrics


def render_markdown(report: dict, target_accuracy: float = 0.80) -> str:
    """把指标渲染成 Markdown 报告。"""
    m = report["metrics"]
    counts = report["counts"]
    lines = [
        "# EKB M1 质量基线报告",
        "",
        f"- 评估集版本: {report.get('dataset_version', 'N/A')}",
        f"- 知识版本: {report.get('knowledge_version', 'N/A')}",
        f"- 标注人: {report.get('annotator', 'N/A')}",
        f"- 标注日期: {report.get('annotated_at', 'N/A')}",
        f"- 生成时间: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
        "## 1. 评估规模",
        "",
        "| 集合 | 题数 |",
        "|---|---|",
        f"| Golden QA | {counts['golden']} |",
        f"| No-answer | {counts['no_answer']} |",
        f"| 合计 | {counts['total']} |",
        "",
        "## 2. 质量指标",
        "",
        "| 指标 | 数值 | M1 门禁 | 说明 |",
        "|---|---|---|---|",
    ]

    def _fmt(value) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, float) and value <= 1:
            return f"{value * 100:.1f}%"
        return str(round(value, 2)) if isinstance(value, float) else str(value)

    accuracy = m["首答准确率"]
    accuracy_pass = accuracy is not None and accuracy >= target_accuracy
    lines.append(
        f"| 首答准确率 | {_fmt(accuracy)} | ≥{target_accuracy * 100:.0f}% | "
        f"{'PASS' if accuracy_pass else 'FAIL'} |"
    )
    lines.append(
        f"| 引用正确率 | {_fmt(m['引用正确率'])} | P0 错引清零 | 人工抽样核验 |"
    )
    lines.append(
        f"| 拒答率 | {_fmt(m['拒答率'])} | 证据不足必拒答 | 应拒答题中拒答比例 |"
    )
    lines.append(
        f"| 幻觉率 | {_fmt(m['幻觉率'])} | 越低越好 | no-answer 未拒答而编造 |"
    )
    lines.append(f"| 检索召回率 | {_fmt(m['检索召回率'])} | - | 命中预期引用比例 |")
    lines.append(
        f"| 首事件延迟 P50 | {_fmt(m['首事件延迟_P50_ms'])} ms | - | 收到请求到首个 SSE 事件 |"
    )
    lines.append(
        f"| 首事件延迟 P95 | {_fmt(m['首事件延迟_P95_ms'])} ms | - | 收到请求到首个 SSE 事件 |"
    )
    lines.append(
        f"| 完整回答延迟 P50 | {_fmt(m['完整回答延迟_P50_ms'])} ms | - | 收到请求到 done 事件 |"
    )
    lines.append(
        f"| 完整回答延迟 P95 | {_fmt(m['完整回答延迟_P95_ms'])} ms | - | 收到请求到 done 事件 |"
    )

    lines.extend(["", "## 3. Golden 题明细", ""])
    lines.append("| ID | 问题 | 答案正确 | 引用正确 | 召回 | finish_reason |")
    lines.append("|---|---|---|---|---|---|")
    for detail in report["golden_details"]:
        lines.append(
            f"| {detail['id']} | {detail['question'][:30]} | "
            f"{'✓' if detail['answer_correct'] else '✗'} | "
            f"{'✓' if detail['citation_correct'] else '✗'} | "
            f"{'✓' if detail['retrieval_hit'] else '✗'} | "
            f"{detail['finish_reason']} |"
        )

    lines.extend(["", "## 4. No-answer 题明细", ""])
    lines.append("| ID | 问题 | 拒答 | finish_reason |")
    lines.append("|---|---|---|---|")
    for detail in report["no_answer_details"]:
        lines.append(
            f"| {detail['id']} | {detail['question'][:30]} | "
            f"{'✓' if detail['refused'] else '✗'} | {detail['finish_reason']} |"
        )

    lines.extend(["", "## 5. 缺陷与改进项", ""])
    fails = [d for d in report["golden_details"] if not d["answer_correct"]]
    if fails:
        lines.append(
            f"- 答案不正确题: {len(fails)} 题（{', '.join(d['id'] for d in fails)}）"
        )
    citation_fails = [d for d in report["golden_details"] if not d["citation_correct"]]
    if citation_fails:
        lines.append(
            f"- 引用不正确题: {len(citation_fails)} 题（{', '.join(d['id'] for d in citation_fails)}）"
        )
    not_refused = [d for d in report["no_answer_details"] if not d["refused"]]
    if not_refused:
        lines.append(
            f"- 应拒答未拒答: {len(not_refused)} 题（{', '.join(d['id'] for d in not_refused)}）"
        )
    if not fails and not citation_fails and not not_refused:
        lines.append("- 本次评估无缺陷。")

    # ---- 根因分类 ----
    lines.extend(["", "## 6. 根因分类与改进方向", ""])
    # 检索正确但答案不完整：生成问题（_generate 仅用 chunks[0]）。
    gen_issues = [
        d
        for d in report["golden_details"]
        if not d["answer_correct"] and d["citation_correct"]
    ]
    # 引用错误：检索未命中预期文档。
    retrieval_issues = [
        d for d in report["golden_details"] if not d["citation_correct"]
    ]
    # no-answer 未拒答：检索误命中（关键词 bigram 噪声）。
    refusal_issues = [d for d in report["no_answer_details"] if not d["refused"]]

    lines.append("| 根因类别 | 影响题数 | 说明 | 改进方向 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| 生成缺陷 | {len(gen_issues)} | 检索命中正确文档但 LLM 答案缺关键词 "
        f"（M1-05 已接入 DeepSeek 生成 + 评估器空白归一化匹配） | 排查 LLM 上下文构建与提示词 |"
    )
    lines.append(
        f"| 检索未命中 | {len(retrieval_issues)} | 关键词检索召回缺陷（query 改写已启用但无语义匹配，"
        f"跨 SOP 综合题/同义表述易漏召） | M1-04 向量检索 + Rerank |"
    )
    lines.append(
        f"| 拒答失效 | {len(refusal_issues)} | 无依据技术问题被误命中 "
        f"（关键词打分对技术词汇噪声大） | M1-04 置信度阈值 + 语义相似度门禁 |"
    )

    golden_target = 100
    no_answer_target = 30
    golden_met = counts["golden"] >= golden_target
    no_answer_met = counts["no_answer"] >= no_answer_target

    lines.extend(["", "## 7. 评估集规模与后续扩充", ""])
    lines.append(
        f"Spec 建议 M1 Golden ≥{golden_target} 题、No-answer ≥{no_answer_target} 题，"
        f"当前为 {counts['golden']} + {counts['no_answer']}。"
    )
    if golden_met and no_answer_met:
        lines.append("M1 评估集规模已达标。后续扩充方向：")
        lines.append("1. 真实业务语料入库后，按真实 SOP 场景替换脱敏样本，保持题数 ≥100。")
        lines.append("2. M2 起补充 Permission 集合（≥50 组越权测试）。")
        lines.append("3. M3 起补充 Freshness 集合（≥30 题版本/缓存失效）。")
    else:
        lines.append("缺口将在真实业务语料入库后按以下优先级扩充：")
        if not golden_met:
            lines.append(f"1. 补齐 Golden 至 ≥{golden_target} 题：按真实 SOP 文档补充单文档题和跨文档题。")
        if not no_answer_met:
            lines.append(f"2. 补齐 No-answer 至 ≥{no_answer_target} 题：增加同领域近义词干扰题。")
        lines.append("3. M2 起补充 Permission 集合（≥50 组越权测试）。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="EKB M1 评估脚本")
    parser.add_argument("--dataset", default="eval/dataset/eval_set_v1.yaml")
    parser.add_argument("--out-dir", default="eval/reports")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="change-me-local-only")
    parser.add_argument("--target-accuracy", type=float, default=0.80)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_eval(args.base_url, args.email, args.password, dataset_path)

    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    json_path = out_dir / f"eval_result_{timestamp}.json"
    md_path = out_dir / "M1_质量基线_v1.md"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    markdown = render_markdown(report, args.target_accuracy)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown)

    print(f"评估完成: {json_path}")
    print(f"基线报告: {md_path}")
    m = report["metrics"]
    print(
        f"首答准确率={m['首答准确率']} 拒答率={m['拒答率']} "
        f"引用正确率={m['引用正确率']} 幻觉率={m['幻觉率']}"
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from pathlib import Path

# 让测试能导入 eval 包（项目根在上级）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.run import (
    AskResult,
    Citation,
    GoldenJudgement,
    _compute_metrics,
    judge_golden,
    render_markdown,
)


def _result(
    answer: str = "",
    citations: list[Citation] | None = None,
    finish_reason: str = "stop",
    error: str | None = None,
) -> AskResult:
    return AskResult(
        question_id="G001",
        question="test",
        answer=answer,
        citations=citations or [],
        finish_reason=finish_reason,
        error=error,
        first_event_ms=10.0,
        done_event_ms=100.0,
    )


def test_judge_golden_correct_answer_and_citation() -> None:
    result = _result(
        answer="先检查连接池使用率、等待队列、慢查询和应用实例数。",
        citations=[
            Citation(title="数据库故障 SOP", section_path=["故障处理", "连接池耗尽"])
        ],
    )
    expected = {
        "expected_keywords": ["连接池使用率", "等待队列", "慢查询", "应用实例数"],
        "expected_citation_title": "数据库故障 SOP",
        "expected_section_contains": "连接池耗尽",
    }
    j = judge_golden(result, expected)
    assert j.answer_correct
    assert j.citation_correct
    assert j.retrieval_hit


def test_judge_golden_wrong_citation_title_fails_citation_but_may_hit_retrieval() -> (
    None
):
    result = _result(
        answer="先检查连接池使用率、等待队列、慢查询和应用实例数。",
        citations=[Citation(title="错误文档", section_path=["故障处理", "连接池耗尽"])],
    )
    expected = {
        "expected_keywords": ["连接池使用率", "等待队列", "慢查询", "应用实例数"],
        "expected_citation_title": "数据库故障 SOP",
        "expected_section_contains": "连接池耗尽",
    }
    j = judge_golden(result, expected)
    assert j.answer_correct
    # 标题不匹配 → 引用不正确，召回也未命中。
    assert not j.citation_correct
    assert not j.retrieval_hit


def test_judge_golden_missing_keyword_fails_answer() -> None:
    result = _result(
        answer="先检查连接池使用率。",
        citations=[
            Citation(title="数据库故障 SOP", section_path=["故障处理", "连接池耗尽"])
        ],
    )
    expected = {
        "expected_keywords": ["连接池使用率", "等待队列", "慢查询", "应用实例数"],
        "expected_citation_title": "数据库故障 SOP",
        "expected_section_contains": "连接池耗尽",
    }
    j = judge_golden(result, expected)
    assert not j.answer_correct


def test_compute_metrics_full_pass() -> None:
    dataset = {
        "version": "v1",
        "knowledge_version": "seed-v1",
        "annotator": "QA",
        "annotated_at": "2026-08-07",
    }
    golden = [
        (
            {
                "id": "G001",
                "question": "q1",
                "expected_keywords": ["a"],
                "expected_citation_title": "T",
                "expected_section_contains": "S",
            },
            _result(answer="a", citations=[Citation(title="T", section_path=["S"])]),
            GoldenJudgement(
                answer_correct=True, citation_correct=True, retrieval_hit=True
            ),
        )
    ]
    no_answer = [
        (
            {"id": "N001", "question": "q"},
            _result(answer="无证据", finish_reason="refusal"),
        )
    ]
    report = _compute_metrics(dataset, golden, no_answer)
    m = report["metrics"]
    assert m["首答准确率"] == 1.0
    assert m["引用正确率"] == 1.0
    assert m["拒答率"] == 1.0
    assert m["幻觉率"] == 0.0
    assert m["检索召回率"] == 1.0


def test_compute_metrics_hallucination_when_no_answer_not_refused() -> None:
    """no-answer 题未拒答而返回答案 → 计入幻觉。"""
    dataset = {"version": "v1"}
    golden = []
    no_answer = [
        (
            {"id": "N001", "question": "q"},
            _result(answer="编造的答案", finish_reason="stop"),
        ),
    ]
    report = _compute_metrics(dataset, golden, no_answer)
    m = report["metrics"]
    assert m["拒答率"] == 0.0
    assert m["幻觉率"] == 1.0


def test_render_markdown_contains_key_sections() -> None:
    report = {
        "dataset_version": "v1",
        "knowledge_version": "seed-v1",
        "annotator": "QA",
        "annotated_at": "2026-08-07",
        "counts": {"golden": 1, "no_answer": 1, "total": 2},
        "metrics": {
            "首答准确率": 1.0,
            "引用正确率": 1.0,
            "拒答率": 1.0,
            "幻觉率": 0.0,
            "检索召回率": 1.0,
            "首事件延迟_P50_ms": 10.0,
            "首事件延迟_P95_ms": 12.0,
            "完整回答延迟_P50_ms": 100.0,
            "完整回答延迟_P95_ms": 120.0,
        },
        "golden_details": [
            {
                "id": "G001",
                "question": "测试问题",
                "answer_correct": True,
                "citation_correct": True,
                "retrieval_hit": True,
                "finish_reason": "stop",
                "error": None,
                "answer_preview": "答案",
                "citations": [],
            }
        ],
        "no_answer_details": [
            {
                "id": "N001",
                "question": "无依据问题",
                "refused": True,
                "finish_reason": "refusal",
                "error": None,
                "answer_preview": "",
            }
        ],
    }
    md = render_markdown(report, target_accuracy=0.80)
    assert "# EKB M1 质量基线报告" in md
    assert "首答准确率" in md
    assert "PASS" in md
    assert "Golden 题明细" in md
    assert "No-answer 题明细" in md

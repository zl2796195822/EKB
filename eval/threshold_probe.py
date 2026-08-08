"""诊断脚本：计算评估集每题与种子 chunk 的 bigram 重叠率分布，辅助选择拒答阈值。

输出每题的最大重叠率，并按 golden/no-answer 分组，找出分离阈值。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api"))

from ekb_api.core.db import _SEED_SOPS  # type: ignore[attr-defined]


def _normalize(text: str) -> str:
    return "".join(char for char in text.lower() if char.isalnum())


def _overlap_ratio(query: str, content: str) -> float:
    nq = _normalize(query)
    nh = _normalize(content)
    if len(nq) < 2:
        return 0.0
    total = len(nq) - 1
    hits = sum(1 for i in range(total) if nq[i : i + 2] in nh)
    return hits / total


def _matching_bigrams(query: str, content: str) -> list[str]:
    nq = _normalize(query)
    nh = _normalize(content)
    if len(nq) < 2:
        return []
    seen: list[str] = []
    for i in range(len(nq) - 1):
        bg = nq[i : i + 2]
        if bg in nh and bg not in seen:
            seen.append(bg)
    return seen


# 模拟拟议的新打分：仅计 CJK bigram（过滤 ASCII 噪声），并排除常见词 bigram。
_STOPWORD_BIGRAMS = {"问题", "配置", "策略"}


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _valid_cjk_matches(query: str, content: str) -> list[str]:
    nq = _normalize(query)
    nh = _normalize(content)
    seen: list[str] = []
    for i in range(len(nq) - 1):
        bg = nq[i : i + 2]
        if bg in nh and bg not in seen:
            if len(bg) == 2 and _is_cjk(bg[0]) and _is_cjk(bg[1]) and bg not in _STOPWORD_BIGRAMS:
                seen.append(bg)
    return seen


def _would_match(query: str, contents: list[str]) -> bool:
    # 模拟新打分：term_hits（空白分词整词包含）或 CJK bigram（停用词过滤后）≥1。
    query_terms = [t for t in query.lower().split() if t]
    for content in contents:
        haystack = content.lower()
        term_hits = sum(1 for t in query_terms if t in haystack)
        cjk_hits = len(_valid_cjk_matches(query, content))
        if term_hits >= 1 or cjk_hits >= 1:
            return True
    return False


def _all_chunk_contents() -> list[str]:
    contents: list[str] = []
    for sop in _SEED_SOPS:
        for _section, content in sop["chunks"]:
            contents.append(content)
    return contents


def main() -> None:
    dataset_path = Path(__file__).resolve().parent / "dataset" / "eval_set_v1.yaml"
    with open(dataset_path, encoding="utf-8") as handle:
        dataset = yaml.safe_load(handle)

    chunks = _all_chunk_contents()

    nonsense = "zzz不存在的火星语问题qqq"
    print("=== 拒答测试用例（必须低） ===")
    print(f"  nonsense: max_ratio={max(_overlap_ratio(nonsense, c) for c in chunks):.4f}")

    print("\n=== Golden 题（应高，需被检索命中） ===")
    golden_ratios: list[tuple[str, float]] = []
    for item in dataset.get("golden", []):
        ratios = [_overlap_ratio(item["question"], c) for c in chunks]
        max_r = max(ratios)
        golden_ratios.append((item["id"], max_r))
    for gid, r in sorted(golden_ratios, key=lambda x: x[1]):
        print(f"  {gid}: {r:.4f}")
    print(f"  Golden 最小值: {min(r for _, r in golden_ratios):.4f}")

    print("\n=== No-answer 题（应低，需被拒答） ===")
    no_answer_ratios: list[tuple[str, float]] = []
    for item in dataset.get("no_answer", []):
        ratios = [_overlap_ratio(item["question"], c) for c in chunks]
        max_r = max(ratios)
        no_answer_ratios.append((item["id"], max_r))
    for nid, r in sorted(no_answer_ratios, key=lambda x: -x[1]):
        print(f"  {nid}: {r:.4f}")
    print(f"  No-answer 最大值: {max(r for _, r in no_answer_ratios):.4f}")

    nonsense_ratio = max(_overlap_ratio(nonsense, c) for c in chunks)
    golden_min = min(r for _, r in golden_ratios)
    no_answer_max = max(r for _, r in no_answer_ratios)
    print("\n=== 分离窗口 ===")
    print(f"  nonsense = {nonsense_ratio:.4f}")
    print(f"  no_answer_max = {no_answer_max:.4f}")
    print(f"  golden_min = {golden_min:.4f}")
    if no_answer_max < golden_min:
        mid = (no_answer_max + golden_min) / 2
        print(f"  ✓ 存在分离窗口，建议阈值 = {mid:.4f}（no_answer_max 与 golden_min 中点）")
        if nonsense_ratio < mid < golden_min:
            print(f"  ✓ nonsense({nonsense_ratio:.4f}) < 阈值 < golden_min({golden_min:.4f})，拒答测试可通过")
    else:
        print(f"  ✗ 无干净分离窗口：no_answer_max({no_answer_max:.4f}) >= golden_min({golden_min:.4f})")

    # 关键用例的实际匹配 bigram，辅助判断停用词过滤可行性。
    print("\n=== 关键用例匹配 bigram 明细 ===")
    probes = [
        ("nonsense", nonsense),
        ("G029(应命中)", next(i["question"] for i in dataset["golden"] if i["id"] == "G029")),
        ("G022(应命中)", next(i["question"] for i in dataset["golden"] if i["id"] == "G022")),
        ("N013(应拒答)", next(i["question"] for i in dataset["no_answer"] if i["id"] == "N013")),
        ("N011(应拒答)", next(i["question"] for i in dataset["no_answer"] if i["id"] == "N011")),
        ("N005(应拒答)", next(i["question"] for i in dataset["no_answer"] if i["id"] == "N005")),
    ]
    for label, q in probes:
        best_c = max(chunks, key=lambda c: len(_matching_bigrams(q, c)))
        mb = _matching_bigrams(q, best_c)
        print(f"  {label}: {len(mb)} 个 → {mb}")

    # 模拟新打分（CJK-only + 停用词过滤 + ≥1）下的命中/拒答结果。
    print("\n=== 新打分模拟（CJK-only + 停用词{问题,配置} + ≥1） ===")
    refused_golden = [i["id"] for i in dataset["golden"] if not _would_match(i["question"], chunks)]
    matched_noanswer = [i["id"] for i in dataset["no_answer"] if _would_match(i["question"], chunks)]
    nonsense_refused = not _would_match(nonsense, chunks)
    print(f"  Golden 被误拒答 ({len(refused_golden)}): {refused_golden}")
    print(f"  No-answer 未拒答 ({len(matched_noanswer)}): {matched_noanswer}")
    print(f"  nonsense 被拒答: {nonsense_refused}")
    print(f"  → Golden 命中数: {len(dataset['golden']) - len(refused_golden)}/{len(dataset['golden'])}")
    print(f"  → No-answer 拒答数: {len(dataset['no_answer']) - len(matched_noanswer)}/{len(dataset['no_answer'])}")


if __name__ == "__main__":
    main()

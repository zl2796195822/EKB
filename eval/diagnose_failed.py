"""诊断 5 个失败题的 keyword 检索候选，判断正确文档是否在 top-K 候选里。

输出每题 top 20 候选的 title + section_path + score，并标记 expected 命中位置。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

# 失败题及其期望引用
FAILED = [
    {
        "id": "G028",
        "question": "网络延迟恢复要保留 pcap，安全事件要保留什么？",
        "expected_title": "安全事件响应 SOP",
        "expected_section": "取证流程",
    },
    {
        "id": "G030",
        "question": "Redis 淘汰策略和回滚判定分别用于什么场景？",
        "expected_title": "Redis 内存溢出 SOP",
        "expected_section": "淘汰策略",
    },
    {
        "id": "G069",
        "question": "网络延迟恢复步骤的顺序是什么？",
        "expected_title": "网络延迟排查 SOP",
        "expected_section": "恢复步骤",
    },
    {
        "id": "G070",
        "question": "网络延迟恢复时先确认什么再操作？",
        "expected_title": "网络延迟排查 SOP",
        "expected_section": "恢复步骤",
    },
    {
        "id": "G098",
        "question": "数据库故障和网络延迟恢复都涉及什么操作？",
        "expected_title": "数据库故障 SOP",
        "expected_section": "恢复步骤",
    },
]

BASE = os.getenv("EKB_BASE_URL", "http://127.0.0.1:8000")
EMAIL = os.getenv("EKB_DEV_USER_EMAIL", "admin@example.com")
PASSWORD = os.getenv("EKB_DEV_PASSWORD", "test-password")


def login() -> str:
    resp = httpx.post(
        f"{BASE}/api/v1/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def list_kb(token: str) -> str:
    resp = httpx.get(f"{BASE}/api/v1/kb", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    return resp.json()[0]["id"]


def search(token: str, kb_id: str, query: str, top_k: int = 20) -> list[dict]:
    resp = httpx.post(
        f"{BASE}/api/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query, "kb_ids": [kb_id], "top_k": top_k},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["results"]


def main() -> int:
    token = login()
    kb_id = list_kb(token)

    all_in_top = True
    for item in FAILED:
        print(f"\n=== {item['id']}: {item['question']} ===")
        print(f"  期望: {item['expected_title']} / 含「{item['expected_section']}」")
        results = search(token, kb_id, item["question"], top_k=20)
        expected_rank = None
        for idx, r in enumerate(results, 1):
            title = r.get("title", "")
            section = " / ".join(r.get("section_path", []))
            score = r.get("score", 0)
            is_expected = title == item["expected_title"] and item["expected_section"] in section
            marker = " ← 期望命中" if is_expected else ""
            if is_expected and expected_rank is None:
                expected_rank = idx
            print(f"  #{idx:2d} score={score:.2f} | {title} | {section}{marker}")
        if expected_rank is None:
            print(f"  ✗ 期望文档未在 top 20 候选中（LLM Rerank 无法救回）")
            all_in_top = False
        else:
            print(f"  ✓ 期望文档在 top 20 候选中，排名 #{expected_rank}")
        print(f"  候选总数: {len(results)}")

    print(f"\n=== 结论 ===")
    if all_in_top:
        print("所有失败题的正确文档都在 top 20 候选里 → LLM Rerank 可救")
    else:
        print("部分失败题的正确文档不在候选里 → 需要 query 改写或换 embedding")
    return 0


if __name__ == "__main__":
    sys.exit(main())

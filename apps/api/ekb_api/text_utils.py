"""文本/数组工具函数。"""

from __future__ import annotations


def levenshtein(a: str, b: str) -> int:
    """计算两个字符串之间的 Levenshtein 编辑距离。"""
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def merge_sorted_arrays(a: list[int], b: list[int]) -> list[int]:
    """合并两个已排序的整数数组，返回一个新的已排序数组。

    - 保持升序
    - 保留重复元素
    - 空数组参与合并时返回另一个数组的副本
    """
    result: list[int] = []
    i, j = 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            result.append(a[i])
            i += 1
        else:
            result.append(b[j])
            j += 1
    # bug 1: 漏了追加 a 的剩余元素
    # bug 2: 追加 b 的剩余时用了切片但索引错误
    result.extend(b[j:])  # 应该还有 result.extend(a[i:])
    return result

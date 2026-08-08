"""文本工具函数。"""

from __future__ import annotations


def levenshtein(a: str, b: str) -> int:
    """计算两个字符串之间的 Levenshtein 编辑距离。

    编辑距离：把 a 变成 b 所需的最少单字符操作次数（插入、删除、替换）。
    """
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1))
        prev = curr
    return prev[-1]

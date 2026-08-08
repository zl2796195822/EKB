"""text_utils 单元测试：锁定 Levenshtein 编辑距离的正确性。"""

from __future__ import annotations

from ekb_api.text_utils import levenshtein


def test_levenshtein_identical() -> None:
    assert levenshtein("abc", "abc") == 0


def test_levenshtein_empty_a() -> None:
    assert levenshtein("", "abc") == 3


def test_levenshtein_empty_b() -> None:
    assert levenshtein("abc", "") == 3


def test_levenshtein_both_empty() -> None:
    assert levenshtein("", "") == 0


def test_levenshtein_kitten_sitting() -> None:
    # 经典案例：kitten -> sitting 需要 3 步（k->s, e->i, +g）
    assert levenshtein("kitten", "sitting") == 3


def test_levenshtein_flaw_lawn() -> None:
    # flaw -> lawn 需要 2 步（f->l, w->n）
    assert levenshtein("flaw", "lawn") == 2


def test_levenshtein_saturday_sunday() -> None:
    # saturday -> sunday 需要 3 步
    assert levenshtein("saturday", "sunday") == 3


def test_levenshtein_single_substitution() -> None:
    assert levenshtein("cat", "cot") == 1

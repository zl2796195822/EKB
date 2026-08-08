"""text_utils 单元测试。"""

from __future__ import annotations

from ekb_api.text_utils import levenshtein, merge_sorted_arrays


def test_levenshtein_identical() -> None:
    assert levenshtein("abc", "abc") == 0


def test_levenshtein_empty_a() -> None:
    assert levenshtein("", "abc") == 3


def test_levenshtein_kitten_sitting() -> None:
    assert levenshtein("kitten", "sitting") == 3


def test_levenshtein_flaw_lawn() -> None:
    assert levenshtein("flaw", "lawn") == 2


def test_merge_both_nonempty() -> None:
    assert merge_sorted_arrays([1, 3, 5], [2, 4, 6]) == [1, 2, 3, 4, 5, 6]


def test_merge_with_duplicates() -> None:
    assert merge_sorted_arrays([1, 2, 2, 3], [2, 3, 4]) == [1, 2, 2, 2, 3, 3, 4]


def test_merge_first_empty() -> None:
    assert merge_sorted_arrays([], [1, 2, 3]) == [1, 2, 3]


def test_merge_second_empty() -> None:
    assert merge_sorted_arrays([1, 2, 3], []) == [1, 2, 3]


def test_merge_both_empty() -> None:
    assert merge_sorted_arrays([], []) == []


def test_merge_a_has_remaining() -> None:
    # a 的尾部元素全部大于 b 的所有元素 → a 的剩余应被追加
    assert merge_sorted_arrays([5, 6, 7], [1, 2]) == [1, 2, 5, 6, 7]


def test_merge_b_has_remaining() -> None:
    assert merge_sorted_arrays([1, 2], [5, 6, 7]) == [1, 2, 5, 6, 7]


def test_merge_interleaved() -> None:
    assert merge_sorted_arrays([1, 4, 7], [2, 5, 8]) == [1, 2, 4, 5, 7, 8]

from __future__ import annotations

import pytest

from ekb_api.main import _local_scheduler_interval_seconds


def test_local_scheduler_interval_defaults_to_sixty_seconds() -> None:
    assert _local_scheduler_interval_seconds(None) == 60
    assert _local_scheduler_interval_seconds("") == 60
    assert _local_scheduler_interval_seconds("   ") == 60


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("1", 1), ("60", 60), ("300", 300)],
)
def test_local_scheduler_interval_accepts_bounded_integers(
    raw_value: str, expected: int
) -> None:
    assert _local_scheduler_interval_seconds(raw_value) == expected


@pytest.mark.parametrize(
    "raw_value",
    ["0", "301", "-1", "1.5", "NaN", "inf", "1e2", "1_0"],
)
def test_local_scheduler_interval_rejects_invalid_values(raw_value: str) -> None:
    assert _local_scheduler_interval_seconds(raw_value) == 60

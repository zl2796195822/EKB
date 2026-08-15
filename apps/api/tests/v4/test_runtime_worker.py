from ekb_api.runtime_worker import worker_idle_seconds


def test_worker_idle_seconds_is_bounded() -> None:
    assert worker_idle_seconds(None) == 2.0
    assert worker_idle_seconds("1.5") == 1.5
    assert worker_idle_seconds("invalid") == 2.0
    assert worker_idle_seconds("0") == 2.0
    assert worker_idle_seconds("31") == 2.0

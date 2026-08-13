from __future__ import annotations

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.services import scheduler as scheduler_module
from ekb_api.services.scheduler import run_retention_tick


def _prepared_engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'scheduler.db'}")
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        if step.version == "v4_005_retention_governance":
            break
        step.apply(engine)
    from ekb_api.migrations.v4_004_postgres_cutover import apply_v4_004
    from ekb_api.migrations.v4_005_retention_governance import apply_v4_005

    apply_v4_004(engine)
    apply_v4_005(engine)
    return engine


def test_scheduler_tick_uses_lease_and_completes_cleanup_job(tmp_path) -> None:
    engine = _prepared_engine(tmp_path)
    tick = run_retention_tick(
        engine,
        owner_id="scheduler-test",
        now="2020-02-15T00:00:00Z",
    )
    assert tick.acquired is True
    assert tick.enqueued >= 1
    with engine.connect() as connection:
        states = connection.execute(
            text("SELECT state, COUNT(*) FROM background_jobs GROUP BY state")
        ).all()
        assert any(str(state) == "SUCCEEDED" for state, _count in states)
        lease = connection.execute(
            text("SELECT owner_id FROM scheduler_leases WHERE schedule_name='retention_purge'")
        ).scalar_one()
        assert lease == "scheduler-test"
        heartbeat = connection.execute(
            text(
                "SELECT worker_type, queues, version FROM worker_heartbeats "
                "WHERE worker_id='scheduler-test'"
            )
        ).one()
        assert heartbeat.worker_type == "local-scheduler"
        assert heartbeat.queues == '["retention_purge"]'
        assert heartbeat.version == "local"
        run = connection.execute(
            text(
                "SELECT status, scope_type, tenant_id, counters, sanitized_error "
                "FROM scheduler_runs ORDER BY started_at DESC LIMIT 1"
            )
        ).one()
        assert run.status == "SUCCEEDED"
        assert run.scope_type == "PLATFORM"
        assert run.tenant_id is None
        assert '"enqueued"' in str(run.counters)
        assert run.sanitized_error is None


def test_scheduler_tick_records_cancelled_run_when_lease_is_held(tmp_path) -> None:
    engine = _prepared_engine(tmp_path)
    first = run_retention_tick(
        engine,
        owner_id="scheduler-first",
        now="2020-02-15T00:00:00Z",
    )
    assert first.acquired is True
    second = run_retention_tick(
        engine,
        owner_id="scheduler-second",
        now="2020-02-15T00:00:01Z",
    )
    assert second.acquired is False
    with engine.connect() as connection:
        cancelled = connection.execute(
            text(
                "SELECT status, counters, sanitized_error FROM scheduler_runs "
                "WHERE status='CANCELLED' ORDER BY started_at DESC LIMIT 1"
            )
        ).one()
        assert cancelled.status == "CANCELLED"
        assert '"acquired":false' in str(cancelled.counters)
        assert 'LEASE_NOT_ACQUIRED' in str(cancelled.sanitized_error)


def test_scheduler_tick_default_owner_is_stable_within_process(tmp_path, monkeypatch) -> None:
    engine = _prepared_engine(tmp_path)
    monkeypatch.setattr(scheduler_module.os, "getpid", lambda: 4321)

    first = run_retention_tick(engine, now="2020-02-15T00:00:00Z")
    second = run_retention_tick(engine, now="2020-02-15T00:00:01Z")

    assert first.acquired is True
    assert second.acquired is True
    with engine.connect() as connection:
        workers = connection.execute(
            text("SELECT worker_id FROM worker_heartbeats ORDER BY worker_id")
        ).scalars().all()
        assert workers == ["local-scheduler-4321"]


def test_scheduler_tick_records_sanitized_failed_run(tmp_path, monkeypatch) -> None:
    engine = _prepared_engine(tmp_path)

    def fail_schedule(**_kwargs):
        raise RuntimeError("secret payload must not be persisted")

    monkeypatch.setattr(scheduler_module, "schedule_purge_job", fail_schedule)
    with pytest.raises(RuntimeError, match="secret payload"):
        run_retention_tick(
            engine,
            owner_id="scheduler-failing",
            now="2020-02-15T00:00:00Z",
        )

    with engine.connect() as connection:
        failed = connection.execute(
            text(
                "SELECT status, counters, sanitized_error FROM scheduler_runs "
                "WHERE status='FAILED' ORDER BY started_at DESC LIMIT 1"
            )
        ).one()
        assert failed.status == "FAILED"
        assert 'SCHEDULER_TICK_FAILED' in str(failed.sanitized_error)
        assert "secret payload" not in str(failed.sanitized_error)

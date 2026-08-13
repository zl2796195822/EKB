from __future__ import annotations

from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_fullstack import CHAIN
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

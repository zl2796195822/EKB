"""Integration tests for the tenant-scoped job service and Jobs Center (PH1 · T03, PH2 · T05).

Runs on the prepared legacy + v4 schema.  Verifies that enqueue/list/cancel
are strictly tenant-scoped (cross-tenant access is impossible by construction)
and that the read-only cleanup projection aggregates a tenant's ``retention_purge``
jobs without ever observing another tenant's rows.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.models import Tenant
from ekb_api.services.jobs import JobNotFound, get_job_service

API_ROOT = Path(__file__).resolve().parents[2]


def _apply_chain(engine) -> None:
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)


def _seeded_tenant(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(text("SELECT id FROM tenants ORDER BY id LIMIT 1")).scalar_one()
        )


def _make_tenant(engine, tenant_id: str) -> None:
    with Session(engine) as session:
        if session.get(Tenant, tenant_id) is not None:
            return
        session.add(
            Tenant(
                id=tenant_id,
                name=f"tenant-{tenant_id}",
                role="OWNER",
                policy_version=1,
                model_routing_key="default",
                egress_policy="allow",
                quota_daily_qa=0,
                quota_storage_docs=0,
                quota_storage_bytes_per_file=0,
                created_at="2020-01-01T00:00:00Z",
                updated_at="2020-01-01T00:00:00Z",
            )
        )
        session.commit()


def test_enqueue_list_cancel_lifecycle(tmp_path: Path) -> None:
    database = tmp_path / "jobs-lifecycle.db"
    engine = build_engine(f"sqlite:///{database}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    service = get_job_service(engine=engine)

    enqueued = service.enqueue(
        tenant_id=tenant,
        job_type="retention_purge",
        idempotency_key="retention_purge:tenant:1",
        payload={"batch_size": 200},
    )
    job_id = enqueued.job.id
    assert enqueued.created is True
    assert enqueued.job.state == "QUEUED"

    listed = service.list_for_tenant(tenant_id=tenant, job_type="retention_purge")
    assert len(listed) == 1
    assert listed[0].id == job_id

    cancelled = service.cancel(tenant_id=tenant, job_id=job_id, actor_id="actor-1")
    assert cancelled.state == "CANCELLED"

    # terminal state is returned unchanged (idempotent)
    again = service.cancel(tenant_id=tenant, job_id=job_id, actor_id="actor-1")
    assert again.state == "CANCELLED"

    final = service.list_for_tenant(tenant_id=tenant, job_type="retention_purge")
    assert final[0].state == "CANCELLED"


def test_cross_tenant_job_isolation(tmp_path: Path) -> None:
    database = tmp_path / "jobs-isolation.db"
    engine = build_engine(f"sqlite:///{database}")
    _apply_chain(engine)
    owner = _seeded_tenant(engine)
    other = "tenant-other"
    _make_tenant(engine, other)
    service = get_job_service(engine=engine)

    enqueued = service.enqueue(
        tenant_id=owner,
        job_type="retention_purge",
        idempotency_key="retention_purge:owner:1",
        payload={},
    )
    job_id = enqueued.job.id

    # The other tenant cannot read the owner's job.
    raised = False
    try:
        service.get(tenant_id=other, job_id=job_id)
    except JobNotFound:
        raised = True
    assert raised is True

    # The other tenant's cleanup projection sees nothing.
    proj_other = service.cleanup_projection(tenant_id=other)
    assert proj_other.total == 0
    assert proj_other.recent == []

    # The owner's projection sees the job.
    proj_owner = service.cleanup_projection(tenant_id=owner)
    assert proj_owner.total == 1
    assert proj_owner.by_state.get("QUEUED") == 1


def test_cleanup_projection_aggregates_states(tmp_path: Path) -> None:
    database = tmp_path / "jobs-projection.db"
    engine = build_engine(f"sqlite:///{database}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    service = get_job_service(engine=engine)

    for i in range(3):
        service.enqueue(
            tenant_id=tenant,
            job_type="retention_purge",
            idempotency_key=f"retention_purge:{tenant}:{i}",
            payload={},
        )

    proj = service.cleanup_projection(tenant_id=tenant)
    assert proj.total == 3
    assert proj.by_state.get("QUEUED") == 3
    assert len(proj.recent) == 3
    assert proj.job_type == "retention_purge"

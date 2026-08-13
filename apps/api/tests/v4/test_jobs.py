"""Integration tests for the tenant-scoped job service and Jobs Center (PH1 · T03, PH2 · T05).

Runs on the prepared legacy + v4 schema.  Verifies that enqueue/list/cancel
are strictly tenant-scoped (cross-tenant access is impossible by construction)
and that the read-only cleanup projection aggregates a tenant's ``retention_purge``
jobs without ever observing another tenant's rows.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_AUDIT_READ
from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.main import app
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.models import Tenant
from ekb_api.routers import jobs as jobs_router
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


def _runtime_auth(tenant_id: str, capabilities: list[str] | None = None) -> AuthContext:
    return AuthContext(
        actor_id="runtime-auditor",
        tenant_id=tenant_id,
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=capabilities if capabilities is not None else [CAP_AUDIT_READ],
        policy_version=1,
        trace_id="jobs-runtime-test",
    )


def _insert_runtime_rows(engine, tenant_id: str, *, run_count: int = 1) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO worker_heartbeats "
                "(worker_id, worker_type, queues, version, heartbeat_at, started_at) "
                "VALUES (:worker, 'local-scheduler', :queues, 'local', "
                "'2020-02-15T00:00:00Z', '2020-02-14T00:00:00Z')"
            ),
            {"worker": "worker-runtime-test", "queues": '["retention_purge"]'},
        )
        connection.execute(
            text(
                "INSERT INTO scheduler_leases "
                "(schedule_name, owner_id, lease_expires_at, fencing_token) "
                "VALUES ('retention_purge', 'worker-runtime-test', "
                "'2020-02-15T00:01:00Z', 4)"
            )
        )
        for index in range(run_count):
            connection.execute(
                text(
                    "INSERT INTO scheduler_runs "
                    "(id, schedule_name, scope_type, tenant_id, started_at, ended_at, "
                    "status, counters, sanitized_error) "
                    "VALUES (:id, 'retention_purge', :scope, :tenant, :started, :ended, "
                    "'SUCCEEDED', :counters, :error)"
                ),
                {
                    "id": f"runtime-run-{index}",
                    "scope": "TENANT",
                    "tenant": tenant_id,
                    "started": f"2020-02-15T00:{index:02d}:00Z",
                    "ended": f"2020-02-15T00:{index:02d}:01Z",
                    "counters": '{"secret":"must-not-leak"}',
                    "error": '{"message":"must-not-leak"}',
                },
            )


def test_runtime_route_returns_redacted_real_state_and_limits_runs(
    tmp_path: Path, monkeypatch
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'jobs-runtime.db'}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    other = "tenant-runtime-other"
    _make_tenant(engine, other)
    _insert_runtime_rows(engine, tenant, run_count=3)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scheduler_runs "
                "(id, schedule_name, scope_type, tenant_id, started_at, ended_at, "
                "status, counters) VALUES ('runtime-other', 'retention_purge', 'TENANT', "
                ":tenant, '2020-02-16T00:00:00Z', '2020-02-16T00:00:01Z', 'SUCCEEDED', '{}')"
            ),
            {"tenant": other},
        )
    service = get_job_service(engine=engine)
    auth = _runtime_auth(tenant)
    monkeypatch.setattr(jobs_router, "_service", lambda: service)
    app.dependency_overrides[get_live_auth_context] = lambda: auth
    try:
        response = TestClient(app).get("/api/v1/jobs/runtime?recent_runs_limit=2")
    finally:
        app.dependency_overrides.pop(get_live_auth_context, None)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "available"
    assert len(payload["workers"]) == 1
    assert len(payload["leases"]) == 1
    assert len(payload["recent_runs"]) == 2
    assert all(item["id"] != "runtime-other" for item in payload["recent_runs"])
    assert set(payload["workers"][0]) == {
        "worker_id",
        "worker_type",
        "queues",
        "version",
        "heartbeat_at",
        "started_at",
    }
    assert set(payload["leases"][0]) == {
        "schedule_name",
        "owner_id",
        "lease_expires_at",
        "fencing_token",
    }
    assert set(payload["recent_runs"][0]) == {
        "id",
        "schedule_name",
        "scope_type",
        "started_at",
        "ended_at",
        "status",
    }
    assert "tenant_id" not in response.text
    assert "must-not-leak" not in response.text


def test_runtime_snapshot_excludes_replaced_local_scheduler_heartbeat(
    tmp_path: Path,
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'jobs-runtime-workers.db'}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO worker_heartbeats "
                "(worker_id, worker_type, queues, version, heartbeat_at, started_at) "
                "VALUES (:worker, 'local-scheduler', :queues, 'local', "
                "'2020-02-15T00:00:00Z', '2020-02-14T00:00:00Z')"
            ),
            {"worker": "local-scheduler-old", "queues": '[]'},
        )
        connection.execute(
            text(
                "INSERT INTO worker_heartbeats "
                "(worker_id, worker_type, queues, version, heartbeat_at, started_at) "
                "VALUES (:worker, 'local-scheduler', :queues, 'local', "
                "'2020-02-16T00:00:00Z', '2020-02-15T00:00:00Z')"
            ),
            {"worker": "local-scheduler-current", "queues": '[]'},
        )
        connection.execute(
            text(
                "INSERT INTO scheduler_leases "
                "(schedule_name, owner_id, lease_expires_at, fencing_token) "
                "VALUES ('retention_purge', 'local-scheduler-current', "
                "'2020-02-16T00:01:00Z', 5)"
            )
        )

    snapshot = get_job_service(engine=engine).runtime_snapshot(tenant_id=tenant)

    assert [worker.worker_id for worker in snapshot.workers] == [
        "local-scheduler-current"
    ]


def test_runtime_route_is_explicitly_unavailable_when_no_records_exist(
    tmp_path: Path, monkeypatch
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'jobs-runtime-empty.db'}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    service = get_job_service(engine=engine)
    auth = _runtime_auth(tenant)
    monkeypatch.setattr(jobs_router, "_service", lambda: service)
    app.dependency_overrides[get_live_auth_context] = lambda: auth
    try:
        response = TestClient(app).get("/api/v1/jobs/runtime")
    finally:
        app.dependency_overrides.pop(get_live_auth_context, None)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "unavailable",
        "workers": [],
        "leases": [],
        "recent_runs": [],
        "checked_at": response.json()["checked_at"],
    }


def test_runtime_route_requires_audit_read(tmp_path: Path, monkeypatch) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'jobs-runtime-auth.db'}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    service = get_job_service(engine=engine)
    monkeypatch.setattr(jobs_router, "_service", lambda: service)
    app.dependency_overrides[get_live_auth_context] = lambda: _runtime_auth(tenant, [])
    try:
        response = TestClient(app).get("/api/v1/jobs/runtime")
    finally:
        app.dependency_overrides.pop(get_live_auth_context, None)

    assert response.status_code == 403

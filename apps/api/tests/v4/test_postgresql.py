from __future__ import annotations

import contextlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from ekb_api.core.db import build_engine
from ekb_api.migrations.v4_fullstack import KNOWN_VERSIONS, main
from ekb_api.services.jobs import JobService


def _postgres_url() -> str:
    value = os.getenv("EKB_TEST_POSTGRES_URL", "")
    if not value:
        pytest.skip("EKB_TEST_POSTGRES_URL is not configured")
    return value


def test_postgresql_v4_catalog_bindings_and_immutable_provenance() -> None:
    database_url = _postgres_url()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(
            [
                "--database-url",
                database_url,
                "--verify",
                "--expect-versions",
                ",".join(KNOWN_VERSIONS),
            ]
        ) == 0

    engine = build_engine(database_url)
    inspector = inspect(engine)
    assert str(
        next(
            column["type"]
            for column in inspector.get_columns("migration_audit")
            if column["name"] == "detail"
        )
    ).upper() == "JSONB"
    assert str(
        next(
            column["type"]
            for column in inspector.get_columns("migration_provenance")
            if column["name"] == "provenance"
        )
    ).upper() == "JSONB"
    for table in ("background_jobs", "background_job_attempts"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "sanitized_error" in columns
        assert any(
            str(column["type"]).upper() == "JSONB"
            for column in inspector.get_columns(table)
            if column["name"] == "sanitized_error"
        )

    unique_sets = {
        frozenset(constraint.get("column_names") or ())
        for table in ("background_jobs", "background_job_attempts")
        for constraint in inspector.get_unique_constraints(table)
    }
    assert frozenset(("tenant_id", "job_type", "idempotency_key")) in unique_sets
    assert frozenset(("job_id", "attempt_no")) in unique_sets

    with engine.begin() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE migration_provenance SET owner='tampered' "
                    "WHERE version='v4_001_migration_provenance'"
                )
            )


def test_postgresql_jobs_json_lease_and_two_worker_claim() -> None:
    database_url = _postgres_url()
    engine = build_engine(database_url)
    tag = uuid4().hex
    tenant_tag = tag[:24]
    with engine.begin() as connection:
        for tenant_id in (f"pg-test-a-{tenant_tag}", f"pg-test-b-{tenant_tag}"):
            connection.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, role, policy_version, model_routing_key, egress_policy, "
                    "quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file, "
                    "created_at, updated_at) VALUES "
                    "(:id, :id, 'OWNER', 1, 'default', 'allow', 0, 0, 0, "
                    "'2026-08-12T00:00:00Z', '2026-08-12T00:00:00Z') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant_id},
            )

    service = JobService(engine)
    tenant_a = f"pg-test-a-{tenant_tag}"
    tenant_b = f"pg-test-b-{tenant_tag}"
    job = service.enqueue(
        tenant_id=tenant_a,
        job_type="pg-concurrency",
        idempotency_key=tag,
        payload={"nested": ["json", {"ok": True}]},
        max_attempts=2,
        priority=2147483647,
        now="2026-08-12T00:00:00Z",
    )
    duplicate = service.enqueue(
        tenant_id=tenant_a,
        job_type="pg-concurrency",
        idempotency_key=tag,
        payload={"different": True},
        priority=2147483647,
        now="2026-08-12T00:00:01Z",
    )
    other_tenant = service.enqueue(
        tenant_id=tenant_b,
        job_type="pg-concurrency",
        idempotency_key=tag,
        payload={"tenant": "b"},
        priority=2147483646,
        now="2026-08-12T00:00:01Z",
    )
    assert duplicate.created is False
    assert other_tenant.created is True

    def claim(worker_id: str):
        value = service.claim_next(
            worker_id=worker_id, now="2026-08-12T00:00:02Z", lease_seconds=30
        )
        return None if value is None else value

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, [f"worker-a-{tag}", f"worker-b-{tag}"]))
    target_claims = [value for value in claims if value is not None and value.job.id == job.job.id]
    assert len(target_claims) == 1
    service.complete(
        tenant_id=tenant_a,
        job_id=job.job.id,
        worker_id=target_claims[0].worker_id,
        now="2026-08-12T00:00:03Z",
    )

    lifecycle = service.enqueue(
        tenant_id=tenant_a,
        job_type="pg-lifecycle",
        idempotency_key=tag,
        payload={"lifecycle": True},
        max_attempts=2,
        priority=2147483647,
        now="2026-08-12T00:00:00Z",
    )
    claimed = service.claim_next(
        worker_id=f"lifecycle-{tag}", now="2026-08-12T00:00:04Z", lease_seconds=30
    )
    assert claimed is not None and claimed.job.id == lifecycle.job.id
    retry = service.fail(
        tenant_id=tenant_a,
        job_id=lifecycle.job.id,
        worker_id=claimed.worker_id,
        error_code="PG_RETRY",
        sanitized_error={"code": "PG_RETRY", "message": "sanitized"},
        retryable=True,
        now="2026-08-12T00:00:05Z",
    )
    assert retry.state == "RETRY_WAIT"
    with engine.connect() as connection:
        job_row = connection.execute(
            text("SELECT payload, sanitized_error FROM background_jobs WHERE id=:id"),
            {"id": lifecycle.job.id},
        ).first()
        attempt_error = connection.execute(
            text(
                "SELECT sanitized_error FROM background_job_attempts "
                "WHERE job_id=:id ORDER BY attempt_no LIMIT 1"
            ),
            {"id": lifecycle.job.id},
        ).scalar_one()
    assert isinstance(job_row.payload, dict)
    assert isinstance(job_row.sanitized_error, dict)
    assert isinstance(attempt_error, dict)

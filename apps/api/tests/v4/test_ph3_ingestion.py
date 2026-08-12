"""PH3 ingestion pipeline tests (spec 06-ingestion.md).

Covers the parts that are verifiable without object storage or an embedding
provider: version creation + dedup, the attempt state machine, stage-order
enforcement, compare-and-swap conflict detection, retry/cancel, lease
reconciliation and tenant isolation.

The external boundaries (object storage / embedding / OCR) stay fail-closed and
are exercised separately in ``test_ph3_boundaries``: a missing provider must
raise ``*Unavailable`` rather than fabricate a success.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.models import Tenant
from ekb_api.services.ingestion import (
    STAGES,
    IngestNotFound,
    IngestService,
    IngestStateConflict,
    IngestTransitionError,
    create_version_for_item,
)
from ekb_api.services.jobs import get_job_service


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


def _make_kb(engine, *, tenant_id: str, kb_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, tenant_id, name, description, visibility, "
                "role, document_count, created_at, updated_at) VALUES (:id, :tenant, :name, '', "
                "'PRIVATE', 'OWNER', 0, '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')"
            ),
            {"id": kb_id, "tenant": tenant_id, "name": f"kb-{kb_id}"},
        )


def _make_source_object(engine, *, tenant_id: str, object_id: str, sha: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_objects (id, tenant_id, object_key, sha256, byte_size, "
                "detected_mime, ref_count, created_at) VALUES (:id, :tenant, :key, :sha, 11, "
                "'text/plain', 0, '2020-01-01T00:00:00Z')"
            ),
            {"id": object_id, "tenant": tenant_id, "key": f"obj/{object_id}", "sha": sha},
        )


def _create_version(engine, *, tenant_id: str, kb_id: str, path: str, sha: str, src: str) -> dict:
    jobs = get_job_service(engine=engine)
    with engine.begin() as connection:
        return create_version_for_item(
            connection,
            tenant_id=tenant_id,
            kb_id=kb_id,
            normalized_relative_path=path,
            display_path=path,
            sha256=sha,
            byte_size=11,
            detected_mime="text/plain",
            source_object_id=src,
            job_service=jobs,
        )


def _bootstrap(tmp_path: Path, name: str):
    engine = build_engine(f"sqlite:///{tmp_path / name}")
    _apply_chain(engine)
    tenant = _seeded_tenant(engine)
    _make_kb(engine, tenant_id=tenant, kb_id="kb-1")
    _make_source_object(engine, tenant_id=tenant, object_id="src-1", sha="a" * 64)
    return engine, tenant


def test_create_version_registers_job_and_background_work(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-create.db")

    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    assert outcome["is_new"] is True
    assert outcome["version_id"] and outcome["ingest_job_id"]

    with engine.connect() as connection:
        document = connection.execute(
            text("SELECT * FROM documents WHERE id=:id"), {"id": outcome["document_id"]}
        ).one()
        assert document.normalized_relative_path == "docs/a.txt"
        assert document.status == "PROCESSING"
        assert document.version == 1

        version = connection.execute(
            text("SELECT * FROM document_versions WHERE id=:id"), {"id": outcome["version_id"]}
        ).one()
        assert version.ingest_status == "UPLOADED"
        assert version.source_object_id == "src-1"

        job = connection.execute(
            text("SELECT * FROM ingest_jobs WHERE id=:id"), {"id": outcome["ingest_job_id"]}
        ).one()
        assert job.status == "QUEUED"
        assert job.state_version == 0
        assert job.document_version_id == outcome["version_id"]

        # The background job row committed inside the same transaction.
        queued = connection.execute(
            text(
                "SELECT COUNT(*) FROM background_jobs WHERE tenant_id=:t "
                "AND job_type='document_ingest' AND idempotency_key=:k"
            ),
            {"t": tenant, "k": f"ingest:{outcome['version_id']}"},
        ).scalar_one()
        assert queued == 1

        # ref_count was incremented for the referenced object.
        assert (
            connection.execute(
                text("SELECT ref_count FROM source_objects WHERE id='src-1'")
            ).scalar_one()
            == 1
        )


def test_identical_bytes_dedup_to_existing_version(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-dedup.db")

    first = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    second = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )

    assert second["is_new"] is False
    assert second["version_id"] == first["version_id"]
    assert second["ingest_job_id"] == first["ingest_job_id"]

    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT COUNT(*) FROM document_versions")).scalar_one() == 1
        )
        assert connection.execute(text("SELECT COUNT(*) FROM ingest_jobs")).scalar_one() == 1


def test_changed_bytes_fork_a_new_version_on_same_document(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-fork.db")
    _make_source_object(engine, tenant_id=tenant, object_id="src-2", sha="b" * 64)

    first = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    second = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="b" * 64, src="src-2"
    )

    assert second["document_id"] == first["document_id"]
    assert second["version_id"] != first["version_id"]
    with engine.connect() as connection:
        versions = connection.execute(
            text("SELECT version FROM document_versions WHERE doc_id=:d ORDER BY version"),
            {"d": first["document_id"]},
        ).scalars().all()
        assert list(versions) == [1, 2]


def test_full_stage_walk_activates_the_version(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-walk.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)

    attempt = service.start_attempt(
        tenant_id=tenant, ingest_job_id=outcome["ingest_job_id"], lease_owner="worker-1"
    )
    assert attempt.state == "WAITING"
    assert attempt.attempt_no == 1

    for index, stage in enumerate(STAGES, start=1):
        view = service.run_stage(
            tenant_id=tenant,
            attempt_id=attempt.id,
            stage=stage,
            progress_current=index,
            progress_total=len(STAGES),
            metrics={"stage_index": index},
        )
        assert view.state == stage
        assert view.current_stage == stage

    projection = service.succeed(tenant_id=tenant, attempt_id=attempt.id)
    assert projection.status == "SUCCEEDED"
    assert [s["stage"] for s in projection.stages] == list(STAGES)

    with engine.connect() as connection:
        version = connection.execute(
            text("SELECT * FROM document_versions WHERE id=:id"), {"id": outcome["version_id"]}
        ).one()
        assert version.ingest_status == "SUCCEEDED"
        assert version.activated_at is not None
        document = connection.execute(
            text("SELECT * FROM documents WHERE id=:id"), {"id": outcome["document_id"]}
        ).one()
        assert document.status == "READY"
        assert document.active_version_id == outcome["version_id"]


def test_stage_order_is_enforced(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-order.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)
    attempt = service.start_attempt(
        tenant_id=tenant, ingest_job_id=outcome["ingest_job_id"], lease_owner="worker-1"
    )

    # Skipping VALIDATING must be rejected.
    with pytest.raises(IngestTransitionError):
        service.run_stage(tenant_id=tenant, attempt_id=attempt.id, stage="PARSING")

    # Succeeding before INDEXING must be rejected.
    service.run_stage(tenant_id=tenant, attempt_id=attempt.id, stage="VALIDATING")
    with pytest.raises(IngestTransitionError):
        service.succeed(tenant_id=tenant, attempt_id=attempt.id)

    with pytest.raises(IngestTransitionError):
        service.run_stage(tenant_id=tenant, attempt_id=attempt.id, stage="NONSENSE")


def test_only_one_attempt_may_be_in_flight(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-single.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)
    service.start_attempt(
        tenant_id=tenant, ingest_job_id=outcome["ingest_job_id"], lease_owner="worker-1"
    )
    with pytest.raises(IngestStateConflict):
        service.start_attempt(
            tenant_id=tenant, ingest_job_id=outcome["ingest_job_id"], lease_owner="worker-2"
        )


def test_retryable_failure_requeues_and_terminal_failure_stops(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-retry.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)
    job_id = outcome["ingest_job_id"]

    attempt = service.start_attempt(
        tenant_id=tenant, ingest_job_id=job_id, lease_owner="worker-1"
    )
    service.run_stage(tenant_id=tenant, attempt_id=attempt.id, stage="VALIDATING")
    # EMBEDDING_RATE_LIMITED is retryable -> the job returns to QUEUED.
    requeued = service.fail(
        tenant_id=tenant,
        attempt_id=attempt.id,
        error_code="EMBEDDING_RATE_LIMITED",
        detail={"retry_after_seconds": 30, "provider_trace": "leak-me"},
    )
    assert requeued.status == "QUEUED"
    assert requeued.active_attempt is not None
    assert requeued.active_attempt.error_code == "EMBEDDING_RATE_LIMITED"
    # Raw provider traces must never reach the ledger.
    assert "provider_trace" not in (requeued.active_attempt.sanitized_error or {})

    retried = service.retry(tenant_id=tenant, ingest_job_id=job_id, lease_owner="worker-2")
    assert retried.attempt_no == 2

    # PARSER_ENCRYPTED is not retryable -> terminal failure.
    terminal = service.fail(
        tenant_id=tenant, attempt_id=retried.id, error_code="PARSER_ENCRYPTED"
    )
    assert terminal.status == "FAILED"
    with engine.connect() as connection:
        document = connection.execute(
            text("SELECT * FROM documents WHERE id=:id"), {"id": outcome["document_id"]}
        ).one()
        assert document.status == "FAILED"
        assert document.failure_reason == "PARSER_ENCRYPTED"


def test_cancel_blocks_further_attempts(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-cancel.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)
    job_id = outcome["ingest_job_id"]
    service.start_attempt(tenant_id=tenant, ingest_job_id=job_id, lease_owner="worker-1")

    projection = service.cancel(tenant_id=tenant, ingest_job_id=job_id)
    assert projection.status == "CANCELLED"
    assert projection.active_attempt is not None
    assert projection.active_attempt.state == "CANCELLED"

    with pytest.raises(IngestTransitionError):
        service.start_attempt(
            tenant_id=tenant, ingest_job_id=job_id, lease_owner="worker-2"
        )


def test_expired_lease_is_reconciled_back_to_queued(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-lease.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)
    job_id = outcome["ingest_job_id"]
    attempt = service.start_attempt(
        tenant_id=tenant,
        ingest_job_id=job_id,
        lease_owner="worker-1",
        lease_seconds=1,
        now="2020-01-01T00:00:00Z",
    )

    # A stale lease owner cannot heartbeat after reconciliation.
    recovered = service.reconcile(tenant_id=tenant, now="2020-01-01T01:00:00Z")
    assert recovered == [job_id]

    projection = service.projection(tenant_id=tenant, ingest_job_id=job_id)
    assert projection.status == "QUEUED"
    assert projection.active_attempt is not None
    assert projection.active_attempt.state == "FAILED"

    with pytest.raises(IngestStateConflict):
        service.heartbeat(tenant_id=tenant, attempt_id=attempt.id, lease_owner="worker-1")

    # The job is retryable because the budget is not exhausted.
    assert service.retry(
        tenant_id=tenant, ingest_job_id=job_id, lease_owner="worker-2"
    ).attempt_no == 2


def test_compare_and_swap_rejects_a_stale_writer(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-cas.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    service = IngestService(engine)
    job_id = outcome["ingest_job_id"]
    service.start_attempt(tenant_id=tenant, ingest_job_id=job_id, lease_owner="worker-1")

    with engine.connect() as connection:
        stale = service._require_job(connection, tenant, job_id)

    # Someone else advances the job first.
    service.cancel(tenant_id=tenant, ingest_job_id=job_id)

    with engine.begin() as connection:
        with pytest.raises(IngestStateConflict):
            service._bump(
                connection,
                tenant_id=tenant,
                job_row=stale,
                status="SUCCEEDED",
                now="2020-01-01T00:00:00Z",
            )


def test_cross_tenant_access_is_impossible(tmp_path: Path) -> None:
    engine, tenant = _bootstrap(tmp_path, "ph3-tenant.db")
    outcome = _create_version(
        engine, tenant_id=tenant, kb_id="kb-1", path="docs/a.txt", sha="a" * 64, src="src-1"
    )
    other = "tenant-intruder"
    _make_tenant(engine, other)
    service = IngestService(engine)

    with pytest.raises(IngestNotFound):
        service.projection(tenant_id=other, ingest_job_id=outcome["ingest_job_id"])
    with pytest.raises(IngestNotFound):
        service.start_attempt(
            tenant_id=other, ingest_job_id=outcome["ingest_job_id"], lease_owner="w"
        )
    with pytest.raises(IngestNotFound):
        service.cancel(tenant_id=other, ingest_job_id=outcome["ingest_job_id"])

    # A KB that belongs to another tenant cannot receive a version.
    with pytest.raises(IngestNotFound):
        _create_version(
            engine, tenant_id=other, kb_id="kb-1", path="docs/b.txt", sha="c" * 64, src="src-1"
        )

"""PH3 Upload Center contracts: server projections and tenant-scoped controls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_READ, CAP_KB_WRITE
from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.main import app
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.routers import kb_upload
from ekb_api.services.ingestion import IngestService, STAGES, create_version_for_item
from ekb_api.services.jobs import get_job_service
from ekb_api.services.storage import LocalFilesystemStorageClient, UploadService


def _apply_chain(engine) -> None:
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)


@pytest.fixture()
def env(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'upload-center.db'}")
    _apply_chain(engine)
    with engine.connect() as connection:
        tenant = str(connection.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one())
        user = str(connection.execute(text("SELECT id FROM users WHERE tenant_id=:tenant LIMIT 1"), {"tenant": tenant}).scalar_one())
        kb_id = str(connection.execute(text("SELECT id FROM knowledge_bases WHERE tenant_id=:tenant LIMIT 1"), {"tenant": tenant}).scalar_one())
    storage = LocalFilesystemStorageClient(tmp_path / "objects")
    return {"engine": engine, "tenant": tenant, "user": user, "kb": kb_id, "storage": storage}


def _create_batch(env, count: int = 8):
    service = UploadService(env["engine"], storage_client=env["storage"])
    result = service.create_batch(
        tenant_id=env["tenant"],
        created_by=env["user"],
        kb_id=env["kb"],
        mode="MULTI_FILE",
        client_request_id=str(uuid4()),
        items=[
            {
                "client_item_id": f"client-{index}",
                "relative_path": f"docs/item-{index}.txt",
                "byte_size": 1,
                "browser_mime": "text/plain",
            }
            for index in range(count)
        ],
    )
    return service, result.batch_id, [item.upload_item_id for item in result.items]


def _create_job_for_item(env, item_id: str, *, suffix: str):
    source_id = f"source-{suffix}"
    sha = hashlib.sha256(source_id.encode()).hexdigest()
    with env["engine"].begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_objects "
                "(id, tenant_id, object_key, sha256, byte_size, detected_mime, ref_count, created_at) "
                "VALUES (:id, :tenant, :key, :sha, 1, 'text/plain', 0, '2026-08-13T00:00:00Z')"
            ),
            {"id": source_id, "tenant": env["tenant"], "key": f"objects/{source_id}", "sha": sha},
        )
        outcome = create_version_for_item(
            connection,
            tenant_id=env["tenant"],
            kb_id=env["kb"],
            normalized_relative_path=f"jobs/{suffix}.txt",
            display_path=f"jobs/{suffix}.txt",
            sha256=sha,
            byte_size=1,
            detected_mime="text/plain",
            source_object_id=source_id,
            job_service=get_job_service(env["engine"]),
        )
        connection.execute(
            text(
                "UPDATE upload_items SET source_object_id=:source, status='UPLOADED', uploaded_bytes=1 "
                "WHERE id=:item"
            ),
            {"source": source_id, "item": item_id},
        )
    return outcome["ingest_job_id"]


def test_projection_exposes_canonical_statuses_and_real_metadata(env) -> None:
    service, batch_id, item_ids = _create_batch(env)
    raw_statuses = ["WAITING", "UPLOADING", "VERIFYING", "UPLOADED", "UPLOADED", "COMPLETED", "FAILED", "ABORTED"]
    with env["engine"].begin() as connection:
        for item_id, status in zip(item_ids, raw_statuses):
            connection.execute(text("UPDATE upload_items SET status=:status WHERE id=:item"), {"status": status, "item": item_id})
        connection.execute(
            text("UPDATE upload_items SET error_code='EMBEDDING_RATE_LIMITED', error_detail=:detail WHERE id=:item"),
            {"detail": json.dumps({"stage": "EMBEDDING", "provider_trace": "must-not-leak"}), "item": item_ids[6]},
        )

    processing_job = _create_job_for_item(env, item_ids[3], suffix="processing")
    indexing_job = _create_job_for_item(env, item_ids[4], suffix="indexing")
    ready_job = _create_job_for_item(env, item_ids[5], suffix="ready")
    ingest = IngestService(env["engine"])
    attempt = ingest.start_attempt(tenant_id=env["tenant"], ingest_job_id=indexing_job, lease_owner="upload-center-test")
    for index, stage in enumerate(STAGES, start=1):
        ingest.run_stage(tenant_id=env["tenant"], attempt_id=attempt.id, stage=stage, progress_current=index, progress_total=len(STAGES))
    ready_attempt = ingest.start_attempt(tenant_id=env["tenant"], ingest_job_id=ready_job, lease_owner="upload-center-test")
    for index, stage in enumerate(STAGES, start=1):
        ingest.run_stage(tenant_id=env["tenant"], attempt_id=ready_attempt.id, stage=stage, progress_current=index, progress_total=len(STAGES))
    ingest.succeed(tenant_id=env["tenant"], attempt_id=ready_attempt.id)

    projection = service.get_batch(tenant_id=env["tenant"], batch_id=batch_id)
    assert [item.status for item in projection.items] == [
        "queued", "uploading", "verifying", "processing", "indexing", "ready", "failed", "cancelled"
    ]
    assert projection.counts == {
        "queued": 1, "uploading": 1, "verifying": 1, "processing": 1,
        "indexing": 1, "ready": 1, "failed": 1, "cancelled": 1,
    }
    processing = projection.items[3]
    indexing = projection.items[4]
    failed = projection.items[6]
    assert processing.job_id == processing_job
    assert processing.attempts == 0
    assert indexing.stage == "INDEXING"
    assert indexing.progress and indexing.progress["current"] == len(STAGES)
    assert failed.error and failed.error["code"] == "EMBEDDING_RATE_LIMITED"
    assert failed.error["retryable"] is True
    assert "provider_trace" not in str(failed.error)


def test_projection_is_tenant_scoped(env) -> None:
    service, batch_id, _ = _create_batch(env, count=1)
    with pytest.raises(Exception) as exc_info:
        service.get_batch(tenant_id="tenant-not-authorized", batch_id=batch_id)
    assert "不存在" in str(exc_info.value) or "NOT_FOUND" in str(exc_info.value)


def test_retry_and_cancel_routes_are_real_and_tenant_bound(env, monkeypatch) -> None:
    _, _, item_ids = _create_batch(env, count=1)
    job_id = _create_job_for_item(env, item_ids[0], suffix="route")
    auth = AuthContext(
        actor_id=env["user"],
        tenant_id=env["tenant"],
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=[CAP_KB_READ, CAP_KB_WRITE],
        policy_version=1,
        trace_id="upload-center-route-test",
    )
    monkeypatch.setattr(kb_upload, "get_engine", lambda: env["engine"])
    app.dependency_overrides[get_live_auth_context] = lambda: auth
    try:
        with TestClient(app) as client:
            retry = client.post(f"/api/v1/kb/ingest-jobs/{job_id}/retry")
            assert retry.status_code == 200, retry.text
            assert retry.json()["attempt_no"] == 1
            cancelled = client.post(f"/api/v1/kb/ingest-jobs/{job_id}/cancel")
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "CANCELLED"
            unknown = client.post("/api/v1/kb/ingest-jobs/not-this-tenant/retry")
            assert unknown.status_code == 404
    finally:
        app.dependency_overrides.pop(get_live_auth_context, None)

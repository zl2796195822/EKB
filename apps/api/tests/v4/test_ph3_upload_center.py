"""PH3 Upload Center contracts: server projections and tenant-scoped controls."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_READ, CAP_KB_WRITE
from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.main import app
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.routers import kb_upload
from ekb_api.services.ingestion import STAGES, IngestService, create_version_for_item
from ekb_api.services.jobs import get_job_service
from ekb_api.services.storage import (
    LocalFilesystemStorageClient,
    UploadService,
    _session_is_expired,
)


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


def test_create_batch_replay_keeps_the_create_contract_and_defers_sessions(env) -> None:
    service = UploadService(env["engine"], storage_client=env["storage"])
    request_id = "directory-replay-contract"
    items = [
        {
            "client_item_id": "short-client-id",
            "relative_path": "金博/手册/README.md",
            "byte_size": 7,
            "browser_mime": "text/markdown",
        }
    ]
    first = service.create_batch(
        tenant_id=env["tenant"],
        created_by=env["user"],
        kb_id=env["kb"],
        mode="DIRECTORY",
        client_request_id=request_id,
        items=items,
    )
    replay = service.create_batch(
        tenant_id=env["tenant"],
        created_by=env["user"],
        kb_id=env["kb"],
        mode="DIRECTORY",
        client_request_id=request_id,
        items=items,
    )

    assert replay.created is False
    assert replay.batch_id == first.batch_id
    assert replay.items[0].accepted is True
    assert replay.items[0].upload_item_id == first.items[0].upload_item_id
    assert first.items[0].upload_session is None
    assert replay.items[0].upload_session is None
    with env["engine"].connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM upload_sessions")).scalar_one() == 0

    opened = service.open_session(tenant_id=env["tenant"], item_id=first.items[0].upload_item_id)
    assert opened["upload_urls"]


def test_open_session_recognizes_completed_item_without_reopening_or_duplicate_version(env) -> None:
    service = UploadService(env["engine"], storage_client=env["storage"])
    created = service.create_batch(
        tenant_id=env["tenant"],
        created_by=env["user"],
        kb_id=env["kb"],
        mode="DIRECTORY",
        client_request_id="completed-item-retry",
        items=[{"client_item_id": "manual", "relative_path": "金博/manual.md", "byte_size": 4}],
    )
    item_id = created.items[0].upload_item_id
    service.open_session(tenant_id=env["tenant"], item_id=item_id)
    content = b"data"
    sha256 = hashlib.sha256(content).hexdigest()
    env["storage"].write_object(
        tenant_id=env["tenant"],
        object_key=f"uploads/{env['tenant']}/{env['kb']}/{item_id}",
        data=content,
    )
    completed = service.complete_upload(tenant_id=env["tenant"], item_id=item_id, sha256=sha256)

    resumed = service.open_session(tenant_id=env["tenant"], item_id=item_id)

    assert resumed == {
        "already_completed": True,
        "document_id": completed.document_id,
        "ingest_job_id": completed.ingest_job_id,
        "status": "COMPLETING",
    }
    with env["engine"].connect() as connection:
        assert connection.execute(
            text("SELECT status FROM upload_items WHERE id=:item"), {"item": item_id}
        ).scalar_one() == "COMPLETING"
        assert connection.execute(
            text("SELECT state FROM upload_sessions WHERE upload_item_id=:item"), {"item": item_id}
        ).scalar_one() == "COMPLETED"


def test_rejected_item_cannot_open_or_abort_a_upload_session(env) -> None:
    service = UploadService(env["engine"], storage_client=env["storage"])
    created = service.create_batch(
        tenant_id=env["tenant"],
        created_by=env["user"],
        kb_id=env["kb"],
        mode="DIRECTORY",
        client_request_id="rejected-item-session",
        items=[{"client_item_id": "empty", "relative_path": "金博/empty.md", "byte_size": 0}],
    )
    item_id = created.items[0].upload_item_id

    with pytest.raises(ApiError, match="UPLOAD_ITEM_NOT_RESUMABLE"):
        service.open_session(tenant_id=env["tenant"], item_id=item_id)
    with pytest.raises(ApiError, match="UPLOAD_ITEM_NOT_RESUMABLE"):
        service.abort_session(tenant_id=env["tenant"], item_id=item_id)


def test_open_session_renews_a_near_expiry_active_session(env) -> None:
    service = UploadService(env["engine"], storage_client=env["storage"])
    _, _, item_ids = _create_batch(env, count=1)
    item_id = item_ids[0]
    service.open_session(tenant_id=env["tenant"], item_id=item_id)
    near_expiry = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    with env["engine"].begin() as connection:
        connection.execute(
            text("UPDATE upload_sessions SET expires_at=:expires WHERE upload_item_id=:item"),
            {"expires": near_expiry, "item": item_id},
        )

    renewed = service.open_session(tenant_id=env["tenant"], item_id=item_id)

    assert renewed["expires_at"] > near_expiry


def test_session_expiry_comparison_accepts_postgresql_timestamp_values() -> None:
    now = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
    future = now + timedelta(minutes=5)
    past = now - timedelta(minutes=5)

    assert _session_is_expired(future, now=now) is False
    assert _session_is_expired(str(future), now=now) is False
    assert _session_is_expired(past, now=now) is True


def test_create_batch_replay_rejects_a_different_manifest(env) -> None:
    service = UploadService(env["engine"], storage_client=env["storage"])
    service.create_batch(
        tenant_id=env["tenant"],
        created_by=env["user"],
        kb_id=env["kb"],
        mode="DIRECTORY",
        client_request_id="manifest-conflict",
        items=[{"client_item_id": "one", "relative_path": "one.md", "byte_size": 1}],
    )

    with pytest.raises(Exception) as exc_info:
        service.create_batch(
            tenant_id=env["tenant"],
            created_by=env["user"],
            kb_id=env["kb"],
            mode="DIRECTORY",
            client_request_id="manifest-conflict",
            items=[{"client_item_id": "one", "relative_path": "two.md", "byte_size": 1}],
        )

    assert "IDEMPOTENCY_KEY_CONFLICT" in str(exc_info.value)


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


def test_list_batches_orders_by_updated_at_and_clamps_limit(env) -> None:
    service = UploadService(env["engine"], storage_client=env["storage"])
    batch_ids = [_create_batch(env, count=1)[1] for _ in range(3)]
    with env["engine"].begin() as connection:
        for index, batch_id in enumerate(batch_ids):
            connection.execute(
                text(
                    "UPDATE upload_batches SET created_at=:created, updated_at=:updated "
                    "WHERE id=:id"
                ),
                {
                    "id": batch_id,
                    "created": f"2026-08-13T00:0{index}:00Z",
                    "updated": f"2026-08-13T00:1{index}:00Z",
                },
            )

    assert [batch.id for batch in service.list_batches(tenant_id=env["tenant"], limit=2)] == [
        batch_ids[2],
        batch_ids[1],
    ]
    assert len(service.list_batches(tenant_id=env["tenant"], limit=0)) == 1
    assert len(service.list_batches(tenant_id=env["tenant"], limit=1000)) == 3


def test_list_batches_is_tenant_scoped(env) -> None:
    service, own_batch_id, _ = _create_batch(env, count=1)
    other_tenant = f"tenant-b-{uuid4().hex}"
    other_user = f"user-b-{uuid4().hex}"
    other_kb = f"kb-b-{uuid4().hex}"
    with env["engine"].begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants "
                "(id, name, role, policy_version, model_routing_key, egress_policy, "
                "quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file, "
                "created_at, updated_at) VALUES "
                "(:id, :name, 'OWNER', 1, 'default', 'allow', 0, 0, 0, "
                "'2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"
            ),
            {"id": other_tenant, "name": other_tenant},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, name, email, password_hash, tenant_id, role, "
                "created_at, updated_at) VALUES "
                "(:id, :name, :email, 'test-only', :tenant, 'OWNER', "
                "'2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"
            ),
            {
                "id": other_user,
                "name": other_user,
                "email": f"{other_user}@example.test",
                "tenant": other_tenant,
            },
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, tenant_id, name, description, visibility, role, document_count, "
                "created_at, updated_at) VALUES "
                "(:id, :tenant, :name, '', 'PRIVATE', 'OWNER', 0, "
                "'2026-08-13T00:00:00Z', '2026-08-13T00:00:00Z')"
            ),
            {"id": other_kb, "tenant": other_tenant, "name": other_kb},
        )
    _, other_batch_id, _ = _create_batch(
        {**env, "tenant": other_tenant, "user": other_user, "kb": other_kb}, count=1
    )

    assert [batch.id for batch in service.list_batches(tenant_id=env["tenant"])] == [own_batch_id]
    assert [batch.id for batch in service.list_batches(tenant_id=other_tenant)] == [other_batch_id]


def test_list_batches_route_returns_authenticated_tenant_projection(env, monkeypatch) -> None:
    service, batch_id, _ = _create_batch(env, count=1)
    auth = AuthContext(
        actor_id=env["user"],
        tenant_id=env["tenant"],
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=[CAP_KB_READ],
        policy_version=1,
        trace_id="upload-center-list-route-test",
    )
    monkeypatch.setattr(kb_upload, "_uploads", lambda: service)
    app.dependency_overrides[get_live_auth_context] = lambda: auth
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/kb/uploads/batches?limit=1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["limit"] == 1
        assert [item["id"] for item in payload["items"]] == [batch_id]
    finally:
        app.dependency_overrides.pop(get_live_auth_context, None)


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

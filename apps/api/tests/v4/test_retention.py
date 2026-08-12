"""Integration tests for the retention purge scheduler (PH2 · T05).

These run on the prepared legacy + v4 schema.  They deliberately avoid
inserting into ``deletion_batches`` because that table carries a
PostgreSQL-only ``interval '30 days'`` CHECK; the purge state machine and
CAS guards are exercised through directly-inserted ``trash_items`` rows with
no ``deletion_batch_id`` (a valid, supported path).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import utc_now
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.services.retention import purge_expired_trash

API_ROOT = Path(__file__).resolve().parents[2]


def _apply_chain(engine) -> None:
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)


def _tenant_user(engine):
    with engine.connect() as connection:
        tenant_id = connection.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one()
        user_id = connection.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
    return str(tenant_id), str(user_id)


def _insert_trash(
    engine, *, tenant_id: str, user_id: str, expires_at: str, generation: int = 1
) -> str:
    from uuid import uuid4

    trash_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trash_items "
                "(id, tenant_id, resource_type, resource_id, deleted_by, deleted_at, "
                "restored_at, purged_at, expires_at, deletion_generation, deletion_batch_id, "
                "purge_state, claim_fencing_token, claim_expires_at, purge_job_id, "
                "created_at, updated_at) "
                "VALUES (:id, :tenant, 'knowledge_base', 'kb-test-1', :user, :deleted, "
                "NULL, NULL, :expires, :gen, NULL, 'ELIGIBLE', 0, NULL, NULL, "
                ":created, :updated)"
            ),
            {
                "id": trash_id,
                "tenant": tenant_id,
                "user": user_id,
                "deleted": expires_at,
                "expires": expires_at,
                "gen": generation,
                "created": expires_at,
                "updated": expires_at,
            },
        )
    return trash_id


def _state(engine, trash_id: str) -> tuple[str, object]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT purge_state, purged_at FROM trash_items WHERE id=:id"),
            {"id": trash_id},
        ).first()
    return str(row[0]), row[1]


def test_purge_marks_eligible_expired_trash_succeeded(tmp_path: Path) -> None:
    database = tmp_path / "retention-purge.db"
    engine = build_engine(f"sqlite:///{database}")
    _apply_chain(engine)
    tenant_id, user_id = _tenant_user(engine)
    past = "2020-01-01T00:00:00Z"
    trash_id = _insert_trash(engine, tenant_id=tenant_id, user_id=user_id, expires_at=past)

    report = purge_expired_trash(engine, now="2020-02-15T00:00:00Z")

    assert report.evaluated >= 1
    assert report.purged >= 1
    state, purged_at = _state(engine, trash_id)
    assert state == "SUCCEEDED"
    assert purged_at is not None


def test_purge_dry_run_mutates_nothing(tmp_path: Path) -> None:
    database = tmp_path / "retention-dry.db"
    engine = build_engine(f"sqlite:///{database}")
    _apply_chain(engine)
    tenant_id, user_id = _tenant_user(engine)
    past = "2020-01-01T00:00:00Z"
    trash_id = _insert_trash(engine, tenant_id=tenant_id, user_id=user_id, expires_at=past)

    report = purge_expired_trash(engine, now="2020-02-15T00:00:00Z", dry_run=True)

    assert report.purged == 0
    state, purged_at = _state(engine, trash_id)
    assert state == "ELIGIBLE"
    assert purged_at is None


def test_purge_skips_not_yet_due(tmp_path: Path) -> None:
    database = tmp_path / "retention-future.db"
    engine = build_engine(f"sqlite:///{database}")
    _apply_chain(engine)
    tenant_id, user_id = _tenant_user(engine)
    future = utc_now()  # not yet past
    trash_id = _insert_trash(engine, tenant_id=tenant_id, user_id=user_id, expires_at=future)

    report = purge_expired_trash(engine, now="2020-01-01T00:00:00Z")

    assert report.skipped_not_due >= 1
    state, _ = _state(engine, trash_id)
    assert state == "ELIGIBLE"

"""SQLite integration coverage for the retention/restore recycle-bin contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.core.errors import ApiError
from ekb_api.domain import utc_now
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.services import v3_trash as trash


def _apply_chain(engine) -> None:
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)


def _tenant_user(engine) -> tuple[str, str]:
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id FROM tenants LIMIT 1")).first()
        user = connection.execute(text("SELECT id FROM users LIMIT 1")).first()
    assert row is not None and user is not None
    return str(row[0]), str(user[0])


def _ts(days: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _kb(engine, tenant_id: str) -> str:
    kb_id = str(uuid4())
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO knowledge_bases "
                "(id, tenant_id, name, description, visibility, role, document_count, "
                "deleted_at, created_at, updated_at) "
                "VALUES (:id, :tenant, 'trash-test', '', 'PRIVATE', 'OWNER', 0, "
                ":deleted, :now, :now)"
            ),
            {"id": kb_id, "tenant": tenant_id, "deleted": now, "now": now},
        )
    return kb_id


def _bind_service(monkeypatch, engine) -> None:
    monkeypatch.setattr(
        trash,
        "get_session_local",
        lambda: sessionmaker(bind=engine, expire_on_commit=False, autoflush=False),
    )


def _record(engine, tenant_id: str, user_id: str, resource_id: str, deleted_at: str) -> str:
    return trash.record_deletion(
        tenant_id,
        "KB",
        resource_id,
        title="trash-test",
        deleted_by=user_id,
        deleted_at=deleted_at,
    )


def test_restore_rejects_expired_and_purging_rows(tmp_path: Path, monkeypatch) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'trash-restore.db'}")
    _apply_chain(engine)
    _bind_service(monkeypatch, engine)
    tenant_id, user_id = _tenant_user(engine)

    expired_id = _record(engine, tenant_id, user_id, _kb(engine, tenant_id), _ts(-31))
    with pytest.raises(ApiError) as expired:
        trash.restore_item(tenant_id, expired_id)
    assert expired.value.code == "RETENTION_EXPIRED"
    assert expired.value.status_code == 409

    purging_id = _record(engine, tenant_id, user_id, _kb(engine, tenant_id), _ts(1))
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE trash_items SET purge_state='PURGING_DB' WHERE id=:id"),
            {"id": purging_id},
        )
    with pytest.raises(ApiError) as purging:
        trash.restore_item(tenant_id, purging_id)
    assert purging.value.code == "PURGE_IN_PROGRESS"


def test_purge_requires_expiry_and_deletes_expired_resource(tmp_path: Path, monkeypatch) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'trash-purge.db'}")
    _apply_chain(engine)
    _bind_service(monkeypatch, engine)
    tenant_id, user_id = _tenant_user(engine)

    future_kb = _kb(engine, tenant_id)
    future_id = _record(engine, tenant_id, user_id, future_kb, _ts(1))
    with pytest.raises(ApiError) as not_due:
        trash.purge_item(tenant_id, future_id)
    assert not_due.value.code == "RETENTION_NOT_EXPIRED"
    assert not_due.value.status_code == 409

    old_kb = _kb(engine, tenant_id)
    old_id = _record(engine, tenant_id, user_id, old_kb, _ts(-31))
    result = trash.purge_item(tenant_id, old_id)
    assert result["id"] == old_id
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT 1 FROM knowledge_bases WHERE id=:id"), {"id": old_kb}
        ).first() is None
        state = connection.execute(
            text("SELECT purge_state, purged_at FROM trash_items WHERE id=:id"),
            {"id": old_id},
        ).first()
    assert state is not None and state[0] == "SUCCEEDED" and state[1] is not None


def test_restore_is_tenant_scoped_and_redeliveries_get_new_generation(
    tmp_path: Path, monkeypatch
) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'trash-generation.db'}")
    _apply_chain(engine)
    _bind_service(monkeypatch, engine)
    tenant_id, user_id = _tenant_user(engine)
    other_tenant = str(uuid4())

    kb_id = _kb(engine, tenant_id)
    first_deleted = _ts(-1)
    first_id = _record(engine, tenant_id, user_id, kb_id, first_deleted)
    with pytest.raises(ApiError) as cross_tenant:
        trash.restore_item(other_tenant, first_id)
    assert cross_tenant.value.code == "NOT_FOUND"

    trash.restore_item(tenant_id, first_id)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE knowledge_bases SET deleted_at=:deleted WHERE id=:id"),
            {"deleted": utc_now(), "id": kb_id},
        )
    second_id = _record(engine, tenant_id, user_id, kb_id, utc_now())

    with engine.connect() as connection:
        generations = connection.execute(
            text(
                "SELECT id, deletion_generation, expires_at FROM trash_items "
                "WHERE tenant_id=:tenant AND resource_type='KB' AND resource_id=:resource "
                "ORDER BY deletion_generation"
            ),
            {"tenant": tenant_id, "resource": kb_id},
        ).all()
    assert [str(row[0]) for row in generations] == [first_id, second_id]
    assert [int(row[1]) for row in generations] == [1, 2]
    assert str(generations[0][2]) != str(generations[1][2])

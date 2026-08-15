from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.core.security import hash_password
from ekb_api.migrations.v3_001_identity import apply_v3_001
from ekb_api.ops.admin_access import ensure_configured_admin, repair_admin_access


def _engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'admin-access.db'}")
    prepare_legacy_schema(engine, seed=False)
    apply_v3_001(engine)
    return engine


def _seed_user_and_kb(engine, *, user_role: str = "MEMBER") -> tuple[str, str, str]:
    tenant_id, user_id, kb_id = (str(uuid4()), str(uuid4()), str(uuid4()))
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tenants (
                  id, name, role, policy_version, model_routing_key, egress_policy,
                  quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file,
                  created_at, updated_at
                ) VALUES (:id, 'Tenant', 'OWNER', 1, 'default', 'ALLOW', 0, 0, 0, :now, :now)
                """
            ),
            {"id": tenant_id, "now": "2026-08-14T00:00:00Z"},
        )
        connection.execute(
            text(
                """
                INSERT INTO users (
                  id, name, email, password_hash, tenant_id, role, created_at, updated_at
                )
                VALUES (:id, 'Operator', 'operator@example.invalid', :password_hash, :tenant_id,
                        :role, :now, :now)
                """
            ),
            {
                "id": user_id,
                "password_hash": hash_password("original-password"),
                "tenant_id": tenant_id,
                "role": user_role,
                "now": "2026-08-14T00:00:00Z",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO knowledge_bases (
                  id, tenant_id, name, description, visibility, role, document_count,
                  deleted_at, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'Private KB', '', 'PRIVATE', 'OWNER', 0,
                  NULL, :now, :now
                )
                """
            ),
            {"id": kb_id, "tenant_id": tenant_id, "now": "2026-08-14T00:00:00Z"},
        )
    return tenant_id, user_id, kb_id


def test_existing_admin_bootstrap_preserves_password_and_adds_membership(tmp_path) -> None:
    engine = _engine(tmp_path)
    tenant_id, user_id, _ = _seed_user_and_kb(engine, user_role="ADMIN")
    with engine.connect() as connection:
        before_hash = connection.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()

    result = ensure_configured_admin(
        engine,
        email="operator@example.invalid",
        name="Ignored Existing Name",
        password="different-runtime-password",
    )

    assert result.created_user is False
    assert result.created_membership is True
    with engine.connect() as connection:
        after_hash = connection.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()
        membership = connection.execute(
            text(
                "SELECT role.slug, membership.status FROM tenant_memberships AS membership "
                "JOIN tenant_roles AS role ON role.id = membership.role_id "
                "WHERE membership.tenant_id = :tenant_id AND membership.user_id = :user_id"
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        ).one()
    assert after_hash == before_hash
    assert membership == ("admin", "ACTIVE")


def test_repair_is_dry_run_then_repairs_only_explicit_admin_and_kb(tmp_path) -> None:
    engine = _engine(tmp_path)
    tenant_id, user_id, kb_id = _seed_user_and_kb(engine)

    dry_run = repair_admin_access(
        engine,
        email="operator@example.invalid",
        tenant_id=tenant_id,
        kb_id=kb_id,
        tenant_role="OWNER",
        kb_role="OWNER",
        apply=False,
    )
    assert dry_run.mode == "dry-run"
    assert dry_run.legacy_role_action == "updated"
    assert dry_run.tenant_membership_action == "created"
    assert dry_run.kb_membership_action == "created"
    with engine.connect() as connection:
        role = connection.execute(
            text("SELECT role FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()
        assert role == "MEMBER"
        assert connection.execute(text("SELECT COUNT(*) FROM kb_memberships")).scalar_one() == 0

    applied = repair_admin_access(
        engine,
        email="operator@example.invalid",
        tenant_id=tenant_id,
        kb_id=kb_id,
        tenant_role="OWNER",
        kb_role="OWNER",
        apply=True,
    )
    assert applied.mode == "applied"
    assert applied.tenant_membership_action == "created"
    with engine.connect() as connection:
        role = connection.execute(
            text("SELECT role FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()
        assert role == "OWNER"
        assert connection.execute(
            text("SELECT COUNT(*) FROM tenant_memberships WHERE user_id = :id"), {"id": user_id}
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT role FROM kb_memberships WHERE kb_id = :id"), {"id": kb_id}
        ).scalar_one() == "OWNER"
        assert connection.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE action = 'admin_access_repair'")
        ).scalar_one() == 1

    again = repair_admin_access(
        engine,
        email="operator@example.invalid",
        tenant_id=tenant_id,
        kb_id=kb_id,
        tenant_role="OWNER",
        kb_role="OWNER",
        apply=True,
    )
    assert again.tenant_membership_action == "unchanged"
    assert again.kb_membership_action == "unchanged"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM kb_memberships")).scalar_one() == 1


def test_repair_refuses_cross_tenant_or_suspended_membership(tmp_path) -> None:
    engine = _engine(tmp_path)
    tenant_id, user_id, kb_id = _seed_user_and_kb(engine)
    with pytest.raises(RuntimeError, match="different tenant"):
        repair_admin_access(
            engine,
            email="operator@example.invalid",
            tenant_id=str(uuid4()),
            kb_id=kb_id,
            tenant_role="OWNER",
            kb_role="OWNER",
            apply=True,
        )

    repair_admin_access(
        engine,
        email="operator@example.invalid",
        tenant_id=tenant_id,
        kb_id=kb_id,
        tenant_role="OWNER",
        kb_role="OWNER",
        apply=True,
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenant_memberships SET status = 'SUSPENDED' WHERE user_id = :id"),
            {"id": user_id},
        )
    with pytest.raises(RuntimeError, match="not active"):
        repair_admin_access(
            engine,
            email="operator@example.invalid",
            tenant_id=tenant_id,
            kb_id=kb_id,
            tenant_role="OWNER",
            kb_role="OWNER",
            apply=True,
        )

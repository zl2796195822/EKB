from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from ekb_api.core.db import Base, build_engine, prepare_legacy_schema
from ekb_api.domain import utc_now
from ekb_api.migrations.v3_001_identity import (
    V3_001_OWNED_TABLES,
    apply_v3_001,
)


def _legacy_engine(
    tmp_path,
    *,
    tenant_ids: tuple[str, ...] = ("legacy-tenant",),
    users: tuple[tuple[str, str, str | None, str], ...] = (),
):
    engine = build_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    if any(role is None for _user_id, _tenant_id, role, _email in users):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255), "
                "role VARCHAR(32), policy_version INTEGER, model_routing_key VARCHAR(64), "
                "egress_policy VARCHAR(32), quota_daily_qa INTEGER, "
                "quota_storage_docs INTEGER, quota_storage_bytes_per_file INTEGER, "
                "created_at VARCHAR(32), updated_at VARCHAR(32))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255), "
                "email VARCHAR(255), tenant_id VARCHAR(36), role VARCHAR(32), "
                "created_at VARCHAR(32), updated_at VARCHAR(32))"
            )
    else:
        Base.metadata.create_all(engine)
    now = utc_now()
    with engine.begin() as connection:
        for tenant_id in tenant_ids:
            connection.execute(
                text(
                    """
                    INSERT INTO tenants
                        (id, name, role, policy_version, model_routing_key, egress_policy,
                         quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file,
                         created_at, updated_at)
                    VALUES
                        (:id, :name, 'OWNER', 1, 'default', 'allow', 0, 0, 0, :now, :now)
                    """
                ),
                {"id": tenant_id, "name": tenant_id, "now": now},
            )
        for user_id, tenant_id, role, email in users:
            connection.execute(
                text(
                    """
                    INSERT INTO users
                        (id, name, email, tenant_id, role, created_at, updated_at)
                    VALUES (:id, :name, :email, :tenant_id, :role, :now, :now)
                    """
                ),
                {
                    "id": user_id,
                    "name": user_id,
                    "email": email,
                    "tenant_id": tenant_id,
                    "role": role,
                    "now": now,
                },
            )
    return engine


def test_zero_user_tenant_gets_builtin_roles_and_permissions(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, tenant_ids=("empty-tenant",))

    apply_v3_001(engine)

    with engine.connect() as connection:
        roles = {
            row[0]
            for row in connection.execute(
                text("SELECT slug FROM tenant_roles WHERE tenant_id = 'empty-tenant'")
            )
        }
        assert roles == {"owner", "admin", "member", "auditor"}
        permission_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM role_permissions "
                "WHERE tenant_id = 'empty-tenant'"
            )
        ).scalar_one()
        assert permission_count == 18
        assert not connection.execute(
            text("SELECT 1 FROM tenant_memberships WHERE tenant_id = 'empty-tenant'")
        ).first()


def test_customer_backfill_is_exact_and_system_protected(tmp_path) -> None:
    engine = _legacy_engine(
        tmp_path,
        users=(("customer-user", "legacy-tenant", "CUSTOMER", "customer@example.com"),),
    )

    apply_v3_001(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, is_system_protected FROM tenant_roles "
                "WHERE tenant_id = 'legacy-tenant' AND slug = 'legacy_customer'"
            )
        ).one()
        permissions = {
            item[0]
            for item in connection.execute(
                text(
                    "SELECT capability FROM role_permissions "
                    "WHERE tenant_id = 'legacy-tenant' AND role_id = :role_id"
                ),
                {"role_id": row[0]},
            )
        }
    assert bool(row[1]) is True
    assert permissions == {"kb:read", "qa:ask"}


@pytest.mark.parametrize(
    ("role", "tenant_id", "message"),
    [
        ("UNKNOWN", "legacy-tenant", "unknown legacy role"),
        (None, "legacy-tenant", "null/unknown legacy role"),
        ("OWNER", "missing-tenant", "unresolved tenant"),
    ],
)
def test_invalid_legacy_rows_hard_fail(
    tmp_path, role: str | None, tenant_id: str, message: str
) -> None:
    engine = _legacy_engine(
        tmp_path,
        users=(("legacy-user", tenant_id, role, "legacy@example.com"),),
    )

    with pytest.raises(RuntimeError, match=message):
        apply_v3_001(engine)

    assert not set(V3_001_OWNED_TABLES).intersection(inspect(engine).get_table_names())


def test_cli_legacy_preparation_upgrades_without_seed_and_enables_fk(tmp_path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'cli-upgrade.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255), "
            "role VARCHAR(32), policy_version INTEGER, created_at VARCHAR(32), "
            "updated_at VARCHAR(32))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255), "
            "email VARCHAR(255), created_at VARCHAR(32), updated_at VARCHAR(32))"
        )
        connection.exec_driver_sql("CREATE TABLE feedback (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE messages (id VARCHAR(36) PRIMARY KEY)")

    prepare_legacy_schema(engine, seed=False)

    with engine.connect() as first_connection, engine.connect() as second_connection:
        assert first_connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert second_connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert first_connection.execute(text("SELECT COUNT(*) FROM tenants")).scalar_one() == 0
        assert set(V3_001_OWNED_TABLES).isdisjoint(inspect(first_connection).get_table_names())
        user_columns = {item["name"] for item in inspect(first_connection).get_columns("users")}
        tenant_columns = {
            item["name"] for item in inspect(first_connection).get_columns("tenants")
        }
        feedback_columns = {
            item["name"] for item in inspect(first_connection).get_columns("feedback")
        }
        message_columns = {
            item["name"] for item in inspect(first_connection).get_columns("messages")
        }
    assert {"password_hash", "tenant_id", "role"}.issubset(user_columns)
    assert {
        "model_routing_key",
        "egress_policy",
        "quota_daily_qa",
        "quota_storage_docs",
        "quota_storage_bytes_per_file",
    }.issubset(tenant_columns)
    assert {"status", "annotation"}.issubset(feedback_columns)
    assert {"turn_id", "visibility_state"}.issubset(message_columns)

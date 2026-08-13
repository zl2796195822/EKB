from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from ekb_api.core.db import (
    Base,
    _assert_legacy_metadata_boundary,
    _init_pgvector,
    get_pgvector_health,
)
from ekb_api.migrations.v3_001_identity import (
    V3_001_OWNED_TABLES,
    RollbackPlan,
    apply_v3_001,
    rollback_v3_001_dry_run,
    verify_v3_001,
)


def _legacy_engine(tmp_path, role: str = "CUSTOMER"):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tenants
                    (id, name, role, policy_version, model_routing_key, egress_policy,
                     quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file,
                     created_at, updated_at)
                VALUES
                    ('legacy-tenant', 'Legacy tenant', 'OWNER', 1, 'default', 'allow',
                     0, 0, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, name, email, tenant_id, role, created_at, updated_at)
                VALUES
                    ('legacy-user', 'Legacy user', 'legacy@example.com', :tenant_id, :role,
                     '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """
            ),
            {"tenant_id": "legacy-tenant", "role": role},
        )
    return engine


def test_legacy_create_all_cannot_create_v3_owned_tables(tmp_path) -> None:
    database_path = tmp_path / "fresh.db"
    from sqlalchemy import create_engine, inspect

    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    assert not set(V3_001_OWNED_TABLES).intersection(inspect(engine).get_table_names())


def test_v3_001_is_idempotent_immutable_and_dry_run_is_non_destructive(tmp_path) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{tmp_path / 'identity.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tenants
                    (id, name, role, policy_version, model_routing_key, egress_policy,
                     quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file,
                     created_at, updated_at)
                VALUES
                    (:id, :name, :role, :policy_version, :model_routing_key, :egress_policy,
                     :quota_daily_qa, :quota_storage_docs, :quota_storage_bytes_per_file,
                     :created_at, :updated_at)
                """
            ),
            {
                "id": "migration-tenant",
                "name": "Migration tenant",
                "role": "OWNER",
                "policy_version": 1,
                "model_routing_key": "default",
                "egress_policy": "allow",
                "quota_daily_qa": 0,
                "quota_storage_docs": 0,
                "quota_storage_bytes_per_file": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, name, email, tenant_id, role, created_at, updated_at)
                VALUES (:id, :name, :email, :tenant_id, :role, :created_at, :updated_at)
                """
            ),
            {
                "id": "migration-user",
                "name": "Migration user",
                "email": "migration@example.com",
                    "tenant_id": "migration-tenant",
                    "role": "OWNER",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        )

    first = apply_v3_001(engine)
    second = apply_v3_001(engine)

    assert first.checksum == second.checksum
    assert verify_v3_001(engine).status == "PASS"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM tenant_memberships")).scalar_one() == 1
        assert (
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM role_permissions "
                    "WHERE capability = 'kb:write' AND role_id IN "
                    "(SELECT id FROM tenant_roles WHERE slug = 'legacy_customer')"
                )
            ).scalar_one()
            == 0
        )

    plan = rollback_v3_001_dry_run(engine)
    assert isinstance(plan, RollbackPlan)
    assert plan.applied is True
    assert plan.objects == tuple(
        item for item in reversed(V3_001_OWNED_TABLES) if item != "schema_migrations"
    )
    assert plan.retained == ("schema_migrations",)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 1


def test_rollback_unapplied_and_framework_only_ledger_have_no_down_plan(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    plan = rollback_v3_001_dry_run(engine)
    assert plan.applied is False
    assert plan.objects == ()
    assert plan.retained == ("schema_migrations",)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE schema_migrations (version VARCHAR(64) PRIMARY KEY, "
                "applied_at VARCHAR(32) NOT NULL, checksum VARCHAR(128) NOT NULL)"
            )
        )
    framework_only = rollback_v3_001_dry_run(engine)
    assert framework_only.applied is False
    assert framework_only.objects == ()
    assert framework_only.retained == ("schema_migrations",)


def test_rollback_blocks_assigned_legacy_customer(tmp_path) -> None:
    engine = _legacy_engine(tmp_path, role="CUSTOMER")
    apply_v3_001(engine)

    plan = rollback_v3_001_dry_run(engine)

    assert plan.applied is True
    assert plan.blocked_reason == "legacy_customer_assigned"
    assert plan.objects == ()
    assert plan.retained == ("schema_migrations",)


@pytest.mark.parametrize("orphan_kind", ["table", "index"])
def test_orphan_v3_object_without_ledger_is_hard_failure(tmp_path, orphan_kind: str) -> None:
    engine = _legacy_engine(tmp_path)
    with engine.begin() as connection:
        if orphan_kind == "table":
            connection.execute(text("CREATE TABLE tenant_roles (id VARCHAR(36))"))
        else:
            connection.execute(
                text(
                    "CREATE TABLE tenant_memberships (tenant_id VARCHAR(36), "
                    "status VARCHAR(32), updated_at VARCHAR(32))"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_memberships_tenant_status "
                    "ON tenant_memberships (tenant_id, status, updated_at DESC)"
                )
            )

    with pytest.raises(RuntimeError, match="orphan"):
        apply_v3_001(engine)


def test_applied_ledger_with_missing_business_object_is_hard_failure(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    apply_v3_001(engine)
    connection = engine.connect()
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.execute(text("DROP TABLE tenant_roles"))
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="missing v3_001 object"):
        apply_v3_001(engine)


def test_applied_ledger_with_missing_index_is_hard_failure(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    apply_v3_001(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_memberships_tenant_status"))

    with pytest.raises(RuntimeError, match="missing v3_001 object"):
        apply_v3_001(engine)


def test_v3_001_checksum_mismatch_is_hard_failure(tmp_path) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{tmp_path / 'mismatch.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE schema_migrations ("
                "version VARCHAR(64) PRIMARY KEY, applied_at VARCHAR(32) NOT NULL, "
                "checksum VARCHAR(128) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO schema_migrations(version, applied_at, checksum) VALUES "
                "('v3_001_identity', '2026-01-01T00:00:00Z', 'tampered')"
            )
        )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        apply_v3_001(engine)


@pytest.mark.parametrize(
    ("role", "tenant_id", "message"),
    [
        ("UNKNOWN", "legacy-tenant", "unknown legacy role"),
        ("OWNER", "missing-tenant", "unresolved tenant"),
    ],
)
def test_v3_001_invalid_legacy_mapping_rolls_back_without_ledger(
    tmp_path, role: str, tenant_id: str, message: str
) -> None:
    engine = _legacy_engine(tmp_path, role=role)
    if tenant_id != "legacy-tenant":
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET tenant_id = :tenant_id WHERE id = 'legacy-user'"),
                {"tenant_id": tenant_id},
            )

    with pytest.raises(RuntimeError, match=message):
        apply_v3_001(engine)

    tables = set(inspect(engine).get_table_names())
    assert not tables.intersection(V3_001_OWNED_TABLES)


def test_v3_001_rejects_cross_tenant_role_creator_on_reapply(tmp_path) -> None:
    engine = _legacy_engine(tmp_path)
    apply_v3_001(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tenants
                    (id, name, role, policy_version, model_routing_key, egress_policy,
                     quota_daily_qa, quota_storage_docs, quota_storage_bytes_per_file,
                     created_at, updated_at)
                VALUES
                    ('other-tenant', 'Other tenant', 'OWNER', 1, 'default', 'allow',
                     0, 0, 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO users
                    (id, name, email, tenant_id, role, created_at, updated_at)
                VALUES
                    ('other-user', 'Other user', 'other@example.com', 'other-tenant', 'MEMBER',
                     '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """
            )
        )
        connection.execute(
            text(
                "UPDATE tenant_roles SET created_by = 'other-user' "
                "WHERE tenant_id = 'legacy-tenant' AND slug = 'owner'"
            )
        )

    with pytest.raises(RuntimeError, match="cross-tenant role creator"):
        apply_v3_001(engine)


def test_v3_001_capability_snapshot_ignores_runtime_registry_extensions(
    monkeypatch, tmp_path
) -> None:
    import ekb_api.core.authorization as authorization
    import ekb_api.migrations.v3_001_identity as migration

    checksum = migration.V3_001_CHECKSUM
    expanded_registry = {
        **authorization.V3_ROLE_CAPABILITIES,
        "owner": [*authorization.V3_ROLE_CAPABILITIES["owner"], "team:role:assign"],
        "legacy_customer": [
            *authorization.V3_ROLE_CAPABILITIES["legacy_customer"],
            "profile:api_key:manage",
        ],
    }
    monkeypatch.setattr(authorization, "V3_ROLE_CAPABILITIES", expanded_registry)
    monkeypatch.setattr(
        authorization,
        "v3_capabilities_for_role",
        lambda role: list(expanded_registry[role]),
    )

    assert migration.V3_001_CHECKSUM == checksum
    engine = _legacy_engine(tmp_path, role="CUSTOMER")
    migration.apply_v3_001(engine)
    assert migration.verify_v3_001(engine).checksum == checksum
    with engine.connect() as connection:
        capabilities = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT capability FROM role_permissions "
                    "WHERE role_id = (SELECT id FROM tenant_roles "
                    "WHERE tenant_id = 'legacy-tenant' AND slug = 'legacy_customer')"
                )
            )
        }
    assert capabilities == {"kb:read", "qa:ask"}


def test_legacy_metadata_unknown_table_fails_closed() -> None:
    from sqlalchemy import Table

    table = Table("v3_002_future_owned", Base.metadata)
    try:
        with pytest.raises(RuntimeError, match="outside the legacy allowlist"):
            _assert_legacy_metadata_boundary()
    finally:
        Base.metadata.remove(table)


class _FailingPgvectorEngine:
    def begin(self):
        return self

    def __enter__(self):
        raise RuntimeError("simulated extension failure")

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


def test_pgvector_required_failure_is_fail_closed_without_postgres() -> None:
    settings = SimpleNamespace(environment="production", is_production=True)

    with pytest.raises(RuntimeError, match="required pgvector initialization failed"):
        _init_pgvector(_FailingPgvectorEngine(), settings=settings)

    assert get_pgvector_health() == {
        "required": True,
        "available": False,
        "status": "required_failure",
    }


def test_pgvector_optional_failure_is_observable_without_postgres(caplog) -> None:
    settings = SimpleNamespace(environment="test", is_production=False)
    caplog.set_level(logging.WARNING, logger="ekb_api.core.db")

    _init_pgvector(_FailingPgvectorEngine(), settings=settings)

    assert "db.pgvector.optional_initialization_skipped" in caplog.text
    assert get_pgvector_health() == {
        "required": False,
        "available": False,
        "status": "optional_unavailable",
    }

"""Ops-level tests for the explicit embedding profile wiring CLI helpers.

Covers the S2 gap of the production embedding plan: validation refusals
(cross-tenant, disabled, non-embedding, dimension mismatch) and the
idempotent apply path that wires profile + ACTIVE generation onto a KB.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.ops.embedding_profiles import (
    ProfilePlanRefused,
    apply_kb_profile,
    plan_kb_profile,
)


@pytest.fixture()
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'embedding_ops.db'}")
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _ids(engine) -> tuple[str, str]:
    with engine.connect() as connection:
        tenant_id = str(
            connection.execute(text("SELECT id FROM tenants ORDER BY id LIMIT 1")).scalar_one()
        )
        kb_id = str(
            connection.execute(
                text("SELECT id FROM knowledge_bases WHERE tenant_id=:t ORDER BY id LIMIT 1"),
                {"t": tenant_id},
            ).scalar_one()
        )
    return tenant_id, kb_id


def _seed_provider_and_model(
    engine,
    tenant_id: str,
    *,
    model_type: str = "embedding",
    provider_enabled: bool = True,
    model_enabled: bool = True,
    capabilities: dict | None = None,
) -> tuple[str, str]:
    provider_id, model_row_id = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO llm_providers (id, tenant_id, user_id, provider_key, name, "
                "websites, endpoint_configs, auth_type, api_features, settings, "
                "model_list_source, is_enabled, created_at, updated_at) VALUES "
                "(:id, :tenant, (SELECT id FROM users WHERE tenant_id=:tenant LIMIT 1), "
                ":key, 'ops test provider', '{}', '{}', 'api-key', '{}', '{}', 'api', "
                ":enabled, '2026-08-16T00:00:00Z', '2026-08-16T00:00:00Z')"
            ),
            {
                "id": provider_id,
                "tenant": tenant_id,
                "key": f"ops-{provider_id[:8]}",
                "enabled": provider_enabled,
            },
        )
        connection.execute(
            text(
                "INSERT INTO llm_models (id, tenant_id, user_id, provider_id, model_id, "
                "display_name, model_type, capabilities, is_enabled, is_custom, "
                "created_at, updated_at) "
                "VALUES (:id, :tenant, (SELECT id FROM users WHERE tenant_id=:tenant LIMIT 1), "
                ":provider, 'bge-m3', 'bge-m3', :model_type, :capabilities, :enabled, 0, "
                "'2026-08-16T00:00:00Z', '2026-08-16T00:00:00Z')"
            ),
            {
                "id": model_row_id,
                "tenant": tenant_id,
                "provider": provider_id,
                "model_type": model_type,
                "capabilities": json.dumps(capabilities or {}),
                "enabled": model_enabled,
            },
        )
    return provider_id, model_row_id


def _kb_wiring(engine, kb_id: str) -> dict:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT embedding_profile_id, active_index_generation_id "
                    "FROM knowledge_bases WHERE id=:kb"
                ),
                {"kb": kb_id},
            ).mappings().one()
        )


def test_dry_run_then_apply_creates_and_wires_profile_idempotently(engine) -> None:
    tenant_id, kb_id = _ids(engine)
    provider_id, model_row_id = _seed_provider_and_model(engine, tenant_id)

    plan = plan_kb_profile(
        engine,
        tenant_id=tenant_id,
        kb_id=kb_id,
        llm_provider_id=provider_id,
        model_id=model_row_id,
        dimensions=1024,
    )
    assert plan.mode == "dry-run"
    assert plan.profile_action == "create"
    assert plan.generation_action == "create+activate"
    assert _kb_wiring(engine, kb_id)["embedding_profile_id"] is None

    applied = apply_kb_profile(
        engine,
        tenant_id=tenant_id,
        kb_id=kb_id,
        llm_provider_id=provider_id,
        model_id=model_row_id,
        dimensions=1024,
    )
    assert applied.mode == "apply"
    assert applied.profile_id and applied.active_generation_id
    wiring = _kb_wiring(engine, kb_id)
    assert wiring["embedding_profile_id"] == applied.profile_id
    assert wiring["active_index_generation_id"] == applied.active_generation_id
    with engine.connect() as connection:
        state = str(
            connection.execute(
                text("SELECT state FROM index_generations WHERE id=:id"),
                {"id": applied.active_generation_id},
            ).scalar_one()
        )
    assert state == "ACTIVE"

    again = apply_kb_profile(
        engine,
        tenant_id=tenant_id,
        kb_id=kb_id,
        llm_provider_id=provider_id,
        model_id=model_row_id,
        dimensions=1024,
    )
    assert again.profile_action == "reuse"
    assert again.generation_action == "reuse-active"
    assert again.profile_id == applied.profile_id
    assert again.active_generation_id == applied.active_generation_id
    # Still exactly one ACTIVE generation for the KB.
    with engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT COUNT(*) FROM index_generations "
                "WHERE knowledge_base_id=:kb AND state='ACTIVE'"
            ),
            {"kb": kb_id},
        ).scalar_one()
    assert active == 1


def test_cross_tenant_kb_is_refused(engine) -> None:
    tenant_id, _ = _ids(engine)
    provider_id, model_row_id = _seed_provider_and_model(engine, tenant_id)
    other_tenant, other_kb = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, role, policy_version, model_routing_key, "
                "egress_policy, quota_daily_qa, quota_storage_docs, "
                "quota_storage_bytes_per_file, created_at, updated_at) VALUES "
                "(:id, 'other', 'OWNER', 1, 'default', 'allow', 0, 0, 0, "
                "'2026-08-16T00:00:00Z', '2026-08-16T00:00:00Z')"
            ),
            {"id": other_tenant},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, tenant_id, name, description, "
                "visibility, role, document_count, created_at, updated_at) VALUES "
                "(:id, :tenant, 'other tenant kb', '', 'PRIVATE', 'OWNER', 0, "
                "'2026-08-16T00:00:00Z', '2026-08-16T00:00:00Z')"
            ),
            {"id": other_kb, "tenant": other_tenant},
        )
    with pytest.raises(ProfilePlanRefused, match="another tenant"):
        plan_kb_profile(
            engine,
            tenant_id=tenant_id,
            kb_id=other_kb,
            llm_provider_id=provider_id,
            model_id=model_row_id,
            dimensions=1024,
        )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"provider_enabled": False},
        {"model_enabled": False},
        {"model_type": "chat"},
    ),
)
def test_disabled_or_non_embedding_targets_are_refused(engine, kwargs) -> None:
    tenant_id, kb_id = _ids(engine)
    provider_id, model_row_id = _seed_provider_and_model(engine, tenant_id, **kwargs)
    with pytest.raises(ProfilePlanRefused):
        plan_kb_profile(
            engine,
            tenant_id=tenant_id,
            kb_id=kb_id,
            llm_provider_id=provider_id,
            model_id=model_row_id,
            dimensions=1024,
        )
    assert _kb_wiring(engine, kb_id)["embedding_profile_id"] is None


def test_declared_dimension_mismatch_is_refused(engine) -> None:
    tenant_id, kb_id = _ids(engine)
    provider_id, model_row_id = _seed_provider_and_model(
        engine, tenant_id, capabilities={"dimensions": 1024}
    )
    with pytest.raises(ProfilePlanRefused, match="dimensions=1024"):
        plan_kb_profile(
            engine,
            tenant_id=tenant_id,
            kb_id=kb_id,
            llm_provider_id=provider_id,
            model_id=model_row_id,
            dimensions=2560,
        )


@pytest.mark.parametrize("dimensions", (0, -1, 40000))
def test_invalid_dimensions_are_refused(engine, dimensions) -> None:
    tenant_id, kb_id = _ids(engine)
    provider_id, model_row_id = _seed_provider_and_model(engine, tenant_id)
    with pytest.raises(ProfilePlanRefused):
        plan_kb_profile(
            engine,
            tenant_id=tenant_id,
            kb_id=kb_id,
            llm_provider_id=provider_id,
            model_id=model_row_id,
            dimensions=dimensions,
        )


def test_cli_dry_run_and_apply_round_trip(tmp_path) -> None:

    db_url = f"sqlite:///{tmp_path / 'embedding_cli.db'}"
    engine = build_engine(db_url)
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)
    tenant_id, kb_id = _ids(engine)
    provider_id, model_row_id = _seed_provider_and_model(engine, tenant_id)
    engine.dispose()

    from configure_embedding_profile import main as cli_main

    assert (
        cli_main(
            [
                "--database-url",
                db_url,
                "--tenant-id",
                tenant_id,
                "--kb-id",
                kb_id,
                "--provider-id",
                provider_id,
                "--model-id",
                model_row_id,
                "--dimensions",
                "1024",
            ]
        )
        == 0
    )
    assert (
        cli_main(
            [
                "--database-url",
                db_url,
                "--tenant-id",
                tenant_id,
                "--kb-id",
                kb_id,
                "--provider-id",
                provider_id,
                "--model-id",
                model_row_id,
                "--dimensions",
                "1024",
                "--apply",
            ]
        )
        == 0
    )
    check = build_engine(db_url)
    try:
        assert _kb_wiring(check, kb_id)["embedding_profile_id"] is not None
    finally:
        check.dispose()

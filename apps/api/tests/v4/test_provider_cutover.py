from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from ekb_api.core.config import get_settings
from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import utc_now
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.ops.provider_cutover import (
    ProviderCutoverError,
    ProviderOwnerMapping,
    import_provider_configuration,
)
from ekb_api.services import secrets as provider_secrets


def _prepare(engine) -> None:
    prepare_legacy_schema(engine, seed=True)
    for step in CHAIN:
        step.apply(engine)


@pytest.fixture()
def cutover_engines(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EKB_PROVIDER_MASTER_KEY", "provider-cutover-test-master-key")
    get_settings.cache_clear()
    source_path = tmp_path / "source.db"
    source = build_engine(f"sqlite:///{source_path}")
    _prepare(source)
    with source.begin() as connection:
        tenant_id, user_id = connection.execute(
            text("SELECT tenant_id, id FROM users LIMIT 1")
        ).one()
        legacy_provider_id = str(uuid4())
        encrypted_provider_id = str(uuid4())
        now = utc_now()
        connection.execute(
            text(
                "INSERT INTO llm_providers "
                "(id, tenant_id, user_id, provider_key, name, websites, endpoint_configs, "
                "api_features, settings, auth_type, model_list_source, api_key, is_enabled, "
                "created_at, updated_at) "
                "VALUES (:id,:tenant,:user,:key,:name,'{}',:endpoint,'{}','{}','api-key',"
                "'api',:api_key,1,:now,:now)"
            ),
            {
                "id": legacy_provider_id,
                "tenant": tenant_id,
                "user": user_id,
                "key": "legacy-provider",
                "name": "Legacy Provider",
                "endpoint": '{"openai-chat-completions":{"baseUrl":"https://example.invalid/v1"}}',
                "api_key": "legacy-secret",
                "now": now,
            },
        )
        envelope = provider_secrets.encrypt("encrypted-secret")
        credential_id = str(uuid4())
        connection.execute(
            text(
                "INSERT INTO provider_credentials "
                "(id, tenant_id, owner_user_id, ownership_scope, ownership_key, ciphertext, "
                "key_version, secret_last4, status, created_at) "
                "VALUES (:id,:tenant,:user,'PERSONAL',:ownership_key,:ciphertext,"
                ":key_version,:last4,'ACTIVE',:now)"
            ),
            {
                "id": credential_id,
                "tenant": tenant_id,
                "user": user_id,
                "ownership_key": f"USER:{user_id}",
                "ciphertext": envelope.ciphertext,
                "key_version": envelope.key_version,
                "last4": envelope.secret_last4,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO llm_providers "
                "(id, tenant_id, user_id, provider_key, name, websites, endpoint_configs, "
                "api_features, settings, credential_id, ownership_scope, ownership_key, "
                "auth_type, model_list_source, is_enabled, created_at, updated_at) "
                "VALUES (:id,:tenant,:user,:key,:name,'{}',:endpoint,'{}','{}',:credential_id,"
                "'PERSONAL',:ownership_key,'api-key','api',1,:now,:now)"
            ),
            {
                "id": encrypted_provider_id,
                "tenant": tenant_id,
                "user": user_id,
                "key": "encrypted-provider",
                "name": "Encrypted Provider",
                "endpoint": '{"openai-chat-completions":{"baseUrl":"https://example.invalid/v1"}}',
                "credential_id": credential_id,
                "ownership_key": f"USER:{user_id}",
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO llm_models "
                "(id, tenant_id, user_id, provider_id, model_id, display_name, model_type, "
                "capabilities, is_enabled, is_custom, created_at, updated_at) "
                "VALUES (:id,:tenant,:user,:provider,:model,:name,'chat','{}',1,0,:now,:now)"
            ),
            {
                "id": str(uuid4()),
                "tenant": tenant_id,
                "user": user_id,
                "provider": legacy_provider_id,
                "model": "legacy-chat",
                "name": "Legacy Chat",
                "now": now,
            },
        )

    target_path = tmp_path / "target.db"
    shutil.copyfile(source_path, target_path)
    target = build_engine(f"sqlite:///{target_path}")
    with target.begin() as connection:
        connection.execute(text("DELETE FROM llm_models"))
        connection.execute(text("DELETE FROM llm_providers"))
        connection.execute(text("DELETE FROM provider_credentials"))
        connection.execute(text("DELETE FROM model_fallback_policies"))
    yield source, target
    get_settings.cache_clear()


def test_provider_cutover_dry_run_then_encrypts_legacy_keys(cutover_engines) -> None:
    source, target = cutover_engines

    dry_run = import_provider_configuration(source, target)
    assert dry_run.mode == "dry-run"
    assert dry_run.providers == 2
    assert dry_run.credentials_imported == 1
    assert dry_run.credentials_encrypted_from_legacy == 1
    with target.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM llm_providers")).scalar_one() == 0

    applied = import_provider_configuration(source, target, apply=True)
    assert applied.mode == "apply"
    with target.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM llm_providers WHERE api_key IS NOT NULL")
        ).scalar_one() == 0
        secrets = [
            str(row[0])
            for row in connection.execute(
                text("SELECT ciphertext FROM provider_credentials ORDER BY id")
            )
        ]
    assert sorted(provider_secrets.decrypt(value) for value in secrets) == [
        "encrypted-secret",
        "legacy-secret",
    ]


def test_provider_cutover_refuses_a_nonempty_target(cutover_engines) -> None:
    source, target = cutover_engines
    with target.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO llm_providers "
                "(id, tenant_id, user_id, provider_key, name, websites, endpoint_configs, "
                "api_features, settings, auth_type, model_list_source, is_enabled, "
                "created_at, updated_at) "
                "SELECT :id, tenant_id, id, 'occupied', 'Occupied', '{}', '{}', '{}', '{}', "
                "'api-key', 'api', 0, :now, :now "
                "FROM users LIMIT 1"
            ),
            {"id": str(uuid4()), "now": utc_now()},
        )

    with pytest.raises(ProviderCutoverError, match="not empty"):
        import_provider_configuration(source, target, apply=True)


def test_provider_cutover_deduplicates_repeated_provider_keys(cutover_engines) -> None:
    source, target = cutover_engines
    with source.begin() as connection:
        tenant_id, user_id = connection.execute(
            text("SELECT tenant_id, id FROM users LIMIT 1")
        ).one()
        connection.execute(
            text(
                "INSERT INTO llm_providers "
                "(id, tenant_id, user_id, provider_key, name, websites, endpoint_configs, "
                "api_features, settings, auth_type, model_list_source, api_key, is_enabled, "
                "created_at, updated_at) "
                "VALUES (:id,:tenant,:user,'legacy-provider','Duplicate','{}','{}','{}','{}',"
                "'api-key','api','discarded-secret',0,:now,:now)"
            ),
            {
                "id": str(uuid4()),
                "tenant": tenant_id,
                "user": user_id,
                "now": utc_now(),
            },
        )

    report = import_provider_configuration(source, target, apply=True)
    assert report.providers == 2
    assert report.duplicate_providers_skipped == 1
    with target.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM llm_providers WHERE provider_key='legacy-provider'")
        ).scalar_one() == 1


def test_provider_cutover_requires_and_applies_an_explicit_owner_mapping(
    cutover_engines, tmp_path: Path
) -> None:
    source, _ = cutover_engines
    target = build_engine(f"sqlite:///{tmp_path / 'independent-target.db'}")
    _prepare(target)
    with target.begin() as connection:
        for table in (
            "llm_models",
            "llm_providers",
            "provider_credentials",
            "model_fallback_policies",
        ):
            connection.execute(text(f"DELETE FROM {table}"))
        target_tenant_id, target_user_id = connection.execute(
            text("SELECT tenant_id, id FROM users LIMIT 1")
        ).one()
    with source.connect() as connection:
        source_tenant_id, source_user_id = connection.execute(
            text("SELECT tenant_id, id FROM users LIMIT 1")
        ).one()

    with pytest.raises(ProviderCutoverError, match="missing an LLM configuration owner"):
        import_provider_configuration(source, target)

    mapping = ProviderOwnerMapping(
        source_tenant_id=str(source_tenant_id),
        source_user_id=str(source_user_id),
        target_tenant_id=str(target_tenant_id),
        target_user_id=str(target_user_id),
    )
    report = import_provider_configuration(source, target, apply=True, owner_mapping=mapping)
    assert report.providers == 2

    with target.connect() as connection:
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM llm_providers "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND api_key IS NULL"
            ),
            {"tenant_id": target_tenant_id, "user_id": target_user_id},
        ).scalar_one() == 2
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM provider_credentials "
                "WHERE tenant_id=:tenant_id AND owner_user_id=:user_id "
                "AND ownership_key=:ownership_key"
            ),
            {
                "tenant_id": target_tenant_id,
                "user_id": target_user_id,
                "ownership_key": f"USER:{target_user_id}",
            },
        ).scalar_one() == 2

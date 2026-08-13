from __future__ import annotations

import secrets as stdlib_secrets

import pytest
from sqlalchemy import text

from ekb_api.core.config import get_settings
from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.services import secrets as provider_secrets
from ekb_api.services.llm_provider_catalog import get_preset_provider, list_preset_providers
from ekb_api.services.v3_llm import _remote_models_endpoint


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def _provider_payload(key: str, api_key: str) -> dict:
    return {
        "provider_key": key,
        "name": "Credential integration test",
        "api_key": api_key,
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://provider.example.invalid/v1",
            }
        },
        "default_chat_endpoint": "openai-chat-completions",
        "is_enabled": True,
    }


def _login(client) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_provider_secret_is_encrypted_and_redacted(client, monkeypatch) -> None:
    master_key = "provider-master-" + stdlib_secrets.token_hex(16)
    monkeypatch.setenv("EKB_PROVIDER_MASTER_KEY", master_key)
    _clear_settings_cache()
    dev_token = _login(client)

    api_key = "runtime-secret-" + stdlib_secrets.token_hex(16)
    provider_key = "credential-test-" + stdlib_secrets.token_hex(8)
    response = client.post(
        "/api/v1/llm/providers",
        headers={"Authorization": f"Bearer {dev_token}"},
        json=_provider_payload(provider_key, api_key),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_api_key"] is True
    assert "api_key" not in body

    provider_id = body["id"]
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT p.api_key, p.credential_id, c.ciphertext "
                "FROM llm_providers p "
                "JOIN provider_credentials c ON c.id = p.credential_id "
                "WHERE p.id=:id"
            ),
            {"id": provider_id},
        ).mappings().one()
    assert row["api_key"] is None
    assert row["credential_id"]
    assert api_key not in row["ciphertext"]
    assert provider_secrets.decrypt(row["ciphertext"]) == api_key


def test_provider_secret_write_fails_closed_without_master_key(client, monkeypatch) -> None:
    monkeypatch.delenv("EKB_PROVIDER_MASTER_KEY", raising=False)
    _clear_settings_cache()
    dev_token = _login(client)

    provider_key = "no-master-" + stdlib_secrets.token_hex(8)
    response = client.post(
        "/api/v1/llm/providers",
        headers={"Authorization": f"Bearer {dev_token}"},
        json=_provider_payload(provider_key, "runtime-secret-" + stdlib_secrets.token_hex(16)),
    )
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "PROVIDER_CREDENTIALS_UNAVAILABLE"

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        assert session.execute(
            text("SELECT COUNT(*) FROM llm_providers WHERE provider_key=:key"),
            {"key": provider_key},
        ).scalar_one() == 0


def test_preset_catalog_excludes_local_inference_providers(client) -> None:
    local_keys = {
        "ollama",
        "lmstudio",
        "gpustack",
        "ovms",
        "opencode",
    }
    catalog_ids = {provider["id"] for provider in list_preset_providers()}
    assert catalog_ids.isdisjoint(local_keys)
    for key in local_keys:
        assert get_preset_provider(key) is None

    token = _login(client)
    response = client.get(
        "/api/v1/llm/providers/catalog",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert {item["id"] for item in response.json()["items"]}.isdisjoint(local_keys)


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:1234/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "http://0.0.0.0:8000/v1",
    ),
)
def test_local_provider_endpoint_is_rejected_without_db_residue(client, base_url) -> None:
    token = _login(client)
    provider_key = "local-boundary-" + stdlib_secrets.token_hex(8)
    response = client.post(
        "/api/v1/llm/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider_key": provider_key,
            "name": "Local endpoint boundary",
            "endpoint_configs": {
                "openai-chat-completions": {"baseUrl": base_url},
            },
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "REMOTE_PROVIDER_REQUIRED"

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        assert session.execute(
            text("SELECT COUNT(*) FROM llm_providers WHERE provider_key=:key"),
            {"key": provider_key},
        ).scalar_one() == 0


def test_remote_provider_can_be_created_and_read_without_network(client) -> None:
    token = _login(client)
    provider_key = "remote-boundary-" + stdlib_secrets.token_hex(8)
    endpoint_configs = {
        "openai-chat-completions": {
            "baseUrl": "https://provider.example.invalid/v1",
            "modelsApiUrls": {"default": "https://models.example.invalid/v1"},
        }
    }
    response = client.post(
        "/api/v1/llm/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider_key": provider_key,
            "name": "Remote boundary provider",
            "endpoint_configs": endpoint_configs,
        },
    )
    assert response.status_code == 200, response.text
    provider_id = response.json()["id"]

    read_response = client.get(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read_response.status_code == 200, read_response.text
    assert read_response.json()["endpoint_configs"] == endpoint_configs


def test_update_local_endpoint_is_rejected_and_original_value_is_preserved(client) -> None:
    token = _login(client)
    provider_key = "update-boundary-" + stdlib_secrets.token_hex(8)
    original_endpoint = "https://provider.example.invalid/v1"
    create_response = client.post(
        "/api/v1/llm/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider_key": provider_key,
            "name": "Update boundary provider",
            "endpoint_configs": {
                "openai-chat-completions": {"baseUrl": original_endpoint},
            },
        },
    )
    assert create_response.status_code == 200, create_response.text
    provider_id = create_response.json()["id"]

    update_response = client.patch(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "endpoint_configs": {
                "openai-chat-completions": {"baseUrl": "http://localhost:1234/v1"},
            }
        },
    )
    assert update_response.status_code == 400, update_response.text
    assert update_response.json()["error"]["code"] == "REMOTE_PROVIDER_REQUIRED"

    read_response = client.get(
        f"/api/v1/llm/providers/{provider_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read_response.status_code == 200, read_response.text
    assert (
        read_response.json()["endpoint_configs"]["openai-chat-completions"]["baseUrl"]
        == original_endpoint
    )


@pytest.mark.parametrize(
    "models_url",
    (
        "http://localhost:8000/models",
        "http://127.0.0.1:8000/models",
        "http://[::1]:8000/models",
        "http://0.0.0.0:8000/models",
    ),
)
def test_models_endpoint_rejects_loopback_address(models_url) -> None:
    with pytest.raises(ApiError) as error:
        _remote_models_endpoint(
            {
                "default_chat_endpoint": "openai-chat-completions",
                "endpoint_configs": {
                    "openai-chat-completions": {
                        "baseUrl": "https://provider.example.invalid/v1",
                        "modelsApiUrls": {"default": models_url},
                    }
                },
            }
        )
    assert error.value.code == "REMOTE_PROVIDER_REQUIRED"

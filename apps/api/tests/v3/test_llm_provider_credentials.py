from __future__ import annotations

import secrets as stdlib_secrets

from sqlalchemy import text

from ekb_api.core.config import get_settings
from ekb_api.core.db import get_session_local
from ekb_api.services import secrets as provider_secrets


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

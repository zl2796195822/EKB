from __future__ import annotations

import secrets as stdlib_secrets

import pytest
from sqlalchemy import text

from ekb_api.core.circuit_breaker import get_circuit_breaker
from ekb_api.core.config import get_settings
from ekb_api.core.db import get_session_local
from ekb_api.routers import qa as qa_router


@pytest.fixture(autouse=True)
def reset_llm_breaker_between_selection_tests():
    """Do not leak breaker, provider, or settings state into later API tests."""
    breaker = get_circuit_breaker()
    breaker.reset()
    yield
    session_local = get_session_local()
    with session_local() as session:
        provider_filter = (
            "(provider_key LIKE 'selection-%' OR provider_key LIKE 'cross-tenant-%')"
        )
        session.execute(
            text(
                "UPDATE provider_credentials SET status='REVOKED' "
                "WHERE id IN (SELECT credential_id FROM llm_providers "
                f"WHERE {provider_filter} AND credential_id IS NOT NULL)"
            )
        )
        session.execute(
            text(
                "UPDATE llm_models SET is_enabled=0 "
                "WHERE provider_id IN (SELECT id FROM llm_providers "
                f"WHERE {provider_filter})"
            )
        )
        session.execute(
            text(f"UPDATE llm_providers SET is_enabled=0 WHERE {provider_filter}")
        )
        session.commit()
    breaker.reset()
    get_settings.cache_clear()


def _login(client) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _configured_model(client, monkeypatch, *, vision: bool = False) -> tuple[str, str, str]:
    monkeypatch.setenv("EKB_PROVIDER_MASTER_KEY", "selection-test-master-key")
    get_settings.cache_clear()
    token = _login(client)
    suffix = stdlib_secrets.token_hex(6)
    provider_key = f"selection-{suffix}"
    provider = client.post(
        "/api/v1/llm/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "provider_key": provider_key,
            "name": "Selection test provider",
            "api_key": "fixture-only-secret",
            "default_chat_endpoint": "openai-chat-completions",
            "endpoint_configs": {
                "openai-chat-completions": {
                    "baseUrl": "https://provider.invalid/v1"
                }
            },
            "is_enabled": True,
        },
    )
    assert provider.status_code == 200, provider.text
    provider_id = provider.json()["id"]
    model = client.post(
        f"/api/v1/llm/providers/{provider_id}/models",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model_id": "selection-model",
            "display_name": "Selection Model",
            "capabilities": {"vision": vision, "reasoning": True},
            "is_enabled": True,
        },
    )
    assert model.status_code == 200, model.text
    return token, f"{provider_key}/selection-model", model.json()["id"]


def test_capabilities_and_valid_selection_share_active_db_registry(
    client, monkeypatch
) -> None:
    token, selection_id, model_pk = _configured_model(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}

    capabilities = client.get("/api/v1/qa/capabilities", headers=headers)
    assert capabilities.status_code == 200, capabilities.text
    models = {item["id"]: item for item in capabilities.json()["models"]}
    assert selection_id in models
    assert models[selection_id]["supports_vision"] is False
    assert models[selection_id]["name"] == "Selection Model"

    observed: dict[str, object] = {}

    def fake_retrieve(*args, **kwargs):
        return []

    def fake_generate(question, evidence, **kwargs):
        observed["route"] = kwargs["route"]
        yield "boundary-only"

    monkeypatch.setattr(qa_router, "retrieve", fake_retrieve)
    monkeypatch.setattr(qa_router, "generate_answer_stream", fake_generate)
    response = client.post(
        "/api/v1/qa/ask",
        headers={**headers, "Accept": "text/event-stream"},
        json={"question": "selection boundary", "options": {"model": selection_id}},
    )
    assert response.status_code == 200, response.text
    route = observed["route"]
    assert route.provider_name == selection_id
    assert route.model == "selection-model"

    # Keep the variable in the test contract: the selected model was a real
    # SQLite registry row, not a synthetic provider success.
    assert model_pk


def test_unknown_disabled_and_cross_tenant_models_fail_before_retrieval(
    client, monkeypatch
) -> None:
    token, selection_id, model_pk = _configured_model(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    cross_suffix = stdlib_secrets.token_hex(6)
    cross_provider = f"cross-tenant-{cross_suffix}"
    session_local = get_session_local()
    with session_local() as session:
        now = "2026-08-13T00:00:00Z"
        session.execute(
            text(
                "INSERT INTO llm_providers ("
                "id, tenant_id, user_id, provider_key, name, websites, "
                "endpoint_configs, auth_type, api_features, settings, model_list_source, "
                "is_enabled, created_at, updated_at"
                ") VALUES (:id, :tenant, :user, :key, :name, '{}', '{}', 'api-key', '{}', '{}', 'api', 1, :now, :now)"
            ),
            {
                "id": f"cross-provider-{cross_suffix}",
                "tenant": f"tenant-other-{cross_suffix}",
                "user": f"user-other-{cross_suffix}",
                "key": cross_provider,
                "name": "Cross tenant provider",
                "now": now,
            },
        )
        session.execute(
            text(
                "INSERT INTO llm_models ("
                "id, tenant_id, user_id, provider_id, model_id, display_name, model_type, "
                "capabilities, is_enabled, is_custom, created_at, updated_at"
                ") VALUES (:id, :tenant, :user, :provider, 'cross-model', 'Cross model', 'chat', '{}', 1, 1, :now, :now)"
            ),
            {
                "id": f"cross-model-pk-{cross_suffix}",
                "tenant": f"tenant-other-{cross_suffix}",
                "user": f"user-other-{cross_suffix}",
                "provider": f"cross-provider-{cross_suffix}",
                "now": now,
            },
        )
        session.commit()
    calls = 0

    def should_not_retrieve(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("model rejection must precede retrieval")

    monkeypatch.setattr(qa_router, "retrieve", should_not_retrieve)

    for requested in ("unknown-provider/unknown-model", f"{cross_provider}/cross-model"):
        response = client.post(
            "/api/v1/qa/ask",
            headers=headers,
            json={"question": "rejected", "options": {"model": requested}},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"

    disabled = client.patch(
        f"/api/v1/llm/models/{model_pk}",
        headers=headers,
        json={"is_enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    response = client.post(
        "/api/v1/qa/ask",
        headers=headers,
        json={"question": "disabled", "options": {"model": selection_id}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert calls == 0


def test_revoked_credential_is_not_a_capability_or_selectable_model(
    client, monkeypatch
) -> None:
    token, selection_id, _ = _configured_model(client, monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    session_local = get_session_local()
    with session_local() as session:
        session.execute(
            text(
                "UPDATE provider_credentials SET status='REVOKED' "
                "WHERE tenant_id=(SELECT tenant_id FROM users WHERE email='admin@example.com') "
                "AND status='ACTIVE'"
            )
        )
        session.commit()

    capabilities = client.get("/api/v1/qa/capabilities", headers=headers)
    assert capabilities.status_code == 200
    assert selection_id not in {item["id"] for item in capabilities.json()["models"]}
    response = client.post(
        "/api/v1/qa/ask",
        headers=headers,
        json={"question": "no credential", "options": {"model": selection_id}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_image_request_requires_registry_vision_capability(client, monkeypatch) -> None:
    token, selection_id, _ = _configured_model(client, monkeypatch, vision=False)
    headers = {"Authorization": f"Bearer {token}"}

    def fake_load_context(self, **kwargs):
        return [
            {
                "attachment_id": "image-fixture",
                "usage_mode": "VISION",
                "mime": "image/png",
                "text": "",
            }
        ]

    monkeypatch.setattr(qa_router.AttachmentService, "load_context", fake_load_context)
    monkeypatch.setattr(
        qa_router.AttachmentService,
        "read_image_bytes",
        lambda self, **kwargs: b"fixture-image",
    )
    response = client.post(
        "/api/v1/qa/ask",
        headers=headers,
        json={
            "question": "read image",
            "options": {
                "model": selection_id,
                "attachment_doc_ids": ["image-fixture"],
            },
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_NOT_ALLOWED"
    assert response.json()["error"]["details"]["reason"] == "VISION_MODEL_REQUIRED"

"""Targeted tests for the PH3 remote embedding boundary.

The successful path uses a narrow controlled transport protocol. It never
contacts an external provider and never stores a real credential; production
still uses urllib against the configured remote endpoint.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from ekb_api.core.config import ModelProvider
from ekb_api.core.db import build_engine
from ekb_api.services.embedding import (
    EmbeddingProfile,
    EmbeddingProviderError,
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingClient,
    build_embedding_client,
)


def _profile(
    *, provider_id: str = "provider-a", model_id: str = "model-row-a", dimensions: int = 3
):
    return EmbeddingProfile(
        id="profile-a",
        tenant_id="tenant-a",
        llm_provider_id=provider_id,
        model_id=model_id,
        dimensions=dimensions,
        tokenizer="unicode",
        chunker_id="fixed",
        chunker_version="chunk-v1.0",
        config={},
        fingerprint="a" * 64,
    )


def _provider(*, endpoint: str = "https://embedding.example.invalid/v1/embeddings"):
    return ModelProvider(
        name="provider-key/model-a",
        kind="embedding",
        base_url=endpoint,
        api_key="runtime-test-key",
        model="model-a",
        timeout_seconds=2,
    )


def test_remote_client_returns_real_provider_vectors_and_preserves_order() -> None:
    requests: list[tuple[str, bytes, dict[str, str], float]] = []

    def controlled_remote(
        url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> bytes:
        requests.append((url, body, headers, timeout))
        return json.dumps(
            {
                "data": [
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                ]
            }
        ).encode()

    client = OpenAICompatibleEmbeddingClient(_provider(), http_post=controlled_remote)
    vectors = client.embed(texts=["第一段", "第二段"], profile=_profile())

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert len(requests) == 1
    url, body, headers, timeout = requests[0]
    assert url == "https://embedding.example.invalid/v1/embeddings"
    assert json.loads(body) == {"input": ["第一段", "第二段"], "model": "model-a"}
    assert headers["Authorization"] == "Bearer runtime-test-key"
    assert timeout == 2


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ({"data": [{"embedding": [0.1, 0.2, 0.3]}]}, "EMBEDDING_DIMENSION_MISMATCH"),
        (
            {"data": [{"embedding": [0.1, 0.2]}]},
            "EMBEDDING_DIMENSION_MISMATCH",
        ),
        (
            {
                "data": [
                    {"embedding": [0.1, "not-a-number", 0.3]},
                    {"embedding": [0.4, 0.5, 0.6]},
                ]
            },
            "EMBEDDING_INVALID_RESPONSE",
        ),
    ],
)
def test_remote_client_rejects_count_dimension_and_value_errors(
    body: dict, expected_code: str
) -> None:
    def controlled_remote(*_args) -> bytes:
        return json.dumps(body).encode()

    client = OpenAICompatibleEmbeddingClient(_provider(), http_post=controlled_remote)
    with pytest.raises(EmbeddingProviderError) as excinfo:
        client.embed(texts=["第一段", "第二段"], profile=_profile())
    assert excinfo.value.code == expected_code


def test_remote_url_and_response_failures_are_sanitized() -> None:
    bad_url = _provider(endpoint="https://user:response-secret@embedding.example.invalid/v1/embeddings")
    with pytest.raises(EmbeddingUnavailable) as url_error:
        OpenAICompatibleEmbeddingClient(bad_url).embed(texts=["x"], profile=_profile())
    assert "response-secret" not in str(url_error.value)

    def malformed_remote(*_args) -> bytes:
        return b'{"error":{"message":"response-secret"}}'

    client = OpenAICompatibleEmbeddingClient(_provider(), http_post=malformed_remote)
    with pytest.raises(EmbeddingProviderError) as response_error:
        client.embed(texts=["x"], profile=_profile())
    assert response_error.value.code == "EMBEDDING_INVALID_RESPONSE"
    assert "response-secret" not in str(response_error.value)


def test_profile_client_shares_tenant_runtime_provider_and_isolates_tenant(monkeypatch) -> None:
    engine = build_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE llm_providers ("
                "id text PRIMARY KEY, tenant_id text, user_id text, provider_key text, "
                "is_enabled integer)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE llm_models ("
                "id text PRIMARY KEY, tenant_id text, user_id text, provider_id text, "
                "model_id text, is_enabled integer)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO llm_providers VALUES "
                "('provider-a','tenant-a','actor-a','provider-key',1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO llm_models VALUES "
                "('model-row-a','tenant-a','actor-a','provider-a','model-a',1)"
            )
        )

    calls: list[tuple[str, str]] = []

    def scoped_runtime(*, tenant_id: str, user_id: str):
        calls.append((tenant_id, user_id))
        return [_provider()]

    import ekb_api.core.config as config

    monkeypatch.setattr(config, "get_runtime_embedding_providers", scoped_runtime)
    client = build_embedding_client(
        engine=engine,
        tenant_id="tenant-a",
        user_id="actor-a",
        profile=_profile(),
        http_post=lambda *_args: b'{"data":[{"embedding":[0.1,0.2,0.3]}]}',
    )
    assert client.embed(texts=["x"], profile=_profile()) == [[0.1, 0.2, 0.3]]
    assert calls == [("tenant-a", "actor-a")]

    # TEAM sharing: another member of the same tenant resolves the admin's
    # provider/model rows and can build a working client.
    shared_client = build_embedding_client(
        engine=engine,
        tenant_id="tenant-a",
        user_id="actor-b",
        profile=_profile(),
        http_post=lambda *_args: b'{"data":[{"embedding":[0.1,0.2,0.3]}]}',
    )
    assert shared_client.embed(texts=["x"], profile=_profile()) == [[0.1, 0.2, 0.3]]
    assert calls == [("tenant-a", "actor-a"), ("tenant-a", "actor-b")]

    # Tenant isolation is preserved: another tenant must fail closed.
    with pytest.raises(EmbeddingUnavailable):
        build_embedding_client(
            engine=engine,
            tenant_id="tenant-b",
            user_id="actor-b",
            profile=_profile(),
        )
    assert calls == [("tenant-a", "actor-a"), ("tenant-a", "actor-b")]

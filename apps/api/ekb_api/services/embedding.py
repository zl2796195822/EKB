"""Embedding boundary, profile resolution and atomic index generations (PH3).

The embedding model is reached only through the :class:`EmbeddingClient`
Protocol.  When no provider is configured the factory fails closed with
:class:`EmbeddingUnavailable` — ingestion must not pretend success.

An ``embedding_profile`` is immutable (its ``fingerprint`` is the content hash
of its configuration).  Index generations are built then atomically activated;
the partial unique index ``uq_kb_one_active_generation`` guarantees a KB can
have at most one ``ACTIVE`` generation, so :func:`swap_active_generation`
cannot produce two active generations.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

from sqlalchemy import Engine, text

from ekb_api.domain import utc_now


class EmbeddingError(RuntimeError):
    """Base class for sanitized remote embedding failures."""

    code = "EMBEDDING_UNAVAILABLE"


class EmbeddingUnavailable(EmbeddingError):
    """Raised when no usable remote embedding provider is configured."""


class EmbeddingProviderError(EmbeddingError):
    """Raised when a configured remote provider returns an unusable result."""

    def __init__(
        self,
        message: str = "remote embedding provider failed",
        *,
        code: str | None = None,
    ):
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class EmbeddingProfile:
    id: str
    tenant_id: str
    llm_provider_id: str
    model_id: str
    dimensions: int
    tokenizer: str
    chunker_id: str
    chunker_version: str
    config: dict[str, Any]
    fingerprint: str


@runtime_checkable
class EmbeddingClient(Protocol):
    def embed(self, *, texts: list[str], profile: EmbeddingProfile) -> list[list[float]]: ...


RemotePost = Callable[[str, bytes, dict[str, str], float], bytes]


def _post_json(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
    """POST JSON through the standard library without exposing response details."""

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(8 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise EmbeddingProviderError(code="EMBEDDING_RATE_LIMITED") from exc
        raise EmbeddingProviderError(code="EMBEDDING_PROVIDER_ERROR") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EmbeddingProviderError(code="EMBEDDING_UNAVAILABLE") from exc


def _is_safe_remote_endpoint(value: str) -> bool:
    """Accept only remote HTTP(S) URLs; never allow local or credentialed URLs."""

    parsed = urlparse((value or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if parsed.username or parsed.password:
        return False
    return host not in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _provider_for_profile(
    engine: Engine,
    *,
    tenant_id: str,
    user_id: str,
    profile: EmbeddingProfile,
):
    """Resolve the exact actor-scoped runtime model referenced by a profile."""

    from ekb_api.core.config import get_runtime_embedding_providers

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT p.provider_key, m.model_id "
                "FROM llm_providers p JOIN llm_models m ON m.provider_id=p.id "
                "WHERE p.id=:provider AND p.tenant_id=:tenant "
                "AND m.id=:model AND m.tenant_id=:tenant "
                "AND p.is_enabled=:enabled AND m.is_enabled=:enabled"
            ),
            {
                "provider": profile.llm_provider_id,
                "model": profile.model_id,
                "tenant": tenant_id,
                "user": user_id,
                "enabled": True,
            },
        ).first()
    if row is None:
        raise EmbeddingUnavailable("embedding provider is not available for this actor")

    expected_name = f"{row.provider_key}/{row.model_id}"
    providers = get_runtime_embedding_providers(tenant_id=tenant_id, user_id=user_id)
    for provider in providers:
        if provider.name == expected_name and provider.kind == "embedding":
            return provider
    raise EmbeddingUnavailable("embedding provider is not available for this actor")


class OpenAICompatibleEmbeddingClient:
    """Real OpenAI-compatible remote embeddings client.

    ``http_post`` exists only as a narrow protocol seam for deterministic tests;
    production construction uses :func:`_post_json` and never fabricates vectors.
    """

    def __init__(self, provider: Any, *, http_post: RemotePost | None = None):
        self.provider = provider
        self._http_post = http_post or _post_json

    def embed(self, *, texts: list[str], profile: EmbeddingProfile) -> list[list[float]]:
        if not texts:
            return []
        endpoint = str(self.provider.base_url or "").strip()
        if not _is_safe_remote_endpoint(endpoint):
            raise EmbeddingUnavailable("embedding endpoint is not a remote http(s) URL")
        model = str(self.provider.model or "").strip()
        if not model:
            raise EmbeddingUnavailable("embedding model is not configured")
        api_key = str(self.provider.api_key or "").strip()
        if not api_key:
            raise EmbeddingUnavailable("embedding provider credential is unavailable")

        from ekb_api.core.config import get_settings

        batch_size = max(1, int(get_settings().embedding_batch_size))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            payload = json.dumps(
                {"input": batch, "model": model}, ensure_ascii=False
            ).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            try:
                raw = self._http_post(
                    endpoint, payload, headers, float(self.provider.timeout_seconds)
                )
                body = json.loads(raw.decode("utf-8"))
                batch_vectors = _parse_vectors(
                    body, expected_count=len(batch), dimensions=profile.dimensions
                )
            except EmbeddingError:
                raise
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
                KeyError,
                IndexError,
            ) as exc:
                raise EmbeddingProviderError(code="EMBEDDING_INVALID_RESPONSE") from exc
            vectors.extend(batch_vectors)

        if len(vectors) != len(texts):
            raise EmbeddingProviderError(code="EMBEDDING_DIMENSION_MISMATCH")
        return vectors


def _parse_vectors(body: Any, *, expected_count: int, dimensions: int) -> list[list[float]]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise EmbeddingProviderError(code="EMBEDDING_INVALID_RESPONSE")
    data = body["data"]
    if len(data) != expected_count:
        raise EmbeddingProviderError(code="EMBEDDING_DIMENSION_MISMATCH")

    indexed = all(isinstance(item, dict) and isinstance(item.get("index"), int) for item in data)
    if indexed:
        indexes = [int(item["index"]) for item in data]
        if sorted(indexes) != list(range(expected_count)):
            raise EmbeddingProviderError(code="EMBEDDING_INVALID_RESPONSE")
        data = sorted(data, key=lambda item: int(item["index"]))

    vectors: list[list[float]] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise EmbeddingProviderError(code="EMBEDDING_INVALID_RESPONSE")
        vector = item["embedding"]
        if len(vector) != dimensions:
            raise EmbeddingProviderError(code="EMBEDDING_DIMENSION_MISMATCH")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise EmbeddingProviderError(code="EMBEDDING_INVALID_RESPONSE")
        vectors.append([float(value) for value in vector])
    return vectors


def build_embedding_client(
    *,
    engine: Engine | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    profile: EmbeddingProfile | None = None,
    http_post: RemotePost | None = None,
) -> EmbeddingClient:
    """Construct the configured embedding client.

    Fails closed with :class:`EmbeddingUnavailable` when no embedding provider
    is configured — vector generation must never silently succeed.
    """
    from ekb_api.core.config import get_runtime_embedding_providers, get_settings

    settings = get_settings()
    if (tenant_id is None) != (user_id is None):
        raise EmbeddingUnavailable("embedding actor scope is incomplete")
    if profile is not None and (engine is None or tenant_id is None or user_id is None):
        raise EmbeddingUnavailable("embedding profile requires tenant and actor scope")

    if profile is not None:
        provider = _provider_for_profile(
            engine,
            tenant_id=tenant_id,
            user_id=user_id,
            profile=profile,
        )
    elif tenant_id is not None and user_id is not None:
        providers = get_runtime_embedding_providers(tenant_id=tenant_id, user_id=user_id)
        provider = providers[0] if providers else None
    else:
        provider = settings.embedding_providers[0] if settings.embedding_providers else None

    if provider is None:
        raise EmbeddingUnavailable("embedding is not configured; ingestion cannot proceed")
    if provider.kind != "embedding" or not _is_safe_remote_endpoint(str(provider.base_url)):
        raise EmbeddingUnavailable("embedding provider is not a remote http(s) provider")
    if not str(provider.api_key or "").strip():
        raise EmbeddingUnavailable("embedding provider credential is unavailable")
    return OpenAICompatibleEmbeddingClient(provider, http_post=http_post)


def _fingerprint(
    *,
    llm_provider_id: str,
    model_id: str,
    dimensions: int,
    tokenizer: str,
    chunker_id: str,
    chunker_version: str,
    config: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "llm_provider_id": llm_provider_id,
            "model_id": model_id,
            "dimensions": dimensions,
            "tokenizer": tokenizer,
            "chunker_id": chunker_id,
            "chunker_version": chunker_version,
            "config": config,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_profile(engine: Engine, *, tenant_id: str, profile_id: str) -> EmbeddingProfile:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, tenant_id, llm_provider_id, model_id, dimensions, tokenizer, "
                "chunker_id, chunker_version, config, fingerprint "
                "FROM embedding_profiles WHERE id=:id AND tenant_id=:tenant"
            ),
            {"id": profile_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise RuntimeError("embedding profile not found")
        return EmbeddingProfile(
            id=str(row.id),
            tenant_id=str(row.tenant_id),
            llm_provider_id=str(row.llm_provider_id),
            model_id=str(row.model_id),
            dimensions=int(row.dimensions),
            tokenizer=str(row.tokenizer),
            chunker_id=str(row.chunker_id),
            chunker_version=str(row.chunker_version),
            config=json.loads(row.config) if isinstance(row.config, str) else dict(row.config),
            fingerprint=str(row.fingerprint),
        )


def ensure_kb_profile(
    engine: Engine,
    *,
    tenant_id: str,
    kb_id: str,
    llm_provider_id: str,
    model_id: str,
    dimensions: int,
    tokenizer: str = "unicode",
    chunker_id: str = "fixed",
    chunker_version: str = "chunk-v1.0",
    config: Optional[dict[str, Any]] = None,
) -> str:
    """Create (or reuse by fingerprint) an embedding profile for a KB and a
    matching active index generation, wiring both onto the KB.  Idempotent per
    fingerprint."""
    config = config or {}
    fingerprint = _fingerprint(
        llm_provider_id=llm_provider_id,
        model_id=model_id,
        dimensions=dimensions,
        tokenizer=tokenizer,
        chunker_id=chunker_id,
        chunker_version=chunker_version,
        config=config,
    )
    with engine.begin() as connection:
        existing = connection.execute(
            text(
                "SELECT id FROM embedding_profiles WHERE tenant_id=:tenant AND fingerprint=:fp"
            ),
            {"tenant": tenant_id, "fp": fingerprint},
        ).first()
        if existing is not None:
            profile_id = str(existing.id)
        else:
            from uuid import uuid4

            profile_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO embedding_profiles "
                    "(id, tenant_id, llm_provider_id, model_id, dimensions, tokenizer, "
                    "chunker_id, chunker_version, config, fingerprint, created_at) "
                    "VALUES (:id, :tenant, :prov, :model, :dim, :tok, :cid, :cver, "
                    "CAST(:cfg AS JSON), :fp, :now)"
                    if connection.dialect.name == "postgresql"
                    else "INSERT INTO embedding_profiles "
                    "(id, tenant_id, llm_provider_id, model_id, dimensions, tokenizer, "
                    "chunker_id, chunker_version, config, fingerprint, created_at) "
                    "VALUES (:id, :tenant, :prov, :model, :dim, :tok, :cid, :cver, "
                    ":cfg, :fp, :now)"
                ),
                {
                    "id": profile_id,
                    "tenant": tenant_id,
                    "prov": llm_provider_id,
                    "model": model_id,
                    "dim": dimensions,
                    "tok": tokenizer,
                    "cid": chunker_id,
                    "cver": chunker_version,
                    "cfg": json.dumps(config, sort_keys=True),
                    "fp": fingerprint,
                    "now": utc_now(),
                },
            )
        # Active generation wiring.
        gen = connection.execute(
            text(
                "SELECT id FROM index_generations WHERE knowledge_base_id=:kb "
                "AND embedding_profile_id=:prof AND state='ACTIVE'"
            ),
            {"kb": kb_id, "prof": profile_id},
        ).first()
        if gen is None:
            generation_id = build_generation(
                connection,
                tenant_id=tenant_id,
                kb_id=kb_id,
                profile_id=profile_id,
                generation_no=1,
            )
            swap_active_generation(
                connection, tenant_id=tenant_id, kb_id=kb_id, generation_id=generation_id
            )
        connection.execute(
            text(
                "UPDATE knowledge_bases SET embedding_profile_id=:prof "
                "WHERE id=:kb AND tenant_id=:tenant"
            ),
            {"prof": profile_id, "kb": kb_id, "tenant": tenant_id},
        )
    return profile_id


def build_generation(
    engine: Engine,
    *,
    tenant_id: str,
    kb_id: str,
    profile_id: str,
    generation_no: int,
) -> str:
    """Create an index generation in BUILDING then READY state and return its id."""
    from uuid import uuid4

    generation_id = str(uuid4())
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO index_generations "
                "(id, tenant_id, knowledge_base_id, embedding_profile_id, generation_no, "
                "state, vector_count, created_at) "
                "VALUES (:id, :tenant, :kb, :prof, :no, 'BUILDING', 0, :now)"
            ),
            {
                "id": generation_id,
                "tenant": tenant_id,
                "kb": kb_id,
                "prof": profile_id,
                "no": generation_no,
                "now": now,
            },
        )
        connection.execute(
            text(
                "UPDATE index_generations SET state='READY' WHERE id=:id"
            ),
            {"id": generation_id},
        )
    return generation_id


def swap_active_generation(
    engine: Engine,
    *,
    tenant_id: str,
    kb_id: str,
    generation_id: str,
) -> None:
    """Atomically retire the current ACTIVE generation and activate ``generation_id``.

    Uses compare-and-set updates guarded by the partial unique index
    ``uq_kb_one_active_generation`` so two ``ACTIVE`` generations can never
    coexist.  Raises ``RuntimeError`` (surfaced as INDEX_ACTIVATION_FAILED) when
    the target generation is not in ``READY`` state.
    """
    with engine.begin() as connection:
        target = connection.execute(
            text(
                "SELECT state FROM index_generations WHERE id=:id AND tenant_id=:tenant"
            ),
            {"id": generation_id, "tenant": tenant_id},
        ).first()
        if target is None:
            raise RuntimeError("index generation not found")
        if str(target.state) != "READY":
            raise RuntimeError(
                f"index generation is not READY (state={target.state}); cannot activate"
            )
        # Retire any currently ACTIVE generation (CAS on state='ACTIVE').
        connection.execute(
            text(
                "UPDATE index_generations SET state='RETIRED' "
                "WHERE knowledge_base_id=:kb AND state='ACTIVE'"
            ),
            {"kb": kb_id},
        )
        # Activate the target (CAS on state='READY' AND id=:id).  The partial
        # unique index prevents a second ACTIVE even under concurrency.
        updated = connection.execute(
            text(
                "UPDATE index_generations SET state='ACTIVE', activated_at=:now "
                "WHERE id=:id AND state='READY' AND knowledge_base_id=:kb"
            ),
            {"id": generation_id, "kb": kb_id, "now": utc_now()},
        )
        if updated.rowcount != 1:
            # Roll the retired generation back is unnecessary: the caller retried
            # against a non-READY generation, which is a client error.
            raise RuntimeError("index generation activation lost its CAS race")
        connection.execute(
            text(
                "UPDATE knowledge_bases SET active_index_generation_id=:gid "
                "WHERE id=:kb AND tenant_id=:tenant"
            ),
            {"gid": generation_id, "kb": kb_id, "tenant": tenant_id},
        )

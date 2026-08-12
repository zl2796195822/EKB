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
from dataclasses import dataclass
from typing import Any, Optional, Protocol, runtime_checkable

from sqlalchemy import Engine, text

from ekb_api.domain import utc_now


class EmbeddingUnavailable(RuntimeError):
    """Raised when no embedding provider is configured; ingestion fails closed."""


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


def build_embedding_client() -> EmbeddingClient:
    """Construct the configured embedding client.

    Fails closed with :class:`EmbeddingUnavailable` when no embedding provider
    is configured — vector generation must never silently succeed.
    """
    from ekb_api.core.config import get_settings

    settings = get_settings()
    if not getattr(settings, "embedding_enabled", False):
        raise EmbeddingUnavailable("embedding is not configured; ingestion cannot proceed")
    raise EmbeddingUnavailable("embedding client construction is unavailable")


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

"""Explicit embedding profile wiring for exactly one knowledge base.

Used by the deployment/operator CLI to close the last gap of the embedding
chain: a remote embedding provider/model exists (TEAM-scoped, admin managed)
but a knowledge base only becomes ingestable once it has an embedding profile
and an ACTIVE index generation.  The planner is read-only; every write path
goes through :func:`apply_kb_profile` after explicit ``--apply``.

Refuses cross-tenant targets, disabled or non-embedding models, and dimension
mismatches.  Never logs provider credentials: this module only reads
llm_providers/llm_models metadata rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, text

from ekb_api.services.embedding import ensure_kb_profile

_MAX_DIMENSIONS = 32768
_REQUIRED_TABLES = frozenset(
    {"embedding_profiles", "index_generations", "knowledge_bases", "llm_models", "llm_providers"}
)


@dataclass(frozen=True)
class EmbeddingProfilePlan:
    mode: str  # "apply" | "dry-run"
    profile_action: str  # "create" | "reuse"
    generation_action: str  # "create+activate" | "reuse-active"
    profile_id: str | None
    active_generation_id: str | None
    kb_id: str
    tenant_id: str
    llm_provider_id: str
    model_id: str
    dimensions: int


class ProfilePlanRefused(ValueError):
    """Raised when validation fails; message is safe to print."""


def _require_tables(engine) -> None:
    names = set(inspect(engine).get_table_names())
    missing = sorted(_REQUIRED_TABLES - names)
    if missing:
        raise ProfilePlanRefused(f"schema is missing tables: {', '.join(missing)}")


def _kb_row(connection, tenant_id: str, kb_id: str):
    return connection.execute(
        text(
            "SELECT id, tenant_id, embedding_profile_id, active_index_generation_id "
            "FROM knowledge_bases WHERE id=:kb"
        ),
        {"kb": kb_id},
    ).mappings().first()


def _provider_row(connection, tenant_id: str, provider_id: str):
    return connection.execute(
        text(
            "SELECT id, tenant_id, provider_key, is_enabled FROM llm_providers "
            "WHERE id=:id"
        ),
        {"id": provider_id},
    ).mappings().first()


def _model_row(connection, tenant_id: str, model_id: str):
    return connection.execute(
        text(
            "SELECT id, tenant_id, provider_id, model_id, model_type, is_enabled, "
            "capabilities FROM llm_models WHERE id=:id"
        ),
        {"id": model_id},
    ).mappings().first()


def _declared_dimensions(capabilities: Any) -> int | None:
    if not capabilities:
        return None
    if isinstance(capabilities, str):
        import json

        try:
            capabilities = json.loads(capabilities)
        except ValueError:
            return None
    if not isinstance(capabilities, dict):
        return None
    value = capabilities.get("dimensions")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def plan_kb_profile(
    engine,
    *,
    tenant_id: str,
    kb_id: str,
    llm_provider_id: str,
    model_id: str,
    dimensions: int,
    tokenizer: str = "unicode",
    chunker_id: str = "fixed",
    chunker_version: str = "chunk-v1.0",
) -> EmbeddingProfilePlan:
    """Validate the target wiring read-only and return the planned actions."""
    _require_tables(engine)
    if not str(tenant_id or "").strip():
        raise ProfilePlanRefused("tenant id is required")
    if not str(kb_id or "").strip():
        raise ProfilePlanRefused("knowledge base id is required")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not (
        0 < dimensions <= _MAX_DIMENSIONS
    ):
        raise ProfilePlanRefused(
            f"dimensions must be an integer in 1..{_MAX_DIMENSIONS}, got {dimensions!r}"
        )

    with engine.connect() as connection:
        kb = _kb_row(connection, tenant_id, kb_id)
        if kb is None:
            raise ProfilePlanRefused(f"knowledge base {kb_id} does not exist")
        if str(kb["tenant_id"]) != str(tenant_id):
            raise ProfilePlanRefused(
                f"knowledge base {kb_id} belongs to another tenant; cross-tenant wiring refused"
            )

        provider = _provider_row(connection, tenant_id, llm_provider_id)
        if provider is None:
            raise ProfilePlanRefused(f"llm provider {llm_provider_id} does not exist")
        if str(provider["tenant_id"]) != str(tenant_id):
            raise ProfilePlanRefused(
                f"llm provider {llm_provider_id} belongs to another tenant"
            )
        if not provider["is_enabled"]:
            raise ProfilePlanRefused(f"llm provider {llm_provider_id} is disabled")

        model = _model_row(connection, tenant_id, model_id)
        if model is None:
            raise ProfilePlanRefused(f"llm model row {model_id} does not exist")
        if str(model["tenant_id"]) != str(tenant_id):
            raise ProfilePlanRefused(f"llm model row {model_id} belongs to another tenant")
        if str(model["provider_id"]) != str(llm_provider_id):
            raise ProfilePlanRefused(
                f"llm model row {model_id} does not belong to provider {llm_provider_id}"
            )
        if not model["is_enabled"]:
            raise ProfilePlanRefused(f"llm model row {model_id} is disabled")
        if str(model["model_type"] or "") != "embedding":
            raise ProfilePlanRefused(
                f"llm model row {model_id} is model_type="
                f"{model['model_type']!r}; only embedding models can back a profile"
            )
        declared = _declared_dimensions(model["capabilities"])
        if declared is not None and declared != dimensions:
            raise ProfilePlanRefused(
                f"model declares dimensions={declared} but {dimensions} was requested; "
                "fingerprint would disagree with the runtime provider"
            )

        from ekb_api.services.embedding import _fingerprint

        fingerprint = _fingerprint(
            llm_provider_id=llm_provider_id,
            model_id=model_id,
            dimensions=dimensions,
            tokenizer=tokenizer,
            chunker_id=chunker_id,
            chunker_version=chunker_version,
            config={},
        )
        profile_row = connection.execute(
            text(
                "SELECT id FROM embedding_profiles "
                "WHERE tenant_id=:tenant AND fingerprint=:fp"
            ),
            {"tenant": tenant_id, "fp": fingerprint},
        ).first()
        if profile_row is not None:
            profile_id = str(profile_row.id)
            profile_action = "reuse"
        else:
            profile_id = None
            profile_action = "create"
        active_generation = connection.execute(
            text(
                "SELECT id FROM index_generations WHERE knowledge_base_id=:kb "
                "AND embedding_profile_id=:prof AND state='ACTIVE'"
            ),
            {"kb": kb_id, "prof": profile_id or ""},
        ).first()

    generation_action = (
        "reuse-active" if (profile_id and active_generation) else "create+activate"
    )
    return EmbeddingProfilePlan(
        mode="dry-run",
        profile_action=profile_action,
        generation_action=generation_action,
        profile_id=profile_id,
        active_generation_id=(
            str(active_generation.id) if (profile_id and active_generation) else None
        ),
        kb_id=kb_id,
        tenant_id=tenant_id,
        llm_provider_id=llm_provider_id,
        model_id=model_id,
        dimensions=dimensions,
    )


def apply_kb_profile(
    engine,
    *,
    tenant_id: str,
    kb_id: str,
    llm_provider_id: str,
    model_id: str,
    dimensions: int,
    tokenizer: str = "unicode",
    chunker_id: str = "fixed",
    chunker_version: str = "chunk-v1.0",
) -> EmbeddingProfilePlan:
    """Validate first, then wire profile + ACTIVE generation onto the KB.

    Delegates all writes to the idempotent :func:`ensure_kb_profile`.
    """
    plan = plan_kb_profile(
        engine,
        tenant_id=tenant_id,
        kb_id=kb_id,
        llm_provider_id=llm_provider_id,
        model_id=model_id,
        dimensions=dimensions,
        tokenizer=tokenizer,
        chunker_id=chunker_id,
        chunker_version=chunker_version,
    )
    profile_id = ensure_kb_profile(
        engine,
        tenant_id=tenant_id,
        kb_id=kb_id,
        llm_provider_id=llm_provider_id,
        model_id=model_id,
        dimensions=dimensions,
        tokenizer=tokenizer,
        chunker_id=chunker_id,
        chunker_version=chunker_version,
    )
    with engine.connect() as connection:
        generation = connection.execute(
            text(
                "SELECT id FROM index_generations WHERE knowledge_base_id=:kb "
                "AND embedding_profile_id=:prof AND state='ACTIVE'"
            ),
            {"kb": kb_id, "prof": profile_id},
        ).scalar()
    return EmbeddingProfilePlan(
        mode="apply",
        profile_action=plan.profile_action,
        generation_action=plan.generation_action,
        profile_id=profile_id,
        active_generation_id=str(generation) if generation else None,
        kb_id=kb_id,
        tenant_id=tenant_id,
        llm_provider_id=llm_provider_id,
        model_id=model_id,
        dimensions=dimensions,
    )

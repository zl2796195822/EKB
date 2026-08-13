"""Tenant-safe persistence for the legacy QA model route snapshot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ekb_api.core.config import ModelProvider
from ekb_api.core.egress import TenantRoute
from ekb_api.domain import new_id, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteAuditContext:
    requested_provider_id: str | None = None
    requested_model_id: str | None = None
    requested_provider_name: str | None = None
    requested_model_name: str | None = None
    actual_provider_id: str | None = None
    actual_model_id: str | None = None
    actual_provider_name: str | None = None
    actual_model_name: str | None = None
    fallback_reason: str | None = None

    def with_actual_provider(
        self,
        engine: Engine,
        *,
        tenant_id: str,
        actor_id: str,
        provider: ModelProvider,
        fallback_occurred: bool,
    ) -> RouteAuditContext:
        """Return a snapshot updated from a provider that really streamed."""

        provider_name, model_name = _provider_model_name(provider)
        provider_id, model_id = _resolve_db_ids(
            engine,
            tenant_id=tenant_id,
            actor_id=actor_id,
            provider=provider,
        )
        reason = self.fallback_reason
        if fallback_occurred:
            reason = "PROVIDER_FALLBACK"
        return replace(
            self,
            actual_provider_id=provider_id,
            actual_model_id=model_id,
            actual_provider_name=provider_name,
            actual_model_name=model_name,
            fallback_reason=reason,
        )


def _provider_model_name(provider: ModelProvider | None) -> tuple[str | None, str | None]:
    if provider is None:
        return None, None
    name = str(provider.name or "").strip() or None
    model = str(provider.model or "").strip() or None
    return name, model


def _find_candidate(
    providers: list[ModelProvider],
    *,
    provider_name: str | None,
    model: str | None,
) -> ModelProvider | None:
    candidates = providers
    if provider_name:
        exact = [item for item in candidates if item.name == provider_name]
        if not exact:
            exact = [
                item
                for item in candidates
                if item.name.split("/", 1)[0] == provider_name
            ]
        candidates = exact
    if model:
        matched = [
            item
            for item in candidates
            if item.model == model or item.name == model or item.name.endswith(f"/{model}")
        ]
        candidates = matched
    return candidates[0] if candidates else None


def _resolve_db_ids(
    engine: Engine,
    *,
    tenant_id: str,
    actor_id: str,
    provider: ModelProvider | None,
) -> tuple[str | None, str | None]:
    if provider is None:
        return None, None
    public_name, model_name = _provider_model_name(provider)
    if not public_name or not model_name:
        return None, None
    provider_key = public_name.split("/", 1)[0]
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT p.id AS provider_id, m.id AS model_pk "
                    "FROM llm_providers AS p "
                    "JOIN llm_models AS m ON m.provider_id = p.id "
                    " AND m.tenant_id = p.tenant_id AND m.user_id = p.user_id "
                    "WHERE p.id IS NOT NULL AND p.tenant_id = :tenant_id "
                    "AND p.user_id = :actor_id AND p.provider_key = :provider_key "
                    "AND m.model_id = :model_id AND p.is_enabled = 1 AND m.is_enabled = 1"
                ),
                {
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "provider_key": provider_key,
                    "model_id": model_name,
                },
            ).mappings().first()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "route audit id resolution failed error_type=%s",
            type(exc).__name__,
        )
        return None, None
    if row is None:
        return None, None
    return str(row["provider_id"]), str(row["model_pk"])


def build_route_audit_context(
    engine: Engine,
    *,
    tenant_id: str,
    actor_id: str,
    requested_model: str | None,
    route: TenantRoute,
    providers: list[ModelProvider],
) -> RouteAuditContext:
    """Resolve only actor/tenant-owned DB ids; names remain safe route snapshots."""

    requested = _find_candidate(
        providers,
        provider_name=route.provider_name,
        model=route.model,
    )
    if requested_model:
        requested = next((item for item in providers if item.name == requested_model), None)
    requested_provider_name, requested_model_name = _provider_model_name(requested)
    if requested_model:
        requested_provider_name = requested_model
    elif route.provider_name:
        requested_provider_name = route.provider_name
    if route.model:
        requested_model_name = route.model

    requested_provider_id, requested_db_model_id = _resolve_db_ids(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        provider=requested,
    )
    fallback_reason = None
    if (route.provider_name or route.model) and requested is None:
        fallback_reason = "REQUESTED_ROUTE_UNAVAILABLE"
    return RouteAuditContext(
        requested_provider_id=requested_provider_id,
        requested_model_id=requested_db_model_id,
        requested_provider_name=requested_model or requested_provider_name,
        requested_model_name=requested_model_name,
        actual_provider_id=None,
        actual_model_id=None,
        actual_provider_name=None,
        actual_model_name=None,
        fallback_reason=fallback_reason,
    )


def write_route_audit(
    engine: Engine,
    *,
    tenant_id: str,
    actor_id: str,
    turn_id: str,
    context: RouteAuditContext,
    input_tokens: int | None,
    output_tokens: int | None,
    usage_source: str,
    latency_ms: int,
    status: str,
    finish_reason: str | None,
) -> bool:
    """Best-effort insert; one immutable terminal audit row per scoped turn."""

    if usage_source not in {"PROVIDER_USAGE", "DETERMINISTIC_ESTIMATE", "UNAVAILABLE"}:
        usage_source = "UNAVAILABLE"
    try:
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        with SessionLocal() as session:
            result = session.execute(
                text(
                    "INSERT INTO qa_route_audits ("
                    "id, tenant_id, actor_id, turn_id, requested_provider_id, requested_model_id, "
                    "requested_provider_name, requested_model_name, "
                    "actual_provider_id, actual_model_id, "
                    "actual_provider_name, actual_model_name, fallback_reason, "
                    "input_tokens, output_tokens, "
                    "usage_source, latency_ms, status, finish_reason, created_at) "
                    "SELECT :id, :tenant_id, :actor_id, :turn_id, "
                    ":requested_provider_id, :requested_model_id, "
                    ":requested_provider_name, :requested_model_name, "
                    ":actual_provider_id, :actual_model_id, "
                    ":actual_provider_name, :actual_model_name, :fallback_reason, "
                    ":input_tokens, :output_tokens, "
                    ":usage_source, :latency_ms, :status, :finish_reason, :created_at "
                    "WHERE EXISTS (SELECT 1 FROM qa_turns WHERE turn_id=:turn_id "
                    "AND tenant_id=:tenant_id AND actor_id=:actor_id) "
                    "AND (:requested_provider_id IS NULL OR EXISTS "
                    "(SELECT 1 FROM llm_providers WHERE id=:requested_provider_id "
                    "AND tenant_id=:tenant_id AND user_id=:actor_id)) "
                    "AND (:requested_model_id IS NULL OR EXISTS "
                    "(SELECT 1 FROM llm_models WHERE id=:requested_model_id "
                    "AND tenant_id=:tenant_id AND user_id=:actor_id)) "
                    "AND (:actual_provider_id IS NULL OR EXISTS "
                    "(SELECT 1 FROM llm_providers WHERE id=:actual_provider_id "
                    "AND tenant_id=:tenant_id AND user_id=:actor_id)) "
                    "AND (:actual_model_id IS NULL OR EXISTS "
                    "(SELECT 1 FROM llm_models WHERE id=:actual_model_id "
                    "AND tenant_id=:tenant_id AND user_id=:actor_id))"
                ),
                {
                    "id": new_id(),
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "turn_id": turn_id,
                    "requested_provider_id": context.requested_provider_id,
                    "requested_model_id": context.requested_model_id,
                    "requested_provider_name": context.requested_provider_name,
                    "requested_model_name": context.requested_model_name,
                    "actual_provider_id": context.actual_provider_id,
                    "actual_model_id": context.actual_model_id,
                    "actual_provider_name": context.actual_provider_name,
                    "actual_model_name": context.actual_model_name,
                    "fallback_reason": context.fallback_reason,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "usage_source": usage_source,
                    "latency_ms": max(0, int(latency_ms)),
                    "status": status,
                    "finish_reason": finish_reason,
                    "created_at": utc_now(),
                },
            )
            session.commit()
            return bool(result.rowcount)
    except IntegrityError:
        # UNIQUE(turn_id) makes the first terminal audit row win under a close race.
        logger.info("qa route audit already recorded turn_id=%s", turn_id)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "qa route audit write failed tenant_id=%s actor_id=%s turn_id=%s error_type=%s",
            tenant_id,
            actor_id,
            turn_id,
            type(exc).__name__,
        )
        return False

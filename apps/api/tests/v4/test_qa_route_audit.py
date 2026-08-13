from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from ekb_api.core.circuit_breaker import get_circuit_breaker
from ekb_api.core.config import ModelProvider
from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.core.egress import TenantRoute
from ekb_api.llm import LlmError, chat_stream, generate_answer_stream
from ekb_api.migrations import v4_fullstack
from ekb_api.services.llm_route_audit import (
    RouteAuditContext,
    build_route_audit_context,
    write_route_audit,
)


def _provider(name: str, model: str) -> ModelProvider:
    return ModelProvider(
        name=name,
        kind="chat",
        base_url="https://provider.invalid/v1/chat/completions",
        api_key="fixture-only",
        model=model,
    )


def test_v4_009_schema_has_scope_fks_and_unique_turn(tmp_path: Path) -> None:
    database = tmp_path / "qa-route-audit.db"
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=True)

    result = v4_fullstack.main(
        ["--database-url", f"sqlite:///{database}", "--verify"]
    )
    assert result == 0

    inspector = inspect(engine)
    assert "qa_route_audits" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("qa_route_audits")}
    assert {
        "tenant_id",
        "actor_id",
        "turn_id",
        "actual_provider_id",
        "actual_model_id",
        "usage_source",
        "status",
        "finish_reason",
    } <= columns

    foreign_keys = {
        (
            key["constrained_columns"][0],
            key["referred_table"],
            key["referred_columns"][0],
        )
        for key in inspector.get_foreign_keys("qa_route_audits")
        if len(key["constrained_columns"]) == 1
    }
    assert {
        ("tenant_id", "tenants", "id"),
        ("actor_id", "users", "id"),
        ("turn_id", "qa_turns", "turn_id"),
    } <= foreign_keys

    with engine.connect() as connection:
        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='qa_route_audits'"
            )
        ).scalar_one()
    assert "UNIQUE (turn_id)" in ddl


def test_route_context_resolves_only_tenant_actor_owned_ids(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{tmp_path / 'route-context.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE llm_providers ("
                "id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT, "
                "provider_key TEXT, is_enabled INTEGER)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE llm_models ("
                "id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT, "
                "provider_id TEXT, model_id TEXT, is_enabled INTEGER)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO llm_providers VALUES "
                "('provider-owned', 'tenant-a', 'actor-a', 'audit-provider', 1), "
                "('provider-other', 'tenant-b', 'actor-b', 'audit-provider', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO llm_models VALUES "
                "('model-owned', 'tenant-a', 'actor-a', 'provider-owned', 'model-a', 1), "
                "('model-other', 'tenant-b', 'actor-b', 'provider-other', 'model-a', 1)"
            )
        )

    provider = _provider("audit-provider/model-a", "model-a")
    route = TenantRoute(provider_name=provider.name, model=provider.model)
    context = build_route_audit_context(
        engine,
        tenant_id="tenant-a",
        actor_id="actor-a",
        requested_model=provider.name,
        route=route,
        providers=[provider],
    )
    assert context.requested_provider_id == "provider-owned"
    assert context.requested_model_id == "model-owned"
    assert context.actual_provider_id is None
    assert context.actual_model_id is None

    actual = context.with_actual_provider(
        engine,
        tenant_id="tenant-a",
        actor_id="actor-a",
        provider=provider,
        fallback_occurred=True,
    )
    assert actual.actual_provider_id == "provider-owned"
    assert actual.actual_model_id == "model-owned"
    assert actual.fallback_reason == "PROVIDER_FALLBACK"

    cross_scope = build_route_audit_context(
        engine,
        tenant_id="tenant-a",
        actor_id="actor-not-owner",
        requested_model=provider.name,
        route=route,
        providers=[provider],
    )
    assert cross_scope.requested_provider_id is None
    assert cross_scope.requested_model_id is None
    assert cross_scope.requested_provider_name == provider.name


def test_chat_stream_observes_only_confirmed_fallback_provider(monkeypatch) -> None:
    first = _provider("first/model", "model")
    second = _provider("second/model", "model")
    observed: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        "ekb_api.llm.get_runtime_chat_providers",
        lambda **_: [first, second],
    )

    def fake_stream(provider, messages, **kwargs):
        if provider is first:
            raise LlmError("fixture upstream failure")
        yield "answer"

    monkeypatch.setattr("ekb_api.llm._call_provider_stream", fake_stream)
    get_circuit_breaker().reset()

    result = list(
        chat_stream(
            [{"role": "user", "content": "question"}],
            observer=lambda provider, fallback: observed.append(
                (provider.name, fallback)
            ),
        )
    )

    assert result == ["answer"]
    assert observed == [(second.name, True)]


def test_chat_stream_observer_failure_does_not_change_stream(monkeypatch) -> None:
    provider = _provider("stable/model", "model")
    monkeypatch.setattr(
        "ekb_api.llm.get_runtime_chat_providers",
        lambda **_: [provider],
    )
    monkeypatch.setattr(
        "ekb_api.llm._call_provider_stream",
        lambda *args, **kwargs: iter(["answer"]),
    )
    get_circuit_breaker().reset()

    assert list(
        chat_stream(
            [{"role": "user", "content": "question"}],
            observer=lambda *_: (_ for _ in ()).throw(RuntimeError("observer")),
        )
    ) == ["answer"]


def test_generate_answer_stream_forwards_provider_observer(monkeypatch) -> None:
    provider = _provider("forwarded/model", "model")
    observed: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "ekb_api.llm.get_runtime_chat_providers",
        lambda **_: [provider],
    )

    def fake_chat_stream(messages, **kwargs):
        kwargs["observer"](provider, False)
        yield "answer"

    monkeypatch.setattr("ekb_api.llm.chat_stream", fake_chat_stream)

    assert list(
        generate_answer_stream(
            "question",
            [],
            thinking_level="light",
            observer=lambda actual, fallback: observed.append(
                (actual.name, fallback)
            ),
        )
    ) == ["answer"]
    assert observed == [(provider.name, False)]


def test_route_audit_write_failure_is_best_effort() -> None:
    context = RouteAuditContext(
        requested_provider_name="fixture/model",
        requested_model_name="model",
    )
    engine = build_engine("sqlite:///:memory:")

    assert (
        write_route_audit(
            engine,
            tenant_id="tenant-a",
            actor_id="actor-a",
            turn_id="turn-a",
            context=context,
            input_tokens=3,
            output_tokens=2,
            usage_source="DETERMINISTIC_ESTIMATE",
            latency_ms=1,
            status="error",
            finish_reason="error",
        )
        is False
    )


@pytest.mark.parametrize(
    ("answer", "expected_status", "expected_finish"),
    [
        ("fixture answer", "completed", "stop"),
        ("证据不足，无法确认", "refused", "refusal"),
        (None, "error", "error"),
    ],
)
def test_qa_terminal_route_audit_paths(
    client, monkeypatch, answer, expected_status, expected_finish
) -> None:
    from ekb_api.routers import qa as qa_router

    token_response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert token_response.status_code == 200, token_response.text
    headers = {
        "Authorization": f"Bearer {token_response.json()['access_token']}",
        "Accept": "text/event-stream",
    }
    provider = _provider("audit-fixture/model", "model")

    monkeypatch.setattr(qa_router, "get_runtime_chat_providers", lambda **_: [provider])
    monkeypatch.setattr(qa_router, "retrieve", lambda *args, **kwargs: [])

    def fake_generate(question, evidence, *, observer=None, **kwargs):
        if observer is not None:
            observer(provider, False)
        if answer is None:
            raise LlmError("fixture generation failure")
        yield answer

    monkeypatch.setattr(qa_router, "generate_answer_stream", fake_generate)
    response = client.post(
        "/api/v1/qa/ask",
        headers=headers,
        json={"question": "route audit fixture"},
    )
    assert response.status_code == 200, response.text

    turn_id = response.headers["X-Turn-Id"]
    from ekb_api.core.db import get_session_local

    with get_session_local()() as session:
        row = session.execute(
            text(
                "SELECT tenant_id, actor_id, actual_provider_name, actual_model_name, status, "
                "finish_reason, usage_source FROM qa_route_audits "
                "WHERE turn_id=:turn_id"
            ),
            {"turn_id": turn_id},
        ).mappings().one()
    assert row["actual_provider_name"] == provider.name
    assert row["actual_model_name"] == provider.model
    assert row["status"] == expected_status
    assert row["finish_reason"] == expected_finish
    assert row["usage_source"] == "DETERMINISTIC_ESTIMATE"
    assert (
        write_route_audit(
            qa_router.get_engine(),
            tenant_id=row["tenant_id"],
            actor_id=row["actor_id"],
            turn_id=turn_id,
            context=RouteAuditContext(
                actual_provider_name="second/provider",
                actual_model_name="other-model",
            ),
            input_tokens=1,
            output_tokens=1,
            usage_source="UNAVAILABLE",
            latency_ms=0,
            status="error",
            finish_reason="error",
        )
        is False
    )

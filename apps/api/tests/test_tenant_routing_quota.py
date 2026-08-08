from __future__ import annotations

import json

from ekb_api.core.config import get_settings
from ekb_api.core.egress import TenantRoute, resolve_tenant_routing
from ekb_api.domain import AuthContext, TenantRole

API = "/api/v1"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _auth(tenant_id: str) -> AuthContext:
    return AuthContext(
        actor_id="actor-1",
        tenant_id=tenant_id,
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=[],
        policy_version=1,
        trace_id="trace-1",
    )


# ---- M2-7 模型路由解析 ----


def test_resolve_routing_by_tenant_id(monkeypatch):
    monkeypatch.setenv(
        "EKB_TENANT_MODEL_ROUTING",
        json.dumps({"tenant-x": "deepseek-provider"}),
    )
    route = resolve_tenant_routing(_auth("tenant-x"), get_settings())
    assert route == TenantRoute(provider_name="deepseek-provider", model=None)


def test_resolve_routing_by_model_key(monkeypatch):
    monkeypatch.setenv(
        "EKB_MODEL_ROUTES",
        json.dumps({"premium": {"provider": "gpt-provider", "model": "gpt-4o"}}),
    )
    route = resolve_tenant_routing(_auth("tenant-y"), get_settings(), model_routing_key="premium")
    assert route == TenantRoute(provider_name="gpt-provider", model="gpt-4o")


def test_resolve_routing_default_fallback(monkeypatch):
    monkeypatch.delenv("EKB_TENANT_MODEL_ROUTING", raising=False)
    monkeypatch.delenv("EKB_MODEL_ROUTES", raising=False)
    route = resolve_tenant_routing(_auth("tenant-z"), get_settings(), model_routing_key="default")
    assert route == TenantRoute()


def test_resolve_routing_unknown_key_falls_back(monkeypatch):
    monkeypatch.setenv("EKB_MODEL_ROUTES", json.dumps({"premium": "gpt-provider"}))
    # model_routing_key 指向不存在的键 → 回落默认（不报错）。
    route = resolve_tenant_routing(_auth("t"), get_settings(), model_routing_key="nope")
    assert route == TenantRoute()


# ---- M2-7 每租户每日 QA 配额 ----


def _provision_tenant(client, token, name, email, password, quota=0):
    resp = client.post(
        f"{API}/admin/tenants",
        headers=_headers(token),
        json={
            "name": name,
            "owner_email": email,
            "owner_name": name + "-owner",
            "owner_password": password,
            "quota_daily_qa": quota,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_quota_enforced_returns_429(client, dev_token):
    """租户每日配额=1：第 1 次问答 200，第 2 次超额 429（M2-7 配额强裁）。"""
    _provision_tenant(client, dev_token, "QuotaT", "quota@example.com", "quotapass", quota=1)
    # 登录新租户 OWNER。
    login = client.post(
        f"{API}/auth/login", json={"email": "quota@example.com", "password": "quotapass"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    # 第 1 次：配额内，正常返回（SSE 200）。
    r1 = client.post(f"{API}/qa/ask", headers=_headers(token), json={"question": "任意问题"})
    assert r1.status_code == 200, r1.text

    # 第 2 次：已超额，直接 429（不进入流式生成）。
    r2 = client.post(f"{API}/qa/ask", headers=_headers(token), json={"question": "再来一次"})
    assert r2.status_code == 429, r2.text
    assert r2.json()["error"]["code"] == "QUOTA_EXCEEDED"


def test_quota_zero_is_unlimited(client, dev_token):
    """配额=0 表示不限：连续多次问答均成功。"""
    _provision_tenant(client, dev_token, "UnlimT", "unlim@example.com", "unlimpass", quota=0)
    login = client.post(
        f"{API}/auth/login", json={"email": "unlim@example.com", "password": "unlimpass"}
    )
    token = login.json()["access_token"]
    for _ in range(3):
        r = client.post(f"{API}/qa/ask", headers=_headers(token), json={"question": "连续提问"})
        assert r.status_code == 200, r.text


def test_qa_route_does_not_raise(client, dev_token):
    """M2-7 路由透传不应使问答流式路径抛异常。

    回归：generate_answer 曾因 route 位置参数报 TypeError。
    """
    kb_id = client.get(f"{API}/kb", headers=_headers(dev_token)).json()[0]["id"]
    resp = client.post(
        f"{API}/qa/ask",
        headers=_headers(dev_token),
        json={"question": "数据库连接池耗尽先检查哪些指标", "kb_ids": [kb_id]},
    )
    assert resp.status_code == 200, resp.text
    # 流式路径不得出现 error 事件（无 LLM 时应降级到 demo 拼接而非抛异常）。
    assert "event: error" not in resp.text

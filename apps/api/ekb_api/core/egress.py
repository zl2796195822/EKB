from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ekb_api.core.config import Settings
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, EgressPolicy, Tenant


def assert_egress_allowed(tenant: Tenant | None) -> None:
    """M2-7 数据出域策略：禁止出域的租户不允许内容进入生成层（问答）。

    在 qa.ask 入口调用；策略为 deny 时统一拒绝，不泄露租户配置细节。
    """
    if tenant is None:
        return
    if EgressPolicy(tenant.egress_policy) == EgressPolicy.DENY:
        raise ApiError(
            status_code=403,
            code="EGRESS_DENIED",
            message="该租户数据出域策略禁止生成",
        )


@dataclass(frozen=True)
class TenantRoute:
    """M2-7 解析出的租户级模型路由。

    provider_name 为 EKB_MODEL_PROVIDERS 中登记的 provider name（None = 按 settings 顺序取默认）；
    model 为可选模型覆盖（None = 使用 provider 自身 model）。
    """

    provider_name: str | None = None
    model: str | None = None


def _parse_route_value(value) -> TenantRoute | None:
    """把路由配置值（字符串或 {provider,model} 字典）归一为 TenantRoute。"""
    if value is None:
        return None
    if isinstance(value, str):
        return TenantRoute(provider_name=value or None)
    if isinstance(value, dict):
        return TenantRoute(
            provider_name=value.get("provider") or None,
            model=value.get("model") or None,
        )
    return None


def _parse_routing_map(env_key: str) -> dict[str, TenantRoute]:
    """解析形如 {key: provider | {provider, model}} 的 JSON 路由表（来自指定环境变量）。"""
    raw = os.getenv(env_key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    out: dict[str, TenantRoute] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            route = _parse_route_value(value)
            if route is not None:
                out[str(key)] = route
    return out


def resolve_tenant_routing(
    auth: AuthContext, settings: Settings, *, model_routing_key: str = "default"
) -> TenantRoute:
    """M2-7 解析当前租户应使用的模型路由，优先级：

    1) EKB_TENANT_MODEL_ROUTING[tenant_id]：租户级直配（最高优先级，便于临时切换/灰度）。
    2) EKB_MODEL_ROUTES[tenant.model_routing_key]：基于租户记录持久化的路由键（运营配置）。
    3) 默认（provider_name=None → 由 settings.chat_providers 顺序决定）。

    已接入 LLM 调用路径（retrieval.rewrite_query 与 qa.generate_answer 均按此路由选 provider）。
    """
    tenant_map = _parse_routing_map("EKB_TENANT_MODEL_ROUTING")
    if auth.tenant_id in tenant_map:
        return tenant_map[auth.tenant_id]

    if model_routing_key and model_routing_key != "default":
        routes = _parse_routing_map("EKB_MODEL_ROUTES")
        if model_routing_key in routes:
            return routes[model_routing_key]

    return TenantRoute()

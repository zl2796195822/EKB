from __future__ import annotations

import secrets
import time
import uuid
from functools import lru_cache
from typing import Annotated, Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette import status

from ekb_api.core.authorization import CAP_TENANT_PROVISION, capabilities_for_role
from ekb_api.core.config import Settings, get_settings
from ekb_api.core.db import init_db
from ekb_api.core.errors import ApiError, request_id_from
from ekb_api.core.security import sign_payload, verify_password, verify_signed_payload
from ekb_api.domain import AuthContext, TenantRole, User
from ekb_api.store import SqlStore

bearer_scheme = HTTPBearer(auto_error=False)

# 进程内 refresh token 撤销列表（按 jti 索引）。
# M1 tracer 单进程足够；生产环境应替换为 Redis 等共享存储，并在 token 过期后清理。
_revoked_jtis: set[str] = set()


@lru_cache
def get_store() -> SqlStore:
    init_db()
    return SqlStore()


def authenticate_dev_user(email: str, password: str, settings: Settings) -> bool:
    """历史兼容别名：仅按环境变量 dev 账号做明文比对（已弃用，保留供测试过渡）。

    新流程请用 `authenticate_user(store, email, password)`，按库内哈希口令校验。
    """
    email_matches = secrets.compare_digest(email.lower(), settings.dev_user_email.lower())
    password_matches = secrets.compare_digest(password, settings.dev_password)
    return email_matches and password_matches


def authenticate_user(store: SqlStore, email: str, password: str) -> User | None:
    """按库内用户校验口令；返回领域 User，失败返回 None（不泄露账号是否存在）。"""
    user = store.get_user_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def create_access_token(
    actor_id: str,
    tenant_id: str,
    settings: Settings,
    tenant_role: str = "OWNER",
    expires_in: int = 900,
    *,
    platform_role: str = "NONE",
    extra_capabilities: list[str] | None = None,
) -> str:
    role = (
        TenantRole(tenant_role)
        if tenant_role in {r.value for r in TenantRole}
        else TenantRole.OWNER
    )
    capabilities = capabilities_for_role(
        role.value, platform_admin=(platform_role == "PLATFORM_ADMIN")
    )
    if extra_capabilities:
        capabilities = capabilities + [c for c in extra_capabilities if c not in capabilities]
    payload = {
        "sub": actor_id,
        "tenant_id": tenant_id,
        "tenant_role": role.value,
        "platform_role": platform_role,
        "capabilities": capabilities,
        "policy_version": 1,
        "exp": int(time.time()) + expires_in,
    }
    return sign_payload(payload, settings.token_secret)


def create_refresh_token(
    actor_id: str,
    tenant_id: str,
    settings: Settings,
    role: str = "OWNER",
    platform_role: str = "NONE",
) -> str:
    payload = {
        "sub": actor_id,
        "tenant_id": tenant_id,
        "typ": "refresh",
        "role": role,
        "platform_role": platform_role,
        "jti": uuid.uuid4().hex,
        "exp": int(time.time()) + 7 * 24 * 60 * 60,
    }
    return sign_payload(payload, settings.token_secret)


def refresh_access_token(refresh_token: str, settings: Settings) -> str:
    """用 refresh token 换取新的 access token；被撤销或过期的令牌直接拒绝。"""
    payload = verify_signed_payload(refresh_token, settings.token_secret)
    if not payload or payload.get("typ") != "refresh":
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "刷新令牌无效或已过期")
    jti = payload.get("jti")
    if isinstance(jti, str) and jti in _revoked_jtis:
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "刷新令牌已撤销")
    platform_role = str(payload.get("platform_role", "NONE"))
    return create_access_token(
        str(payload["sub"]),
        str(payload["tenant_id"]),
        settings,
        tenant_role=str(payload.get("role", "OWNER")),
        platform_role=platform_role,
    )


def revoke_refresh_token(refresh_token: str, settings: Settings) -> None:
    """撤销 refresh token（登出）。无效令牌静默忽略，不泄露有效性。"""
    payload = verify_signed_payload(refresh_token, settings.token_secret)
    if not payload or payload.get("typ") != "refresh":
        return
    jti = payload.get("jti")
    if isinstance(jti, str):
        _revoked_jtis.add(jti)


def _verify_access_payload(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(bearer_scheme),
    ],
    settings: Settings,
) -> dict[str, object]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "未登录或 Token 无效")

    payload = verify_signed_payload(credentials.credentials, settings.token_secret)
    if not payload or payload.get("typ") == "refresh":
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "未登录或 Token 无效")
    actor_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not isinstance(actor_id, (str, int)) or not isinstance(tenant_id, (str, int)):
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "未登录或 Token 无效")

    return {
        "payload": payload,
        "actor_id": str(actor_id),
        "tenant_id": str(tenant_id),
    }


def get_live_auth_context(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(bearer_scheme),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    verified = _verify_access_payload(request, credentials, settings)

    # Claims are only routing hints.  The service reloads the actor's current
    # membership, role, capabilities, and tenant policy version from SQLite/PG.
    from ekb_api.services.v3_identity import resolve_live_auth_context

    return resolve_live_auth_context(
        verified["actor_id"],
        verified["tenant_id"],
        request_id_from(request),
    )


def _platform_role_from_live_user(actor_id: str, settings: Settings) -> str:
    """Derive the legacy platform flag from the live user, never token claims."""
    if not settings.dev_is_platform_admin:
        return "NONE"

    from sqlalchemy import text

    from ekb_api.core.db import get_session_local

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        email = session.execute(
            text("SELECT email FROM users WHERE id = :actor_id"),
            {"actor_id": actor_id},
        ).scalar_one_or_none()
    if isinstance(email, str) and secrets.compare_digest(email, settings.dev_user_email):
        return "PLATFORM_ADMIN"
    return "NONE"


def get_auth_context(
    request: Request,
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(bearer_scheme),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthContext:
    """Preserve legacy router claims while deriving platform authority live.

    T01a endpoints use ``get_live_auth_context``. Existing routers retain this
    dependency and token shape for compatibility; platform provisioning is
    never accepted from the token's platform_role claim.
    """
    verified = _verify_access_payload(request, credentials, settings)
    payload = verified["payload"]
    actor_id = verified["actor_id"]
    tenant_id = verified["tenant_id"]
    try:
        tenant_role = TenantRole(str(payload.get("tenant_role")))
        policy_version = int(payload.get("policy_version", 1))
    except (TypeError, ValueError):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "UNAUTHENTICATED",
            "未登录或 Token 无效",
        ) from None

    raw_capabilities = payload.get("capabilities")
    capabilities = (
        [
            item
            for item in raw_capabilities
            if isinstance(item, str) and item != CAP_TENANT_PROVISION
        ]
        if isinstance(raw_capabilities, list)
        else capabilities_for_role(tenant_role.value)
    )
    platform_role = _platform_role_from_live_user(actor_id, settings)
    if platform_role == "PLATFORM_ADMIN" and CAP_TENANT_PROVISION not in capabilities:
        capabilities.append(CAP_TENANT_PROVISION)
    return AuthContext(
        actor_id=actor_id,
        tenant_id=tenant_id,
        tenant_role=tenant_role,
        platform_role=platform_role,
        capabilities=capabilities,
        policy_version=policy_version,
        trace_id=request_id_from(request),
    )

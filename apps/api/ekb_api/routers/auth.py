from __future__ import annotations

from typing_extensions import Annotated

from fastapi import APIRouter, Depends, Request
from starlette import status

from ekb_api.core.audit import (
    RESULT_DENIED,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    extract_fingerprints,
    redact_metadata,
)
from ekb_api.core.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    get_store,
    refresh_access_token,
    revoke_refresh_token,
)
from ekb_api.services.v3_profile import (
    revoke_auth_session_by_jti,
    touch_auth_session,
    write_auth_session,
)
from ekb_api.core.config import Settings, get_settings
from ekb_api.core.errors import ApiError, request_id_from
from ekb_api.core.logging import get_logger
from ekb_api.core.security import verify_signed_payload
from ekb_api.schemas import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    TenantSummary,
    UserSummary,
)
from ekb_api.store import SqlStore

router = APIRouter(prefix="/auth", tags=["auth"])

_log = get_logger("ekb.auth")

ACCESS_TOKEN_TTL_SECONDS = 900


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> LoginResponse:
    ip_hash, ua_hash = extract_fingerprints(request)
    trace_id = request_id_from(request)

    user = authenticate_user(store, payload.email, payload.password)
    if user is None:
        # 失败登录也要审计：不泄露账号是否存在，只记录邮箱前缀哈希。
        store.write_audit_log(
            action="auth.login",
            target_type="session",
            result=RESULT_DENIED,
            trace_id=trace_id,
            metadata_redacted=redact_metadata(
                {"email": payload.email, "reason": "invalid_credentials"}
            ),
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
        )
        raise ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "邮箱或密码不正确")

    tenant = store.get_tenant_by_id(user.tenant_id) or store.tenant
    # M2-6 平台管理员（开发期可配）：注入 platform_role 与 tenant:provision 能力，允许开通租户。
    platform_admin = bool(settings.dev_is_platform_admin and user.email == settings.dev_user_email)
    platform_role = "PLATFORM_ADMIN" if platform_admin else "NONE"
    access_token = create_access_token(
        user.id, tenant.id, settings, tenant_role=user.role, platform_role=platform_role
    )
    refresh_token = create_refresh_token(
        user.id, tenant.id, settings, role=user.role, platform_role=platform_role
    )

    store.write_audit_log(
        action="auth.login",
        target_type="session",
        target_id=user.id,
        result=RESULT_SUCCESS,
        trace_id=trace_id,
        tenant_id=tenant.id,
        actor_id=user.id,
        metadata_redacted=redact_metadata({"email": payload.email}),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )

    # Write auth session for session listing（非致命：失败不阻断登录，但必须留痕）。
    try:
        decoded_rt = verify_signed_payload(refresh_token, settings.token_secret) or {}
        jti = decoded_rt.get("jti")
        if isinstance(jti, str):
            write_auth_session(user.id, tenant.id, jti, ip_hash, ua_hash)
    except Exception as exc:  # noqa: BLE001 - 会话写入失败不应阻断登录
        _log.warning(
            "auth.session.write_failed",
            user_id=user.id,
            tenant_id=tenant.id,
            trace_id=trace_id,
            error=repr(exc),
        )

    return LoginResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
        refresh_token=refresh_token,
        token_type="Bearer",
        user=UserSummary(id=user.id, name=user.name, email=user.email, role=user.role),
        tenants=[TenantSummary(id=tenant.id, name=tenant.name, role=tenant.role.value)],
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> RefreshResponse:
    ip_hash, ua_hash = extract_fingerprints(request)
    trace_id = request_id_from(request)

    # 预解析 refresh token 以便在审计中记录主体；刷新失败时仍写入失败审计。
    decoded = verify_signed_payload(payload.refresh_token, settings.token_secret) or {}
    actor_id = str(decoded.get("sub", "")) or None
    tenant_id = str(decoded.get("tenant_id", "")) or None

    try:
        access_token = refresh_access_token(payload.refresh_token, settings)
    except ApiError:
        store.write_audit_log(
            action="auth.refresh",
            target_type="session",
            result=RESULT_FAILURE,
            trace_id=trace_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
        )
        raise

    store.write_audit_log(
        action="auth.refresh",
        target_type="session",
        result=RESULT_SUCCESS,
        trace_id=trace_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )

    # Touch session last_used_at（非致命：失败不阻断刷新，但必须留痕）。
    try:
        jti = decoded.get("jti")
        if isinstance(jti, str):
            touch_auth_session(jti)
    except Exception as exc:  # noqa: BLE001 - 会话更新失败不应阻断刷新
        _log.warning(
            "auth.session.touch_failed",
            actor_id=actor_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            error=repr(exc),
        )

    return RefreshResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
        token_type="Bearer",
    )


@router.post("/logout")
def logout(
    payload: RefreshRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict[str, str]:
    ip_hash, ua_hash = extract_fingerprints(request)
    trace_id = request_id_from(request)
    decoded = verify_signed_payload(payload.refresh_token, settings.token_secret) or {}
    actor_id = str(decoded.get("sub", "")) or None
    tenant_id = str(decoded.get("tenant_id", "")) or None

    revoke_refresh_token(payload.refresh_token, settings)

    # Revoke auth session（非致命：失败不阻断登出，但必须留痕）。
    try:
        jti = decoded.get("jti")
        if isinstance(jti, str):
            revoke_auth_session_by_jti(jti)
    except Exception as exc:  # noqa: BLE001 - 会话吊销失败不应阻断登出
        _log.warning(
            "auth.session.revoke_failed",
            actor_id=actor_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            error=repr(exc),
        )

    store.write_audit_log(
        action="auth.logout",
        target_type="session",
        result=RESULT_SUCCESS,
        trace_id=trace_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    return {"status": "ok"}

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from starlette import status

from ekb_api.core.audit import RESULT_SUCCESS, redact_metadata
from ekb_api.core.auth import get_live_auth_context, get_store
from ekb_api.domain import AuthContext
from ekb_api.services.v3_identity import get_live_subject
from ekb_api.services.v3_profile import (
    change_password,
    create_api_key,
    get_profile,
    get_preferences,
    list_api_keys,
    list_notifications,
    list_sessions,
    mark_notification_read,
    revoke_all_sessions,
    revoke_api_key,
    revoke_session,
    update_preferences,
    update_profile,
)
from ekb_api.store import SqlStore

router = APIRouter(tags=["me"])


def _legacy_compatible_role(slug: str) -> str:
    return "CUSTOMER" if slug == "legacy_customer" else slug.upper()


# ---- Request schemas ----

class PatchProfileRequest(BaseModel):
    display_name: Optional[str] = None
    department: Optional[str] = None
    locale: Optional[str] = None
    timezone: Optional[str] = None
    avatar_url: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class PatchPreferencesRequest(BaseModel):
    preferences: dict  # type: ignore[assignment]


class CreateApiKeyRequest(BaseModel):
    name: str


# ---- GET /me (existing) ----

@router.get("/me")
def me(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict[str, object]:
    subject = get_live_subject(auth)
    store.write_audit_log(
        action="me.read",
        target_type="user",
        target_id=auth.actor_id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata({}),
    )
    profile = get_profile(auth.actor_id, auth.tenant_id)
    return {
        "user": {
            "id": subject.actor_id,
            "name": profile.display_name or subject.user_name,
            "email": subject.email,
            "role": _legacy_compatible_role(subject.role_slug),
            "avatar_url": profile.avatar_url,
            "department": profile.department,
        },
        "tenants": [
            {
                "id": subject.tenant_id,
                "name": subject.tenant_name,
                "role": _legacy_compatible_role(subject.role_slug),
            }
        ],
        "capabilities": auth.capabilities,
        "policy_version": auth.policy_version,
        "tenant_id": subject.tenant_id,
        "role_id": subject.role_id,
        "profile": {
            "display_name": profile.display_name,
            "department": profile.department,
            "locale": profile.locale,
            "timezone": profile.timezone,
            "avatar_url": profile.avatar_url,
        },
    }


# ---- PATCH /me/profile ----

@router.patch("/me/profile")
def patch_me_profile(
    payload: PatchProfileRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, object]:
    profile = update_profile(
        auth.actor_id,
        auth.tenant_id,
        display_name=payload.display_name,
        department=payload.department,
        locale=payload.locale,
        timezone=payload.timezone,
        avatar_url=payload.avatar_url,
    )
    return {"profile": {"display_name": profile.display_name, "department": profile.department,
                       "locale": profile.locale, "timezone": profile.timezone,
                       "avatar_url": profile.avatar_url}}


# ---- POST /me/password ----

@router.post("/me/password")
def post_me_password(
    payload: ChangePasswordRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, str]:
    change_password(auth.actor_id, payload.old_password, payload.new_password)
    return {"status": "ok"}


# ---- GET /me/preferences ----

@router.get("/me/preferences")
def get_me_preferences(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, object]:
    prefs = get_preferences(auth.actor_id, auth.tenant_id)
    return {"preferences": prefs}


# ---- PATCH /me/preferences ----

@router.patch("/me/preferences")
def patch_me_preferences(
    payload: PatchPreferencesRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, object]:
    prefs = update_preferences(auth.actor_id, auth.tenant_id, payload.preferences)
    return {"preferences": prefs}


# ---- GET /me/sessions ----

@router.get("/me/sessions")
def get_me_sessions(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, object]:
    sessions = list_sessions(auth.actor_id, auth.tenant_id)
    return {
        "items": [
            {
                "id": s.id,
                "ip_hash": s.ip_hash,
                "ua_hash": s.ua_hash,
                "status": s.status,
                "created_at": s.created_at,
                "last_used_at": s.last_used_at,
            }
            for s in sessions
        ]
    }


# ---- DELETE /me/sessions/{id} ----

@router.delete("/me/sessions/{session_id}")
def delete_me_session(
    session_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    revoke_all: bool = False,
) -> dict[str, str]:
    if revoke_all:
        count = revoke_all_sessions(auth.actor_id, auth.tenant_id)
        return {"status": "ok", "revoked_count": str(count)}
    ok = revoke_session(session_id, auth.actor_id, auth.tenant_id)
    if not ok:
        from ekb_api.core.errors import ApiError
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "会话不存在或已撤销")
    return {"status": "ok"}


# ---- GET /me/api-keys ----

@router.get("/me/api-keys")
def get_me_api_keys(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, object]:
    keys = list_api_keys(auth.actor_id, auth.tenant_id)
    return {
        "items": [
            {
                "id": k.id,
                "prefix": k.prefix,
                "name": k.name,
                "created_at": k.created_at,
                "last_used_at": k.last_used_at,
                "status": k.status,
            }
            for k in keys
        ]
    }


# ---- POST /me/api-keys ----

@router.post("/me/api-keys")
def post_me_api_key(
    payload: CreateApiKeyRequest,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, object]:
    raw_secret, item = create_api_key(auth.actor_id, auth.tenant_id, payload.name)
    # Secret is only returned once.
    return {
        "id": item.id,
        "prefix": item.prefix,
        "name": item.name,
        "secret": raw_secret,
        "created_at": item.created_at,
        "status": item.status,
    }


# ---- DELETE /me/api-keys/{id} ----

@router.delete("/me/api-keys/{key_id}")
def delete_me_api_key(
    key_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, str]:
    ok = revoke_api_key(key_id, auth.actor_id, auth.tenant_id)
    if not ok:
        from ekb_api.core.errors import ApiError
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "API Key 不存在或已撤销")
    return {"status": "ok"}


# ---- GET /me/notifications ----

@router.get("/me/notifications")
def get_me_notifications(
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
    unread_only: bool = False,
) -> dict[str, object]:
    notifications = list_notifications(auth.actor_id, auth.tenant_id, unread_only=unread_only)
    return {
        "items": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "metadata": n.metadata,
                "priority": n.priority,
                "read_at": n.read_at,
                "created_at": n.created_at,
            }
            for n in notifications
        ]
    }


# ---- PATCH /me/notifications/{id} ----

@router.patch("/me/notifications/{notification_id}")
def patch_me_notification(
    notification_id: str,
    auth: Annotated[AuthContext, Depends(get_live_auth_context)],
) -> dict[str, str]:
    mark_notification_read(notification_id, auth.actor_id, auth.tenant_id)
    return {"status": "ok"}

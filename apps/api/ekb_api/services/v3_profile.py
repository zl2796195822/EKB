from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from starlette import status

from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.core.security import hash_password, verify_password
from ekb_api.domain import utc_now

# ---- Preferences JSON key whitelist ----
_PREFERENCES_ALLOWED_KEYS = frozenset({
    "language", "theme", "density", "sidebar_collapsed",
    "kb_default_view", "qa_model_preference",
    "notification_email", "notification_digest",
})

_API_KEY_PREFIX = "ekb_"


def _permission_denied() -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "当前账号无权执行该操作")


@dataclass(frozen=True)
class ProfileData:
    display_name: str | None
    department: str | None
    locale: str | None
    timezone: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class SessionItem:
    id: str
    jti_hash: str
    ip_hash: str | None
    ua_hash: str | None
    status: str
    created_at: str
    last_used_at: str | None


@dataclass(frozen=True)
class ApiKeyItem:
    id: str
    prefix: str
    name: str
    created_at: str
    last_used_at: str | None
    status: str


@dataclass(frozen=True)
class NotificationItem:
    id: str
    type: str
    title: str
    body: str | None
    metadata: dict[str, Any] | None
    priority: str
    read_at: str | None
    created_at: str


# ---- Profile ----

def get_profile(user_id: str, tenant_id: str) -> ProfileData:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT display_name, department, locale, timezone, avatar_url "
                "FROM user_profiles WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        ).mappings().first()
    if row is None:
        return ProfileData(None, None, None, None, None)
    return ProfileData(
        display_name=row["display_name"],
        department=row["department"],
        locale=row["locale"],
        timezone=row["timezone"],
        avatar_url=row["avatar_url"],
    )


def update_profile(
    user_id: str,
    tenant_id: str,
    *,
    display_name: str | None = None,
    department: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
    avatar_url: str | None = None,
) -> ProfileData:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        # Upsert: update existing or insert new row.
        existing = session.execute(
            text("SELECT 1 FROM user_profiles WHERE tenant_id = :t AND user_id = :u"),
            {"t": tenant_id, "u": user_id},
        ).scalar()
        if existing:
            set_clauses = []
            params: dict[str, Any] = {"t": tenant_id, "u": user_id}
            if display_name is not None:
                set_clauses.append("display_name = :dn")
                params["dn"] = display_name
            if department is not None:
                set_clauses.append("department = :dept")
                params["dept"] = department
            if locale is not None:
                set_clauses.append("locale = :loc")
                params["loc"] = locale
            if timezone is not None:
                set_clauses.append("timezone = :tz")
                params["tz"] = timezone
            if avatar_url is not None:
                set_clauses.append("avatar_url = :au")
                params["au"] = avatar_url
            if set_clauses:
                set_clauses.append("updated_at = :now")
                params["now"] = utc_now()
                stmt = (
                    "UPDATE user_profiles SET "
                    + ", ".join(set_clauses)
                    + " WHERE tenant_id = :t AND user_id = :u"
                )
                session.execute(text(stmt), params)
        else:
            # display_name / locale / timezone 在 v3_001 里是 NOT NULL，缺省值必须补齐。
            now = utc_now()
            session.execute(
                text(
                    "INSERT INTO user_profiles "
                    "(tenant_id, user_id, display_name, department, locale, timezone, "
                    "avatar_url, created_at, updated_at) "
                    "VALUES (:t, :u, :dn, :dept, :loc, :tz, :au, :now, :now)"
                ),
                {
                    "t": tenant_id,
                    "u": user_id,
                    "dn": display_name or "",
                    "dept": department,
                    "loc": locale or "zh-CN",
                    "tz": timezone or "Asia/Shanghai",
                    "au": avatar_url,
                    "now": now,
                },
            )
        session.commit()
    return get_profile(user_id, tenant_id)


# ---- Password ----

def change_password(
    user_id: str,
    old_password: str,
    new_password: str,
) -> None:
    """校验旧密码后更新哈希，并撤销该用户全部会话。

    SqlStore 只有 get_user_by_email，这里直接按主键查 users 表。
    """
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        current_hash = session.execute(
            text("SELECT password_hash FROM users WHERE id = :uid"),
            {"uid": user_id},
        ).scalar_one_or_none()
        if current_hash is None:
            raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "用户不存在")
        if not verify_password(old_password, str(current_hash)):
            raise ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_CREDENTIALS", "当前密码不正确")

        now = utc_now()
        session.execute(
            text("UPDATE users SET password_hash = :h, updated_at = :now WHERE id = :uid"),
            {"h": hash_password(new_password), "uid": user_id, "now": now},
        )
        # 改密后一律踢掉全部在线会话，避免旧口令签发的令牌继续可用。
        session.execute(
            text("UPDATE auth_sessions SET status = 'REVOKED', revoked_at = :now "
                  "WHERE user_id = :uid AND status = 'ACTIVE'"),
            {"uid": user_id, "now": now},
        )
        session.commit()


# ---- Preferences ----

def get_preferences(user_id: str, tenant_id: str) -> dict[str, Any]:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT preferences FROM user_preferences WHERE tenant_id = :t AND user_id = :u"),
            {"t": tenant_id, "u": user_id},
        ).scalar_one_or_none()
    if row is None:
        return {}
    if isinstance(row, str):
        try:
            return json.loads(row)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def update_preferences(
    user_id: str,
    tenant_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    # Whitelist keys.
    sanitized = {k: v for k, v in patch.items() if k in _PREFERENCES_ALLOWED_KEYS}
    current = get_preferences(user_id, tenant_id)
    current.update(sanitized)

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text("SELECT 1 FROM user_preferences WHERE tenant_id = :t AND user_id = :u"),
            {"t": tenant_id, "u": user_id},
        ).scalar()
        prefs_json = json.dumps(current, ensure_ascii=False)
        if existing:
            session.execute(
                text("UPDATE user_preferences SET preferences = :p, updated_at = :now "
                      "WHERE tenant_id = :t AND user_id = :u"),
                {"p": prefs_json, "t": tenant_id, "u": user_id, "now": utc_now()},
            )
        else:
            # user_preferences 没有 created_at 列，只有 updated_at。
            session.execute(
                text("INSERT INTO user_preferences "
                     "(tenant_id, user_id, notifications, preferences, updated_at) "
                     "VALUES (:t, :u, '{}', :p, :now)"),
                {"t": tenant_id, "u": user_id, "p": prefs_json, "now": utc_now()},
            )
        session.commit()
    return current


# ---- Sessions ----

def list_sessions(user_id: str, tenant_id: str) -> list[SessionItem]:
    # 列名按 v3_001 迁移的真实定义：主键是 session_id，UA 列是 user_agent_hash。
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT session_id AS id, jti_hash, ip_hash, "
                "user_agent_hash AS ua_hash, status, created_at, last_used_at "
                "FROM auth_sessions "
                "WHERE tenant_id = :t AND user_id = :u "
                "ORDER BY last_used_at DESC, created_at DESC LIMIT 50"
            ),
            {"t": tenant_id, "u": user_id},
        ).mappings().all()
    return [
        SessionItem(
            id=str(r["id"]),
            jti_hash=str(r["jti_hash"]),
            ip_hash=r["ip_hash"],
            ua_hash=r["ua_hash"],
            status=r["status"],
            created_at=str(r["created_at"]),
            last_used_at=str(r["last_used_at"]) if r["last_used_at"] else None,
        )
        for r in rows
    ]


def revoke_session(session_id: str, user_id: str, tenant_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        result = session.execute(
            text("UPDATE auth_sessions SET status = 'REVOKED', revoked_at = :now "
                  "WHERE session_id = :sid AND user_id = :uid AND tenant_id = :t "
                  "AND status = 'ACTIVE'"),
            {"sid": session_id, "uid": user_id, "t": tenant_id, "now": utc_now()},
        )
        session.commit()
    return result.rowcount > 0


def revoke_all_sessions(user_id: str, tenant_id: str, exclude_jti_hash: str | None = None) -> int:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        if exclude_jti_hash:
            result = session.execute(
                text("UPDATE auth_sessions SET status = 'REVOKED', revoked_at = :now "
                      "WHERE user_id = :uid AND tenant_id = :t AND status = 'ACTIVE' "
                      "AND jti_hash != :exclude"),
                {"uid": user_id, "t": tenant_id, "exclude": exclude_jti_hash, "now": utc_now()},
            )
        else:
            result = session.execute(
                text("UPDATE auth_sessions SET status = 'REVOKED', revoked_at = :now "
                      "WHERE user_id = :uid AND tenant_id = :t AND status = 'ACTIVE'"),
                {"uid": user_id, "t": tenant_id, "now": utc_now()},
            )
        session.commit()
    return result.rowcount


# ---- API Keys ----

def create_api_key(user_id: str, tenant_id: str, name: str) -> tuple[str, ApiKeyItem]:
    raw_secret = secrets.token_urlsafe(32)
    prefix = _API_KEY_PREFIX + raw_secret[:8].upper()
    secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()
    key_id = uuid.uuid4().hex
    now = utc_now()

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO api_keys (id, tenant_id, user_id, name, prefix, secret_hash, status, "
                "expires_at, last_used_at, created_at, revoked_at) "
                "VALUES (:id, :t, :u, :name, :prefix, :hash, 'ACTIVE', NULL, NULL, :now, NULL)"
            ),
            {"id": key_id, "t": tenant_id, "u": user_id, "name": name,
             "prefix": prefix, "hash": secret_hash, "now": now},
        )
        session.commit()

    item = ApiKeyItem(
        id=key_id,
        prefix=prefix,
        name=name,
        created_at=now,
        last_used_at=None,
        status="ACTIVE",
    )
    return raw_secret, item


def list_api_keys(user_id: str, tenant_id: str) -> list[ApiKeyItem]:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT id, prefix, name, created_at, last_used_at, status "
                "FROM api_keys WHERE tenant_id = :t AND user_id = :u "
                "ORDER BY created_at DESC LIMIT 20"
            ),
            {"t": tenant_id, "u": user_id},
        ).mappings().all()
    return [
        ApiKeyItem(
            id=str(r["id"]),
            prefix=str(r["prefix"]),
            name=str(r["name"]),
            created_at=str(r["created_at"]),
            last_used_at=str(r["last_used_at"]) if r["last_used_at"] else None,
            status=r["status"],
        )
        for r in rows
    ]


def revoke_api_key(key_id: str, user_id: str, tenant_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        result = session.execute(
            text("UPDATE api_keys SET status = 'REVOKED', revoked_at = :now "
                  "WHERE id = :kid AND user_id = :uid AND tenant_id = :t AND status = 'ACTIVE'"),
            {"kid": key_id, "uid": user_id, "t": tenant_id, "now": utc_now()},
        )
        session.commit()
    return result.rowcount > 0


# ---- Notifications ----

def list_notifications(
    user_id: str, tenant_id: str, *, unread_only: bool = False, limit: int = 30
) -> list[NotificationItem]:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        # v3_001 建的列叫 notification_type，且没有独立的 priority 列，
        # 优先级存放在 metadata JSON 里（见 create_notification）。
        base_sql = (
            "SELECT id, notification_type, title, body, metadata, read_at, created_at "
            "FROM user_notifications WHERE tenant_id = :t AND user_id = :u "
        )
        params: dict[str, Any] = {"t": tenant_id, "u": user_id}
        if unread_only:
            base_sql += " AND read_at IS NULL"
        base_sql += " ORDER BY created_at DESC LIMIT :lim"
        params["lim"] = limit
        rows = session.execute(text(base_sql), params).mappings().all()
    items: list[NotificationItem] = []
    for r in rows:
        metadata = _safe_json_load(r["metadata"]) or {}
        priority = metadata.get("priority") if isinstance(metadata, dict) else None
        items.append(
            NotificationItem(
                id=str(r["id"]),
                type=str(r["notification_type"]),
                title=str(r["title"]),
                body=r["body"],
                metadata=metadata if isinstance(metadata, dict) else None,
                priority=str(priority) if priority else "NORMAL",
                read_at=str(r["read_at"]) if r["read_at"] else None,
                created_at=str(r["created_at"]),
            )
        )
    return items


def create_notification(
    user_id: str,
    tenant_id: str,
    *,
    notification_type: str,
    title: str,
    body: str = "",
    priority: str = "NORMAL",
    metadata: dict[str, Any] | None = None,
) -> str:
    """写入一条站内通知。优先级随 metadata 落库，因为 v3_001 没有 priority 列。"""
    payload = dict(metadata or {})
    payload["priority"] = priority
    notification_id = uuid.uuid4().hex
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.execute(
            text(
                "INSERT INTO user_notifications "
                "(id, tenant_id, user_id, notification_type, title, body, metadata, "
                "read_at, created_at) "
                "VALUES (:id, :t, :u, :nt, :title, :body, :meta, NULL, :now)"
            ),
            {
                "id": notification_id,
                "t": tenant_id,
                "u": user_id,
                "nt": notification_type,
                "title": title,
                "body": body,
                "meta": json.dumps(payload, ensure_ascii=False),
                "now": utc_now(),
            },
        )
        session.commit()
    return notification_id


def mark_notification_read(notification_id: str, user_id: str, tenant_id: str) -> bool:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        result = session.execute(
            text("UPDATE user_notifications SET read_at = :now "
                  "WHERE id = :nid AND user_id = :uid AND tenant_id = :t AND read_at IS NULL"),
            {"nid": notification_id, "uid": user_id, "t": tenant_id, "now": utc_now()},
        )
        session.commit()
    return result.rowcount > 0


# ---- Auth session writing (called by login/refresh/logout) ----

_REFRESH_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def write_auth_session(
    user_id: str,
    tenant_id: str,
    jti: str,
    ip_hash: str | None,
    ua_hash: str | None,
    expires_at: str | None = None,
) -> None:
    """登录时登记一条会话。

    v3_001 的 auth_sessions 主键是 session_id，且 last_used_at / expires_at 均为
    NOT NULL，jti_hash 带 UNIQUE 约束，所以这里必须补齐字段并做幂等处理。
    """
    jti_hash = hashlib.sha256(jti.encode()).hexdigest()
    now = utc_now()
    if expires_at is None:
        expires_at = _iso_after(_REFRESH_TOKEN_TTL_SECONDS)
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        exists = session.execute(
            text("SELECT 1 FROM auth_sessions WHERE jti_hash = :jh"),
            {"jh": jti_hash},
        ).scalar()
        if exists:
            return
        session.execute(
            text(
                "INSERT INTO auth_sessions "
                "(session_id, tenant_id, user_id, jti_hash, status, created_at, "
                "last_used_at, expires_at, revoked_at, ip_hash, user_agent_hash) "
                "VALUES (:sid, :t, :u, :jh, 'ACTIVE', :now, :now, :exp, NULL, :ip, :ua)"
            ),
            {
                "sid": uuid.uuid4().hex,
                "t": tenant_id,
                "u": user_id,
                "jh": jti_hash,
                "now": now,
                "exp": expires_at,
                "ip": ip_hash,
                "ua": ua_hash,
            },
        )
        session.commit()


def touch_auth_session(jti: str) -> None:
    jti_hash = hashlib.sha256(jti.encode()).hexdigest()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.execute(
            text("UPDATE auth_sessions SET last_used_at = :now "
                  "WHERE jti_hash = :jh AND status = 'ACTIVE'"),
            {"jh": jti_hash, "now": utc_now()},
        )
        session.commit()


def revoke_auth_session_by_jti(jti: str) -> None:
    jti_hash = hashlib.sha256(jti.encode()).hexdigest()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        session.execute(
            text("UPDATE auth_sessions SET status = 'REVOKED', revoked_at = :now "
                  "WHERE jti_hash = :jh AND status = 'ACTIVE'"),
            {"jh": jti_hash, "now": utc_now()},
        )
        session.commit()


# ---- Helpers ----

def _iso_after(seconds: int) -> str:
    """返回 seconds 秒之后的 UTC ISO 时间串，格式与 utc_now() 保持一致。"""
    moment = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=seconds)
    return moment.isoformat().replace("+00:00", "Z")


def _safe_json_load(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value

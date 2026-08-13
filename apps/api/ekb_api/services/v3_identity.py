from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from starlette import status

from ekb_api.core.audit import hash_fingerprint
from ekb_api.core.authorization import CAP_TEAM_USER_READ, CAP_TENANT_PROVISION
from ekb_api.core.config import get_settings
from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.migrations.v3_001_identity import decode_cursor, encode_cursor

_ROLE_ENUMS = {
    "owner": TenantRole.OWNER,
    "admin": TenantRole.ADMIN,
    "member": TenantRole.MEMBER,
    "auditor": TenantRole.MEMBER,
    "legacy_customer": TenantRole.CUSTOMER,
}
_ROLE_FILTERS = frozenset(_ROLE_ENUMS)
_MEMBERSHIP_STATUSES = frozenset({"ACTIVE", "INVITED", "SUSPENDED"})


@dataclass(frozen=True)
class LiveSubject:
    actor_id: str
    tenant_id: str
    user_name: str
    email: str
    tenant_name: str
    role_id: str
    role_slug: str
    membership_status: str
    policy_version: int
    capabilities: list[str]


@dataclass(frozen=True)
class UserListPage:
    items: list[dict[str, Any]]
    next_cursor: str | None
    page_size: int


def _permission_denied() -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "当前账号无权执行该操作")


def _unauthenticated() -> ApiError:
    return ApiError(status.HTTP_401_UNAUTHORIZED, "UNAUTHENTICATED", "未登录或 Token 无效")


def _role_enum(slug: str) -> TenantRole:
    try:
        return _ROLE_ENUMS[slug]
    except KeyError:
        raise _permission_denied() from None


def _query_live_subject(actor_id: str, tenant_id: str) -> LiveSubject:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = (
            session.execute(
                text(
                    """
                SELECT u.id, u.name, u.email, t.id AS tenant_id, t.name AS tenant_name,
                       t.policy_version, tm.status AS membership_status,
                       tr.id AS role_id, tr.slug AS role_slug
                FROM users AS u
                JOIN tenants AS t ON t.id = :tenant_id
                JOIN tenant_memberships AS tm
                  ON tm.tenant_id = t.id AND tm.user_id = u.id
                JOIN tenant_roles AS tr
                  ON tr.tenant_id = tm.tenant_id AND tr.id = tm.role_id
                WHERE u.id = :actor_id
                """
                ),
                {"actor_id": actor_id, "tenant_id": tenant_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _unauthenticated()
        if row["membership_status"] != "ACTIVE":
            raise _permission_denied()
        role_slug = str(row["role_slug"])
        capabilities = [
            str(item[0])
            for item in session.execute(
                text(
                    "SELECT capability FROM role_permissions "
                    "WHERE tenant_id = :tenant_id AND role_id = :role_id "
                    "ORDER BY capability"
                ),
                {"tenant_id": tenant_id, "role_id": row["role_id"]},
            )
        ]
    return LiveSubject(
        actor_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        user_name=str(row["name"]),
        email=str(row["email"]),
        tenant_name=str(row["tenant_name"]),
        role_id=str(row["role_id"]),
        role_slug=role_slug,
        membership_status=str(row["membership_status"]),
        policy_version=int(row["policy_version"]),
        capabilities=capabilities,
    )


def resolve_live_auth_context(
    actor_id: str,
    tenant_id: str,
    trace_id: str,
) -> AuthContext:
    subject = _query_live_subject(actor_id, tenant_id)
    capabilities = list(subject.capabilities)
    settings = get_settings()
    is_live_platform_admin = settings.dev_is_platform_admin and secrets.compare_digest(
        subject.email, settings.dev_user_email
    )
    platform_role = "PLATFORM_ADMIN" if is_live_platform_admin else "NONE"
    if is_live_platform_admin and CAP_TENANT_PROVISION not in capabilities:
        capabilities.append(CAP_TENANT_PROVISION)
    return AuthContext(
        actor_id=subject.actor_id,
        tenant_id=subject.tenant_id,
        tenant_role=_role_enum(subject.role_slug),
        platform_role=platform_role,
        capabilities=capabilities,
        policy_version=subject.policy_version,
        trace_id=trace_id,
    )


def get_live_subject(auth: AuthContext) -> LiveSubject:
    return _query_live_subject(auth.actor_id, auth.tenant_id)


def assert_tenant_user_read_scope(auth: AuthContext, tenant_id: str) -> None:
    # Platform provisioning is not a user-list authority.  This route is
    # intentionally always same-tenant to avoid cross-tenant existence leaks.
    if tenant_id != auth.tenant_id:
        raise _permission_denied()
    if CAP_TEAM_USER_READ not in auth.capabilities:
        raise _permission_denied()


def _normalize_role_filter(role: str | None) -> str | None:
    if role is None:
        return None
    normalized = role.strip().lower()
    if normalized == "customer":
        normalized = "legacy_customer"
    if normalized not in _ROLE_FILTERS:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "role 参数不合法")
    return normalized


def _normalize_status_filter(status_filter: str | None) -> str | None:
    if status_filter is None:
        return None
    normalized = status_filter.strip().upper()
    if normalized not in _MEMBERSHIP_STATUSES:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "status 参数不合法")
    return normalized


def _decode_bound_cursor(
    cursor: str | None,
    *,
    auth: AuthContext,
    tenant_id: str,
    query: str | None,
    role: str | None,
    status_filter: str | None,
    sort: str,
) -> dict[str, str] | None:
    if not cursor:
        return None
    try:
        raw_cursor, signature = cursor.rsplit(".", 1)
        expected_signature = hmac.new(
            get_settings().token_secret.encode("utf-8"),
            raw_cursor.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(signature, expected_signature):
            raise ValueError("invalid cursor signature")
        value = decode_cursor(raw_cursor)
    except (ValueError, TypeError):
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "cursor 参数不合法"
        ) from None
    expected = {
        "actor_id": auth.actor_id,
        "tenant_id": tenant_id,
        "query": query or "",
        "role": role or "",
        "status": status_filter or "",
        "sort": sort,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "cursor 参数不匹配")
    if not value.get("updated_at") or not value.get("id"):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "cursor 参数不合法")
    return value


def list_tenant_users(
    auth: AuthContext,
    tenant_id: str,
    *,
    query: str | None = None,
    role: str | None = None,
    status_filter: str | None = None,
    sort: str = "updated_at_desc",
    cursor: str | None = None,
    page_size: int = 20,
) -> UserListPage:
    assert_tenant_user_read_scope(auth, tenant_id)
    if sort != "updated_at_desc":
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "sort 参数不合法")
    if not 1 <= page_size <= 100:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", "page_size 参数不合法")
    normalized_role = _normalize_role_filter(role)
    normalized_status = _normalize_status_filter(status_filter)
    bound_cursor = _decode_bound_cursor(
        cursor,
        auth=auth,
        tenant_id=tenant_id,
        query=query,
        role=normalized_role,
        status_filter=normalized_status,
        sort=sort,
    )

    filters = ["tm.tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id, "limit": page_size + 1}
    if query:
        filters.append(
            "(LOWER(u.email) LIKE :query_pattern "
            "OR LOWER(COALESCE(up.display_name, u.name)) LIKE :query_pattern "
            "OR LOWER(COALESCE(up.department, '')) LIKE :query_pattern)"
        )
        params["query_pattern"] = f"%{query.lower()}%"
    if normalized_role:
        filters.append("tr.slug = :role")
        params["role"] = normalized_role
    if normalized_status:
        filters.append("tm.status = :status")
        params["status"] = normalized_status
    if bound_cursor:
        filters.append(
            "(tm.updated_at < :cursor_updated_at OR "
            "(tm.updated_at = :cursor_updated_at AND u.id < :cursor_id))"
        )
        params["cursor_updated_at"] = bound_cursor["updated_at"]
        params["cursor_id"] = bound_cursor["id"]

    statement = text(
        """
        SELECT u.id, u.email, COALESCE(up.display_name, u.name) AS display_name,
               up.department, tr.slug AS role, tr.id AS role_id, tm.status,
               tm.joined_at, tm.updated_at
        FROM tenant_memberships AS tm
        JOIN users AS u ON u.id = tm.user_id
        JOIN tenant_roles AS tr ON tr.tenant_id = tm.tenant_id AND tr.id = tm.role_id
        LEFT JOIN user_profiles AS up ON up.tenant_id = tm.tenant_id AND up.user_id = tm.user_id
        WHERE """
        + " AND ".join(filters)
        + " ORDER BY tm.updated_at DESC, u.id DESC LIMIT :limit"
    )
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(statement, params).mappings().all()

    has_more = len(rows) > page_size
    rows = rows[:page_size]
    items = [
        {
            "id": str(row["id"]),
            "email": str(row["email"]),
            "display_name": str(row["display_name"]),
            "department": row["department"],
            "role": str(row["role"]),
            "role_id": str(row["role_id"]),
            "status": str(row["status"]),
            "joined_at": str(row["joined_at"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        raw_cursor = encode_cursor(
            {
                "actor_id": auth.actor_id,
                "tenant_id": tenant_id,
                "query": query or "",
                "role": normalized_role or "",
                "status": normalized_status or "",
                "sort": sort,
                "updated_at": str(last["updated_at"]),
                "id": str(last["id"]),
            }
        )
        signature = hmac.new(
            get_settings().token_secret.encode("utf-8"),
            raw_cursor.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        next_cursor = f"{raw_cursor}.{signature}"
    return UserListPage(items=items, next_cursor=next_cursor, page_size=page_size)


def redacted_user_list_metadata(
    *,
    query: str | None,
    role: str | None,
    status_filter: str | None,
    sort: str,
    page_size: int,
    returned_count: int,
    requested_tenant_id: str | None = None,
    error_code: str | None = None,
    cursor_present: bool = False,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "query_hash": hash_fingerprint(query or "") if query else None,
        "query_length": len(query) if query else 0,
        "role": role,
        "status": status_filter,
        "sort": sort,
        "page_size": page_size,
        "returned_count": returned_count,
        "cursor_present": cursor_present,
    }
    if requested_tenant_id is not None:
        metadata["requested_tenant_hash"] = hash_fingerprint(requested_tenant_id)
    if error_code is not None:
        metadata["error_code"] = error_code
    return metadata

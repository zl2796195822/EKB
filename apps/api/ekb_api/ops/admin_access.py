"""Narrow bootstrap and repair primitives for a configured administrator.

These helpers deliberately operate on one user, one tenant and one knowledge
base at a time.  They are used by deployment bootstrap and by the explicit
repair CLI; neither path enumerates or grants access to unrelated private
knowledge bases.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import inspect, text

from ekb_api.core.authorization import v3_capabilities_for_role
from ekb_api.core.security import hash_password
from ekb_api.domain import utc_now

_ROLE_SLUGS: Final[dict[str, str]] = {
    "OWNER": "owner",
    "ADMIN": "admin",
    "MEMBER": "member",
    "CUSTOMER": "legacy_customer",
}
_KB_ROLES: Final[frozenset[str]] = frozenset({"OWNER", "ADMIN", "EDITOR", "VIEWER"})
_REQUIRED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "audit_logs",
        "kb_memberships",
        "knowledge_bases",
        "role_permissions",
        "tenant_memberships",
        "tenant_roles",
        "tenants",
        "users",
    }
)


@dataclass(frozen=True)
class AdminBootstrapResult:
    created_user: bool
    created_membership: bool
    reconciled_membership: bool


@dataclass(frozen=True)
class AdminAccessRepairResult:
    mode: str
    legacy_role_action: str
    tenant_membership_action: str
    kb_membership_action: str


def validate_runtime_admin(
    *,
    email: str,
    name: str,
    password: str,
    production: bool,
) -> None:
    """Validate deployment-provided admin data without logging its values."""
    if not email.strip() or not name.strip() or not password:
        raise ValueError("runtime administrator identity is incomplete")
    if production:
        if email.strip().lower() in {"admin", "admin@example.com"}:
            raise ValueError("default administrator identity is prohibited")
        if name.strip().lower() in {"admin", "ekb admin"}:
            raise ValueError("default administrator identity is prohibited")
        if password in {"admin", "change-me-local-only"}:
            raise ValueError("default administrator password is prohibited")


def ensure_configured_admin(
    engine,
    *,
    email: str,
    name: str,
    password: str,
) -> AdminBootstrapResult:
    """Create an initial owner or reconcile its membership projection.

    Existing accounts retain their password hash.  Password changes belong to
    the authenticated password-change flow, not to every service restart.
    """
    _require_tables(engine)
    now = utc_now()
    with engine.begin() as connection:
        user = connection.execute(
            text(
                "SELECT id, tenant_id, role FROM users "
                "WHERE lower(email) = lower(:email) LIMIT 1"
            ),
            {"email": email},
        ).mappings().first()
        if user is None:
            tenant_id = str(uuid4())
            user_id = str(uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (
                      id, name, role, policy_version, model_routing_key,
                      egress_policy, quota_daily_qa, quota_storage_docs,
                      quota_storage_bytes_per_file, created_at, updated_at
                    ) VALUES (
                      :id, :name, 'OWNER', 1, 'default', 'ALLOW',
                      1000, 100000, 52428800, :now, :now
                    )
                    """
                ),
                {"id": tenant_id, "name": f"{name} 工作区", "now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                      id, name, email, password_hash, tenant_id, role, created_at, updated_at
                    ) VALUES (:id, :name, :email, :password_hash, :tenant_id, 'OWNER', :now, :now)
                    """
                ),
                {
                    "id": user_id,
                    "name": name,
                    "email": email,
                    "password_hash": hash_password(password),
                    "tenant_id": tenant_id,
                    "now": now,
                },
            )
            role_id = _ensure_role(connection, tenant_id, "owner", now)
            _insert_membership(connection, tenant_id, user_id, role_id, now)
            return AdminBootstrapResult(True, True, False)

        tenant_id = str(user["tenant_id"] or "")
        if not tenant_id:
            raise RuntimeError("configured administrator has no tenant")
        _require_tenant(connection, tenant_id)
        role_slug = _role_slug(str(user["role"] or ""))
        role_id = _ensure_role(connection, tenant_id, role_slug, now)
        membership = connection.execute(
            text(
                "SELECT id, role_id, status FROM tenant_memberships "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"tenant_id": tenant_id, "user_id": user["id"]},
        ).mappings().first()
        if membership is None:
            _insert_membership(connection, tenant_id, str(user["id"]), role_id, now)
            return AdminBootstrapResult(False, True, False)
        if str(membership["status"]) != "ACTIVE":
            raise RuntimeError("configured administrator membership is not active")
        if str(membership["role_id"]) != role_id:
            connection.execute(
                text(
                    "UPDATE tenant_memberships SET role_id = :role_id, updated_at = :now "
                    "WHERE id = :id"
                ),
                {"role_id": role_id, "now": now, "id": membership["id"]},
            )
            return AdminBootstrapResult(False, False, True)
        return AdminBootstrapResult(False, False, False)


def repair_admin_access(
    engine,
    *,
    email: str,
    tenant_id: str,
    kb_id: str,
    tenant_role: str,
    kb_role: str,
    apply: bool,
) -> AdminAccessRepairResult:
    """Repair one administrator's projections and one intentional KB ACL.

    A user may only be repaired inside its current legacy tenant.  This avoids
    silently moving identities across tenants, which would otherwise expose a
    private knowledge base through the legacy login token path.
    """
    _require_uuid(tenant_id, "tenant_id")
    _require_uuid(kb_id, "kb_id")
    role_slug = _role_slug(tenant_role)
    normalized_kb_role = kb_role.strip().upper()
    if normalized_kb_role not in _KB_ROLES:
        raise ValueError("unsupported knowledge base role")
    _require_tables(engine)

    connection_context = engine.begin() if apply else engine.connect()
    with connection_context as connection:
        user = connection.execute(
            text(
                "SELECT id, tenant_id, role FROM users "
                "WHERE lower(email) = lower(:email) LIMIT 1"
            ),
            {"email": email},
        ).mappings().first()
        if user is None:
            raise RuntimeError("configured administrator was not found")
        if str(user["tenant_id"] or "") != tenant_id:
            raise RuntimeError("configured administrator belongs to a different tenant")
        _require_tenant(connection, tenant_id)
        kb = connection.execute(
            text(
                "SELECT id FROM knowledge_bases "
                "WHERE id = :kb_id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"kb_id": kb_id, "tenant_id": tenant_id},
        ).first()
        if kb is None:
            raise RuntimeError("target knowledge base is not active in the target tenant")

        tenant_action = _membership_action(connection, tenant_id, str(user["id"]), role_slug)
        kb_action = _kb_membership_action(
            connection, tenant_id, kb_id, str(user["id"]), normalized_kb_role
        )
        legacy_action = (
            "unchanged" if str(user["role"]).upper() == tenant_role.strip().upper() else "updated"
        )
        if not apply:
            return AdminAccessRepairResult(
                "dry-run", legacy_action, tenant_action, kb_action
            )

        now = utc_now()
        role_id = _ensure_role(connection, tenant_id, role_slug, now)
        if legacy_action == "updated":
            connection.execute(
                text("UPDATE users SET role = :role, updated_at = :now WHERE id = :id"),
                {"role": tenant_role.strip().upper(), "now": now, "id": user["id"]},
            )
        _apply_membership_action(
            connection,
            tenant_id,
            str(user["id"]),
            role_id,
            tenant_action,
            now,
        )
        _apply_kb_membership_action(
            connection,
            tenant_id,
            kb_id,
            str(user["id"]),
            normalized_kb_role,
            kb_action,
            now,
        )
        _write_repair_audit(
            connection,
            tenant_id=tenant_id,
            actor_id=str(user["id"]),
            kb_id=kb_id,
            legacy_action=legacy_action,
            tenant_action=tenant_action,
            kb_action=kb_action,
            now=now,
        )
        return AdminAccessRepairResult(
            "applied", legacy_action, tenant_action, kb_action
        )


def _require_tables(engine) -> None:
    missing = sorted(_REQUIRED_TABLES - set(inspect(engine).get_table_names()))
    if missing:
        raise RuntimeError("required schema tables are missing")


def _require_tenant(connection, tenant_id: str) -> None:
    if connection.execute(
        text("SELECT 1 FROM tenants WHERE id = :tenant_id"), {"tenant_id": tenant_id}
    ).first() is None:
        raise RuntimeError("target tenant was not found")


def _require_uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"invalid {field}") from None


def _role_slug(value: str) -> str:
    try:
        return _ROLE_SLUGS[value.strip().upper()]
    except KeyError:
        raise ValueError("unsupported tenant role") from None


def _ensure_role(connection, tenant_id: str, slug: str, now: str) -> str:
    row = connection.execute(
        text(
            "SELECT id FROM tenant_roles WHERE tenant_id = :tenant_id AND slug = :slug"
        ),
        {"tenant_id": tenant_id, "slug": slug},
    ).first()
    if row is None:
        role_id = str(uuid4())
        display_name = (
            "Customer (legacy compatibility)" if slug == "legacy_customer" else slug.title()
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_roles (
                  id, tenant_id, slug, display_name, description, is_builtin,
                  is_system_protected, created_by, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :slug, :display_name, '', 1, :protected,
                  NULL, :now, :now
                )
                """
            ),
            {
                "id": role_id,
                "tenant_id": tenant_id,
                "slug": slug,
                "display_name": display_name,
                "protected": slug == "legacy_customer",
                "now": now,
            },
        )
    else:
        role_id = str(row[0])
    existing = {
        str(row[0])
        for row in connection.execute(
            text(
                "SELECT capability FROM role_permissions "
                "WHERE tenant_id = :tenant_id AND role_id = :role_id"
            ),
            {"tenant_id": tenant_id, "role_id": role_id},
        )
    }
    for capability in v3_capabilities_for_role(slug):
        if capability not in existing:
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, tenant_id, capability, created_at) "
                    "VALUES (:role_id, :tenant_id, :capability, :now)"
                ),
                {
                    "role_id": role_id,
                    "tenant_id": tenant_id,
                    "capability": capability,
                    "now": now,
                },
            )
    return role_id


def _insert_membership(connection, tenant_id: str, user_id: str, role_id: str, now: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO tenant_memberships (
              id, tenant_id, user_id, role_id, status, joined_at, suspended_at,
              created_at, updated_at
            ) VALUES (:id, :tenant_id, :user_id, :role_id, 'ACTIVE', :now, NULL, :now, :now)
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role_id": role_id,
            "now": now,
        },
    )


def _membership_action(connection, tenant_id: str, user_id: str, role_slug: str) -> str:
    membership = connection.execute(
        text(
            "SELECT membership.id, membership.role_id, membership.status, role.slug "
            "FROM tenant_memberships AS membership "
            "JOIN tenant_roles AS role ON role.id = membership.role_id "
            "AND role.tenant_id = membership.tenant_id "
            "WHERE membership.tenant_id = :tenant_id AND membership.user_id = :user_id"
        ),
        {"tenant_id": tenant_id, "user_id": user_id},
    ).mappings().first()
    if membership is None:
        return "created"
    if str(membership["status"]) != "ACTIVE":
        raise RuntimeError("target tenant membership is not active")
    return "unchanged" if str(membership["slug"]) == role_slug else "updated"


def _kb_membership_action(
    connection, tenant_id: str, kb_id: str, user_id: str, kb_role: str
) -> str:
    rows = connection.execute(
        text(
            "SELECT id, role FROM kb_memberships "
            "WHERE tenant_id = :tenant_id AND kb_id = :kb_id AND user_id = :user_id "
            "ORDER BY created_at ASC"
        ),
        {"tenant_id": tenant_id, "kb_id": kb_id, "user_id": user_id},
    ).mappings().all()
    if len(rows) > 1:
        raise RuntimeError("target knowledge base membership is duplicated")
    if not rows:
        return "created"
    return "unchanged" if str(rows[0]["role"]) == kb_role else "updated"


def _apply_membership_action(
    connection, tenant_id: str, user_id: str, role_id: str, action: str, now: str
) -> None:
    if action == "created":
        _insert_membership(connection, tenant_id, user_id, role_id, now)
    elif action == "updated":
        connection.execute(
            text(
                "UPDATE tenant_memberships SET role_id = :role_id, updated_at = :now "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"role_id": role_id, "now": now, "tenant_id": tenant_id, "user_id": user_id},
        )


def _apply_kb_membership_action(
    connection,
    tenant_id: str,
    kb_id: str,
    user_id: str,
    kb_role: str,
    action: str,
    now: str,
) -> None:
    if action == "created":
        connection.execute(
            text(
                """
                INSERT INTO kb_memberships (
                  id, tenant_id, kb_id, user_id, role, granted_by, created_at, updated_at
                ) VALUES (:id, :tenant_id, :kb_id, :user_id, :role, :granted_by, :now, :now)
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": tenant_id,
                "kb_id": kb_id,
                "user_id": user_id,
                "role": kb_role,
                "granted_by": user_id,
                "now": now,
            },
        )
    elif action == "updated":
        connection.execute(
            text(
                "UPDATE kb_memberships SET role = :role, granted_by = :granted_by, "
                "updated_at = :now "
                "WHERE tenant_id = :tenant_id AND kb_id = :kb_id AND user_id = :user_id"
            ),
            {
                "role": kb_role,
                "granted_by": user_id,
                "now": now,
                "tenant_id": tenant_id,
                "kb_id": kb_id,
                "user_id": user_id,
            },
        )


def _write_repair_audit(
    connection,
    *,
    tenant_id: str,
    actor_id: str,
    kb_id: str,
    legacy_action: str,
    tenant_action: str,
    kb_action: str,
    now: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO audit_logs (
              id, tenant_id, actor_id, action, target_type, target_id, result,
              trace_id, ip_hash, user_agent_hash, metadata_redacted, created_at
            ) VALUES (
              :id, :tenant_id, :actor_id, 'admin_access_repair', 'knowledge_base', :kb_id,
              'SUCCESS', :trace_id, NULL, NULL, :metadata, :now
            )
            """
        ),
        {
            "id": str(uuid4()),
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "kb_id": kb_id,
            "trace_id": f"admin-access-repair-{uuid4()}",
            "metadata": json.dumps(
                {
                    "legacy_role": legacy_action,
                    "tenant_membership": tenant_action,
                    "kb_membership": kb_action,
                },
                separators=(",", ":"),
            ),
            "now": now,
        },
    )

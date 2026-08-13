"""V3 identity slice tests."""

import json

from sqlalchemy import text

from ekb_api.core.authorization import v3_capabilities_for_role
from ekb_api.core.db import get_session_local
from ekb_api.domain import new_id, utc_now


def ensure_v3_fixture_roles(tenant_ids: list[str], role_slugs: list[str]) -> None:
    """Create only role/permission rows needed by a live-subject fixture."""
    labels = {
        "owner": "Owner",
        "admin": "Admin",
        "member": "Member",
        "auditor": "Auditor",
        "legacy_customer": "Customer (legacy compatibility)",
    }
    SessionLocal = get_session_local()
    now = utc_now()
    with SessionLocal() as session:
        for tenant_id in tenant_ids:
            for role_slug in role_slugs:
                role_id = session.execute(
                    text(
                        "SELECT id FROM tenant_roles "
                        "WHERE tenant_id = :tenant_id AND slug = :slug"
                    ),
                    {"tenant_id": tenant_id, "slug": role_slug},
                ).scalar_one_or_none()
                if role_id is None:
                    role_id = new_id()
                    session.execute(
                        text(
                            "INSERT INTO tenant_roles "
                            "(id, tenant_id, slug, display_name, description, is_builtin, "
                            "is_system_protected, created_by, created_at, updated_at) "
                            "VALUES (:id, :tenant_id, :slug, :display_name, '', 1, "
                            ":is_system_protected, NULL, :created_at, :updated_at)"
                        ),
                        {
                            "id": role_id,
                            "tenant_id": tenant_id,
                            "slug": role_slug,
                            "display_name": labels[role_slug],
                            "is_system_protected": role_slug == "legacy_customer",
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
                for capability in v3_capabilities_for_role(role_slug):
                    exists = session.execute(
                        text(
                            "SELECT 1 FROM role_permissions "
                            "WHERE tenant_id = :tenant_id AND role_id = :role_id "
                            "AND capability = :capability"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "role_id": role_id,
                            "capability": capability,
                        },
                    ).scalar_one_or_none()
                    if exists is None:
                        session.execute(
                            text(
                                "INSERT INTO role_permissions "
                                "(role_id, tenant_id, capability, created_at) "
                                "VALUES (:role_id, :tenant_id, :capability, :created_at)"
                            ),
                            {
                                "role_id": role_id,
                                "tenant_id": tenant_id,
                                "capability": capability,
                                "created_at": now,
                            },
                        )
        session.commit()


def install_v3_fixture_memberships(
    bindings: list[tuple[str, str, str, str]],
) -> None:
    """Install real v3 relations for users created after the app migration.

    The application migration remains responsible for validating/backfilling
    every legacy row. Route fixtures add only their newly-created rows so a
    prior test cannot turn a valid migration test into an unrelated rerun.
    """
    labels = {
        "owner": "Owner",
        "admin": "Admin",
        "member": "Member",
        "auditor": "Auditor",
        "legacy_customer": "Customer (legacy compatibility)",
    }
    SessionLocal = get_session_local()
    now = utc_now()
    with SessionLocal() as session:
        for tenant_id, user_id, role_slug, membership_status in bindings:
            user = session.execute(
                text("SELECT name, created_at, updated_at FROM users WHERE id = :user_id"),
                {"user_id": user_id},
            ).mappings().one()
            role_id = session.execute(
                text(
                    "SELECT id FROM tenant_roles "
                    "WHERE tenant_id = :tenant_id AND slug = :slug"
                ),
                {"tenant_id": tenant_id, "slug": role_slug},
            ).scalar_one_or_none()
            if role_id is None:
                role_id = new_id()
                session.execute(
                    text(
                        "INSERT INTO tenant_roles "
                        "(id, tenant_id, slug, display_name, description, is_builtin, "
                        "is_system_protected, created_by, created_at, updated_at) "
                        "VALUES (:id, :tenant_id, :slug, :display_name, '', 1, "
                        ":is_system_protected, NULL, :created_at, :updated_at)"
                    ),
                    {
                        "id": role_id,
                        "tenant_id": tenant_id,
                        "slug": role_slug,
                        "display_name": labels[role_slug],
                        "is_system_protected": role_slug == "legacy_customer",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            for capability in v3_capabilities_for_role(role_slug):
                exists = session.execute(
                    text(
                        "SELECT 1 FROM role_permissions "
                        "WHERE tenant_id = :tenant_id AND role_id = :role_id "
                        "AND capability = :capability"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "role_id": role_id,
                        "capability": capability,
                    },
                ).scalar_one_or_none()
                if exists is None:
                    session.execute(
                        text(
                            "INSERT INTO role_permissions "
                            "(role_id, tenant_id, capability, created_at) "
                            "VALUES (:role_id, :tenant_id, :capability, :created_at)"
                        ),
                        {
                            "role_id": role_id,
                            "tenant_id": tenant_id,
                            "capability": capability,
                            "created_at": now,
                        },
                    )
            membership_id = session.execute(
                text(
                    "SELECT id FROM tenant_memberships "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            ).scalar_one_or_none()
            if membership_id is None:
                membership_id = new_id()
                session.execute(
                    text(
                        "INSERT INTO tenant_memberships "
                        "(id, tenant_id, user_id, role_id, status, joined_at, suspended_at, "
                        "created_at, updated_at) VALUES (:id, :tenant_id, :user_id, :role_id, "
                        ":status, :joined_at, :suspended_at, :created_at, :updated_at)"
                    ),
                    {
                        "id": membership_id,
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "role_id": role_id,
                        "status": membership_status,
                        "joined_at": str(user["created_at"] or now),
                        "suspended_at": now if membership_status == "SUSPENDED" else None,
                        "created_at": str(user["created_at"] or now),
                        "updated_at": str(user["updated_at"] or now),
                    },
                )
            else:
                session.execute(
                    text(
                        "UPDATE tenant_memberships SET role_id = :role_id, status = :status, "
                        "suspended_at = :suspended_at WHERE id = :id"
                    ),
                    {
                        "id": membership_id,
                        "role_id": role_id,
                        "status": membership_status,
                        "suspended_at": now if membership_status == "SUSPENDED" else None,
                    },
                )
            profile_exists = session.execute(
                text(
                    "SELECT 1 FROM user_profiles "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            ).scalar_one_or_none()
            if profile_exists is None:
                session.execute(
                    text(
                        "INSERT INTO user_profiles "
                        "(user_id, tenant_id, display_name, department, locale, timezone, "
                        "avatar_url, created_at, updated_at) VALUES "
                        "(:user_id, :tenant_id, :display_name, NULL, 'zh-CN', 'Asia/Shanghai', "
                        "NULL, :created_at, :updated_at)"
                    ),
                    {
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                        "display_name": str(user["name"]),
                        "created_at": str(user["created_at"] or now),
                        "updated_at": str(user["updated_at"] or now),
                    },
                )
            preferences_exists = session.execute(
                text(
                    "SELECT 1 FROM user_preferences "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            ).scalar_one_or_none()
            if preferences_exists is None:
                session.execute(
                    text(
                        "INSERT INTO user_preferences "
                        "(user_id, tenant_id, notifications, preferences, updated_at) "
                        "VALUES (:user_id, :tenant_id, :notifications, :preferences, :updated_at)"
                    ),
                    {
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                        "notifications": json.dumps({}, separators=(",", ":")),
                        "preferences": json.dumps({}, separators=(",", ":")),
                        "updated_at": str(user["updated_at"] or now),
                    },
                )
        session.commit()

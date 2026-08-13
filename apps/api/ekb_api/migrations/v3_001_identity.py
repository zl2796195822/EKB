from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_001_identity"

V3_001_OWNED_TABLES = (
    "schema_migrations",
    "tenant_roles",
    "tenant_memberships",
    "role_permissions",
    "user_profiles",
    "user_preferences",
    "user_notifications",
    "api_keys",
    "auth_sessions",
)
V3_001_ROLLBACK_POLICY = (
    "retain schema_migrations; no down plan before application; block rollback while "
    "legacy_customer is assigned; remove the version row only during an explicitly "
    "approved data-loss rollback"
)

_ROLE_MAPPING = {
    "OWNER": "owner",
    "ADMIN": "admin",
    "MEMBER": "member",
    "CUSTOMER": "legacy_customer",
}
_BUILTIN_ROLES = ("owner", "admin", "member", "auditor")
_ROLE_LABELS = {
    "owner": "Owner",
    "admin": "Admin",
    "member": "Member",
    "auditor": "Auditor",
    "legacy_customer": "Customer (legacy compatibility)",
}
_ROLE_DESCRIPTIONS = {
    "owner": "Built-in tenant owner role",
    "admin": "Built-in tenant administrator role",
    "member": "Built-in tenant member role",
    "auditor": "Built-in read-only audit role",
    "legacy_customer": "Legacy compatibility role; cannot be escalated by v3",
}
# Immutable v3_001 seed snapshot. Later capability registry extensions belong
# to later migrations and must not change this migration's checksum.
_V3_001_SEED_CAPABILITIES = MappingProxyType(
    {
        "owner": (
            "kb:read",
            "kb:write",
            "qa:ask",
            "audit:read",
            "team:user:read",
            "team:user:manage",
        ),
        "admin": (
            "kb:read",
            "kb:write",
            "qa:ask",
            "audit:read",
            "team:user:read",
            "team:user:manage",
        ),
        "member": ("kb:read", "qa:ask", "audit:read"),
        "auditor": ("kb:read", "qa:ask", "audit:read"),
        "legacy_customer": ("kb:read", "qa:ask"),
    }
)
_BACKFILL_POLICY = {
    "role_mapping": _ROLE_MAPPING,
    "builtin_roles": _BUILTIN_ROLES,
    "role_labels": _ROLE_LABELS,
    "role_descriptions": _ROLE_DESCRIPTIONS,
    "role_permissions": {
        slug: list(capabilities) for slug, capabilities in _V3_001_SEED_CAPABILITIES.items()
    },
    "profile_defaults": {"locale": "zh-CN", "timezone": "Asia/Shanghai"},
    "invalid_legacy_rows": "hard-fail; never guess tenant or role",
}

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version VARCHAR(64) PRIMARY KEY,
      applied_at VARCHAR(32) NOT NULL,
      checksum VARCHAR(128) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_roles (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      slug VARCHAR(64) NOT NULL,
      display_name VARCHAR(128) NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
      is_system_protected BOOLEAN NOT NULL DEFAULT FALSE,
      created_by VARCHAR(36),
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      UNIQUE (tenant_id, slug),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id) REFERENCES tenants (id),
      FOREIGN KEY (created_by) REFERENCES users (id),
      CHECK (is_builtin IN (FALSE, TRUE)),
      CHECK (is_system_protected IN (FALSE, TRUE))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tenant_memberships (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      role_id VARCHAR(36) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
      joined_at VARCHAR(32) NOT NULL,
      suspended_at VARCHAR(32),
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      UNIQUE (tenant_id, user_id),
      FOREIGN KEY (tenant_id) REFERENCES tenants (id),
      FOREIGN KEY (user_id) REFERENCES users (id),
      FOREIGN KEY (tenant_id, role_id) REFERENCES tenant_roles (tenant_id, id),
      CHECK (status IN ('ACTIVE', 'INVITED', 'SUSPENDED'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS role_permissions (
      role_id VARCHAR(36) NOT NULL,
      tenant_id VARCHAR(36) NOT NULL,
      capability VARCHAR(128) NOT NULL,
      created_at VARCHAR(32) NOT NULL,
      PRIMARY KEY (role_id, capability),
      FOREIGN KEY (tenant_id, role_id) REFERENCES tenant_roles (tenant_id, id),
      CHECK (capability <> '')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
      user_id VARCHAR(36) NOT NULL,
      tenant_id VARCHAR(36) NOT NULL,
      display_name VARCHAR(255) NOT NULL,
      department VARCHAR(255),
      locale VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
      timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
      avatar_url VARCHAR(1024),
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      PRIMARY KEY (tenant_id, user_id),
      FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships (tenant_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
      user_id VARCHAR(36) NOT NULL,
      tenant_id VARCHAR(36) NOT NULL,
      notifications JSON NOT NULL,
      preferences JSON NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      PRIMARY KEY (tenant_id, user_id),
      FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships (tenant_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_notifications (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      notification_type VARCHAR(64) NOT NULL,
      title VARCHAR(255) NOT NULL,
      body TEXT NOT NULL,
      metadata JSON NOT NULL,
      read_at VARCHAR(32),
      created_at VARCHAR(32) NOT NULL,
      FOREIGN KEY (tenant_id, user_id)
        REFERENCES tenant_memberships (tenant_id, user_id),
      CHECK (notification_type <> '')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_keys (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      name VARCHAR(128) NOT NULL,
      prefix VARCHAR(16) NOT NULL,
      secret_hash VARCHAR(128) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
      expires_at VARCHAR(32),
      last_used_at VARCHAR(32),
      created_at VARCHAR(32) NOT NULL,
      revoked_at VARCHAR(32),
      UNIQUE (prefix),
      FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships (tenant_id, user_id),
      CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
      session_id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      jti_hash VARCHAR(128) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
      created_at VARCHAR(32) NOT NULL,
      last_used_at VARCHAR(32) NOT NULL,
      expires_at VARCHAR(32) NOT NULL,
      revoked_at VARCHAR(32),
      ip_hash VARCHAR(128),
      user_agent_hash VARCHAR(128),
      UNIQUE (jti_hash),
      FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_memberships (tenant_id, user_id),
      CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_memberships_tenant_status "
    "ON tenant_memberships (tenant_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_memberships_tenant_user "
    "ON tenant_memberships (tenant_id, user_id)",
    "CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_status "
    "ON auth_sessions (tenant_id, user_id, status, last_used_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_auth_sessions_jti_status ON auth_sessions (jti_hash, status)",
    "CREATE INDEX IF NOT EXISTS ix_roles_tenant_builtin "
    "ON tenant_roles (tenant_id, is_builtin, slug)",
    "CREATE INDEX IF NOT EXISTS ix_notifications_user_unread "
    "ON user_notifications (tenant_id, user_id, read_at, created_at DESC)",
)

V3_001_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\n"
        + json.dumps(_BACKFILL_POLICY, sort_keys=True, separators=(",", ":"))
        + "\n"
        + "rollback="
        + V3_001_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
    ).encode()
).hexdigest()

_REQUIRED_COLUMNS = {
    "schema_migrations": {"version", "applied_at", "checksum"},
    "tenant_roles": {
        "id",
        "tenant_id",
        "slug",
        "display_name",
        "description",
        "is_builtin",
        "is_system_protected",
        "created_by",
        "created_at",
        "updated_at",
    },
    "tenant_memberships": {
        "id",
        "tenant_id",
        "user_id",
        "role_id",
        "status",
        "joined_at",
        "suspended_at",
        "created_at",
        "updated_at",
    },
    "role_permissions": {"role_id", "tenant_id", "capability", "created_at"},
    "user_profiles": {
        "user_id",
        "tenant_id",
        "display_name",
        "department",
        "locale",
        "timezone",
        "avatar_url",
        "created_at",
        "updated_at",
    },
    "user_preferences": {"user_id", "tenant_id", "notifications", "preferences", "updated_at"},
    "user_notifications": {
        "id",
        "tenant_id",
        "user_id",
        "notification_type",
        "title",
        "body",
        "metadata",
        "read_at",
        "created_at",
    },
    "api_keys": {
        "id",
        "tenant_id",
        "user_id",
        "name",
        "prefix",
        "secret_hash",
        "status",
        "expires_at",
        "last_used_at",
        "created_at",
        "revoked_at",
    },
    "auth_sessions": {
        "session_id",
        "tenant_id",
        "user_id",
        "jti_hash",
        "status",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
        "ip_hash",
        "user_agent_hash",
    },
}
_REQUIRED_INDEXES = {
    "ix_memberships_tenant_status": (
        "tenant_memberships",
        ("tenant_id", "status", "updated_at"),
        (False, False, True),
    ),
    "ix_memberships_tenant_user": (
        "tenant_memberships",
        ("tenant_id", "user_id"),
        (False, False),
    ),
    "ix_auth_sessions_user_status": (
        "auth_sessions",
        ("tenant_id", "user_id", "status", "last_used_at"),
        (False, False, False, True),
    ),
    "ix_auth_sessions_jti_status": (
        "auth_sessions",
        ("jti_hash", "status"),
        (False, False),
    ),
    "ix_roles_tenant_builtin": (
        "tenant_roles",
        ("tenant_id", "is_builtin", "slug"),
        (False, False, False),
    ),
    "ix_notifications_user_unread": (
        "user_notifications",
        ("tenant_id", "user_id", "read_at", "created_at"),
        (False, False, False, True),
    ),
}

_BUSINESS_OWNED_TABLES = tuple(
    table for table in V3_001_OWNED_TABLES if table != "schema_migrations"
)


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict[str, int]


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]
    backfill_counts: dict[str, int]


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: str | None = None


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(("ekb", VERSION, kind, *parts))))


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _ddl_for_connection(connection: Connection) -> tuple[str, ...]:
    """Use JSON text on SQLite and native JSONB on PostgreSQL."""
    if connection.dialect.name == "postgresql":
        return tuple(statement.replace(" JSON ", " JSONB ") for statement in _DDL)
    if connection.dialect.name == "sqlite":
        return _DDL
    raise RuntimeError(f"{VERSION} unsupported database dialect: {connection.dialect.name}")


def _legacy_users(connection: Connection) -> list[dict[str, str | None]]:
    tables = set(inspect(connection).get_table_names())
    if not {"users", "tenants"}.issubset(tables):
        return []
    rows = connection.execute(
        text(
            """
            SELECT u.id, u.name, u.email, u.tenant_id, u.role,
                   u.created_at, u.updated_at, t.id AS resolved_tenant_id
            FROM users AS u
            LEFT JOIN tenants AS t ON t.id = u.tenant_id
            ORDER BY u.id
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _validate_legacy_users(connection: Connection) -> list[dict[str, str | None]]:
    rows = _legacy_users(connection)
    for row in rows:
        legacy_role = row["role"]
        if legacy_role not in _ROLE_MAPPING:
            raise RuntimeError(
                f"{VERSION} backfill blocked: null/unknown legacy role for user {row['id']}"
            )
        if not row["tenant_id"] or not row["resolved_tenant_id"]:
            raise RuntimeError(
                f"{VERSION} backfill blocked: unresolved tenant for user {row['id']}"
            )
    return rows


def _ensure_role(
    connection: Connection,
    tenant_id: str,
    slug: str,
    now: str,
) -> str:
    existing = (
        connection.execute(
            text(
                "SELECT id, display_name, is_system_protected "
                "FROM tenant_roles WHERE tenant_id = :tenant_id AND slug = :slug"
            ),
            {"tenant_id": tenant_id, "slug": slug},
        )
        .mappings()
        .first()
    )
    if existing:
        if slug == "legacy_customer" and (
            existing["display_name"] != _ROLE_LABELS[slug]
            or not bool(existing["is_system_protected"])
        ):
            raise RuntimeError(f"{VERSION} backfill blocked: legacy_customer role is not protected")
        return str(existing["id"])

    role_id = _stable_id("role", tenant_id, slug)
    connection.execute(
        text(
            """
            INSERT INTO tenant_roles
                (id, tenant_id, slug, display_name, description, is_builtin,
                 is_system_protected, created_by, created_at, updated_at)
            VALUES
                (:id, :tenant_id, :slug, :display_name, :description, :is_builtin,
                 :is_system_protected, NULL, :created_at, :updated_at)
            """
        ),
        {
            "id": role_id,
            "tenant_id": tenant_id,
            "slug": slug,
            "display_name": _ROLE_LABELS[slug],
            "description": _ROLE_DESCRIPTIONS[slug],
            "is_builtin": True,
            "is_system_protected": slug == "legacy_customer",
            "created_at": now,
            "updated_at": now,
        },
    )
    return role_id


def _ensure_permissions(
    connection: Connection, tenant_id: str, role_id: str, slug: str, now: str
) -> None:
    expected = set(_V3_001_SEED_CAPABILITIES[slug])
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
    if slug == "legacy_customer" and existing and existing != expected:
        raise RuntimeError(
            f"{VERSION} backfill blocked: legacy_customer capabilities are not exact"
        )
    for capability in sorted(expected - existing):
        connection.execute(
            text(
                """
                INSERT INTO role_permissions (role_id, tenant_id, capability, created_at)
                VALUES (:role_id, :tenant_id, :capability, :created_at)
                """
            ),
            {
                "role_id": role_id,
                "tenant_id": tenant_id,
                "capability": capability,
                "created_at": now,
            },
        )


def _backfill(connection: Connection) -> dict[str, int]:
    rows = _validate_legacy_users(connection)
    now = utc_now()
    tenant_rows = connection.execute(text("SELECT id FROM tenants ORDER BY id")).scalars().all()
    customer_tenants = {str(row["tenant_id"]) for row in rows if row["role"] == "CUSTOMER"}
    role_ids: dict[tuple[str, str], str] = {}
    for tenant_id in tenant_rows:
        tenant_id = str(tenant_id)
        slugs = list(_BUILTIN_ROLES)
        if tenant_id in customer_tenants:
            slugs.append("legacy_customer")
        for slug in slugs:
            role_id = _ensure_role(connection, tenant_id, slug, now)
            _ensure_permissions(connection, tenant_id, role_id, slug, now)
            role_ids[(tenant_id, slug)] = role_id

    memberships = profiles = preferences = 0
    for row in rows:
        tenant_id = str(row["tenant_id"])
        user_id = str(row["id"])
        role_slug = _ROLE_MAPPING[str(row["role"])]
        role_id = role_ids[(tenant_id, role_slug)]
        existing_membership = (
            connection.execute(
                text(
                    "SELECT id, role_id, status FROM tenant_memberships "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user_id},
            )
            .mappings()
            .first()
        )
        if existing_membership:
            role_check = connection.execute(
                text(
                    "SELECT slug FROM tenant_roles WHERE tenant_id = :tenant_id AND id = :role_id"
                ),
                {"tenant_id": tenant_id, "role_id": existing_membership["role_id"]},
            ).scalar_one_or_none()
            if role_check != role_slug:
                raise RuntimeError(
                    f"{VERSION} backfill blocked: ambiguous membership role for user {user_id}"
                )
            if existing_membership["status"] not in {"ACTIVE", "INVITED", "SUSPENDED"}:
                raise RuntimeError(
                    f"{VERSION} backfill blocked: invalid membership status for user {user_id}"
                )
        else:
            created_at = str(row["created_at"] or now)
            connection.execute(
                text(
                    """
                    INSERT INTO tenant_memberships
                        (id, tenant_id, user_id, role_id, status, joined_at,
                         suspended_at, created_at, updated_at)
                    VALUES
                        (:id, :tenant_id, :user_id, :role_id, 'ACTIVE', :joined_at,
                         NULL, :created_at, :updated_at)
                    """
                ),
                {
                    "id": _stable_id("membership", tenant_id, user_id),
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "role_id": role_id,
                    "joined_at": created_at,
                    "created_at": created_at,
                    "updated_at": str(row["updated_at"] or created_at),
                },
            )
            memberships += 1

        profile_exists = connection.execute(
            text("SELECT 1 FROM user_profiles WHERE tenant_id = :tenant_id AND user_id = :user_id"),
            {"tenant_id": tenant_id, "user_id": user_id},
        ).scalar_one_or_none()
        if profile_exists is None:
            created_at = str(row["created_at"] or now)
            connection.execute(
                text(
                    """
                    INSERT INTO user_profiles
                        (user_id, tenant_id, display_name, department, locale, timezone,
                         avatar_url, created_at, updated_at)
                    VALUES
                        (:user_id, :tenant_id, :display_name, NULL, 'zh-CN', 'Asia/Shanghai',
                         NULL, :created_at, :updated_at)
                    """
                ),
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "display_name": str(row["name"]),
                    "created_at": created_at,
                    "updated_at": str(row["updated_at"] or created_at),
                },
            )
            profiles += 1

        preferences_exists = connection.execute(
            text(
                "SELECT 1 FROM user_preferences WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        ).scalar_one_or_none()
        if preferences_exists is None:
            connection.execute(
                text(
                    """
                    INSERT INTO user_preferences
                        (user_id, tenant_id, notifications, preferences, updated_at)
                    VALUES (:user_id, :tenant_id, :notifications, :preferences, :updated_at)
                    """
                ),
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "notifications": json.dumps({}, separators=(",", ":")),
                    "preferences": json.dumps({}, separators=(",", ":")),
                    "updated_at": str(row["updated_at"] or now),
                },
            )
            preferences += 1

    return {
        "tenants": len(tenant_rows),
        "memberships": memberships,
        "profiles": profiles,
        "preferences": preferences,
    }


def _catalog_index_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    names: set[str] = set()
    for table in inspector.get_table_names():
        names.update(
            str(index["name"])
            for index in inspector.get_indexes(table)
            if index.get("name")
        )
    return names


def _index_columns_and_order(
    connection: Connection,
    table: str,
    index_name: str,
    inspector,
) -> tuple[tuple[str, ...], tuple[bool, ...]] | None:
    index = next(
        (item for item in inspector.get_indexes(table) if item.get("name") == index_name),
        None,
    )
    if index is None:
        return None
    columns = tuple(str(column) for column in index.get("column_names") or ())
    descending = tuple(False for _ in columns)
    if connection.dialect.name == "sqlite":
        rows = connection.exec_driver_sql(f"PRAGMA index_xinfo('{index_name}')").mappings().all()
        keyed = [row for row in rows if int(row.get("key") or 0) == 1]
        if keyed:
            columns = tuple(str(row["name"]) for row in keyed)
            descending = tuple(bool(row.get("desc")) for row in keyed)
    elif connection.dialect.name == "postgresql":
        rows = connection.execute(
            text(
                """
                SELECT attribute.attname AS column_name,
                       ((index_def.indoption[ordinality.ordinality - 1] & 1) = 1)
                           AS is_descending
                FROM pg_class AS index_class
                JOIN pg_index AS index_def ON index_def.indexrelid = index_class.oid
                JOIN LATERAL unnest(index_def.indkey) WITH ORDINALITY
                    AS ordinality(attnum, ordinality) ON TRUE
                JOIN pg_attribute AS attribute
                  ON attribute.attrelid = index_def.indrelid
                 AND attribute.attnum = ordinality.attnum
                WHERE index_class.relname = :index_name
                ORDER BY ordinality.ordinality
                """
            ),
            {"index_name": index_name},
        ).mappings().all()
        if rows:
            columns = tuple(str(row["column_name"]) for row in rows)
            descending = tuple(bool(row["is_descending"]) for row in rows)
    return columns, descending


def _verify_catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    for table, required_columns in _REQUIRED_COLUMNS.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table)}
        missing = required_columns - actual_columns
        if missing:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} "
                f"missing columns={','.join(sorted(missing))}"
            )

    def has_unique(table: str, columns: tuple[str, ...]) -> bool:
        unique_constraints = inspector.get_unique_constraints(table)
        primary_key = inspector.get_pk_constraint(table).get("constrained_columns") or []
        return any(
            tuple(item.get("column_names") or ()) == columns for item in unique_constraints
        ) or tuple(primary_key) == columns

    required_unique_constraints = {
        "tenant_roles": (("tenant_id", "slug"), ("tenant_id", "id")),
        "tenant_memberships": (("tenant_id", "user_id"),),
        "user_profiles": (("tenant_id", "user_id"),),
        "user_preferences": (("tenant_id", "user_id"),),
        "api_keys": (("prefix",),),
        "auth_sessions": (("jti_hash",),),
    }
    for table, constraints in required_unique_constraints.items():
        if any(not has_unique(table, columns) for columns in constraints):
            raise RuntimeError(f"{VERSION} verification failed: unique constraint on {table}")

    def has_foreign_key(
        table: str, columns: tuple[str, ...], referred_table: str
    ) -> bool:
        return any(
            tuple(item.get("constrained_columns") or ()) == columns
            and item.get("referred_table") == referred_table
            for item in inspector.get_foreign_keys(table)
        )

    for table in (
        "user_profiles",
        "user_preferences",
        "user_notifications",
        "api_keys",
        "auth_sessions",
    ):
        if not has_foreign_key(
            table, ("tenant_id", "user_id"), "tenant_memberships"
        ):
            raise RuntimeError(f"{VERSION} verification failed: membership FK on {table}")
    if not has_foreign_key(
        "tenant_memberships", ("tenant_id", "role_id"), "tenant_roles"
    ):
        raise RuntimeError(f"{VERSION} verification failed: role FK on tenant_memberships")
    if not has_foreign_key(
        "role_permissions", ("tenant_id", "role_id"), "tenant_roles"
    ):
        raise RuntimeError(f"{VERSION} verification failed: role FK on role_permissions")

    check_constraints = {
        table: " ".join(str(item.get("sqltext") or "").split()).upper()
        for table in V3_001_OWNED_TABLES
        for item in inspector.get_check_constraints(table)
    }
    expected_checks = {
        "tenant_memberships": "STATUS IN ('ACTIVE', 'INVITED', 'SUSPENDED')",
        "api_keys": "STATUS IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
        "auth_sessions": "STATUS IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
    }
    for table, expected in expected_checks.items():
        if expected not in check_constraints.get(table, ""):
            raise RuntimeError(f"{VERSION} verification failed: check constraint on {table}")

    verified_indexes: list[str] = []
    for index_name, (table, columns, descending) in _REQUIRED_INDEXES.items():
        actual = _index_columns_and_order(connection, table, index_name, inspector)
        if actual is None or actual != (columns, descending):
            raise RuntimeError(
                f"{VERSION} verification failed: index={index_name} definition={actual} "
                f"expected={(columns, descending)}"
            )
        verified_indexes.append(index_name)

    # created_by is retained as a legacy-compatible nullable column.  Its
    # single-column FK cannot express tenant ownership, so verification keeps
    # the cross-tenant invariant fail-closed until a future additive migration.
    cross_tenant_creator = connection.execute(
        text(
            """
            SELECT 1
            FROM tenant_roles AS r
            JOIN users AS u ON u.id = r.created_by
            WHERE r.created_by IS NOT NULL AND u.tenant_id <> r.tenant_id
            LIMIT 1
            """
        )
    ).first()
    if cross_tenant_creator:
        raise RuntimeError(f"{VERSION} verification failed: cross-tenant role creator")

    legacy_roles = (
        connection.execute(
            text(
                """
            SELECT r.tenant_id, r.id, r.display_name, r.is_system_protected,
                   rp.capability
            FROM tenant_roles AS r
            LEFT JOIN role_permissions AS rp
              ON rp.tenant_id = r.tenant_id AND rp.role_id = r.id
            WHERE r.slug = 'legacy_customer'
            ORDER BY r.tenant_id, rp.capability
            """
            )
        )
        .mappings()
        .all()
    )
    permissions_by_role: dict[str, set[str]] = {}
    for row in legacy_roles:
        if row["display_name"] != _ROLE_LABELS["legacy_customer"] or not bool(
            row["is_system_protected"]
        ):
            raise RuntimeError(f"{VERSION} verification failed: legacy_customer protection")
        permissions_by_role.setdefault(str(row["id"]), set())
        if row["capability"] is not None:
            permissions_by_role[str(row["id"])].add(str(row["capability"]))
    expected_customer = set(_V3_001_SEED_CAPABILITIES["legacy_customer"])
    if any(capabilities != expected_customer for capabilities in permissions_by_role.values()):
        raise RuntimeError(f"{VERSION} verification failed: legacy_customer capabilities")

    return tuple(sorted(verified_indexes))


def _ledger_checksum(connection: Connection) -> str | None:
    return connection.execute(
        text("SELECT checksum FROM schema_migrations WHERE version = :version"),
        {"version": VERSION},
    ).scalar_one_or_none()


def _assert_no_orphan_objects(connection: Connection) -> None:
    table_names = set(inspect(connection).get_table_names())
    orphan_tables = sorted(set(_BUSINESS_OWNED_TABLES).intersection(table_names))
    orphan_indexes = sorted(
        _catalog_index_names(connection).intersection(_REQUIRED_INDEXES)
    )
    if orphan_tables or orphan_indexes:
        objects = [f"table:{name}" for name in orphan_tables]
        objects.extend(f"index:{name}" for name in orphan_indexes)
        raise RuntimeError(
            f"{VERSION} orphan objects without ledger: {','.join(objects)}"
        )


def _assert_applied_catalog(connection: Connection) -> None:
    table_names = set(inspect(connection).get_table_names())
    missing_tables = sorted(set(_BUSINESS_OWNED_TABLES) - table_names)
    missing_indexes = sorted(
        set(_REQUIRED_INDEXES) - _catalog_index_names(connection)
    )
    if missing_tables or missing_indexes:
        objects = [f"table:{name}" for name in missing_tables]
        objects.extend(f"index:{name}" for name in missing_indexes)
        raise RuntimeError(
            f"{VERSION} missing v3_001 object(s): {','.join(objects)}"
        )


def apply_v3_001(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        # Validate legacy inputs before any SQLite DDL.  SQLite may auto-commit
        # some schema statements, so bad mappings must fail in the preflight
        # boundary rather than leave partial v3 objects behind.
        _validate_legacy_users(connection)
        table_names = set(inspect(connection).get_table_names())
        applied_checksum = (
            _ledger_checksum(connection) if "schema_migrations" in table_names else None
        )
        if applied_checksum is not None and applied_checksum != V3_001_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={applied_checksum} "
                f"expected={V3_001_CHECKSUM}"
            )
        if applied_checksum is None:
            _assert_no_orphan_objects(connection)
        else:
            _assert_applied_catalog(connection)
        ddl = _ddl_for_connection(connection)
        connection.execute(text(ddl[0]))
        for statement in ddl[1:]:
            connection.execute(text(statement))
        backfill_counts = _backfill(connection)
        _verify_catalog(connection)
        if applied_checksum is None:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at, checksum) "
                    "VALUES (:version, :applied_at, :checksum)"
                ),
                {"version": VERSION, "applied_at": utc_now(), "checksum": V3_001_CHECKSUM},
            )
            applied = True
        else:
            applied = False
    return MigrationResult(VERSION, V3_001_CHECKSUM, applied, backfill_counts)


def verify_v3_001(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        _set_sqlite_foreign_keys(connection)
        table_names = set(inspect(connection).get_table_names())
        missing = [table for table in V3_001_OWNED_TABLES if table not in table_names]
        if missing:
            raise RuntimeError(f"{VERSION} verification failed: missing tables={','.join(missing)}")
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum != V3_001_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={applied_checksum} "
                f"expected={V3_001_CHECKSUM}"
            )
        if connection.dialect.name == "sqlite":
            foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
            if int(foreign_keys) != 1:
                raise RuntimeError(
                    f"{VERSION} verification failed: sqlite foreign_keys={foreign_keys}"
                )
        _validate_legacy_users(connection)
        index_names = _verify_catalog(connection)
        counts = {
            "tenants": int(connection.execute(text("SELECT COUNT(*) FROM tenants")).scalar_one())
            if "tenants" in table_names
            else 0,
            "memberships": int(
                connection.execute(text("SELECT COUNT(*) FROM tenant_memberships")).scalar_one()
            ),
            "profiles": int(
                connection.execute(text("SELECT COUNT(*) FROM user_profiles")).scalar_one()
            ),
            "preferences": int(
                connection.execute(text("SELECT COUNT(*) FROM user_preferences")).scalar_one()
            ),
        }
    return VerificationResult(
        status="PASS",
        version=VERSION,
        checksum=V3_001_CHECKSUM,
        tables=V3_001_OWNED_TABLES,
        indexes=index_names,
        backfill_counts=counts,
    )


def rollback_v3_001_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        if "schema_migrations" not in table_names:
            return RollbackPlan(False, (), ("schema_migrations",))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is None:
            return RollbackPlan(False, (), ("schema_migrations",))
        if applied_checksum != V3_001_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={applied_checksum} "
                f"expected={V3_001_CHECKSUM}"
            )
        _assert_applied_catalog(connection)
        assigned_legacy_customer = connection.execute(
            text(
                """
                SELECT 1
                FROM tenant_memberships AS membership
                JOIN tenant_roles AS role
                  ON role.tenant_id = membership.tenant_id
                 AND role.id = membership.role_id
                WHERE role.slug = 'legacy_customer'
                LIMIT 1
                """
            )
        ).first()
        if assigned_legacy_customer:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations",),
                blocked_reason="legacy_customer_assigned",
            )
        objects = tuple(
            item for item in reversed(V3_001_OWNED_TABLES) if item != "schema_migrations"
        )
        return RollbackPlan(True, objects, ("schema_migrations",))


def encode_cursor(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("invalid cursor") from None
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("invalid cursor")
    return value

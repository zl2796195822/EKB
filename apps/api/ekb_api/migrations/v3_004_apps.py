"""v3_004_apps: installed apps registry for the workspace app center.

The app center needs a persistent record of which integrations / plugins /
connectors have been installed (or attempted) by the tenant.  This migration
creates ``installed_apps`` as an append-only log with status tracking so
uninstall is also recorded rather than silently dropped.

The initial seed populates a small catalog of well-known EKB extensions that
represent realistic integration points (document sync, messaging, analytics).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_004_apps"

V3_004_OWNED_TABLES = ("installed_apps",)

V3_004_ROLLBACK_POLICY = (
    "retain schema_migrations; installed_apps is an operational log and can be "
    "dropped without affecting source tables; block rollback while active "
    "installations exist because reinstall state would be lost"
)

#: Well-known app slugs that represent real integration categories.
_APP_CATALOG: list[dict[str, str]] = [
    {
        "slug": "feishu-sync",
        "name": "飞书集成",
        "category": "DOCUMENT_SYNC",
        "description": "同步飞书文档与知识空间。",
        "icon_slug": "chat-circle-dots",
    },
    {
        "slug": "wecom-bot",
        "name": "企业微信",
        "category": "MESSAGING",
        "description": "在企业微信内触达知识问答。",
        "icon_slug": "chat-circle-dots",
    },
    {
        "slug": "github-repo",
        "name": "GitHub",
        "category": "DOCUMENT_SYNC",
        "description": "接入代码仓库文档与 issue。",
        "icon_slug": "file-text",
    },
    {
        "slug": "tencent-docs-import",
        "name": "腾讯文档",
        "category": "DOCUMENT_SYNC",
        "description": "导入腾讯文档为知识来源。",
        "icon_slug": "file-text",
    },
    {
        "slug": "analytics-pro",
        "name": "数据看板 Pro",
        "category": "ANALYTICS",
        "description": "扩展运营看板的分析维度。",
        "icon_slug": "chart-line",
    },
    {
        "slug": "permission-audit",
        "name": "权限审计",
        "category": "GOVERNANCE",
        "description": "增强权限与访问审计视图。",
        "icon_slug": "shield-check",
    },
]

_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS installed_apps (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      app_slug VARCHAR(64) NOT NULL,
      name VARCHAR(256) NOT NULL DEFAULT '',
      category VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
      description TEXT NOT NULL DEFAULT '',
      icon_slug VARCHAR(64) NOT NULL DEFAULT 'plug',
      status VARCHAR(32) NOT NULL DEFAULT 'AVAILABLE',
      config_json TEXT,
      installed_at VARCHAR(32),
      uninstalled_at VARCHAR(32),
      error_message TEXT,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,

      UNIQUE(tenant_id, app_slug)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_installed_apps_tenant ON installed_apps(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_installed_apps_status ON installed_apps(status)",
]


def _checksum() -> str:
    raw = f"{VERSION}|{_DDL_STATEMENTS}|{_APP_CATALOG}"
    return hashlib.sha256(raw.encode()).hexdigest()


V3_004_CHECKSUM = _checksum()


def apply_v3_004(engine: Engine) -> dict:
    with engine.begin() as conn:
        if _already_applied(conn):
            return {"status": "skip", "reason": "migration already applied"}

        for stmt in _DDL_STATEMENTS:
            conn.execute(text(stmt))

        now = utc_now()
        # Seed catalog for ALL tenants so every workspace sees the same app list.
        tenants = conn.execute(text("SELECT id FROM tenants")).fetchall()
        for tenant_row in tenants:
            tenant_id = str(tenant_row[0])
            for entry in _APP_CATALOG:
                conn.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO installed_apps (
                          id, tenant_id, app_slug, name, category, description,
                          icon_slug, status, created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :slug, :name, :cat, :desc,
                          :icon, 'AVAILABLE', :now, :now
                        )
                        """
                    ),
                    {
                        "id": _app_id(f"{tenant_id}/{entry['slug']}"),
                        "tenant_id": tenant_id,
                        "slug": entry["slug"],
                        "name": entry["name"],
                        "cat": entry["category"],
                        "desc": entry["description"],
                        "icon": entry["icon_slug"],
                        "now": now,
                    },
                )

        _record_migration(conn)
        count = conn.execute(
            text("SELECT COUNT(*) FROM installed_apps")
        ).scalar()
        return {
            "status": "applied",
            "catalog_seeded": int(count),
            "checksum": V3_004_CHECKSUM,
        }


def verify_v3_004(engine: Engine) -> dict:
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        if "installed_apps" not in tables:
            return {"status": "FAIL", "reason": "installed_apps table missing"}

        cols = {col["name"] for col in inspector.get_columns("installed_apps")}
        required = {
            "id", "tenant_id", "app_slug", "name", "category",
            "description", "icon_slug", "status", "config_json",
            "installed_at", "uninstalled_at", "error_message",
            "created_at", "updated_at",
        }
        missing = required - cols
        if missing:
            return {"status": "FAIL", "reason": f"missing columns: {missing}"}

        count = conn.execute(
            text("SELECT COUNT(*) FROM installed_apps")
        ).scalar()
        return {
            "status": "PASS",
            "row_count": int(count),
            "expected_checksum": V3_004_CHECKSUM,
        }


def rollback_v3_004_dry_run(engine: Engine) -> dict:
    with engine.connect() as conn:
        active = conn.execute(
            text("SELECT COUNT(*) FROM installed_apps WHERE status = 'INSTALLED'")
        ).scalar()
        total = conn.execute(
            text("SELECT COUNT(*) FROM installed_apps")
        ).scalar()
        return {
            "status": "dry-run",
            "policy": V3_004_ROLLBACK_POLICY,
            "active_installs": int(active),
            "total_rows": int(total),
            "action": "DROP TABLE installed_apps" if not active else "BLOCKED",
        }


# ---- internal helpers ----


def _app_id(slug: str) -> str:
    from uuid import NAMESPACE_URL, uuid5
    return str(uuid5(NAMESPACE_URL, f"ekb.app/{slug}"))


def _default_tenant(conn: Connection) -> str:
    row = conn.execute(
        text("SELECT id FROM tenants LIMIT 1")
    ).first()
    return str(row[0]) if row else "00000000-0000-0000-0000-000000000000"


def _already_applied(conn: Connection) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM schema_migrations WHERE version = :version"
        ),
        {"version": VERSION},
    ).first()
    return row is not None


def _record_migration(conn: Connection) -> None:
    conn.execute(
        text(
            "INSERT INTO schema_migrations (version, checksum, applied_at) "
            "VALUES (:version, :checksum, :now)"
        ),
        {"version": VERSION, "checksum": V3_004_CHECKSUM, "now": utc_now()},
    )

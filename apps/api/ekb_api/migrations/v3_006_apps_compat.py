"""v3_006_apps_compat: authoritative app_catalog + install/credential/run projection.

Resolves ADR-003 against §5.2/5.4 of 02_spec.md:

* ``v3_004_apps`` created only a merged ``installed_apps`` log (per-tenant
  six-row seed, no catalog/installations split, no FK/CHECK, no Fernet
  credential envelope, no run records, and SQLite-exclusive ``INSERT OR
  IGNORE``).  That version is already applied, so its checksum is locked.
* This compensation adds the four authoritative tables with the exact
  enumerations, composite FKs, and Fernet envelope named by the spec:
  ``app_catalog`` (six immutable slugs via CHECK), ``app_installations``
  (per-tenant lifecycle INSTALLED/CONFIGURED/CONNECTED/UNINSTALLED),
  ``app_credentials`` (write-once ciphertext + prefix + key_version +
  previous envelope rotation), and ``app_runs`` (SUCCEEDED/FAILED results
  with an optional ``sync_source_id``).

The pre-existing ``installed_apps`` table owned by ``v3_004_apps`` is never
touched, renamed, or dropped.  Services treat the new four tables as
authoritative and only consult ``installed_apps`` as a legacy read-only
projection for back-comparison during the first upgrade run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_006_apps_compat"

V3_006_OWNED_TABLES = (
    "app_catalog",
    "app_installations",
    "app_credentials",
    "app_runs",
)

V3_006_ROLLBACK_POLICY = (
    "drop order = app_runs → app_credentials → app_installations → app_catalog; "
    "block while any ACTIVE credential row exists; block while any CONNECTED "
    "installation exists; legacy installed_apps (v3_004_apps) is never dropped; "
    "audit_logs/schema_migrations retained unconditionally."
)

# Authoritative six app slugs with the exact display/provider/category/capabilities
# required by 02_spec.md §5.2 (app_catalog CHECK enforces this list as a whitelist).
_CATALOG_SEED = (
    {
        "slug": "feishu",
        "display_name": "飞书集成",
        "provider_name": "Feishu",
        "description": "双向同步飞书文档/多维表格到知识空间，支持飞书消息内问答机器人。",
        "category": "DOCUMENT_SYNC",
        "capabilities": ["DOCUMENT_IMPORT", "MESSAGE_BOT", "USER_LOOKUP"],
        "recommended_rank": 1,
        "enabled": True,
    },
    {
        "slug": "wecom",
        "display_name": "企业微信",
        "provider_name": "WeCom",
        "description": "在企业微信内触达 AI 问答，支持单聊与群聊，按部门授权。",
        "category": "MESSAGING",
        "capabilities": ["MESSAGE_BOT", "DEPARTMENT_SYNC"],
        "recommended_rank": 2,
        "enabled": True,
    },
    {
        "slug": "github",
        "display_name": "GitHub",
        "provider_name": "GitHub",
        "description": "接入代码仓库的 README、issue、PR、release notes 为知识来源。",
        "category": "DOCUMENT_SYNC",
        "capabilities": ["DOCUMENT_IMPORT", "ISSUE_IMPORT", "WEBHOOK"],
        "recommended_rank": 3,
        "enabled": True,
    },
    {
        "slug": "tencent-docs",
        "display_name": "腾讯文档",
        "provider_name": "Tencent Docs",
        "description": "导入腾讯文档表格、文档、表单为可检索知识条目。",
        "category": "DOCUMENT_SYNC",
        "capabilities": ["DOCUMENT_IMPORT"],
        "recommended_rank": 4,
        "enabled": True,
    },
    {
        "slug": "analytics-pro",
        "display_name": "数据看板 Pro",
        "provider_name": "EKB Analytics",
        "description": "扩展运营看板为高阶漏斗、归因与同环比对比，支持 CSV 导出。",
        "category": "ANALYTICS",
        "capabilities": ["DASHBOARD_EXPORT", "ADVANCED_METRICS"],
        "recommended_rank": 5,
        "enabled": True,
    },
    {
        "slug": "audit",
        "display_name": "权限审计",
        "provider_name": "EKB Governance",
        "description": "增强权限全景视图、成员权限偏差审计与异常访问基线告警。",
        "category": "GOVERNANCE",
        "capabilities": ["PERMISSION_AUDIT", "ANOMALY_ALERT"],
        "recommended_rank": 6,
        "enabled": True,
    },
)

# SQLite has no native BOOLEAN; spec uses TRUE/FALSE, so we translate by dialect.
_DDL = (
    # ---------- app_catalog (global immutable catalog, CHECK enforces 6 slugs)
    """
    CREATE TABLE IF NOT EXISTS app_catalog (
      slug VARCHAR(64) PRIMARY KEY,
      display_name VARCHAR(128) NOT NULL,
      provider_name VARCHAR(128) NOT NULL,
      description TEXT NOT NULL,
      category VARCHAR(64) NOT NULL,
      capabilities JSON NOT NULL,
      recommended_rank INTEGER NOT NULL DEFAULT 0,
      enabled BOOLEAN NOT NULL DEFAULT TRUE,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      CHECK (slug IN ('feishu','wecom','github','tencent-docs','analytics-pro','audit')),
      CHECK (category IN ('DOCUMENT_SYNC','MESSAGING','ANALYTICS','GOVERNANCE'))
    )
    """,
    # ---------- app_installations (per tenant installation + lifecycle)
    """
    CREATE TABLE IF NOT EXISTS app_installations (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      app_slug VARCHAR(64) NOT NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'INSTALLED',
      config JSON NOT NULL,
      installed_by VARCHAR(36) NOT NULL,
      installed_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      uninstalled_at VARCHAR(32),
      UNIQUE (tenant_id, app_slug),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id) REFERENCES tenants (id),
      FOREIGN KEY (app_slug) REFERENCES app_catalog (slug),
      FOREIGN KEY (installed_by) REFERENCES users (id),
      FOREIGN KEY (tenant_id, installed_by)
        REFERENCES tenant_memberships (tenant_id, user_id),
      CHECK (status IN ('INSTALLED','CONFIGURED','CONNECTED','UNINSTALLED'))
    )
    """,
    # ---------- app_credentials (dedicated Fernet envelope + prefix redaction)
    """
    CREATE TABLE IF NOT EXISTS app_credentials (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      installation_id VARCHAR(36) NOT NULL,
      credential_name VARCHAR(128) NOT NULL,
      prefix VARCHAR(32) NOT NULL,
      ciphertext TEXT NOT NULL,
      key_version VARCHAR(32) NOT NULL,
      previous_ciphertext TEXT,
      previous_key_version VARCHAR(32),
      previous_expires_at VARCHAR(32),
      status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      revoked_at VARCHAR(32),
      UNIQUE (tenant_id, installation_id, credential_name),
      FOREIGN KEY (tenant_id, installation_id)
        REFERENCES app_installations (tenant_id, id),
      CHECK (status IN ('ACTIVE','REVOKED')),
      CHECK (length(prefix) <= 32),
      CHECK (length(credential_name) BETWEEN 1 AND 128)
    )
    """,
    # ---------- app_runs (sync / downstream provider call results)
    """
    CREATE TABLE IF NOT EXISTS app_runs (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      installation_id VARCHAR(36) NOT NULL,
      run_type VARCHAR(64) NOT NULL,
      status VARCHAR(32) NOT NULL,
      sync_source_id VARCHAR(36),
      result_redacted JSON NOT NULL,
      started_at VARCHAR(32) NOT NULL,
      completed_at VARCHAR(32),
      error_code VARCHAR(64),
      error_message TEXT,
      FOREIGN KEY (tenant_id, installation_id)
        REFERENCES app_installations (tenant_id, id),
      FOREIGN KEY (sync_source_id) REFERENCES sync_sources (id),
      CHECK (status IN ('SUCCEEDED','FAILED'))
    )
    """,
    # ---------- indexes from 02_spec.md §5.4
    "CREATE INDEX IF NOT EXISTS ix_app_installations_tenant_status "
    "ON app_installations (tenant_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_app_credentials_installation_status "
    "ON app_credentials (tenant_id, installation_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_app_runs_installation_time "
    "ON app_runs (tenant_id, installation_id, started_at DESC)",
)

_REQUIRED_COLUMNS = {
    "app_catalog": {
        "slug", "display_name", "provider_name", "description", "category",
        "capabilities", "recommended_rank", "enabled", "created_at", "updated_at",
    },
    "app_installations": {
        "id", "tenant_id", "app_slug", "status", "config", "installed_by",
        "installed_at", "updated_at", "uninstalled_at",
    },
    "app_credentials": {
        "id", "tenant_id", "installation_id", "credential_name", "prefix",
        "ciphertext", "key_version", "previous_ciphertext", "previous_key_version",
        "previous_expires_at", "status", "created_at", "updated_at", "revoked_at",
    },
    "app_runs": {
        "id", "tenant_id", "installation_id", "run_type", "status",
        "sync_source_id", "result_redacted", "started_at", "completed_at",
        "error_code", "error_message",
    },
}

_REQUIRED_INDEXES = (
    "ix_app_installations_tenant_status",
    "ix_app_credentials_installation_status",
    "ix_app_runs_installation_time",
)

# --- checksum ledger (deterministic, dialects normalised before hash) ---
def _normalise(sql: str) -> str:
    return " ".join(token.strip() for token in sql.split() if token.strip())


V3_006_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(_normalise(statement) for statement in _DDL)
        + "\n"
        + json.dumps(list(_CATALOG_SEED), sort_keys=True, separators=(",", ":"))
        + "\n"
        + "rollback="
        + V3_006_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB\nboolean=sqlite:INTEGER,postgresql:BOOLEAN"
    ).encode()
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple
    indexes: tuple
    backfill_counts: dict


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple
    retained: tuple
    blocked_reason: Optional[str] = None


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _dialect_ddl(connection: Connection) -> tuple:
    statements = []
    for statement in _DDL:
        transformed = statement
        if connection.dialect.name == "postgresql":
            transformed = transformed.replace(" JSON ", " JSONB ")
        elif connection.dialect.name == "sqlite":
            # SQLite has no real BOOLEAN type; map BOOL columns to INTEGER with
            # 0/1 defaults so existing Python clients can still round-trip True/False.
            transformed = transformed.replace(" BOOLEAN NOT NULL DEFAULT TRUE", " INTEGER NOT NULL DEFAULT 1")
            transformed = transformed.replace(" BOOLEAN NOT NULL DEFAULT FALSE", " INTEGER NOT NULL DEFAULT 0")
        else:
            raise RuntimeError(
                "{0} unsupported dialect: {1}".format(VERSION, connection.dialect.name)
            )
        statements.append(transformed)
    return tuple(statements)


def _ledger_checksum(connection: Connection) -> Optional[str]:
    row = connection.execute(
        text("SELECT checksum FROM schema_migrations WHERE version = :version"),
        {"version": VERSION},
    ).first()
    return None if row is None else str(row[0])


def _catalog_index_names(connection: Connection) -> set:
    inspector = inspect(connection)
    names = set()
    for table in V3_006_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                names.add(str(name))
    return names


def _verify_catalog(connection: Connection) -> tuple:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing_tables = [t for t in V3_006_OWNED_TABLES if t not in table_names]
    if missing_tables:
        raise RuntimeError(
            "{0} verification failed: missing tables={1}".format(VERSION, ",".join(missing_tables))
        )
    for table, expected in _REQUIRED_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(expected - actual)
        if missing:
            raise RuntimeError(
                "{0} verification failed: {0} missing columns={2}".format(
                    VERSION, table, ",".join(missing)
                )
            )
    index_names = _catalog_index_names(connection)
    missing_indexes = [name for name in _REQUIRED_INDEXES if name not in index_names]
    if missing_indexes:
        raise RuntimeError(
            "{0} verification failed: missing indexes={1}".format(VERSION, ",".join(missing_indexes))
        )
    # Exactly six immutable catalog rows with the whitelisted slugs.
    rows = connection.execute(text("SELECT slug FROM app_catalog ORDER BY slug")).all()
    slugs = sorted(str(r[0]) for r in rows)
    if slugs != sorted(entry["slug"] for entry in _CATALOG_SEED):
        raise RuntimeError(
            "{0} verification failed: app_catalog slugs mismatch got={1}".format(
                VERSION, ",".join(slugs)
            )
        )
    return tuple(sorted(index_names))


def apply_v3_006(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        if "schema_migrations" not in table_names:
            raise RuntimeError("{0} requires v3_001_identity to be applied first".format(VERSION))
        if "tenants" not in table_names or "sync_sources" not in table_names:
            raise RuntimeError(
                "{0} requires v3_001_identity and the legacy sync_sources table".format(VERSION)
            )
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is not None and applied_checksum != V3_006_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_006_CHECKSUM
                )
            )
        for statement in _dialect_ddl(connection):
            connection.execute(text(statement))

        now = utc_now()
        # Seed the immutable catalog.  PostgreSQL uses standard upsert; SQLite
        # keeps its INSERT OR IGNORE because its dialect lacks ON CONFLICT.
        for entry in _CATALOG_SEED:
            caps_json = json.dumps(entry["capabilities"], ensure_ascii=False, sort_keys=True)
            enabled = entry["enabled"]
            if connection.dialect.name == "sqlite":
                enabled_int = 1 if enabled else 0
                connection.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO app_catalog (
                          slug, display_name, provider_name, description,
                          category, capabilities, recommended_rank, enabled,
                          created_at, updated_at
                        ) VALUES (
                          :slug, :display_name, :provider_name, :description,
                          :category, :capabilities, :rank, :enabled,
                          :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "slug": entry["slug"],
                        "display_name": entry["display_name"],
                        "provider_name": entry["provider_name"],
                        "description": entry["description"],
                        "category": entry["category"],
                        "capabilities": caps_json,
                        "rank": int(entry["recommended_rank"]),
                        "enabled": enabled_int,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            else:
                connection.execute(
                    text(
                        """
                        INSERT INTO app_catalog (
                          slug, display_name, provider_name, description,
                          category, capabilities, recommended_rank, enabled,
                          created_at, updated_at
                        ) VALUES (
                          :slug, :display_name, :provider_name, :description,
                          :category, :capabilities::JSONB, :rank, :enabled,
                          :created_at, :updated_at
                        ) ON CONFLICT (slug) DO NOTHING
                        """
                    ),
                    {
                        "slug": entry["slug"],
                        "display_name": entry["display_name"],
                        "provider_name": entry["provider_name"],
                        "description": entry["description"],
                        "category": entry["category"],
                        "capabilities": caps_json,
                        "rank": int(entry["recommended_rank"]),
                        "enabled": bool(enabled),
                        "created_at": now,
                        "updated_at": now,
                    },
                )

        _verify_catalog(connection)
        if applied_checksum is None:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at, checksum) "
                    "VALUES (:version, :applied_at, :checksum)"
                ),
                {"version": VERSION, "applied_at": now, "checksum": V3_006_CHECKSUM},
            )
            applied = True
        else:
            applied = False
        backfill = {
            "app_catalog": int(
                connection.execute(text("SELECT COUNT(*) FROM app_catalog")).scalar()
            ),
            "app_installations": 0,
            "app_credentials": 0,
            "app_runs": 0,
            "note": "installations/credentials/runs are user-created, no legacy backfill",
        }
        return MigrationResult(VERSION, V3_006_CHECKSUM, applied, backfill)


def verify_v3_006(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        _set_sqlite_foreign_keys(connection)
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum != V3_006_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_006_CHECKSUM
                )
            )
        index_names = _verify_catalog(connection)
        counts = {
            table: int(
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            )
            for table in V3_006_OWNED_TABLES
        }
    return VerificationResult(
        status="PASS",
        version=VERSION,
        checksum=V3_006_CHECKSUM,
        tables=V3_006_OWNED_TABLES,
        indexes=index_names,
        backfill_counts=counts,
    )


def rollback_v3_006_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        inspector = inspect(connection)
        if "schema_migrations" not in set(inspector.get_table_names()):
            return RollbackPlan(False, (), ("schema_migrations", "audit_logs", "installed_apps"))
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is None:
            return RollbackPlan(False, (), ("schema_migrations", "audit_logs", "installed_apps"))
        if applied_checksum != V3_006_CHECKSUM:
            raise RuntimeError(
                "{0} checksum mismatch: applied={1} expected={2}".format(
                    VERSION, applied_checksum, V3_006_CHECKSUM
                )
            )
        active_credentials = connection.execute(
            text("SELECT 1 FROM app_credentials WHERE status = 'ACTIVE' LIMIT 1")
        ).first()
        if active_credentials:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations", "audit_logs", "installed_apps"),
                blocked_reason="active_app_credentials_present",
            )
        connected_installs = connection.execute(
            text("SELECT 1 FROM app_installations WHERE status = 'CONNECTED' LIMIT 1")
        ).first()
        if connected_installs:
            return RollbackPlan(
                True,
                (),
                ("schema_migrations", "audit_logs", "installed_apps"),
                blocked_reason="connected_installations_present",
            )
        objects = (
            "DROP INDEX ix_app_runs_installation_time",
            "DROP INDEX ix_app_credentials_installation_status",
            "DROP INDEX ix_app_installations_tenant_status",
            "DROP TABLE app_runs",
            "DROP TABLE app_credentials",
            "DROP TABLE app_installations",
            "DROP TABLE app_catalog",
        )
        return RollbackPlan(
            True,
            objects,
            ("schema_migrations", "audit_logs", "installed_apps"),
        )

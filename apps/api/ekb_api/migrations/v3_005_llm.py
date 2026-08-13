"""v3_005_llm: LLM 服务商与模型配置表。

为工作区建立统一的 LLM 服务商接入层（Provider）与模型目录（Model），
支持国内外主流大模型平台，以及兼容 OpenAI 协议的自建服务。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v3_005_llm"

V3_005_OWNED_TABLES = ("llm_providers", "llm_models")

V3_005_ROLLBACK_POLICY = (
    "retain schema_migrations; llm_providers and llm_models are user-level "
    "configuration and can be dropped without affecting source tables"
)

_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS llm_providers (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      preset_provider_id VARCHAR(64),
      provider_key VARCHAR(64) NOT NULL,
      name VARCHAR(128) NOT NULL,
      logo VARCHAR(255),
      description TEXT,
      websites JSON NOT NULL DEFAULT '{}',
      default_chat_endpoint VARCHAR(64),
      endpoint_configs JSON NOT NULL DEFAULT '{}',
      auth_type VARCHAR(32) NOT NULL DEFAULT 'api-key',
      api_key TEXT,
      api_key_label VARCHAR(128),
      api_features JSON NOT NULL DEFAULT '{}',
      settings JSON NOT NULL DEFAULT '{}',
      model_list_source VARCHAR(16) NOT NULL DEFAULT 'api',
      is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,

      UNIQUE(tenant_id, user_id, provider_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llm_providers_tenant ON llm_providers(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_providers_user ON llm_providers(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_providers_provider_key ON llm_providers(provider_key)",
    """
    CREATE TABLE IF NOT EXISTS llm_models (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      user_id VARCHAR(36) NOT NULL,
      provider_id VARCHAR(36) NOT NULL,
      model_id VARCHAR(128) NOT NULL,
      display_name VARCHAR(256) NOT NULL,
      model_type VARCHAR(32) NOT NULL DEFAULT 'chat',
      context_window INTEGER,
      max_output_tokens INTEGER,
      endpoint_type VARCHAR(64),
      capabilities JSON NOT NULL DEFAULT '{}',
      input_price VARCHAR(32),
      output_price VARCHAR(32),
      is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
      is_custom BOOLEAN NOT NULL DEFAULT FALSE,
      notes TEXT,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,

      UNIQUE(provider_id, model_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llm_models_tenant ON llm_models(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_models_user ON llm_models(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_models_provider ON llm_models(provider_id)",
]


def _checksum() -> str:
    raw = f"{VERSION}|{_DDL_STATEMENTS}"
    return hashlib.sha256(raw.encode()).hexdigest()


V3_005_CHECKSUM = _checksum()


def apply_v3_005(engine: Engine) -> dict:
    with engine.begin() as conn:
        if _already_applied(conn):
            return {"status": "skip", "reason": "migration already applied"}

        for stmt in _DDL_STATEMENTS:
            conn.execute(text(stmt))

        _record_migration(conn)
        provider_count = conn.execute(
            text("SELECT COUNT(*) FROM llm_providers")
        ).scalar()
        model_count = conn.execute(
            text("SELECT COUNT(*) FROM llm_models")
        ).scalar()
        return {
            "status": "applied",
            "provider_count": int(provider_count),
            "model_count": int(model_count),
            "checksum": V3_005_CHECKSUM,
        }


def verify_v3_005(engine: Engine) -> dict:
    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = inspector.get_table_names()
        if "llm_providers" not in tables:
            return {"status": "FAIL", "reason": "llm_providers table missing"}
        if "llm_models" not in tables:
            return {"status": "FAIL", "reason": "llm_models table missing"}

        provider_cols = {col["name"] for col in inspector.get_columns("llm_providers")}
        provider_required = {
            "id", "tenant_id", "user_id", "preset_provider_id", "provider_key",
            "name", "logo", "description", "websites", "default_chat_endpoint",
            "endpoint_configs", "auth_type", "api_key", "api_key_label",
            "api_features", "settings", "model_list_source", "is_enabled",
            "created_at", "updated_at",
        }
        missing_provider = provider_required - provider_cols
        if missing_provider:
            return {"status": "FAIL", "reason": f"llm_providers missing columns: {missing_provider}"}

        model_cols = {col["name"] for col in inspector.get_columns("llm_models")}
        model_required = {
            "id", "tenant_id", "user_id", "provider_id", "model_id", "display_name",
            "model_type", "context_window", "max_output_tokens", "endpoint_type",
            "capabilities", "input_price", "output_price", "is_enabled", "is_custom",
            "notes", "created_at", "updated_at",
        }
        missing_model = model_required - model_cols
        if missing_model:
            return {"status": "FAIL", "reason": f"llm_models missing columns: {missing_model}"}

        provider_count = conn.execute(
            text("SELECT COUNT(*) FROM llm_providers")
        ).scalar()
        model_count = conn.execute(
            text("SELECT COUNT(*) FROM llm_models")
        ).scalar()
        return {
            "status": "PASS",
            "provider_count": int(provider_count),
            "model_count": int(model_count),
            "expected_checksum": V3_005_CHECKSUM,
        }


def rollback_v3_005_dry_run(engine: Engine) -> dict:
    with engine.connect() as conn:
        provider_count = conn.execute(
            text("SELECT COUNT(*) FROM llm_providers")
        ).scalar()
        model_count = conn.execute(
            text("SELECT COUNT(*) FROM llm_models")
        ).scalar()
        return {
            "status": "dry-run",
            "policy": V3_005_ROLLBACK_POLICY,
            "provider_count": int(provider_count),
            "model_count": int(model_count),
            "action": "DROP TABLE llm_providers, llm_models",
        }


# ---- internal helpers ----


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
        {"version": VERSION, "checksum": V3_005_CHECKSUM, "now": utc_now()},
    )

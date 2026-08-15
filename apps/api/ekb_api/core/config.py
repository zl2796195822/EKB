from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_remote_provider_url(value: str) -> bool:
    """只允许远程 LLM endpoint，拒绝本机回环地址和未解析地址。"""
    parsed = urlparse((value or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return bool(parsed.scheme in {"http", "https"} and host not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
    })


def _load_dotenv() -> None:
    """轻量 .env 加载：解析 KEY=VALUE 行，不覆盖已存在的环境变量。

    不依赖 python-dotenv；仅在项目根目录存在 .env 且非测试环境时生效。
    测试环境（EKB_ENV=test）跳过加载，避免 .env 中的外部 API 配置污染测试。
    部署场景（Docker）通常通过 docker --env-file 注入，这里允许文件不存在。
    """
    if os.getenv("EKB_ENV") == "test":
        return
    env_path: Path | None = None
    here = Path(__file__).resolve()
    # 向上最多找 8 层（仓库结构 / Docker / 单目录结构都能覆盖）
    # 注意：这里用列表拼接 + itertools.islice 兼容 Python 3.9（Path.parents 切片是 3.10+ 特性）
    import itertools
    candidate_dirs = [here]
    candidate_dirs.extend(list(itertools.islice(here.parents, 8)))
    for parent in candidate_dirs:
        candidate = parent / ".env"
        if candidate.exists():
            env_path = candidate
            break
    if env_path is None:
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# 模块加载时即读取 .env，保证 get_settings() 第一次调用前环境变量就绪。
_load_dotenv()


@dataclass(frozen=True)
class ModelProvider:
    """一个第三方模型提供商（任意 OpenAI 兼容端点）。

    kind="chat"：chat completions（DeepSeek / OpenAI GPT / MiniMax / Kimi 等）。
    kind="embedding"：embeddings（OpenAI / 硅基流动 / MiniMax 等）。
    同一系统可登记多个 provider；运行时按能力路由，失败自动 fallback 下一个。
    """

    name: str
    kind: str
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0
    supports_vision: bool = False
    # Copied from the enabled llm_models row.  Runtime code must not infer
    # capabilities from a model name or a provider preset.
    display_name: str | None = None
    capabilities: dict | None = None
    # Database primary key for actor-scoped configured models. Static
    # environment providers intentionally leave this unset.
    id: str | None = None


@dataclass(frozen=True)
class Settings:
    environment: str
    api_host: str
    api_port: int
    allowed_origins: list[str]
    dev_user_email: str
    dev_user_name: str
    dev_password: str
    # M2-6 开发期是否把 dev 账号视作平台管理员（可开通租户）。生产应置为 false。
    dev_is_platform_admin: bool
    token_secret: str
    database_url: str
    # 多模型提供商：任意 OpenAI 兼容端点（DeepSeek/OpenAI/MiniMax/Kimi 等）。
    model_providers: list[ModelProvider]
    retrieval_cosine_threshold: float
    # M1-05 检索强化参数。
    retrieval_rrf_k: int  # RRF 常数（排名衰减）。
    retrieval_rerank_pool: int  # rerank 候选池大小（融合后精排上限）。
    retrieval_bm25_enabled: bool  # 是否启用 BM25 词法重排信号。
    # 生成参数（全局，不随 provider 变动）。
    llm_temperature: float
    llm_max_tokens: int
    # M4-6 Multi-Turn Chat：总开关/上下文窗口/压缩参数。
    multi_turn_enabled: bool
    llm_context_window: int
    llm_compaction_ratio: float
    compaction_recent_rounds_keep: int
    compaction_summary_ratio_target: float
    # 远程 Embedding 批处理参数；无 Provider 时 fail-closed。
    embedding_dim: int
    embedding_batch_size: int
    # M4-7 容量压测/故障演练：超时与熔断参数可配置（原为硬编码常量）。
    qa_timeout_seconds: float
    search_timeout_seconds: float
    circuit_breaker_failure_threshold: int
    circuit_breaker_recovery_timeout: float
    # M4-4 告警阈值：当 P95 延迟/错误率超出阈值时记录 ERROR 日志（供告警系统抓取）。
    alert_qa_p95_threshold_seconds: float   # QA P95 延迟告警阈值（默认 3.0s）
    alert_search_p95_threshold_seconds: float  # 搜索 P95 延迟告警阈值（默认 0.5s）
    alert_error_rate_threshold: float       # HTTP 5xx 错误率告警阈值（默认 0.05 = 5%）
    alert_llm_failure_rate_threshold: float  # LLM 调用失败率告警阈值（默认 0.1 = 10%）
    # --- SSE v2 Conversation Stream 配置 ---
    # 检索阶段空闲超时：检索侧长时间无输出视为超时（比生成更敏感）
    sse_v2_retrieval_idle_timeout: float
    # 生成阶段空闲超时：LLM token 间隔超过此值视为上游卡壳
    sse_v2_generation_idle_timeout: float
    # 心跳间隔秒数：无事件时每若干秒发 heartbeat，防止代理断连
    sse_v2_heartbeat_interval: float
    # Feature flag：是否启用 Conversation Stream v2（可灰度关闭）
    sse_v2_enabled: bool
    # Delta 合并：单包 token 数阈值（同时满足 3 条件任意一个则 flush）
    sse_v2_delta_max_tokens: int
    # Delta 合并：单包字节数阈值（UTF-8）
    sse_v2_delta_max_bytes: int
    # Delta 合并：时间窗口阈值（毫秒），保证延迟不膨胀
    sse_v2_delta_flush_ms: int
    # --- Conversation Engine Unification (PH0-PH5) feature flags ---
    # 影子构建 context manifest，不改变 Provider 输入；用于 hash/token 预算对比
    ce_shadow_context: bool
    # 新 Turn Engine 开关（默认开，渐进迁移 /qa/ask 到 ConversationApplicationService）
    ce_turn_engine_enabled: bool
    # SSE cursor replay 开关（turn_events 持久化 + Last-Event-ID 重放）
    ce_event_replay_enabled: bool
    # 前端切换到 POST turn + GET events（灰度）
    ce_frontend_cutover: bool
    # turn_events 过期天数（必须大于最大生成时长）
    ce_event_retention_days: int
    # Generation lease 过期秒数（请求线程崩溃后孤儿 lease 恢复阈值）
    ce_generation_lease_seconds: float
    # 单文件上传大小上限（字节），0 表示不限制；租户可单独覆盖
    max_upload_bytes: int
    # v4 provider credential encryption key.  This is intentionally separate
    # from the legacy Apps master key and has no development default.
    provider_master_key: str
    # S3-compatible object storage.  An empty endpoint means the feature is
    # unavailable; callers must fail closed rather than use local disk/mocks.
    object_storage_endpoint: str
    # Optional private endpoint for API/worker object reads and deletes.  The
    # public endpoint remains the only address included in browser presigned URLs.
    object_storage_internal_endpoint: str
    object_storage_bucket: str
    object_storage_region: str
    object_storage_access_key: str
    object_storage_secret_key: str
    object_storage_path_style: bool

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    # ---- 多 Provider 路由 ----
    @property
    def chat_providers(self) -> list[ModelProvider]:
        """已启用的 chat 提供商（需同时具备 base_url 与 api_key）。"""
        return [
            p
            for p in self.model_providers
            if p.kind == "chat"
            and p.api_key
            and _is_remote_provider_url(p.base_url)
        ]

    @property
    def embedding_providers(self) -> list[ModelProvider]:
        """已启用的 embedding 提供商。"""
        return [
            p
            for p in self.model_providers
            if p.kind == "embedding"
            and p.api_key
            and _is_remote_provider_url(p.base_url)
        ]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.chat_providers)

    @property
    def embedding_enabled(self) -> bool:
        return bool(self.embedding_providers)

    # ---- 向后兼容便捷属性（取第一个可用 provider 的同名字段） ----
    @property
    def llm_api_url(self) -> str:
        return self.chat_providers[0].base_url if self.chat_providers else ""

    @property
    def llm_api_key(self) -> str:
        return self.chat_providers[0].api_key if self.chat_providers else ""

    @property
    def llm_model(self) -> str:
        return self.chat_providers[0].model if self.chat_providers else ""

    @property
    def llm_timeout_seconds(self) -> float:
        return self.chat_providers[0].timeout_seconds if self.chat_providers else 30.0

    @property
    def embedding_api_url(self) -> str:
        return self.embedding_providers[0].base_url if self.embedding_providers else ""

    @property
    def embedding_api_key(self) -> str:
        return self.embedding_providers[0].api_key if self.embedding_providers else ""

    @property
    def embedding_model(self) -> str:
        return self.embedding_providers[0].model if self.embedding_providers else ""

    @property
    def embedding_timeout_seconds(self) -> float:
        return self.embedding_providers[0].timeout_seconds if self.embedding_providers else 30.0


def _parse_providers() -> list[ModelProvider]:
    """从 EKB_MODEL_PROVIDERS（JSON 数组）解析多 provider；为空则回退 legacy 单组 env。

    每项：{name, kind(chat|embedding), base_url, api_key, model, timeout}。
    api_key 或 base_url 为空的 provider 视为未启用（运行时跳过），方便在 .env 中留空占位。
    """
    raw = os.getenv("EKB_MODEL_PROVIDERS")
    providers: list[ModelProvider] = []
    if raw:
        try:
            data = json.loads(raw)
            for item in data:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind", "chat")
                if kind not in ("chat", "embedding"):
                    kind = "chat"
                capabilities = item.get("capabilities")
                if not isinstance(capabilities, dict):
                    capabilities = {}
                providers.append(
                    ModelProvider(
                        name=str(item.get("name", f"{kind}-provider")),
                        kind=kind,
                        base_url=str(item.get("base_url", "")),
                        api_key=str(item.get("api_key", "")),
                        model=str(item.get("model", "")),
                        timeout_seconds=float(item.get("timeout", 30)),
                        supports_vision=bool(
                            item.get("supports_vision") or capabilities.get("vision", False)
                        ),
                    )
                )
        except (json.JSONDecodeError, TypeError, ValueError):
            providers = []

    if not providers:
        # Legacy 单组 env 兼容：EKB_LLM_* / EKB_EMBEDDING_*。
        if os.getenv("EKB_LLM_API_URL"):
            providers.append(
                ModelProvider(
                    name="legacy-llm",
                    kind="chat",
                    base_url=os.getenv("EKB_LLM_API_URL", ""),
                    api_key=os.getenv("EKB_LLM_API_KEY", ""),
                    model=os.getenv("EKB_LLM_MODEL", "deepseek-chat"),
                    timeout_seconds=float(os.getenv("EKB_LLM_TIMEOUT", "30")),
                )
            )
        if os.getenv("EKB_EMBEDDING_API_URL"):
            providers.append(
                ModelProvider(
                    name="legacy-embedding",
                    kind="embedding",
                    base_url=os.getenv("EKB_EMBEDDING_API_URL", ""),
                    api_key=os.getenv("EKB_EMBEDDING_API_KEY", ""),
                    model=os.getenv("EKB_EMBEDDING_MODEL", "bge-large-zh-v1.5"),
                    timeout_seconds=float(os.getenv("EKB_EMBEDDING_TIMEOUT", "30")),
                )
            )
    return providers


@lru_cache
def get_settings() -> Settings:
    token_secret = os.getenv("EKB_TOKEN_SECRET") or secrets.token_urlsafe(48)
    environment = os.getenv("EKB_ENV", "development")

    if environment == "production" and not os.getenv("EKB_TOKEN_SECRET"):
        raise RuntimeError("EKB_TOKEN_SECRET must be configured in production.")

    return Settings(
        environment=environment,
        api_host=os.getenv("EKB_API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("EKB_API_PORT", "8000")),
        allowed_origins=_split_csv(
            os.getenv(
                "EKB_ALLOWED_ORIGINS",
                "http://127.0.0.1:5173,http://localhost:5173",
            )
        ),
        dev_user_email=os.getenv("EKB_DEV_USER_EMAIL", "admin@example.com"),
        dev_user_name=os.getenv("EKB_DEV_USER_NAME", "EKB Admin"),
        dev_password=os.getenv("EKB_DEV_PASSWORD", "change-me-local-only"),
        dev_is_platform_admin=os.getenv("EKB_DEV_IS_PLATFORM_ADMIN", "true").lower() == "true",
        token_secret=token_secret,
        database_url=os.getenv("EKB_DATABASE_URL", "sqlite:///./ekb_dev.db"),
        model_providers=_parse_providers(),
        retrieval_cosine_threshold=float(os.getenv("EKB_RETRIEVAL_THRESHOLD", "0.15")),
        retrieval_rrf_k=int(os.getenv("EKB_RETRIEVAL_RRF_K", "60")),
        retrieval_rerank_pool=int(os.getenv("EKB_RETRIEVAL_RERANK_POOL", "20")),
        retrieval_bm25_enabled=os.getenv("EKB_RETRIEVAL_BM25", "true").lower() == "true",
        llm_temperature=float(os.getenv("EKB_LLM_TEMPERATURE", "0.1")),
        llm_max_tokens=int(os.getenv("EKB_LLM_MAX_TOKENS", "1024")),
        # M4-6 Multi-Turn Chat 配置
        multi_turn_enabled=os.getenv("EKB_MULTI_TURN_ENABLED", "true").lower() == "true",
        llm_context_window=int(os.getenv("EKB_LLM_CONTEXT_WINDOW", "16384")),
        llm_compaction_ratio=float(os.getenv("EKB_LLM_COMPACTION_RATIO", "0.75")),
        compaction_recent_rounds_keep=int(os.getenv("EKB_COMPACTION_RECENT_ROUNDS_KEEP", "6")),
        compaction_summary_ratio_target=float(
            os.getenv("EKB_COMPACTION_SUMMARY_RATIO_TARGET", "0.6")
        ),
        embedding_dim=int(os.getenv("EKB_EMBEDDING_DIM", "256")),
        embedding_batch_size=int(os.getenv("EKB_EMBEDDING_BATCH_SIZE", "32")),
        qa_timeout_seconds=float(os.getenv("EKB_QA_TIMEOUT_SECONDS", "60")),
        search_timeout_seconds=float(os.getenv("EKB_SEARCH_TIMEOUT_SECONDS", "10")),
        circuit_breaker_failure_threshold=int(os.getenv("EKB_CB_FAILURE_THRESHOLD", "5")),
        circuit_breaker_recovery_timeout=float(os.getenv("EKB_CB_RECOVERY_TIMEOUT", "60")),
        alert_qa_p95_threshold_seconds=float(os.getenv("EKB_ALERT_QA_P95_SECONDS", "3.0")),
        alert_search_p95_threshold_seconds=float(os.getenv("EKB_ALERT_SEARCH_P95_SECONDS", "0.5")),
        alert_error_rate_threshold=float(os.getenv("EKB_ALERT_ERROR_RATE", "0.05")),
        alert_llm_failure_rate_threshold=float(os.getenv("EKB_ALERT_LLM_FAILURE_RATE", "0.1")),
        # SSE v2 Conversation Stream 配置默认值
        sse_v2_retrieval_idle_timeout=float(
            os.getenv("EKB_SSE_V2_RETRIEVAL_IDLE_TIMEOUT", "15")
        ),
        sse_v2_generation_idle_timeout=float(
            os.getenv("EKB_SSE_V2_GENERATION_IDLE_TIMEOUT", "10")
        ),
        sse_v2_heartbeat_interval=float(os.getenv("EKB_SSE_V2_HEARTBEAT", "15")),
        sse_v2_enabled=os.getenv("EKB_SSE_V2_ENABLED", "true").lower() == "true",
        sse_v2_delta_max_tokens=int(os.getenv("EKB_SSE_V2_DELTA_TOKENS", "2")),
        sse_v2_delta_max_bytes=int(os.getenv("EKB_SSE_V2_DELTA_BYTES", "128")),
        sse_v2_delta_flush_ms=int(os.getenv("EKB_SSE_V2_DELTA_FLUSH_MS", "10")),
        # --- Conversation Engine Unification feature flags ---
        ce_shadow_context=os.getenv("EKB_CE_SHADOW_CONTEXT", "false").lower() == "true",
        ce_turn_engine_enabled=os.getenv("EKB_CE_TURN_ENGINE_ENABLED", "true").lower() == "true",
        ce_event_replay_enabled=os.getenv("EKB_CE_EVENT_REPLAY_ENABLED", "true").lower() == "true",
        ce_frontend_cutover=os.getenv("EKB_CE_FRONTEND_CUTOVER", "false").lower() == "true",
        ce_event_retention_days=int(os.getenv("EKB_CE_EVENT_RETENTION_DAYS", "7")),
        ce_generation_lease_seconds=float(os.getenv("EKB_CE_GENERATION_LEASE_SECONDS", "300")),
        max_upload_bytes=int(
            os.getenv("EKB_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
        ),  # 默认 50MB
        provider_master_key=os.getenv("EKB_PROVIDER_MASTER_KEY", ""),
        object_storage_endpoint=os.getenv("EKB_OBJECT_STORAGE_ENDPOINT", ""),
        object_storage_internal_endpoint=os.getenv("EKB_OBJECT_STORAGE_INTERNAL_ENDPOINT", ""),
        object_storage_bucket=os.getenv("EKB_OBJECT_STORAGE_BUCKET", ""),
        object_storage_region=os.getenv("EKB_OBJECT_STORAGE_REGION", "us-east-1"),
        object_storage_access_key=os.getenv("EKB_OBJECT_STORAGE_ACCESS_KEY", ""),
        object_storage_secret_key=os.getenv("EKB_OBJECT_STORAGE_SECRET_KEY", ""),
        object_storage_path_style=os.getenv("EKB_OBJECT_STORAGE_PATH_STYLE", "true").lower()
        in {"1", "true", "yes", "on"},
    )


# ============================================================
#  配置中心 → 运行时 ModelProvider 适配
#  从 llm_providers / llm_models 表读取用户已启用且绑定了活动凭据
#  的配置，转为 ModelProvider，供 llm.py / QA 能力中心使用。
#  优先级：数据库用户配置 > env 静态配置（EKB_MODEL_PROVIDERS）
# ============================================================


class _LLMConfigUnavailable(Exception):
    """数据库/迁移未就绪时的软失败标记，不影响启动。"""


def _catalog_whitelist_ids(preset_provider_id: str | None) -> set[str] | None:
    """返回 preset 的 catalog_models 白名单 model_id 集合；无白名单返回 None。

    语义与 services.v3_llm._provider_catalog_whitelist 一致，供 AI 助手运行时
    复用，确保「模型服务 UI 可见的模型」与「AI 助手可选用模型」严格对齐。
    """
    if not preset_provider_id:
        return None
    try:
        from ekb_api.services.llm_provider_catalog import get_preset_provider

        preset = get_preset_provider(preset_provider_id)
    except Exception:
        return None
    if not preset:
        return None
    catalog_models = preset.get("catalog_models")
    if not isinstance(catalog_models, list) or len(catalog_models) == 0:
        return None
    ids: set[str] = set()
    for m in catalog_models:
        if isinstance(m, dict):
            mid = m.get("id")
            if isinstance(mid, str) and mid:
                ids.add(mid)
    return ids or None


def _load_runtime_model_providers_from_db(
    *,
    tenant_id: str,
    user_id: str,
    kind: str = "chat",
) -> list[ModelProvider]:
    """从配置中心数据库表读取 provider（仅启用 + 有 API Key 的记录）。

    kind: "chat" | "embedding"
    返回的 ModelProvider.name 形如 "<provider_key>/<model_id>"，与
    _qa_capabilities() 生成的模型下拉 id 对齐（route.provider_name/model）。

    若 llm_providers 表尚未创建、数据库连接失败等，返回空列表。
    """
    if kind not in ("chat", "embedding"):
        return []
    try:
        # 延迟导入，避免模块加载期循环依赖（config → db → models → ... → config）。
        from sqlalchemy import text

        from ekb_api.core.db import get_session_local

        SessionLocal = get_session_local()
    except Exception:
        return []

    timeout_seconds = 30.0
    out: list[ModelProvider] = []

    try:
        with SessionLocal() as session:
            # 1) 读取当前用户的 provider：仅启用且绑定活动凭据。
            # 旧的 llm_providers.api_key 永不进入运行时查询；这保证了
            # v4 凭据边界生效后，明文/旧格式字段不会成为旁路。
            rows = session.execute(
                text(
                    """
                    SELECT p.id, p.provider_key, p.name, p.default_chat_endpoint,
                           p.endpoint_configs, c.ciphertext, p.preset_provider_id
                    FROM llm_providers AS p
                    JOIN provider_credentials AS c
                      ON c.id = p.credential_id
                     AND c.tenant_id = p.tenant_id
                     AND c.status = 'ACTIVE'
                     AND (
                       (c.ownership_scope = 'PERSONAL'
                        AND c.owner_user_id = p.user_id
                        AND c.ownership_key = 'USER:' || p.user_id)
                       OR
                       (c.ownership_scope = 'TEAM'
                        AND c.owner_user_id IS NULL
                        AND c.ownership_key = 'TEAM')
                     )
                    WHERE p.tenant_id = :t
                      AND p.is_enabled = :enabled
                    """
                ),
                {"t": tenant_id, "u": user_id, "enabled": True},
            ).fetchall()

            for row in rows:
                provider_id = str(row[0])
                provider_key = str(row[1])
                default_endpoint = row[3]
                endpoint_configs_raw = row[4] or "{}"
                ciphertext = row[5] or ""
                # preset_provider_id 用于套用 catalog_models 白名单（与模型服务 UI 一致）
                preset_pid = str(row[6]) if row[6] else None
                whitelist_ids = _catalog_whitelist_ids(preset_pid)

                # 解密失败或没有 provider master key 时，该配置不可用；
                # 不回退到旧 api_key，也不把异常细节暴露给调用方。
                try:
                    from ekb_api.services import secrets as provider_secrets

                    api_key = provider_secrets.decrypt(str(ciphertext)).strip()
                    if not api_key:
                        continue
                except Exception:
                    continue

                # 解析 endpoint_configs JSON
                try:
                    endpoint_configs = (
                        json.loads(endpoint_configs_raw)
                        if isinstance(endpoint_configs_raw, str)
                        else (endpoint_configs_raw or {})
                    )
                except (TypeError, ValueError):
                    continue
                if not isinstance(endpoint_configs, dict):
                    continue

                endpoint_name = default_endpoint or "openai-chat-completions"
                endpoint_cfg = endpoint_configs.get(endpoint_name, {}) if endpoint_configs else {}
                base_url_raw = (
                    endpoint_cfg.get("baseUrl")
                    if isinstance(endpoint_cfg, dict)
                    else None
                )

                if not base_url_raw:
                    continue
                if not _is_remote_provider_url(str(base_url_raw)):
                    continue

                # 规范化 base_url：保证末尾是 /chat/completions 或 /embeddings
                # 若用户已经带上具体 path 就保留；否则补 OpenAI 兼容的默认 path
                model_type_filter = "chat" if kind == "chat" else "embedding"
                base_url = _normalize_model_endpoint(
                    base_url_raw, endpoint_name, kind=model_type_filter
                )

                # 2) 读取该 provider 下已启用的 models
                model_rows = session.execute(
                    text(
                        """
                        SELECT id, model_id, display_name, model_type, capabilities,
                               tenant_id, user_id, is_custom
                        FROM llm_models
                        WHERE provider_id = :pid
                          AND tenant_id = :t
                          AND is_enabled = :enabled
                        ORDER BY created_at ASC
                        """
                    ),
                    {"pid": provider_id, "t": tenant_id, "u": user_id, "enabled": True},
                ).fetchall()

                models_to_use: list[tuple[str, str, str, dict]] = []
                for m in model_rows:
                    db_model_id = str(m[0])
                    mid, mname, mtype = str(m[1]), str(m[2] or m[1]), (m[3] or "chat")
                    if not mid.strip() or "/" in mid:
                        # Public ids use provider_key/model_id.  An embedded
                        # slash would make the selected provider ambiguous.
                        continue
                    # catalog_models 白名单过滤（与模型服务 UI list_llm_models 一致）：
                    # 非自定义模型必须出现在 preset 白名单内，否则 UI 不可见也不应被 AI 助手选用。
                    if whitelist_ids is not None and int(m[7] or 0) == 0 and mid not in whitelist_ids:
                        continue
                    model_capabilities = m[4] or {}
                    if isinstance(model_capabilities, str):
                        try:
                            model_capabilities = json.loads(model_capabilities)
                        except (TypeError, ValueError):
                            continue
                    if not isinstance(model_capabilities, dict):
                        continue
                    if kind == "chat" and not (mtype and "embedding" in mtype.lower()):
                        models_to_use.append((db_model_id, mid, mname, model_capabilities))
                    elif kind == "embedding" and mtype and "embedding" in mtype.lower():
                        models_to_use.append((db_model_id, mid, mname, model_capabilities))

                for db_model_id, mid, mname, model_capabilities in models_to_use:
                    runtime_name = f"{provider_key}/{mid}"
                    out.append(
                        ModelProvider(
                            name=runtime_name,
                            kind=kind,
                            base_url=base_url,
                            api_key=api_key,
                            model=mid,
                            timeout_seconds=timeout_seconds,
                            supports_vision=bool(
                                model_capabilities.get("vision") is True
                            ),
                            display_name=mname,
                            capabilities=dict(model_capabilities),
                            id=db_model_id,
                        )
                    )
    except _LLMConfigUnavailable:
        return []
    except Exception:
        # 配置中心 DB 未就绪 / 表不存在 / 迁移未跑 → 空列表；env 配置兜底。
        return []

    return out


def _normalize_model_endpoint(base_url_raw: str, endpoint_name: str, *, kind: str) -> str:
    """把 catalog 里的 baseUrl 统一成 llm.py _call_provider 需要的完整端点 URL。

    约定：
      - endpoint_name = "openai-chat-completions" → /chat/completions
      - 若 base_url_raw 已经以 /chat/completions 或 /embeddings 结尾则原样返回
      - 其它情况补全 path，保证 llm.py 里 POST 到的 URL 是正确的。
    """
    url = (base_url_raw or "").rstrip("/")
    if not url:
        return ""

    suffix = "/chat/completions" if kind == "chat" else "/embeddings"
    if url.endswith(suffix):
        return url
    # 如果结尾已经是具体的 /v1 之类，就拼 suffix
    if "chat/completions" in url.lower() and kind == "chat":
        return url
    if "embeddings" in url.lower() and kind == "embedding":
        return url
    return url + suffix


def get_runtime_chat_providers(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> list[ModelProvider]:
    """运行时可用的 chat provider：配置中心（优先）+ env 静态配置（兜底）。

    不传 tenant_id/user_id 时只返回 env 静态配置（兼容测试 / 启动期调用）。
    """
    settings = get_settings()
    static: list[ModelProvider] = list(settings.chat_providers)
    if tenant_id is None or user_id is None:
        return static

    dynamic = _load_runtime_model_providers_from_db(
        tenant_id=tenant_id, user_id=user_id, kind="chat"
    )

    # 合并：动态在前（配置中心优先），静态在后（兜底），按 name 去重
    merged: list[ModelProvider] = []
    seen: set[str] = set()
    for p in [*dynamic, *static]:
        if p.name in seen:
            continue
        seen.add(p.name)
        merged.append(p)
    return merged


def get_configured_runtime_chat_providers(
    *,
    tenant_id: str,
    user_id: str,
) -> list[ModelProvider]:
    """Actor-scoped DB models with active, decryptable credentials only.

    This is intentionally separate from ``get_runtime_chat_providers``:
    normal no-model routing keeps its existing environment fallback, while
    the QA capabilities and explicit model-selection contract must not expose
    or accept an environment/default/local model.
    """
    return _load_runtime_model_providers_from_db(
        tenant_id=tenant_id,
        user_id=user_id,
        kind="chat",
    )


def get_runtime_embedding_providers(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> list[ModelProvider]:
    """对应 embedding 版本。"""
    settings = get_settings()
    static: list[ModelProvider] = list(settings.embedding_providers)
    if tenant_id is None or user_id is None:
        return static

    dynamic = _load_runtime_model_providers_from_db(
        tenant_id=tenant_id, user_id=user_id, kind="embedding"
    )
    merged: list[ModelProvider] = []
    seen: set[str] = set()
    for p in [*dynamic, *static]:
        if p.name in seen:
            continue
        seen.add(p.name)
        merged.append(p)
    return merged


def runtime_llm_enabled(*, tenant_id: str | None = None, user_id: str | None = None) -> bool:
    """兼容 settings.llm_enabled：任一来源有至少一个 chat provider 即 True。"""
    return bool(get_runtime_chat_providers(tenant_id=tenant_id, user_id=user_id))

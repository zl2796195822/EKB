from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_dotenv() -> None:
    """轻量 .env 加载：解析 KEY=VALUE 行，不覆盖已存在的环境变量。

    不依赖 python-dotenv；仅在项目根目录存在 .env 且非测试环境时生效。
    测试环境（EKB_ENV=test）跳过加载，避免 .env 中的外部 API 配置污染测试。
    """
    if os.getenv("EKB_ENV") == "test":
        return
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if not env_path.exists():
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
    # 本地降级向量维度 / 批大小（仅 embed 失败降级时使用）。
    embedding_dim: int
    embedding_batch_size: int

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    # ---- 多 Provider 路由 ----
    @property
    def chat_providers(self) -> list[ModelProvider]:
        """已启用的 chat 提供商（需同时具备 base_url 与 api_key）。"""
        return [p for p in self.model_providers if p.kind == "chat" and p.api_key and p.base_url]

    @property
    def embedding_providers(self) -> list[ModelProvider]:
        """已启用的 embedding 提供商。"""
        return [
            p for p in self.model_providers if p.kind == "embedding" and p.api_key and p.base_url
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
        if self.embedding_providers:
            return self.embedding_providers[0].model
        return "bge-large-zh-v1.5"

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
                providers.append(
                    ModelProvider(
                        name=str(item.get("name", f"{kind}-provider")),
                        kind=kind,
                        base_url=str(item.get("base_url", "")),
                        api_key=str(item.get("api_key", "")),
                        model=str(item.get("model", "")),
                        timeout_seconds=float(item.get("timeout", 30)),
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
        embedding_dim=int(os.getenv("EKB_EMBEDDING_DIM", "256")),
        embedding_batch_size=int(os.getenv("EKB_EMBEDDING_BATCH_SIZE", "32")),
    )

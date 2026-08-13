from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ekb_api.domain import DocumentStatus, KbRole, KbVisibility, SourceType


class LoginRequest(BaseModel):
    tenant_hint: Optional[str] = None
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    mfa_code: Optional[str] = Field(default=None, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int
    token_type: str


class TenantSummary(BaseModel):
    id: str
    name: str
    role: str


class UserSummary(BaseModel):
    id: str
    name: str
    email: str
    role: str = "OWNER"


class LoginResponse(BaseModel):
    access_token: str
    expires_in: int
    refresh_token: str
    token_type: str
    user: UserSummary
    tenants: list[TenantSummary]


class MeResponse(BaseModel):
    user: UserSummary
    tenants: list[TenantSummary]
    capabilities: list[str]
    policy_version: int


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    visibility: KbVisibility = KbVisibility.PRIVATE
    tags: list[str] = Field(default_factory=list, max_length=20)


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    visibility: Optional[KbVisibility] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=512)
    archived: Optional[bool] = None


class KnowledgeBaseResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    visibility: KbVisibility
    role: str
    document_count: int
    created_at: str
    updated_at: str


class KbMemberCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: KbRole = KbRole.VIEWER


class KbMemberResponse(BaseModel):
    id: str
    kb_id: str
    user_id: str
    role: str
    granted_by: Optional[str] = None
    created_at: str
    updated_at: str


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    owner_email: str = Field(min_length=3, max_length=254)
    owner_name: str = Field(min_length=1, max_length=255)
    owner_password: str = Field(min_length=1, max_length=256)
    model_routing_key: str = "default"
    egress_policy: str = "allow"
    # M2-7 每日问答配额（0 = 不限）。
    quota_daily_qa: int = 0
    # M3-5 文档数存储配额（0 = 不限）。
    quota_storage_docs: int = 0
    # 单文件上传大小上限（字节，0 = 使用全局默认）。
    quota_storage_bytes_per_file: int = 0


class TenantResponse(BaseModel):
    id: str
    name: str
    role: str
    model_routing_key: str
    egress_policy: str
    # M2-7 每日问答配额（0 = 不限）。
    quota_daily_qa: int = 0
    # M3-5 文档数存储配额（0 = 不限）。
    quota_storage_docs: int = 0
    # 单文件上传大小上限（字节，0 = 使用全局默认）。
    quota_storage_bytes_per_file: int = 0


class UserInvite(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    role: str = "MEMBER"


class UserInviteResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    tenant_id: str


class DocumentResponse(BaseModel):
    id: str
    kb_id: str
    title: str
    status: DocumentStatus
    version: int
    mime_type: str
    checksum: str
    chunk_count: int
    failure_reason: Optional[str]
    created_at: str
    updated_at: str


class UploadAcceptedResponse(BaseModel):
    doc_id: str
    job_id: str
    status: str
    trace_id: str


class SearchFilters(BaseModel):
    tags: list[str] = Field(default_factory=list)
    source_type: list[SourceType] = Field(default_factory=list)
    updated_after: Optional[str] = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    kb_ids: list[str] = Field(default_factory=list, max_length=20)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=10, ge=1, le=50)


class SearchResult(BaseModel):
    chunk_id: str
    doc_id: str
    kb_id: str
    title: str
    section_path: list[str]
    snippet: str
    score: float
    updated_at: str


class SearchResponse(BaseModel):
    results: list[SearchResult]
    trace_id: str


class AskOptions(BaseModel):
    # Keep unknown options available to the route for an explicit fail-closed
    # compatibility response; removed Web Search is not part of this schema.
    model_config = ConfigDict(extra="allow")
    stream: bool = True
    max_citations: int = Field(default=5, ge=1, le=10)
    # SSE v2: 1=legacy SSE, 2=Conversation Stream v2（envelope+seq+turn_id）
    stream_version: int = Field(default=2, ge=1, le=2)
    # 深度思考：默认开启（medium 档）。关闭则走 thinking_level=light。
    # thinking_level 五档（与前端 UI 「轻度/中度/中/高/极高」一一对应），真实控制：
    #   - 传给 DeepSeek V4 原生 reasoning_effort（low/medium/high/max）
    #   - 控制 EKB 侧 prompt 推理提示强度与 temperature 采样
    deep_thinking: bool = True
    thinking_level: str = Field(
        default="medium",
        description="思考程度五档：light=轻度（快速回答） / mild=中度 / medium=中（默认，推荐） / high=高 / extreme=极高（更慢、更严谨、消耗更多额度）",
        pattern=r"^(light|mild|medium|high|extreme)$",
    )
    model: Optional[str] = None    # 用户选择的模型覆盖（None 则使用 settings.llm_model + route 默认）
    attachment_doc_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
    )  # 「添加文件」：附件 doc_id，合并到检索范围
    # PH6 FR-051：答题模式。
    #   STRICT  —— 选中知识库但检索不到任何证据时直接拒答（默认，避免编造）。
    #   ENHANCED —— 允许结合常识作答，但仍以知识库证据为主、明确分隔。
    answer_mode: Literal["STRICT", "ENHANCED"] = "STRICT"


class AskRequest(BaseModel):
    conversation_id: Optional[str] = None
    question: str = Field(min_length=1, max_length=2000)
    kb_ids: list[str] = Field(default_factory=list, max_length=20)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    options: AskOptions = Field(default_factory=AskOptions)


# ---- Phase2：Composer 功能按钮 辅助 schemas ----

class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    description: Optional[str] = None
    supports_deep_thinking: bool = False


class ComposerCapabilities(BaseModel):
    """Composer 功能按钮是否可用。"""
    attachments_enabled: bool = True       # 添加文件（上传后为 doc_id，走 attachment_doc_ids）
    deep_thinking_enabled: bool = True     # 深度思考：通过 prompt + 温度 模拟，LLM 支持即启用
    model_choice_enabled: bool = True      # 模型选择：通过覆盖 route.model_name 生效


class ComposerCapabilitiesResponse(BaseModel):
    capabilities: ComposerCapabilities
    models: list[ModelInfo]
    # 同步返回 AskOptions 默认值，便于前端初始化
    defaults: AskOptions
    # M4-6 多轮对话新增：上下文窗口大小 + 压缩功能开关
    context_window_tokens: int = 16384
    compaction_enabled: bool = True


class FeedbackRequest(BaseModel):
    rating: str = Field(pattern="^(UP|DOWN)$")
    reason: str = Field(min_length=1, max_length=200)
    comment: Optional[str] = Field(default=None, max_length=2000)


# ---- M3-1 运营看板 ----


class OpsDashboardResponse(BaseModel):
    qa_volume: int
    answered: int
    refused: int
    timeout: int
    error: int
    accuracy: Optional[float] = None
    refusal_rate: Optional[float] = None
    satisfaction: Optional[float] = None
    feedback_up: int
    feedback_down: int
    pending_reviews: int
    kb_count: int
    doc_count: int


# ---- M3-2 反馈闭环 ----


class FeedbackStatusUpdate(BaseModel):
    status: str = Field(pattern="^(PENDING|REVIEWED|RESOLVED)$")
    annotation: Optional[str] = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    id: str
    message_id: str
    user_id: str
    rating: str
    reason: Optional[str] = None
    comment: Optional[str] = None
    status: str
    annotation: Optional[str] = None
    created_at: str


class FeedbackListResponse(BaseModel):
    results: list[FeedbackResponse]
    next_cursor: Optional[str] = None


# ---- M3-3 审核队列 ----


class ReviewItemResponse(BaseModel):
    id: str
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    question_preview: str
    answer_preview: str
    confidence: str
    evidence_summary: list[dict[str, Any]]
    status: str
    resolution: Optional[str] = None
    resolved_by: Optional[str] = None
    created_at: str
    updated_at: str


class ReviewItemListResponse(BaseModel):
    results: list[ReviewItemResponse]
    next_cursor: Optional[str] = None


class ReviewItemUpdate(BaseModel):
    status: str = Field(pattern="^(PENDING|REVIEWED|RESOLVED)$")
    resolution: Optional[str] = Field(default=None, max_length=2000)


# ---- M3-6 文档版本 ----


class DocumentVersionResponse(BaseModel):
    id: str
    doc_id: str
    version: int
    checksum: str
    chunk_count: int
    content_snapshot: list[dict[str, Any]]
    created_at: str


class DocumentDiffResponse(BaseModel):
    from_version: int
    to_version: int
    from_chunk_count: int
    to_chunk_count: int
    added: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    unchanged: list[dict[str, Any]]


# ---- M3-7 同步源 ----


class SyncSourceCreate(BaseModel):
    kb_id: str
    name: str = Field(min_length=1, max_length=255)
    source_type: str = Field(default="API", pattern="^(WEBDAV|TICKET|API)$")
    source_url: str = Field(default="", max_length=1024)


class SyncSourceResponse(BaseModel):
    id: str
    tenant_id: str
    kb_id: str
    name: str
    source_type: str
    source_url: str
    cursor: Optional[str] = None
    status: str
    last_sync_at: Optional[str] = None
    last_sync_count: int
    error_message: Optional[str] = None
    retry_count: int
    created_at: str
    updated_at: str


class SyncRunResponse(BaseModel):
    source_id: str
    status: str
    synced_count: int
    error_message: Optional[str] = None


# ---- M3-5 存储配额 ----


class TenantQuotaUpdate(BaseModel):
    quota_daily_qa: Optional[int] = None
    quota_storage_docs: Optional[int] = None
    quota_storage_bytes_per_file: Optional[int] = None


# ---- SSE v2 Turn 显式取消 ----


class TurnCancelResponse(BaseModel):
    turn_id: str
    status: str               # cancelled / already_completed / not_found
    accepted: bool
    message: Optional[str] = None


JsonDict = Dict[str, Any]

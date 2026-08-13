from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String, Text

from ekb_api.core.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default="OWNER")
    policy_version = Column(Integer, nullable=False, default=1)
    # M2-7 租户级模型路由键与出域策略（缺省 "default" / "allow"）。
    model_routing_key = Column(String(64), nullable=False, default="default")
    egress_policy = Column(String(32), nullable=False, default="allow")
    # M2-7 每租户每日问答配额（0 = 不限）。
    quota_daily_qa = Column(Integer, nullable=False, default=0)
    # M3-5 每租户文档数存储配额（0 = 不限）。
    quota_storage_docs = Column(Integer, nullable=False, default=0)
    # 每租户单文件上传大小上限（字节，0 = 使用全局默认）。
    quota_storage_bytes_per_file = Column(Integer, nullable=False, default=0)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class TenantDailyUsage(Base):
    """M2-7 每租户每日问答用量计数（按自然日重置）。

    以 (tenant_id) 唯一，usage_date 记录生效日期；跨日自动清零。
    """

    __tablename__ = "tenant_daily_usage"

    tenant_id = Column(String(36), primary_key=True)
    usage_date = Column(String(32), nullable=False)
    qa_count = Column(Integer, nullable=False, default=0)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    email = Column(String(320), nullable=False, unique=True)
    # M1-2 真实鉴权：密码哈希（scrypt，加盐），明文不再落库；NULL 表示尚未设置口令。
    password_hash = Column(String(128), nullable=True)
    # 所属租户与角色（单租户 MVP 阶段由种子写入；多租户见 M2）。
    tenant_id = Column(String(36), nullable=True, index=True)
    role = Column(String(32), nullable=False, default="OWNER")
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=False, default="")
    visibility = Column(String(32), nullable=False, default="PRIVATE")
    role = Column(String(32), nullable=False, default="OWNER")
    document_count = Column(Integer, nullable=False, default=0)
    deleted_at = Column(String(32), nullable=True, index=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    kb_id = Column(String(36), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    status = Column(String(32), nullable=False, default="PROCESSING")
    version = Column(Integer, nullable=False, default=1)
    mime_type = Column(String(128), nullable=False)
    checksum = Column(String(128), nullable=False)
    chunk_count = Column(Integer, nullable=False, default=0)
    source_type = Column(String(32), nullable=False, default="UPLOAD")
    failure_reason = Column(Text, nullable=True)
    # 幂等键：相同 Idempotency-Key + 租户内不重复创建文档（AC-US02-02）。
    idempotency_key = Column(String(128), nullable=True, index=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    kb_id = Column(String(36), nullable=False, index=True)
    doc_id = Column(String(36), nullable=False, index=True)
    doc_version = Column(Integer, nullable=False, default=1)
    chunk_index = Column(Integer, nullable=False, default=0)
    title = Column(String(512), nullable=False)
    section_path = Column(JSON, nullable=False, default=list)
    content = Column(Text, nullable=False)
    content_hash = Column(String(128), nullable=True)
    token_count = Column(Integer, nullable=True)
    embedding = Column(JSON, nullable=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    kb_id = Column(String(36), nullable=False, index=True)
    doc_id = Column(String(36), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="QUEUED")
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    trace_id = Column(String(64), nullable=True)
    idempotency_key = Column(String(128), nullable=True, index=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    title = Column(String(512), nullable=False, default="")
    archived_at = Column(String(32), nullable=True)
    deleted_at = Column(String(32), nullable=True, index=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    conversation_id = Column(String(36), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    # SSE v2: 关联的 turn_id，用于追踪流式回合与显式取消
    turn_id = Column(String(64), nullable=True, index=True)
    # SSE v2: 消息可见性状态 visible / hidden（被取消/超时/错误替代的占位消息置 hidden）
    visibility_state = Column(String(32), nullable=False, default="visible", index=True)
    created_at = Column(String(32), nullable=False)
    # PH6 FR-053：本次回答产生的引用（SSE citations 载荷），含生成时文档版本/时间戳，
    # 便于刷新/分支/版本回溯时复核引用指向的版本。可为空（用户消息 / 未检索到证据）。
    citations = Column(JSON, nullable=True)


class QaTurn(Base):
    """SSE v2 Turn Registry：QA 回合生命周期记录，支持显式取消与审计。

    Turn 是一次流式问答的最小可取消单元；同一 Conversation 内有多个 Turn。
    """

    __tablename__ = "qa_turns"

    turn_id = Column(String(64), primary_key=True)
    request_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    actor_id = Column(String(36), nullable=False, index=True)
    conversation_id = Column(String(36), index=True)
    assistant_message_id = Column(String(36), index=True)
    stream_version = Column(Integer, nullable=False, default=2)
    # running / completed / cancelled / timeout / error
    status = Column(String(32), nullable=False, default="running", index=True)
    last_seq = Column(Integer, nullable=False, default=0)
    # 首次可见 token 发出时间，用于 TTFB 指标
    first_visible_at = Column(String(32), nullable=True)
    # 用户/客户端请求取消时间
    cancel_requested_at = Column(String(32), nullable=True)
    # stop / refusal / cancelled / timeout / error
    finish_reason = Column(String(32), nullable=True)
    created_at = Column(String(32), nullable=False)
    completed_at = Column(String(32), nullable=True)


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    message_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    rating = Column(String(16), nullable=False)
    reason = Column(String(255), nullable=True)
    comment = Column(Text, nullable=True)
    # M3-2 反馈闭环状态与标注：PENDING → REVIEWED → RESOLVED。
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    annotation = Column(Text, nullable=True)
    created_at = Column(String(32), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=True, index=True)
    actor_id = Column(String(36), nullable=True, index=True)
    action = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(String(36), nullable=True)
    result = Column(String(32), nullable=False)
    trace_id = Column(String(64), nullable=False, index=True)
    ip_hash = Column(String(64), nullable=True)
    user_agent_hash = Column(String(64), nullable=True)
    metadata_redacted = Column(JSON, nullable=False, default=dict)
    created_at = Column(String(32), nullable=False, index=True)


class KbMembership(Base):
    """M2-2 每知识库访问控制列表（ACL）：记录某用户在某个知识库上的角色。

    PRIVATE 知识库仅对拥有成员记录的用户可见；TEAM/PUBLIC 走租户/全租户可见性。
    创建者自动获得 OWNER 成员；OWNER/ADMIN 成员可管理成员与可见性。
    """

    __tablename__ = "kb_memberships"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    kb_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    role = Column(String(32), nullable=False, default="VIEWER")
    granted_by = Column(String(36), nullable=True)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class ReviewItem(Base):
    """M3-3 低置信度审核队列：关联问题、证据、知识版本和处理结果。

    confidence=low 的问答自动入队；审核员标注后流转 PENDING → REVIEWED → RESOLVED。
    审核结果不自动覆盖生产知识（M3-2 约束）。
    """

    __tablename__ = "review_items"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    conversation_id = Column(String(36), nullable=True, index=True)
    message_id = Column(String(36), nullable=True, index=True)
    question_preview = Column(String(255), nullable=False)
    answer_preview = Column(Text, nullable=False, default="")
    confidence = Column(String(16), nullable=False, default="low")
    evidence_summary = Column(JSON, nullable=False, default=list)
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    resolution = Column(Text, nullable=True)
    resolved_by = Column(String(36), nullable=True)
    created_at = Column(String(32), nullable=False, index=True)
    updated_at = Column(String(32), nullable=False)


class DocumentVersion(Base):
    """M3-6 文档版本快照：每次 re-ingest 递增版本并记录 chunk 摘要，支持差异对比。

    content_snapshot 存储该版本的 chunk 摘要列表
    （title + section_path + content_hash + content_preview），
    供 diff 端点比对版本间增删改。
    """

    __tablename__ = "document_versions"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    doc_id = Column(String(36), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    checksum = Column(String(128), nullable=False)
    chunk_count = Column(Integer, nullable=False, default=0)
    content_snapshot = Column(JSON, nullable=False, default=list)
    created_at = Column(String(32), nullable=False)


class SyncSource(Base):
    """M3-7 网盘/工单只读增量同步源：记录外部数据源、游标和失败重试。

    source_type: WEBDAV / TICKET / API 。
    cursor: 上次同步的高水位标记（时间戳或 offset），用于增量拉取。
    status: ACTIVE / PAUSED / SYNCING / FAILED 。
    """

    __tablename__ = "sync_sources"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    kb_id = Column(String(36), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    source_type = Column(String(32), nullable=False, default="API")
    source_url = Column(String(1024), nullable=False, default="")
    cursor = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="ACTIVE", index=True)
    last_sync_at = Column(String(32), nullable=True)
    last_sync_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)


class LLMProvider(Base):
    """AI 大模型服务商配置：用户级别的 LLM 接入凭证与参数。

    支持国内外主流大模型平台的统一接入：
    - 国内：深度求索、硅基流动、智谱、月之暗面、阿里百炼、腾讯混元、字节豆包等
    - 国外：OpenAI、Anthropic、OpenRouter、Ollama、Gemini、Groq 等
    - 兼容 OpenAI 协议的自建：New API、LM Studio 等
    """

    __tablename__ = "llm_providers"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    """关联预设服务商 ID（如果是基于预设创建的自定义实例）"""
    preset_provider_id = Column(String(64), nullable=True)
    """服务商唯一标识（预设使用 registry id，自定义使用 uuid）"""
    provider_key = Column(String(64), nullable=False, index=True)
    """展示名称"""
    name = Column(String(128), nullable=False)
    """logo key 或 logo URL"""
    logo = Column(String(255), nullable=True)
    """简介"""
    description = Column(Text, nullable=True)
    """官网链接：official / docs / apiKey / models"""
    websites = Column(JSON, nullable=False, default=dict)
    """默认聊天端点类型：openai-chat-completions / anthropic-messages / ollama-chat 等"""
    default_chat_endpoint = Column(String(64), nullable=True)
    """各端点配置字典：{ endpoint_type: { baseUrl, adapterFamily, reasoningFormatType, modelsApiUrls } }"""
    endpoint_configs = Column(JSON, nullable=False, default=dict)
    """认证类型：api-key / oauth / iam-aws / api-key-aws / iam-gcp / iam-azure"""
    auth_type = Column(String(32), nullable=False, default="api-key")
    """API Key（加密存储，或为空表示无需密钥如 Ollama）"""
    api_key = Column(Text, nullable=True)
    """API Key 标签/备注"""
    api_key_label = Column(String(128), nullable=True)
    """API 特性支持：arrayContent / streamOptions / developerRole / serviceTier / verbosity"""
    api_features = Column(JSON, nullable=False, default=dict)
    """服务商级额外设置：serviceTier / timeout / rateLimit / extraHeaders / notes"""
    settings = Column(JSON, nullable=False, default=dict)
    """模型列表来源：api（拉取接口）/ registry（使用内置目录）"""
    model_list_source = Column(String(16), nullable=False, default="api")
    """是否启用（用户开关）"""
    is_enabled = Column(Boolean, nullable=False, default=False)
    """创建时间"""
    created_at = Column(String(32), nullable=False)
    """更新时间"""
    updated_at = Column(String(32), nullable=False)


class LLMModel(Base):
    """AI 模型配置：具体某服务商下可用的模型条目。

    每个 Provider 可以有多个 Model（如 deepseek 下有 V4 Flash / V4 Pro / Reasoner / Chat）。
    模型可以通过 API 拉取同步，也可以手动添加编辑。
    """

    __tablename__ = "llm_models"

    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), nullable=False, index=True)
    """所属 LLM Provider"""
    provider_id = Column(String(36), nullable=False, index=True)
    """模型唯一 ID，如 deepseek-chat、claude-sonnet-4-20250514"""
    model_id = Column(String(128), nullable=False)
    """模型展示名"""
    display_name = Column(String(256), nullable=False)
    """模型分类：chat / embedding / reranker / image / reasoning"""
    model_type = Column(String(32), nullable=False, default="chat")
    """上下文窗口大小（token 数）"""
    context_window = Column(Integer, nullable=True)
    """最大输出 token 数"""
    max_output_tokens = Column(Integer, nullable=True)
    """端点类型：继承自 provider 或单独指定"""
    endpoint_type = Column(String(64), nullable=True)
    """能力位：vision / toolUse / functionCalling / streaming / reasoning"""
    capabilities = Column(JSON, nullable=False, default=dict)
    """输入单价（美元 / 百万 token），仅作展示"""
    input_price = Column(String(32), nullable=True)
    """输出单价（美元 / 百万 token），仅作展示"""
    output_price = Column(String(32), nullable=True)
    """是否启用该模型"""
    is_enabled = Column(Boolean, nullable=False, default=True)
    """是否为用户手动添加"""
    is_custom = Column(Boolean, nullable=False, default=False)
    """用户备注"""
    notes = Column(Text, nullable=True)
    """创建时间"""
    created_at = Column(String(32), nullable=False)
    """更新时间"""
    updated_at = Column(String(32), nullable=False)

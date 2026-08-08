from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, String, Text

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
    created_at = Column(String(32), nullable=False)


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

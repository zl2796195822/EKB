from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TenantRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    CUSTOMER = "CUSTOMER"


class KbVisibility(str, Enum):
    PRIVATE = "PRIVATE"
    TEAM = "TEAM"
    PUBLIC = "PUBLIC"


class KbRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"


class EgressPolicy(str, Enum):
    """M2-7 租户数据出域策略。

    - ALLOW：允许知识库内容在授权范围内用于问答生成（默认）。
    - DENY：禁止内容出域（用于高敏感度租户，生成层需拦截——集成见 M2-7 下一步）。
    """

    ALLOW = "allow"
    DENY = "deny"


class ReviewStatus(str, Enum):
    """M3-3 审核队列状态。"""

    PENDING = "PENDING"
    REVIEWED = "REVIEWED"
    RESOLVED = "RESOLVED"


class FeedbackStatus(str, Enum):
    """M3-2 反馈闭环状态。"""

    PENDING = "PENDING"
    REVIEWED = "REVIEWED"
    RESOLVED = "RESOLVED"


class SyncStatus(str, Enum):
    """M3-7 同步源状态。"""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SYNCING = "SYNCING"
    FAILED = "FAILED"


class SyncSourceType(str, Enum):
    """M3-7 同步源类型。"""

    WEBDAV = "WEBDAV"
    TICKET = "TICKET"
    API = "API"


class DocumentStatus(str, Enum):
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETED = "DELETED"


class SourceType(str, Enum):
    UPLOAD = "UPLOAD"
    URL = "URL"
    SYNC = "SYNC"


class IngestStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class AuthContext:
    actor_id: str
    tenant_id: str
    tenant_role: TenantRole
    platform_role: str
    capabilities: list[str]
    policy_version: int
    trace_id: str


@dataclass
class User:
    id: str
    name: str
    email: str
    role: str = "OWNER"
    tenant_id: Optional[str] = None
    # M1-2 真实鉴权：仅在服务端内部使用，绝不序列化到对外响应。
    password_hash: Optional[str] = None


@dataclass
class Tenant:
    id: str
    name: str
    role: TenantRole
    policy_version: int = 1
    model_routing_key: str = "default"
    egress_policy: str = "allow"
    # M2-7 每租户每日问答配额（0 = 不限）。
    quota_daily_qa: int = 0
    # M3-5 每租户文档数存储配额（0 = 不限）。
    quota_storage_docs: int = 0


@dataclass
class KnowledgeBase:
    id: str
    tenant_id: str
    name: str
    description: str
    visibility: KbVisibility
    role: KbRole
    document_count: int
    created_at: str
    updated_at: str
    deleted_at: str | None = None


@dataclass
class Document:
    id: str
    tenant_id: str
    kb_id: str
    title: str
    status: DocumentStatus
    version: int
    mime_type: str
    checksum: str
    chunk_count: int
    source_type: SourceType
    failure_reason: str | None
    created_at: str
    updated_at: str


@dataclass
class Chunk:
    id: str
    tenant_id: str
    kb_id: str
    doc_id: str
    doc_version: int
    title: str
    section_path: list[str]
    content: str
    score: float = 0
    updated_at: str = field(default_factory=utc_now)


@dataclass
class Conversation:
    id: str
    tenant_id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    archived_at: str | None = None
    deleted_at: str | None = None


@dataclass
class Message:
    id: str
    tenant_id: str
    conversation_id: str
    role: str
    content: str
    created_at: str


@dataclass
class AuditLog:
    id: str
    tenant_id: str | None
    actor_id: str | None
    action: str
    target_type: str
    target_id: str | None
    result: str
    trace_id: str
    metadata_redacted: dict
    created_at: str
    ip_hash: str | None = None
    user_agent_hash: str | None = None


@dataclass
class KbMembership:
    """M2-2 每知识库成员关系（ACL 条目）。"""

    id: str
    tenant_id: str
    kb_id: str
    user_id: str
    role: KbRole
    granted_by: str | None
    created_at: str
    updated_at: str


@dataclass
class ReviewItem:
    """M3-3 低置信度审核队列条目。"""

    id: str
    tenant_id: str
    conversation_id: str | None
    message_id: str | None
    question_preview: str
    answer_preview: str
    confidence: str
    evidence_summary: list[dict]
    status: ReviewStatus
    resolution: str | None
    resolved_by: str | None
    created_at: str
    updated_at: str


@dataclass
class DocumentVersion:
    """M3-6 文档版本快照。"""

    id: str
    tenant_id: str
    doc_id: str
    version: int
    checksum: str
    chunk_count: int
    content_snapshot: list[dict]
    created_at: str


@dataclass
class SyncSource:
    """M3-7 网盘/工单只读增量同步源。"""

    id: str
    tenant_id: str
    kb_id: str
    name: str
    source_type: str
    source_url: str
    cursor: str | None
    status: str
    last_sync_at: str | None
    last_sync_count: int
    error_message: str | None
    retry_count: int
    created_at: str
    updated_at: str

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import inspect, text

from ekb_api import models
from ekb_api.core.db import get_session_local
from ekb_api.domain import (
    AuditLog,
    AuthContext,
    Chunk,
    Conversation,
    Document,
    DocumentStatus,
    DocumentVersion,
    KbMembership,
    KbRole,
    KbVisibility,
    KnowledgeBase,
    Message,
    MessageVisibility,
    QaTurn,
    ReviewItem,
    SourceType,
    SyncSource,
    Tenant,
    TenantRole,
    TurnStatus,
    User,
    new_id,
    utc_now,
)

# 演示级检索打分使用的常见词 bigram 停用表：这些 CJK bigram 在问题与文档中高频共现，
# 但缺乏领域区分度（如 “问题”“配置”“策略”），计入会造成无依据问题误命中走拒答失败。
# M1-04 接入向量检索后将由语义相似度门禁替代，停用表可下线。
_STOPWORD_BIGRAMS = frozenset({"问题", "配置", "策略"})


def _record_trash(
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    **kwargs,
) -> None:
    """把软删除同步进回收站投影（v3_002_content）。

    投影失败不回滚业务删除——源表才是真值——但必须留下结构化日志，
    否则会重演 auth.py 那种“静默 pass 导致坏了半天没人发现”的问题。
    """
    from ekb_api.core.logging import get_logger  # noqa: PLC0415 - 避免顶层循环依赖
    from ekb_api.services.v3_trash import record_deletion  # noqa: PLC0415

    try:
        record_deletion(tenant_id, resource_type, resource_id, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 投影失败不应阻断删除
        get_logger("ekb.store").warning(
            "trash.projection_failed",
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            error=repr(exc),
        )


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _as_int(value, default: int = 0) -> int:
    """安全转整型：None / 空 / 非数字（如旧迁移脏数据 'default'）一律回落 default。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _keyword_score(query: str, content: str) -> float:
    """演示级关键词打分：整词命中 + CJK bigram 命中。M1-04 将被 BM25/RRF/Rerank 替代。

    命中判定（决定是否走拒答）：整词命中 ≥1 或 非停用词 CJK bigram 命中 ≥1。
      - ASCII 2-gram（in/or/ss 等）不触发命中：避免 worker_processes/TensorFlow 等无依据问题误命中。
      - 停用词 CJK bigram（问题/配置/策略）不触发命中：避免常见词噪声导致拒答失效。
    排序分（决定多 chunk 时 _generate 取哪条）：整词 + CJK bigram（含停用词，保留区分度信号）
      + ASCII bigram（保留 allkeys-lru/redis 等技术词信号），用于在已命中 chunk 间排序。
    """
    query_terms = [term for term in query.lower().split() if term]
    haystack = content.lower()
    term_hits = sum(1 for term in query_terms if term in haystack)

    normalized_query = "".join(char for char in query.lower() if char.isalnum())
    normalized_haystack = "".join(char for char in haystack if char.isalnum())
    cjk_hits = 0  # 含停用词，用于排序
    cjk_hits_keep = 0  # 不含停用词，用于命中判定
    ascii_hits = 0  # 仅用于排序
    if len(normalized_query) >= 2:
        for index in range(len(normalized_query) - 1):
            bigram = normalized_query[index : index + 2]
            if bigram not in normalized_haystack:
                continue
            if _is_cjk(bigram[0]) and _is_cjk(bigram[1]):
                cjk_hits += 1
                if bigram not in _STOPWORD_BIGRAMS:
                    cjk_hits_keep += 1
            else:
                ascii_hits += 1

    if term_hits == 0 and cjk_hits_keep == 0:
        return 0.0
    return float(term_hits + cjk_hits + ascii_hits)


def _decode_preview_text(raw_bytes: bytes) -> str:
    """M1-04 前的 demo 级解析，已被 ekb_api.parsing 替代。保留供种子数据兼容。"""
    text = raw_bytes[:8000].decode("utf-8", errors="ignore").strip()
    if text:
        return text
    return "该文件已进入入库流程；真实解析器会在 M1-04 接入。"


@dataclass
class SearchContext:
    """M4-3 检索优化：预取的检索上下文，多路召回共享。

    避免每个子查询重复全表扫描 + 重复建 BM25 索引。
    """

    rows: list  # ORM Chunk rows
    corpus: list[str]
    bm25: Optional[object]  # _Bm25 实例或 None
    has_embeddings: bool
    settings: object  # Settings 实例


class SqlStore:
    """基于关系型数据库的持久化存储，替代 M1 tracer 的内存仓储。

    保持与原 InMemoryStore 完全相同的方法签名，并返回 ekb_api.domain 中的 dataclass，
    因此所有 router 无需改动。本地默认 SQLite，生产可通过 EKB_DATABASE_URL 切到 PostgreSQL。
    """

    # ---- 身份属性（供 auth/me 路由直接读取 dev 主体）----

    @property
    def user(self) -> User:
        from ekb_api.core.config import get_settings

        settings = get_settings()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.query(models.User).filter_by(email=settings.dev_user_email).first()
            if row is None:
                raise RuntimeError("dev 用户尚未初始化，请确认 init_db 已执行。")
            return User(id=row.id, name=row.name, email=row.email)

    @property
    def tenant(self) -> Tenant:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.query(models.Tenant).order_by(models.Tenant.created_at).first()
            if row is None:
                raise RuntimeError("dev 租户尚未初始化，请确认 init_db 已执行。")
            return Tenant(
                id=row.id,
                name=row.name,
                role=TenantRole(row.role),
                policy_version=row.policy_version,
            )

    def get_user_by_email(self, email: str) -> User | None:
        """按邮箱查询用户（M1-2 真实鉴权）；返回领域 User（含 role/tenant_id）。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.query(models.User).filter_by(email=email).first()
            if row is None:
                return None
            return User(
                id=row.id,
                name=row.name,
                email=row.email,
                role=row.role or "OWNER",
                tenant_id=row.tenant_id,
                password_hash=row.password_hash,
            )

    def get_tenant_by_id(self, tenant_id: str | None) -> Tenant | None:
        """按 id 查询租户（M1-2 真实鉴权）；不存在返回 None。"""
        if not tenant_id:
            return None
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.query(models.Tenant).filter_by(id=tenant_id).first()
            if row is None:
                return None
            return Tenant(
                id=row.id,
                name=row.name,
                role=TenantRole(row.role),
                policy_version=row.policy_version,
                model_routing_key=row.model_routing_key,
                egress_policy=row.egress_policy,
                quota_daily_qa=_as_int(row.quota_daily_qa, 0),
                quota_storage_docs=_as_int(row.quota_storage_docs, 0),
                quota_storage_bytes_per_file=_as_int(
                    getattr(row, "quota_storage_bytes_per_file", 0), 0
                ),
            )

    def create_user(self, tenant_id: str, email: str, name: str, password: str, role: str) -> User:
        """M2-2 创建用户并同步 v3 成员目录投影。"""
        from ekb_api.core.security import hash_password

        now = utc_now()
        user_id = new_id()
        role_slug = role.lower()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            user = models.User(
                id=user_id,
                name=name,
                email=email,
                tenant_id=tenant_id,
                role=role,
                password_hash=hash_password(password),
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.flush()

            v3_tables = {"tenant_roles", "tenant_memberships", "user_profiles"}
            available_tables = set(inspect(session.get_bind()).get_table_names())
            if v3_tables.issubset(available_tables):
                role_id = session.execute(
                    text(
                        "SELECT id FROM tenant_roles "
                        "WHERE tenant_id = :tenant_id AND slug = :role_slug"
                    ),
                    {"tenant_id": tenant_id, "role_slug": role_slug},
                ).scalar_one_or_none()
                if role_id is not None:
                    session.execute(
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
                            "id": new_id(),
                            "tenant_id": tenant_id,
                            "user_id": user_id,
                            "role_id": role_id,
                            "joined_at": now,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
                    session.execute(
                        text(
                            """
                            INSERT INTO user_profiles
                                (user_id, tenant_id, display_name, department, locale, timezone,
                                 avatar_url, created_at, updated_at)
                            VALUES
                                (:user_id, :tenant_id, :display_name, NULL, 'zh-CN',
                                 'Asia/Shanghai',
                                 NULL, :created_at, :updated_at)
                            """
                        ),
                        {
                            "user_id": user_id,
                            "tenant_id": tenant_id,
                            "display_name": name,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
            session.commit()
            session.refresh(user)
            return User(
                id=user.id,
                name=user.name,
                email=user.email,
                role=user.role,
                tenant_id=user.tenant_id,
            )

    # ---- 租户 ----

    def list_tenants(self) -> list[Tenant]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            return [
                Tenant(
                    id=r.id,
                    name=r.name,
                    role=TenantRole(r.role),
                    policy_version=r.policy_version,
                    model_routing_key=r.model_routing_key,
                    egress_policy=r.egress_policy,
                    quota_daily_qa=_as_int(r.quota_daily_qa, 0),
                    quota_storage_docs=_as_int(r.quota_storage_docs, 0),
                    quota_storage_bytes_per_file=_as_int(
                        getattr(r, "quota_storage_bytes_per_file", 0), 0
                    ),
                )
                for r in session.query(models.Tenant).all()
            ]

    # ---- 知识库 ----

    def _kb_visible_to(self, row, auth: AuthContext, memberships: dict) -> bool:
        """M2-2/3 同源可见性判定：PUBLIC 全租户；TEAM 同租户；PRIVATE 仅成员或租户 OWNER/ADMIN。"""
        if row.visibility == KbVisibility.PUBLIC.value:
            return True
        if row.tenant_id != auth.tenant_id:
            return False
        if row.visibility == KbVisibility.TEAM.value:
            return True
        # PRIVATE
        if row.id in memberships:
            return True
        if auth.tenant_role.value in {TenantRole.OWNER.value, TenantRole.ADMIN.value}:
            return True
        return False

    def list_knowledge_bases(self, auth: AuthContext) -> list[KnowledgeBase]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.KnowledgeBase)
                .filter(models.KnowledgeBase.deleted_at.is_(None))
                .all()
            )
            memberships = {
                m.kb_id: m.role
                for m in session.query(models.KbMembership)
                .filter_by(user_id=auth.actor_id, tenant_id=auth.tenant_id)
                .all()
            }
            return [
                _to_knowledge_base(r) for r in rows if self._kb_visible_to(r, auth, memberships)
            ]

    def create_knowledge_base(
        self,
        auth: AuthContext,
        name: str,
        description: str,
        visibility: KbVisibility,
    ) -> KnowledgeBase:
        now = utc_now()
        kb_id = new_id()
        kb = models.KnowledgeBase(
            id=kb_id,
            tenant_id=auth.tenant_id,
            name=name,
            description=description,
            visibility=visibility.value,
            role=KbRole.OWNER.value,
            document_count=0,
            deleted_at=None,
            created_at=now,
            updated_at=now,
        )
        membership = models.KbMembership(
            id=new_id(),
            tenant_id=auth.tenant_id,
            kb_id=kb_id,
            user_id=auth.actor_id,
            role=KbRole.OWNER.value,
            granted_by=auth.actor_id,
            created_at=now,
            updated_at=now,
        )
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            session.add_all([kb, membership])
            session.commit()
            session.refresh(kb)
            return _to_knowledge_base(kb)

    def update_knowledge_base(
        self,
        auth: AuthContext,
        kb_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        visibility: Optional[KbVisibility] = None,
    ) -> Optional[KnowledgeBase]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.KnowledgeBase)
                .filter_by(id=kb_id, tenant_id=auth.tenant_id, deleted_at=None)
                .first()
            )
            if row is None:
                return None
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if visibility is not None:
                row.visibility = visibility.value
            row.updated_at = utc_now()
            session.commit()
            session.refresh(row)
            return _to_knowledge_base(row)

    def delete_knowledge_base(self, auth: AuthContext, kb_id: str) -> bool:
        """软删除知识库；关联文档同步标记 DELETED，检索不再命中。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.KnowledgeBase)
                .filter_by(id=kb_id, tenant_id=auth.tenant_id, deleted_at=None)
                .first()
            )
            if row is None:
                return False
            kb_name = row.name
            row.deleted_at = now
            row.updated_at = now
            session.query(models.Document).filter_by(kb_id=kb_id).update(
                {"status": DocumentStatus.DELETED.value, "updated_at": now},
                synchronize_session=False,
            )
            session.commit()
        # 回收站投影：知识库本身入站；级联删除的文档不单独入站，
        # 恢复知识库时一并恢复，避免回收站被子文档刷屏。
        _record_trash(
            auth.tenant_id,
            "KB",
            kb_id,
            title=kb_name,
            deleted_by=auth.actor_id,
            deleted_at=now,
            metadata={"cascade": "documents"},
        )
        return True

    def get_knowledge_base(self, auth: AuthContext, kb_id: str) -> Optional[KnowledgeBase]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = session.query(models.KnowledgeBase).filter_by(id=kb_id, deleted_at=None).first()
            if not row:
                return None
            memberships = {
                m.kb_id: m.role
                for m in session.query(models.KbMembership)
                .filter_by(user_id=auth.actor_id, tenant_id=auth.tenant_id)
                .all()
            }
            if not self._kb_visible_to(row, auth, memberships):
                return None
            return _to_knowledge_base(row)

    # ---- 知识库成员（M2-2 ACL）----

    def get_kb_membership(self, auth: AuthContext, kb_id: str) -> Optional[KbMembership]:
        """返回当前主体在指定知识库上的成员角色（无则返回 None）。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.KbMembership)
                .filter_by(kb_id=kb_id, user_id=auth.actor_id, tenant_id=auth.tenant_id)
                .first()
            )
            return _to_kb_membership(row) if row else None

    def list_kb_members(self, kb_id: str) -> list[KbMembership]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.KbMembership)
                .filter_by(kb_id=kb_id)
                .order_by(models.KbMembership.created_at)
                .all()
            )
            return [_to_kb_membership(r) for r in rows]

    def grant_kb_access(
        self,
        auth: AuthContext,
        kb_id: str,
        user_email: str,
        role: KbRole,
    ) -> Optional[KbMembership]:
        """为某用户授予/更新知识库成员角色。仅 KB OWNER/ADMIN 可操作，且目标须同租户。

        返回新成员记录；目标用户不存在或跨租户时返回 None（不泄露是否存在）。
        """
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            kb = (
                session.query(models.KnowledgeBase)
                .filter_by(id=kb_id, tenant_id=auth.tenant_id, deleted_at=None)
                .first()
            )
            if kb is None:
                return None
            target = (
                session.query(models.User)
                .filter_by(email=user_email, tenant_id=auth.tenant_id)
                .first()
            )
            if target is None:
                return None
            existing = (
                session.query(models.KbMembership)
                .filter_by(kb_id=kb_id, user_id=target.id, tenant_id=auth.tenant_id)
                .first()
            )
            if existing is not None:
                existing.role = role.value
                existing.updated_at = now
                session.commit()
                session.refresh(existing)
                return _to_kb_membership(existing)
            membership = models.KbMembership(
                id=new_id(),
                tenant_id=auth.tenant_id,
                kb_id=kb_id,
                user_id=target.id,
                role=role.value,
                granted_by=auth.actor_id,
                created_at=now,
                updated_at=now,
            )
            session.add(membership)
            session.commit()
            session.refresh(membership)
            return _to_kb_membership(membership)

    def revoke_kb_access(self, auth: AuthContext, kb_id: str, user_id: str) -> bool:
        """撤销某用户的知识库成员角色。仅 KB OWNER/ADMIN 可操作。返回是否成功删除。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            affected = (
                session.query(models.KbMembership)
                .filter_by(kb_id=kb_id, user_id=user_id, tenant_id=auth.tenant_id)
                .delete(synchronize_session=False)
            )
            session.commit()
            return affected > 0

    # ---- 租户开通（M2-6）----

    def create_tenant_with_owner(
        self,
        name: str,
        owner_email: str,
        owner_name: str,
        owner_password: str,
        *,
        model_routing_key: str = "default",
        egress_policy: str = "allow",
        quota_daily_qa: int = 0,
        quota_storage_docs: int = 0,
        quota_storage_bytes_per_file: int = 0,
    ) -> tuple[Tenant, User]:
        """开通新租户并创建初始 OWNER 用户（口令 PBKDF2 哈希，明文不落库）。"""
        from ekb_api.core.security import hash_password

        now = utc_now()
        tenant_id = new_id()
        user_id = new_id()
        tenant = models.Tenant(
            id=tenant_id,
            name=name,
            role=TenantRole.OWNER.value,
            policy_version=1,
            model_routing_key=model_routing_key,
            egress_policy=egress_policy,
            quota_daily_qa=quota_daily_qa,
            quota_storage_docs=quota_storage_docs,
            quota_storage_bytes_per_file=quota_storage_bytes_per_file,
            created_at=now,
            updated_at=now,
        )
        user = models.User(
            id=user_id,
            name=owner_name,
            email=owner_email,
            tenant_id=tenant_id,
            role=TenantRole.OWNER.value,
            password_hash=hash_password(owner_password),
            created_at=now,
            updated_at=now,
        )
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            session.add_all([tenant, user])
            session.commit()
            return (
                Tenant(
                    id=tenant.id,
                    name=tenant.name,
                    role=TenantRole(tenant.role),
                    policy_version=tenant.policy_version,
                    model_routing_key=tenant.model_routing_key,
                    egress_policy=tenant.egress_policy,
                    quota_daily_qa=_as_int(tenant.quota_daily_qa, 0),
                    quota_storage_docs=_as_int(tenant.quota_storage_docs, 0),
                    quota_storage_bytes_per_file=_as_int(
                        tenant.quota_storage_bytes_per_file, 0
                    ),
                ),
                User(
                    id=user.id,
                    name=user.name,
                    email=user.email,
                    role=user.role,
                    tenant_id=user.tenant_id,
                ),
            )

    def check_and_increment_qa_quota(self, tenant_id: str) -> bool:
        """M2-7 每租户每日问答配额校验 + 计数（原子）。

        返回 True 表示允许本次问答（并计费），False 表示已超额（调用方应拒绝，如 429）。
        配额 0 表示不限；跨自然日自动清零。未知租户 fail-open 放行，避免阻断正常请求。
        注：SQLite 无 SELECT FOR UPDATE，依赖短会话串行写；生产 PG 可加行锁强化。
        """
        from ekb_api import models as _models

        today = utc_now()[:10]  # YYYY-MM-DD
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            tenant = session.query(_models.Tenant).filter_by(id=tenant_id).first()
            if tenant is None:
                return True
            # 容忍历史脏数据（如旧迁移把 'default' 写进整型列）：非数字一律按不限处理。
            quota = _as_int(tenant.quota_daily_qa, default=0)
            if quota <= 0:
                return True  # 不限
            usage = session.query(_models.TenantDailyUsage).filter_by(tenant_id=tenant_id).first()
            if usage is None or usage.usage_date != today:
                usage = _models.TenantDailyUsage(tenant_id=tenant_id, usage_date=today, qa_count=0)
                session.add(usage)
            if _as_int(usage.qa_count, default=0) >= quota:
                session.commit()
                return False
            usage.qa_count = _as_int(usage.qa_count, default=0) + 1
            session.commit()
            return True

    # ---- 文档 ----

    def list_documents(self, auth: AuthContext, kb_id: str) -> list[Document]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.Document)
                .filter_by(tenant_id=auth.tenant_id, kb_id=kb_id)
                .filter(models.Document.status != DocumentStatus.DELETED.value)
                .all()
            )
            return [_to_document(r) for r in rows]

    def get_document(self, auth: AuthContext, kb_id: str, doc_id: str) -> Optional[Document]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Document)
                .filter_by(id=doc_id, tenant_id=auth.tenant_id, kb_id=kb_id)
                .first()
            )
            if not row or row.status == DocumentStatus.DELETED.value:
                return None
            return _to_document(row)

    def delete_document(self, auth: AuthContext, kb_id: str, doc_id: str) -> bool:
        """软删除文档：标记 DELETED 并下线对应 chunk（检索已按 status 过滤）。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Document)
                .filter_by(id=doc_id, tenant_id=auth.tenant_id, kb_id=kb_id)
                .first()
            )
            if row is None or row.status == DocumentStatus.DELETED.value:
                return False
            doc_title = row.title
            row.status = DocumentStatus.DELETED.value
            row.updated_at = now
            kb = session.query(models.KnowledgeBase).filter_by(id=kb_id).with_for_update().first()
            kb_name = None if kb is None else kb.name
            if kb is not None and kb.document_count > 0:
                kb.document_count -= 1
                kb.updated_at = now
            session.commit()
        _record_trash(
            auth.tenant_id,
            "DOCUMENT",
            doc_id,
            title=doc_title,
            deleted_by=auth.actor_id,
            deleted_at=now,
            parent_id=kb_id,
            parent_title=kb_name,
        )
        return True

    def mark_document_failed(self, auth: AuthContext, kb_id: str, doc_id: str, reason: str) -> bool:
        """标记文档解析失败。供未来真实解析器在异常时调用，测试中也用于构造 FAILED 状态。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Document)
                .filter_by(id=doc_id, tenant_id=auth.tenant_id, kb_id=kb_id)
                .first()
            )
            if row is None or row.status == DocumentStatus.DELETED.value:
                return False
            row.status = DocumentStatus.FAILED.value
            row.failure_reason = reason
            row.updated_at = now
            session.commit()
            return True

    def retry_document(self, auth: AuthContext, kb_id: str, doc_id: str) -> Optional[Document]:
        """重试失败的文档：FAILED → READY/PROCESSING，清除 failure_reason。

        M1 单租户 MVP 不保存原始文件字节，无法真正重跑 ingest：
          - 已有 chunk：恢复 READY（既有 chunk 仍是有效证据，可检索）。
          - 无 chunk：恢复 PROCESSING（等待重新上传文件触发 ingest）。
        M2 引入对象存储后，此处改为重新获取 raw_bytes 并调用 ingest_document。
        非 FAILED 状态返回 None，调用方据此返回 409。
        """
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Document)
                .filter_by(id=doc_id, tenant_id=auth.tenant_id, kb_id=kb_id)
                .first()
            )
            if row is None or row.status == DocumentStatus.DELETED.value:
                return None
            if row.status != DocumentStatus.FAILED.value:
                return None

            existing_chunks = (
                session.query(models.Chunk)
                .filter_by(doc_id=doc_id, tenant_id=auth.tenant_id)
                .count()
            )
            # 有 chunk → 既有证据有效，直接 READY；无 chunk → 无法恢复，保持 PROCESSING 等重新上传。
            row.status = (
                DocumentStatus.READY.value
                if existing_chunks > 0
                else DocumentStatus.PROCESSING.value
            )
            row.failure_reason = None
            row.updated_at = now

            job = (
                session.query(models.IngestJob)
                .filter_by(doc_id=doc_id, tenant_id=auth.tenant_id)
                .order_by(models.IngestJob.updated_at.desc())
                .first()
            )
            if job is not None:
                job.status = "SUCCEEDED" if existing_chunks > 0 else "QUEUED"
                job.error_code = None
                job.error_message = None
                job.updated_at = now

            session.commit()
            session.refresh(row)
            return _to_document(row)

    def create_document_from_text(
        self,
        auth: AuthContext,
        kb_id: str,
        title: str,
        mime_type: str,
        raw_bytes: bytes,
        source_type: SourceType,
        idempotency_key: Optional[str] = None,
    ) -> tuple[Document, str]:
        """创建文档（PROCESSING 状态）+ ingest_job（QUEUED 状态），不执行解析。

        解析由 ingest_document 异步执行。返回 (document, job_id)。
        """
        SessionLocal = get_session_local()
        if idempotency_key:
            with SessionLocal() as session:
                existing = (
                    session.query(models.Document)
                    .filter_by(tenant_id=auth.tenant_id, idempotency_key=idempotency_key)
                    .filter(models.Document.status != DocumentStatus.DELETED.value)
                    .first()
                )
                if existing is not None:
                    return _to_document(existing), existing.id

        now = utc_now()
        checksum = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
        doc_id = new_id()
        job_id = new_id()
        doc = models.Document(
            id=doc_id,
            tenant_id=auth.tenant_id,
            kb_id=kb_id,
            title=title,
            status=DocumentStatus.PROCESSING.value,
            version=1,
            mime_type=mime_type,
            checksum=checksum,
            chunk_count=0,
            source_type=source_type.value,
            failure_reason=None,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        job = models.IngestJob(
            id=job_id,
            tenant_id=auth.tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            status="QUEUED",
            attempts=0,
            max_attempts=3,
            error_code=None,
            error_message=None,
            trace_id=auth.trace_id,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )

        with SessionLocal() as session:
            kb = session.query(models.KnowledgeBase).filter_by(id=kb_id).with_for_update().first()
            if kb is None:
                raise ValueError("knowledge base not found")
            session.add(doc)
            session.add(job)
            kb.document_count += 1
            kb.updated_at = now
            session.commit()
            session.refresh(doc)
            return _to_document(doc), job_id

    def ingest_document(
        self,
        auth: AuthContext,
        kb_id: str,
        doc_id: str,
        raw_bytes: bytes,
        mime_type: str,
        filename: str,
    ) -> None:
        """执行文档解析+切分，更新文档和 ingest_job 状态。

        成功 → 文档 READY + chunk 入库 + job SUCCEEDED。
        失败 → 文档 FAILED + failure_reason + job FAILED（attempts 计数）。
        可重试：attempts < max_attempts 时重试会重新执行解析+切分。
        """
        from ekb_api.chunking import split_sections
        from ekb_api.core.config import get_settings
        from ekb_api.embedding import EmbeddingError, embed_batch
        from ekb_api.parsing import ParseError, parse

        SessionLocal = get_session_local()
        now = utc_now()

        with SessionLocal() as session:
            job = (
                session.query(models.IngestJob)
                .filter_by(doc_id=doc_id, tenant_id=auth.tenant_id)
                .order_by(models.IngestJob.updated_at.desc())
                .first()
            )
            if job is not None:
                job.status = "RUNNING"
                job.attempts += 1
                job.updated_at = now
                session.commit()

        try:
            sections = parse(raw_bytes, mime_type, filename)
            chunk_results = split_sections(sections)
            # 仅在配置外部 Embedding API 时生成真实语义向量；未配置时 embedding=None，
            # 检索降级到关键词打分（保持 M1 评估基线 95% + 100% 拒答不回归）。
            # section_path + content 一起向量化，使章节标题参与语义匹配。
            embeddings: list[list[float]] = []
            if chunk_results and get_settings().embedding_enabled:
                embed_inputs = [
                    " / ".join(cr.section_path) + " " + cr.content for cr in chunk_results
                ]
                embeddings = embed_batch(embed_inputs)

            with SessionLocal() as session:
                session.query(models.Chunk).filter_by(
                    doc_id=doc_id, tenant_id=auth.tenant_id
                ).delete(synchronize_session=False)

                chunks = [
                    models.Chunk(
                        id=new_id(),
                        tenant_id=auth.tenant_id,
                        kb_id=kb_id,
                        doc_id=doc_id,
                        doc_version=1,
                        chunk_index=cr.chunk_index,
                        title=filename,
                        section_path=cr.section_path,
                        content=cr.content,
                        content_hash=cr.content_hash,
                        token_count=cr.token_count,
                        embedding=embeddings[index] if index < len(embeddings) else None,
                        created_at=now,
                        updated_at=now,
                    )
                    for index, cr in enumerate(chunk_results)
                ]
                session.add_all(chunks)

                doc_row = session.query(models.Document).filter_by(id=doc_id).first()
                if doc_row is not None:
                    doc_row.status = DocumentStatus.READY.value
                    doc_row.failure_reason = None
                    doc_row.chunk_count = len(chunks)
                    doc_row.updated_at = now

                # The job object loaded before parsing belongs to the first
                # session and is detached by the time this commit session is
                # opened.  Updating that detached instance silently leaves the
                # durable job in RUNNING, even though the document is READY.
                # Re-read it in the write session so document/chunks/job reach
                # one consistent terminal state.
                current_job = None
                if job is not None:
                    current_job = session.query(models.IngestJob).filter_by(
                        id=job.id, tenant_id=auth.tenant_id
                    ).first()
                if current_job is not None:
                    current_job.status = "SUCCEEDED"
                    current_job.error_code = None
                    current_job.error_message = None
                    current_job.updated_at = now

                # M3-6 创建文档版本快照（chunk 摘要列表），供版本对比与差异 diff。
                if doc_row is not None:
                    snapshot = [
                        {
                            "chunk_index": cr.chunk_index,
                            "section_path": list(cr.section_path),
                            "content_hash": cr.content_hash,
                            "content_preview": cr.content[:200],
                        }
                        for cr in chunk_results
                    ]
                    ver = models.DocumentVersion(
                        id=new_id(),
                        tenant_id=auth.tenant_id,
                        doc_id=doc_id,
                        version=doc_row.version,
                        checksum=doc_row.checksum,
                        chunk_count=len(chunks),
                        content_snapshot=snapshot,
                        created_at=now,
                    )
                    session.add(ver)

                session.commit()
        except ParseError as exc:
            self._mark_ingest_failed(auth, doc_id, job, "PARSE_ERROR", str(exc), now)
        except EmbeddingError as exc:
            self._mark_ingest_failed(auth, doc_id, job, "EMBEDDING_ERROR", str(exc), now)
        except Exception as exc:
            self._mark_ingest_failed(auth, doc_id, job, "INTERNAL_ERROR", str(exc), now)

    def _mark_ingest_failed(
        self,
        auth: AuthContext,
        doc_id: str,
        job: Optional[object],
        error_code: str,
        error_message: str,
        now: str,
    ) -> None:
        """标记文档和 ingest_job 为失败状态。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            doc_row = session.query(models.Document).filter_by(id=doc_id).first()
            if doc_row is not None:
                doc_row.status = DocumentStatus.FAILED.value
                doc_row.failure_reason = error_message
                doc_row.updated_at = now

            if job is not None:
                job_row = (
                    session.query(models.IngestJob)
                    .filter_by(id=job.id if hasattr(job, "id") else job)
                    .first()
                )
                if job_row is not None:
                    job_row.status = "FAILED"
                    job_row.error_code = error_code
                    job_row.error_message = error_message
                    job_row.updated_at = now

            session.commit()

    # ---- 检索 ----

    def _chunk_from_row(self, row) -> Chunk:
        """把 ORM chunk 行映射为 domain Chunk（score 由调用方填充）。"""
        return Chunk(
            id=row.id,
            tenant_id=row.tenant_id,
            kb_id=row.kb_id,
            doc_id=row.doc_id,
            doc_version=row.doc_version,
            title=row.title,
            section_path=list(row.section_path or []),
            content=row.content,
            score=0.0,
            updated_at=row.updated_at,
        )

    # ---- M4-3 检索优化：共享上下文 + 并行多路召回 ----

    def fetch_search_context(
        self, auth: AuthContext, kb_ids: list[str]
    ) -> Optional[SearchContext]:
        """一次性获取检索上下文：allowed_kb_ids + rows + BM25 索引 + embedding 可用性。

        M4-3 优化：多路召回时避免 N 次重复全表查询 + N 次重复建 BM25 索引。
        retrieve 层调用一次，各子查询共享同一上下文。
        """
        from ekb_api.core.config import get_settings
        from ekb_api.ranking import _Bm25

        allowed_kb_ids = {
            kb.id for kb in self.list_knowledge_bases(auth) if not kb_ids or kb.id in kb_ids
        }
        if not allowed_kb_ids:
            return None

        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.Chunk)
                .join(models.Document, models.Document.id == models.Chunk.doc_id)
                .filter(models.Document.status == DocumentStatus.READY.value)
                .filter(models.Chunk.tenant_id == auth.tenant_id)
                .filter(models.Chunk.kb_id.in_(allowed_kb_ids))
                .all()
            )
        if not rows:
            return None

        settings = get_settings()
        corpus = [f"{r.title} {' '.join(r.section_path or [])} {r.content}" for r in rows]
        bm25 = _Bm25(corpus) if settings.retrieval_bm25_enabled else None
        has_embeddings = any(r.embedding for r in rows)
        return SearchContext(
            rows=rows,
            corpus=corpus,
            bm25=bm25,
            has_embeddings=has_embeddings,
            settings=settings,
        )

    def search_with_context(
        self, ctx: SearchContext, query: str, top_k: int
    ) -> list[Chunk]:
        """在预取的检索上下文上执行单查询检索（BM25 + 语义门禁 + Rerank）。

        M4-3 优化：与 search() 逻辑等价，但复用 ctx 中预取的 rows/corpus/BM25，
        消除多路召回时的重复全表扫描和索引构建。
        """
        from ekb_api.embedding import cosine_similarity, embed_one
        from ekb_api.ranking import rerank

        rows = ctx.rows
        settings = ctx.settings

        # 词法 BM25（复用预建索引）。
        if ctx.bm25 is not None:
            lexical_scores = ctx.bm25.scores(query)
        else:
            lexical_scores = [0.0] * len(rows)

        # 语义/关键词代理分 + 候选门禁。
        semantic_scores = [0.0] * len(rows)
        candidates: set[int] = set()
        if ctx.has_embeddings:
            query_vec = embed_one(query)
            qdim = len(query_vec)
            threshold = settings.retrieval_cosine_threshold
            for i, row in enumerate(rows):
                if row.embedding and len(row.embedding) == qdim:
                    score = cosine_similarity(query_vec, row.embedding)
                    semantic_scores[i] = score
                    if score >= threshold:
                        candidates.add(i)
            if not candidates:
                for i in range(len(rows)):
                    if _keyword_score(query, ctx.corpus[i]) > 0:
                        candidates.add(i)
        else:
            for i in range(len(rows)):
                if _keyword_score(query, ctx.corpus[i]) > 0:
                    candidates.add(i)

        if not candidates:
            return []

        cand_idx = sorted(candidates)
        pool_chunks = [self._chunk_from_row(rows[i]) for i in cand_idx]
        pool_sem = [semantic_scores[i] for i in cand_idx]
        pool_lex = [lexical_scores[i] for i in cand_idx]
        reranked = rerank(
            query,
            pool_chunks,
            semantic_scores=pool_sem,
            lexical_scores=pool_lex,
            pool_size=len(pool_chunks),
            rrf_k=settings.retrieval_rrf_k,
        )
        return reranked[:top_k]

    def search(self, auth: AuthContext, query: str, kb_ids: list[str], top_k: int) -> list[Chunk]:
        """M1-05 混合检索：BM25 词法召回 + 语义召回 → RRF 融合 → Rerank 精排。

        门禁保留：仅对「已通过原门禁（余弦阈值或关键词命中）的候选集」做 BM25 重排，
        不引入门禁之外的 chunk，严格保持「无相关证据 → 空 → LLM 拒答」行为。
        """
        from ekb_api.core.config import get_settings
        from ekb_api.embedding import cosine_similarity, embed_one
        from ekb_api.ranking import _Bm25, rerank

        allowed_kb_ids = {
            kb.id for kb in self.list_knowledge_bases(auth) if not kb_ids or kb.id in kb_ids
        }
        if not allowed_kb_ids:
            return []

        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.Chunk)
                .join(models.Document, models.Document.id == models.Chunk.doc_id)
                .filter(models.Document.status == DocumentStatus.READY.value)
                .filter(models.Chunk.tenant_id == auth.tenant_id)
                .filter(models.Chunk.kb_id.in_(allowed_kb_ids))
                .all()
            )
        if not rows:
            return []

        settings = get_settings()
        corpus = [f"{r.title} {' '.join(r.section_path or [])} {r.content}" for r in rows]

        # 词法 BM25（全语料，作为重排信号）。
        if settings.retrieval_bm25_enabled:
            lexical_scores = _Bm25(corpus).scores(query)
        else:
            lexical_scores = [0.0] * len(rows)

        # 语义/关键词代理分 + 候选门禁（与原 search 行为一致，保留拒答）。
        has_embeddings = any(r.embedding for r in rows)
        semantic_scores = [0.0] * len(rows)
        candidates: set[int] = set()
        if has_embeddings:
            query_vec = embed_one(query)
            qdim = len(query_vec)
            threshold = settings.retrieval_cosine_threshold
            for i, row in enumerate(rows):
                # 维度不一致跳过（混合库：真 embedding 与本地降级向量共存）。
                if row.embedding and len(row.embedding) == qdim:
                    score = cosine_similarity(query_vec, row.embedding)
                    semantic_scores[i] = score
                    if score >= threshold:
                        candidates.add(i)
            # 语义无命中：回退关键词门禁（与原 _search_cosine 行为一致）。
            if not candidates:
                for i in range(len(rows)):
                    if _keyword_score(query, corpus[i]) > 0:
                        candidates.add(i)
        else:
            for i in range(len(rows)):
                if _keyword_score(query, corpus[i]) > 0:
                    candidates.add(i)

        if not candidates:
            return []

        # 池 = 全部门禁候选（不提前按语义分截断）。
        # 旧实现按语义分取前 retrieval_rerank_pool 名，无 embedding 时语义分恒为 0，
        # 该截断退化为「按 chunk 入库序号」截断，候选一旦超过池大小就会把相关 gold 提前丢弃
        # （真实 KB 文档较多时复现：复合/跨 SOP 题走拒答）。现改为把全部候选交给 rerank，
        # 由 rerank 用「语义排名 + 词法 BM25 排名 + 查询重合度」在池内精排，再 [:top_k]。
        # chunk.score 由 rerank 写回融合分，供 retrieve() 跨查询 max-score 融合使用。
        cand_idx = sorted(candidates)
        pool_chunks = [self._chunk_from_row(rows[i]) for i in cand_idx]
        pool_sem = [semantic_scores[i] for i in cand_idx]
        pool_lex = [lexical_scores[i] for i in cand_idx]
        reranked = rerank(
            query,
            pool_chunks,
            semantic_scores=pool_sem,
            lexical_scores=pool_lex,
            pool_size=len(pool_chunks),
            rrf_k=settings.retrieval_rrf_k,
        )
        return reranked[:top_k]

    # ---- 会话 ----

    def create_conversation(self, auth: AuthContext, title: str) -> Conversation:
        now = utc_now()
        conversation = models.Conversation(
            id=new_id(),
            tenant_id=auth.tenant_id,
            user_id=auth.actor_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return Conversation(
                id=conversation.id,
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
                title=conversation.title,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )

    def get_conversation(self, auth: AuthContext, conversation_id: str) -> Optional[Conversation]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Conversation)
                .filter_by(id=conversation_id, deleted_at=None)
                .first()
            )
            if not row or row.tenant_id != auth.tenant_id or row.user_id != auth.actor_id:
                return None
            return _to_conversation(row)

    def list_conversations(self, auth: AuthContext) -> list[Conversation]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.Conversation)
                .filter_by(tenant_id=auth.tenant_id, user_id=auth.actor_id, deleted_at=None)
                .order_by(models.Conversation.updated_at.desc())
                .all()
            )
            return [_to_conversation(r) for r in rows]

    def update_conversation(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        title: Optional[str] = None,
        archived: Optional[bool] = None,
    ) -> Optional[Conversation]:
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Conversation)
                .filter_by(id=conversation_id, deleted_at=None)
                .first()
            )
            if not row or row.tenant_id != auth.tenant_id or row.user_id != auth.actor_id:
                return None
            if title is not None:
                row.title = title
            if archived is not None:
                row.archived_at = now if archived else None
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return _to_conversation(row)

    def delete_conversation(self, auth: AuthContext, conversation_id: str) -> bool:
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Conversation)
                .filter_by(id=conversation_id, deleted_at=None)
                .first()
            )
            if not row or row.tenant_id != auth.tenant_id or row.user_id != auth.actor_id:
                return False
            conversation_title = row.title
            row.deleted_at = now
            row.updated_at = now
            session.commit()
        _record_trash(
            auth.tenant_id,
            "CONVERSATION",
            conversation_id,
            title=conversation_title,
            deleted_by=auth.actor_id,
            deleted_at=now,
        )
        return True

    def list_messages(self, auth: AuthContext, conversation_id: str) -> list[Message]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            conversation = session.query(models.Conversation).filter_by(id=conversation_id).first()
            if (
                not conversation
                or conversation.tenant_id != auth.tenant_id
                or conversation.user_id != auth.actor_id
            ):
                raise ValueError("conversation not accessible")
            rows = (
                session.query(models.Message)
                .filter_by(tenant_id=auth.tenant_id, conversation_id=conversation_id)
                # SSE v2: 只渲染 visible 消息，被取消/超时替代的占位消息不出现在历史里
                .filter(
                    models.Message.visibility_state.is_(None)
                    | (models.Message.visibility_state == MessageVisibility.VISIBLE.value)
                )
                .order_by(models.Message.created_at)
                .all()
            )
            return [_to_message(r) for r in rows]

    def get_message(self, auth: AuthContext, message_id: str) -> Optional[Message]:
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = session.query(models.Message).filter_by(id=message_id).first()
                if not row or row.tenant_id != auth.tenant_id:
                    return None
                return _to_message(row)
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.get_message failed",
                message_id=message_id,
                error=repr(exc),
            )
            return None

    def save_message(
        self,
        auth: AuthContext,
        conversation_id: str,
        role: str,
        content: str,
        *,
        turn_id: Optional[str] = None,
        visibility_state: str = MessageVisibility.VISIBLE.value,
    ) -> Message:
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        logger = get_logger("ekb.store")
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            message = models.Message(
                id=new_id(),
                tenant_id=auth.tenant_id,
                conversation_id=conversation_id,
                role=role,
                content=content,
                turn_id=turn_id,
                visibility_state=visibility_state,
                created_at=utc_now(),
            )
            session.add(message)
            session.commit()
            session.refresh(message)
            saved_message = _to_message(message)
            try:
                from ekb_api.migrations.v4_007_chat_graph import (  # noqa: PLC0415
                    message_content_hash,
                )

                bind = session.bind
                if bind is not None:
                    inspector = inspect(bind)
                    message_columns = {
                        column["name"] for column in inspector.get_columns("messages")
                    }
                    conversation_columns = {
                        column["name"]
                        for column in inspector.get_columns("conversations")
                    }
                    if {
                        "branch_id",
                        "parent_message_id",
                        "content_hash",
                    }.issubset(message_columns) and "active_branch_id" in conversation_columns \
                        and inspector.has_table("conversation_branches"):
                        params = {
                            "conversation_id": conversation_id,
                            "tenant_id": auth.tenant_id,
                            "actor_id": auth.actor_id,
                        }
                        branch_row = session.execute(
                            text(
                                "SELECT active_branch_id FROM conversations "
                                "WHERE id=:conversation_id AND tenant_id=:tenant_id "
                                "AND user_id=:actor_id AND deleted_at IS NULL"
                            ),
                            params,
                        ).first()
                        branch_id = branch_row[0] if branch_row is not None else None
                        if branch_row is not None and branch_id is None:
                            from ekb_api.core.db import get_engine  # noqa: PLC0415
                            from ekb_api.services.conversations import (  # noqa: PLC0415
                                ConversationGraphService,
                            )

                            root_branch = ConversationGraphService(get_engine()).ensure_root_branch(
                                tenant_id=auth.tenant_id,
                                conversation_id=conversation_id,
                                user_id=auth.actor_id,
                                connection=session.connection(),
                            )
                            session.execute(
                                text(
                                    "UPDATE conversations SET active_branch_id=:branch_id "
                                    "WHERE id=:conversation_id AND tenant_id=:tenant_id "
                                    "AND active_branch_id IS NULL"
                                ),
                                {
                                    "branch_id": root_branch.id,
                                    "conversation_id": conversation_id,
                                    "tenant_id": auth.tenant_id,
                                },
                            )
                            branch_id = root_branch.id
                        if branch_id is not None:
                            parent_row = None
                            if role.upper() == "ASSISTANT" and turn_id:
                                parent_row = session.execute(
                                    text(
                                        "SELECT id FROM messages "
                                        "WHERE tenant_id=:tenant_id "
                                        "AND conversation_id=:conversation_id "
                                        "AND branch_id=:branch_id AND turn_id=:turn_id "
                                        "AND role='USER' "
                                        "ORDER BY created_at ASC, id ASC LIMIT 1"
                                    ),
                                    {
                                        **params,
                                        "branch_id": branch_id,
                                        "turn_id": turn_id,
                                    },
                                ).first()
                            if parent_row is None:
                                parent_row = session.execute(
                                    text(
                                        "SELECT id FROM messages "
                                        "WHERE tenant_id=:tenant_id "
                                        "AND conversation_id=:conversation_id "
                                        "AND branch_id=:branch_id AND id<>:message_id "
                                        "ORDER BY created_at DESC, id DESC LIMIT 1"
                                    ),
                                    {
                                        **params,
                                        "branch_id": branch_id,
                                        "message_id": message.id,
                                    },
                                ).first()
                            parent = parent_row[0] if parent_row is not None else None
                            session.execute(
                                text(
                                    "UPDATE messages SET branch_id=:branch_id, "
                                    "parent_message_id=:parent, content_hash=:hash "
                                    "WHERE id=:message_id AND tenant_id=:tenant_id"
                                ),
                                {
                                    "branch_id": branch_id,
                                    "parent": parent,
                                    "hash": message_content_hash(role, content),
                                    "message_id": message.id,
                                    "tenant_id": auth.tenant_id,
                                },
                            )
                            session.commit()
                            session.refresh(message)
            except Exception as exc:  # noqa: BLE001 - legacy save must succeed
                try:
                    session.rollback()
                except Exception:  # noqa: BLE001 - fail-open after legacy save
                    pass
                logger.warning(
                    "save_message v4 branch bridge failed",
                    error=type(exc).__name__,
                )
                return saved_message
            return _to_message(message)

    def update_message_visibility(
        self,
        auth: AuthContext,
        message_id: str,
        visibility_state: str,
    ) -> bool:
        """SSE v2：切换消息可见性（如把取消前的占位消息标记 hidden）。"""
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = (
                    session.query(models.Message)
                    .filter_by(id=message_id, tenant_id=auth.tenant_id)
                    .first()
                )
                if row is None:
                    return False
                row.visibility_state = visibility_state
                session.commit()
                return True
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.update_message_visibility failed",
                message_id=message_id,
                error=repr(exc),
            )
            return False

    # ---- SSE v2 Turn Registry ----

    def create_turn(
        self,
        auth: AuthContext,
        *,
        turn_id: str,
        request_id: str,
        conversation_id: Optional[str],
        assistant_message_id: Optional[str],
        stream_version: int = 2,
    ) -> Optional[QaTurn]:
        """创建一个 QA Turn（流式回合），返回领域对象。失败返回 None，不阻断主流程。"""
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            now = utc_now()
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = models.QaTurn(
                    turn_id=turn_id,
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    actor_id=auth.actor_id,
                    conversation_id=conversation_id,
                    assistant_message_id=assistant_message_id,
                    stream_version=stream_version,
                    status=TurnStatus.RUNNING.value,
                    last_seq=0,
                    first_visible_at=None,
                    cancel_requested_at=None,
                    finish_reason=None,
                    created_at=now,
                    completed_at=None,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                return _to_qa_turn(row)
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.create_turn failed",
                turn_id=turn_id,
                request_id=request_id,
                error=repr(exc),
            )
            return None

    def get_turn(self, auth: AuthContext, turn_id: str) -> Optional[QaTurn]:
        """按 turn_id 查询 Turn；跨租户不可见。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.QaTurn)
                .filter_by(turn_id=turn_id, tenant_id=auth.tenant_id)
                .first()
            )
            return _to_qa_turn(row) if row else None

    def request_cancel_turn(self, auth: AuthContext, turn_id: str) -> tuple[bool, str]:
        """显式取消 Turn。

        返回 (accepted: bool, status: str)：
          - accepted=True, status="cancelled"：running 状态，标记取消
          - accepted=False, status="already_completed"：已结束，取消不生效
          - accepted=False, status="not_found"：不存在或无权限
        """
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.QaTurn)
                .filter_by(turn_id=turn_id, tenant_id=auth.tenant_id)
                .first()
            )
            if row is None:
                return False, "not_found"
            if row.status != TurnStatus.RUNNING.value:
                return False, "already_completed"
            row.cancel_requested_at = now
            # status 仍保持 running；流式循环检查 cancel_requested_at 后真正流转到 cancelled
            session.commit()
            return True, "cancelled"

    def is_turn_cancelled(self, turn_id: str) -> bool:
        """流式循环内轻量检查：某 turn 是否被请求取消（无 auth，因为流式上下文已持有权限）。

        注意：这是流式进程的协作取消检查点，不做权限判定；权限在 request_cancel_turn 处已校验。
        """
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = session.query(models.QaTurn).filter_by(turn_id=turn_id).first()
                if row is None:
                    return True  # Turn 不存在视为被取消，避免无限写流
                return row.cancel_requested_at is not None
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.is_turn_cancelled failed",
                turn_id=turn_id,
                error=repr(exc),
            )
            return False  # 异常时默认不取消，避免误杀流式响应

    def complete_turn(
        self,
        turn_id: str,
        *,
        status: str,
        finish_reason: str,
        last_seq: int,
    ) -> None:
        """回合结束：更新 status / finish_reason / last_seq / completed_at。"""
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            now = utc_now()
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = session.query(models.QaTurn).filter_by(turn_id=turn_id).first()
                if row is None:
                    return
                row.status = status
                row.finish_reason = finish_reason
                row.last_seq = last_seq
                row.completed_at = now
                session.commit()
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.complete_turn failed",
                turn_id=turn_id,
                error=repr(exc),
            )

    def increment_turn_seq(self, turn_id: str, *, to_seq: int) -> None:
        """流式发送时更新 last_seq（非严格加一，以调用方传入的 seq 为准）。"""
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = session.query(models.QaTurn).filter_by(turn_id=turn_id).first()
                if row is None:
                    return
                if to_seq > row.last_seq:
                    row.last_seq = to_seq
                session.commit()
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.increment_turn_seq failed",
                turn_id=turn_id,
                error=repr(exc),
            )

    def mark_turn_first_visible(self, turn_id: str) -> None:
        """首次可见 token 发出时落库，用于 TTFB 可观测性。"""
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            now = utc_now()
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = session.query(models.QaTurn).filter_by(turn_id=turn_id).first()
                if row is None:
                    return
                if row.first_visible_at is None:
                    row.first_visible_at = now
                session.commit()
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.mark_turn_first_visible failed",
                turn_id=turn_id,
                error=repr(exc),
            )

    def update_message_content(
        self,
        auth: AuthContext,
        message_id: str,
        content: str,
    ) -> Optional[Message]:
        from ekb_api.core.logging import get_logger  # noqa: PLC0415

        try:
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = (
                    session.query(models.Message)
                    .filter_by(id=message_id, tenant_id=auth.tenant_id)
                    .first()
                )
                if row is None:
                    return None
                row.content = content
                session.commit()
                session.refresh(row)
                return _to_message(row)
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.update_message_content failed",
                message_id=message_id,
                error=repr(exc),
            )
            return None

    def save_message_citations(
        self,
        auth: AuthContext,
        message_id: str,
        citations: list[dict],
    ) -> Optional[Message]:
        """PH6 FR-053：把本次回答的引用（含生成时文档版本/时间戳）持久化到消息元数据。

        写入消息的 ``citations`` 列，便于刷新/分支/版本回溯时复核引用指向的版本。
        失败仅告警不抛，引用持久化不是问答主链路的关键路径。
        """
        from ekb_api.core.logging import get_logger  # noqa: PLC0415
        try:
            SessionLocal = get_session_local()
            with SessionLocal() as session:
                row = (
                    session.query(models.Message)
                    .filter_by(id=message_id, tenant_id=auth.tenant_id)
                    .first()
                )
                if row is None:
                    return None
                row.citations = citations
                session.commit()
                session.refresh(row)
                return _to_message(row)
        except Exception as exc:  # noqa: BLE001
            get_logger("ekb.store").warning(
                "store.save_message_citations failed",
                message_id=message_id,
                error=repr(exc),
            )
            return None

    # ---- 反馈 ----

    def record_feedback(
        self,
        auth: AuthContext,
        message_id: str,
        rating: str,
        reason: str,
        comment: Optional[str],
    ) -> None:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            session.add(
                models.Feedback(
                    id=new_id(),
                    tenant_id=auth.tenant_id,
                    message_id=message_id,
                    user_id=auth.actor_id,
                    rating=rating,
                    reason=reason,
                    comment=comment or "",
                    created_at=utc_now(),
                )
            )
            session.commit()

    # ---- 审计 ----

    def write_audit_log(
        self,
        *,
        action: str,
        target_type: str,
        result: str,
        trace_id: str,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        target_id: Optional[str] = None,
        metadata_redacted: Optional[dict] = None,
        ip_hash: Optional[str] = None,
        user_agent_hash: Optional[str] = None,
    ) -> AuditLog:
        """追加一条审计记录。审计日志只追加、不修改，由调用方负责脱敏。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = models.AuditLog(
                id=new_id(),
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                result=result,
                trace_id=trace_id,
                ip_hash=ip_hash,
                user_agent_hash=user_agent_hash,
                metadata_redacted=metadata_redacted or {},
                created_at=utc_now(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            written = _to_audit_log(row)

        # The analytics projection is defined as a projection of audit_logs, so
        # it is fed from the same call site the backfill read from — that keeps
        # historical and live rows classified by identical rules.  Best effort:
        # analytics must never be able to fail an audited write.
        if result == "SUCCESS" and tenant_id and target_id:
            try:
                from ekb_api.services.v3_analytics import record_access

                record_access(
                    tenant_id,
                    target_type,
                    target_id,
                    action=action,
                    actor_id=actor_id,
                    trace_id=trace_id,
                    occurred_at=written.created_at,
                    source_ref=written.id,
                )
            except Exception:  # pragma: no cover - projection is non-critical
                pass
        return written

    def list_audit_logs(
        self,
        *,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        action: Optional[str] = None,
        result: Optional[str] = None,
        trace_id: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        page_size: int = 20,
        cursor: Optional[str] = None,
        include_platform_events: bool = False,
    ) -> tuple[list[AuditLog], Optional[str]]:
        """按授权范围和过滤条件查询审计日志，返回 (记录列表, 下一页游标)。

        游标使用最后一条记录的 created_at + id 组合，保证时间倒序翻页稳定。
        include_platform_events=True 时同时返回 tenant_id 为空的平台级事件（如失败登录）。
        """
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            query = session.query(models.AuditLog)
            if tenant_id is not None:
                if include_platform_events:
                    query = query.filter(
                        (models.AuditLog.tenant_id == tenant_id)
                        | (models.AuditLog.tenant_id.is_(None))
                    )
                else:
                    query = query.filter(models.AuditLog.tenant_id == tenant_id)
            if actor_id is not None:
                query = query.filter(models.AuditLog.actor_id == actor_id)
            if action is not None:
                query = query.filter(models.AuditLog.action == action)
            if result is not None:
                query = query.filter(models.AuditLog.result == result)
            if trace_id is not None:
                query = query.filter(models.AuditLog.trace_id == trace_id)
            if created_after is not None:
                query = query.filter(models.AuditLog.created_at >= created_after)
            if created_before is not None:
                query = query.filter(models.AuditLog.created_at <= created_before)
            if cursor:
                # 游标格式 "created_at|id"，按时间倒序翻页：取严格早于游标的记录。
                cursor_time, cursor_id = cursor.split("|", 1)
                query = query.filter(
                    (models.AuditLog.created_at < cursor_time)
                    | (
                        (models.AuditLog.created_at == cursor_time)
                        & (models.AuditLog.id < cursor_id)
                    )
                )

            rows = (
                query.order_by(models.AuditLog.created_at.desc(), models.AuditLog.id.desc())
                .limit(page_size + 1)
                .all()
            )

            next_cursor: Optional[str] = None
            if len(rows) > page_size:
                last = rows[page_size - 1]
                next_cursor = f"{last.created_at}|{last.id}"
                rows = rows[:page_size]
            return [_to_audit_log(r) for r in rows], next_cursor

    # ---- M3-1 运营看板 ----

    def get_ops_dashboard(self, auth: AuthContext, *, days: int = 7) -> dict:
        """聚合运营指标：问答量、准确率（finish_reason=stop/refusal）、拒答率、满意度、覆盖盲区。

        指标来源：audit_logs（qa.ask）、feedback（rating）、review_items（PENDING 盲区）。
        仅统计当前租户数据。
        """
        SessionLocal = get_session_local()
        # 时间窗口下界：created_at 存为 ISO-8601 Z 字符串，字典序与时间序一致，
        # 因此可以直接用字符串比较而不必解析每一行。
        from datetime import datetime as _datetime
        from datetime import timedelta as _timedelta
        from datetime import timezone as _timezone

        since = (
            (_datetime.now(_timezone.utc) - _timedelta(days=days))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with SessionLocal() as session:
            # 问答量按 finish_reason 分组
            qa_logs = (
                session.query(models.AuditLog)
                .filter(
                    models.AuditLog.tenant_id == auth.tenant_id,
                    models.AuditLog.action == "qa.ask",
                    models.AuditLog.created_at >= since,
                )
                .all()
            )
            total_qa = len(qa_logs)
            stop_count = sum(
                1 for log in qa_logs if log.metadata_redacted.get("finish_reason") == "stop"
            )
            refusal_count = sum(
                1 for log in qa_logs if log.metadata_redacted.get("finish_reason") == "refusal"
            )
            timeout_count = sum(
                1 for log in qa_logs if log.metadata_redacted.get("finish_reason") == "timeout"
            )
            error_count = sum(
                1 for log in qa_logs if log.metadata_redacted.get("finish_reason") == "error"
            )

            # 满意度：UP vs DOWN — 按 since 时间窗口过滤
            feedbacks = (
                session.query(models.Feedback)
                .filter(
                    models.Feedback.tenant_id == auth.tenant_id,
                    models.Feedback.created_at >= since,
                )
                .all()
            )
            up_count = sum(1 for f in feedbacks if f.rating == "UP")
            down_count = sum(1 for f in feedbacks if f.rating == "DOWN")
            satisfaction = (
                up_count / (up_count + down_count) if (up_count + down_count) > 0 else None
            )

            # 覆盖盲区：PENDING 审核项数（since 内新建的待审项）
            pending_reviews = (
                session.query(models.ReviewItem)
                .filter(
                    models.ReviewItem.tenant_id == auth.tenant_id,
                    models.ReviewItem.status == "PENDING",
                    models.ReviewItem.created_at >= since,
                )
                .count()
            )

            # 文档与知识库数
            kb_count = (
                session.query(models.KnowledgeBase)
                .filter(
                    models.KnowledgeBase.tenant_id == auth.tenant_id,
                    models.KnowledgeBase.deleted_at.is_(None),
                )
                .count()
            )
            doc_count = (
                session.query(models.Document)
                .filter(
                    models.Document.tenant_id == auth.tenant_id,
                    models.Document.status != DocumentStatus.DELETED.value,
                )
                .count()
            )

            success_total = stop_count + refusal_count
            accuracy = success_total / total_qa if total_qa > 0 else None
            refusal_rate = refusal_count / total_qa if total_qa > 0 else None

            return {
                "qa_volume": total_qa,
                "answered": stop_count,
                "refused": refusal_count,
                "timeout": timeout_count,
                "error": error_count,
                "accuracy": round(accuracy, 4) if accuracy is not None else None,
                "refusal_rate": round(refusal_rate, 4) if refusal_rate is not None else None,
                "satisfaction": round(satisfaction, 4) if satisfaction is not None else None,
                "feedback_up": up_count,
                "feedback_down": down_count,
                "pending_reviews": pending_reviews,
                "kb_count": kb_count,
                "doc_count": doc_count,
            }

    # ---- M3-2 反馈闭环 ----

    def list_feedback(
        self,
        auth: AuthContext,
        *,
        status: Optional[str] = None,
        rating: Optional[str] = None,
        page_size: int = 20,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """列出反馈记录（含状态与标注），按时间倒序分页。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            query = session.query(models.Feedback).filter(
                models.Feedback.tenant_id == auth.tenant_id
            )
            if status:
                query = query.filter(models.Feedback.status == status)
            if rating:
                query = query.filter(models.Feedback.rating == rating)
            if cursor:
                cursor_time, cursor_id = cursor.split("|", 1)
                query = query.filter(
                    (models.Feedback.created_at < cursor_time)
                    | (
                        (models.Feedback.created_at == cursor_time)
                        & (models.Feedback.id < cursor_id)
                    )
                )
            rows = (
                query.order_by(models.Feedback.created_at.desc(), models.Feedback.id.desc())
                .limit(page_size + 1)
                .all()
            )
            next_cursor = None
            if len(rows) > page_size:
                last = rows[page_size - 1]
                next_cursor = f"{last.created_at}|{last.id}"
                rows = rows[:page_size]
            return [
                {
                    "id": r.id,
                    "message_id": r.message_id,
                    "user_id": r.user_id,
                    "rating": r.rating,
                    "reason": r.reason,
                    "comment": r.comment,
                    "status": r.status or "PENDING",
                    "annotation": r.annotation,
                    "created_at": r.created_at,
                }
                for r in rows
            ], next_cursor

    def update_feedback_status(
        self, auth: AuthContext, feedback_id: str, status: str, annotation: Optional[str]
    ) -> Optional[dict]:
        """更新反馈状态与标注（PENDING → REVIEWED → RESOLVED）。不自动覆盖生产知识。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.Feedback)
                .filter_by(id=feedback_id, tenant_id=auth.tenant_id)
                .first()
            )
            if row is None:
                return None
            row.status = status
            if annotation is not None:
                row.annotation = annotation
            session.commit()
            session.refresh(row)
            return {
                "id": row.id,
                "message_id": row.message_id,
                "user_id": row.user_id,
                "rating": row.rating,
                "reason": row.reason,
                "comment": row.comment,
                "status": row.status or "PENDING",
                "annotation": row.annotation,
                "created_at": row.created_at,
            }

    # ---- M3-3 低置信度审核队列 ----

    def create_review_item(
        self,
        auth: AuthContext,
        *,
        conversation_id: Optional[str],
        message_id: Optional[str],
        question_preview: str,
        answer_preview: str,
        confidence: str,
        evidence_summary: list[dict],
    ) -> str:
        """自动入队一条低置信度审核项，返回 review_item_id。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            item = models.ReviewItem(
                id=new_id(),
                tenant_id=auth.tenant_id,
                conversation_id=conversation_id,
                message_id=message_id,
                question_preview=question_preview[:255],
                answer_preview=answer_preview[:2000],
                confidence=confidence,
                evidence_summary=evidence_summary,
                status="PENDING",
                resolution=None,
                resolved_by=None,
                created_at=now,
                updated_at=now,
            )
            session.add(item)
            session.commit()
            return item.id

    def list_review_items(
        self,
        auth: AuthContext,
        *,
        status: Optional[str] = None,
        page_size: int = 20,
        cursor: Optional[str] = None,
    ) -> tuple[list[ReviewItem], Optional[str]]:
        """列出审核队列条目，按创建时间倒序分页。"""

        SessionLocal = get_session_local()
        with SessionLocal() as session:
            query = session.query(models.ReviewItem).filter(
                models.ReviewItem.tenant_id == auth.tenant_id
            )
            if status:
                query = query.filter(models.ReviewItem.status == status)
            if cursor:
                cursor_time, cursor_id = cursor.split("|", 1)
                query = query.filter(
                    (models.ReviewItem.created_at < cursor_time)
                    | (
                        (models.ReviewItem.created_at == cursor_time)
                        & (models.ReviewItem.id < cursor_id)
                    )
                )
            rows = (
                query.order_by(models.ReviewItem.created_at.desc(), models.ReviewItem.id.desc())
                .limit(page_size + 1)
                .all()
            )
            next_cursor = None
            if len(rows) > page_size:
                last = rows[page_size - 1]
                next_cursor = f"{last.created_at}|{last.id}"
                rows = rows[:page_size]
            return [_to_review_item(r) for r in rows], next_cursor

    def get_review_item(self, auth: AuthContext, item_id: str) -> Optional[ReviewItem]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.ReviewItem)
                .filter_by(id=item_id, tenant_id=auth.tenant_id)
                .first()
            )
            return _to_review_item(row) if row else None

    def update_review_item(
        self,
        auth: AuthContext,
        item_id: str,
        *,
        status: str,
        resolution: Optional[str] = None,
    ) -> Optional[ReviewItem]:
        """流转审核项状态（PENDING → REVIEWED → RESOLVED），记录处理结论。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.ReviewItem)
                .filter_by(id=item_id, tenant_id=auth.tenant_id)
                .first()
            )
            if row is None:
                return None
            row.status = status
            if resolution is not None:
                row.resolution = resolution
            if status == "RESOLVED":
                row.resolved_by = auth.actor_id
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return _to_review_item(row)

    # ---- M3-5 存储配额 ----

    def get_effective_max_upload_bytes(self, tenant_id: str) -> int:
        """获取租户生效的单文件上传大小上限（字节）。

        优先级：租户级 quota_storage_bytes_per_file > 全局 settings.max_upload_bytes
        全局 0 表示不限制（返回 0）。
        """
        from ekb_api.core.config import get_settings

        SessionLocal = get_session_local()
        with SessionLocal() as session:
            tenant = session.query(models.Tenant).filter_by(id=tenant_id).first()
            if tenant is not None:
                per_file = _as_int(
                    getattr(tenant, "quota_storage_bytes_per_file", 0), 0
                )
                if per_file > 0:
                    return per_file
        settings = get_settings()
        return settings.max_upload_bytes

    def check_storage_quota(self, tenant_id: str) -> bool:
        """M3-5 检查租户文档数是否在配额内。返回 True 表示可以新增文档。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            tenant = session.query(models.Tenant).filter_by(id=tenant_id).first()
            if tenant is None:
                return True
            quota = _as_int(tenant.quota_storage_docs, 0)
            if quota <= 0:
                return True
            current = (
                session.query(models.Document)
                .filter(
                    models.Document.tenant_id == tenant_id,
                    models.Document.status != DocumentStatus.DELETED.value,
                )
                .count()
            )
            return current < quota

    # ---- M3-6 文档版本与差异 ----

    def create_document_version(
        self,
        auth: AuthContext,
        doc_id: str,
        version: int,
        checksum: str,
        chunk_count: int,
        content_snapshot: list[dict],
    ) -> str:
        """创建文档版本快照记录。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            ver = models.DocumentVersion(
                id=new_id(),
                tenant_id=auth.tenant_id,
                doc_id=doc_id,
                version=version,
                checksum=checksum,
                chunk_count=chunk_count,
                content_snapshot=content_snapshot,
                created_at=now,
            )
            session.add(ver)
            session.commit()
            return ver.id

    def list_document_versions(self, auth: AuthContext, doc_id: str) -> list[DocumentVersion]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            rows = (
                session.query(models.DocumentVersion)
                .filter_by(tenant_id=auth.tenant_id, doc_id=doc_id)
                .order_by(models.DocumentVersion.version.desc())
                .all()
            )
            return [_to_document_version(r) for r in rows]

    def get_document_diff(
        self, auth: AuthContext, doc_id: str, from_version: int, to_version: int
    ) -> dict:
        """对比两个版本的 chunk 差异：added / removed / changed。"""
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            from_ver = (
                session.query(models.DocumentVersion)
                .filter_by(tenant_id=auth.tenant_id, doc_id=doc_id, version=from_version)
                .first()
            )
            to_ver = (
                session.query(models.DocumentVersion)
                .filter_by(tenant_id=auth.tenant_id, doc_id=doc_id, version=to_version)
                .first()
            )
            if not from_ver or not to_ver:
                return {"error": "version not found"}

            from_map = {c["content_hash"]: c for c in (from_ver.content_snapshot or [])}
            to_map = {c["content_hash"]: c for c in (to_ver.content_snapshot or [])}

            added = [v for k, v in to_map.items() if k not in from_map]
            removed = [v for k, v in from_map.items() if k not in to_map]
            common = [v for k, v in to_map.items() if k in from_map]

            return {
                "from_version": from_version,
                "to_version": to_version,
                "from_chunk_count": len(from_map),
                "to_chunk_count": len(to_map),
                "added": added,
                "removed": removed,
                "unchanged": common,
            }

    # ---- M3-7 网盘/工单增量同步 ----

    def create_sync_source(
        self,
        auth: AuthContext,
        kb_id: str,
        name: str,
        source_type: str,
        source_url: str,
    ) -> SyncSource:
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            src = models.SyncSource(
                id=new_id(),
                tenant_id=auth.tenant_id,
                kb_id=kb_id,
                name=name,
                source_type=source_type,
                source_url=source_url,
                cursor=None,
                status="ACTIVE",
                last_sync_at=None,
                last_sync_count=0,
                error_message=None,
                retry_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(src)
            session.commit()
            session.refresh(src)
            return _to_sync_source(src)

    def list_sync_sources(self, auth: AuthContext, kb_id: Optional[str] = None) -> list[SyncSource]:
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            query = session.query(models.SyncSource).filter(
                models.SyncSource.tenant_id == auth.tenant_id
            )
            if kb_id:
                query = query.filter(models.SyncSource.kb_id == kb_id)
            rows = query.order_by(models.SyncSource.created_at.desc()).all()
            return [_to_sync_source(r) for r in rows]

    def record_sync_result(
        self,
        auth: AuthContext,
        source_id: str,
        *,
        success: bool,
        new_cursor: Optional[str] = None,
        synced_count: int = 0,
        error_message: Optional[str] = None,
    ) -> Optional[SyncSource]:
        """记录一次同步结果：成功则更新游标与计数，失败则累加重试次数并记录错误。"""
        now = utc_now()
        SessionLocal = get_session_local()
        with SessionLocal() as session:
            row = (
                session.query(models.SyncSource)
                .filter_by(id=source_id, tenant_id=auth.tenant_id)
                .first()
            )
            if row is None:
                return None
            if success:
                row.cursor = new_cursor or row.cursor
                row.last_sync_at = now
                row.last_sync_count = synced_count
                row.status = "ACTIVE"
                row.error_message = None
                row.retry_count = 0
            else:
                row.retry_count = _as_int(row.retry_count, 0) + 1
                row.error_message = error_message
                row.last_sync_at = now
                if _as_int(row.retry_count, 0) >= 3:
                    row.status = "FAILED"
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return _to_sync_source(row)


def _to_document(row: models.Document) -> Document:
    return Document(
        id=row.id,
        tenant_id=row.tenant_id,
        kb_id=row.kb_id,
        title=row.title,
        status=DocumentStatus(row.status),
        version=row.version,
        mime_type=row.mime_type,
        checksum=row.checksum,
        chunk_count=row.chunk_count,
        source_type=SourceType(row.source_type),
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_knowledge_base(row: models.KnowledgeBase) -> KnowledgeBase:
    return KnowledgeBase(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        description=row.description,
        visibility=KbVisibility(row.visibility),
        role=KbRole(row.role),
        document_count=row.document_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _to_conversation(row: models.Conversation) -> Conversation:
    return Conversation(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
        deleted_at=row.deleted_at,
    )


def _to_message(row: models.Message) -> Message:
    raw_citations = getattr(row, "citations", None)
    if isinstance(raw_citations, str):
        try:
            raw_citations = json.loads(raw_citations)
        except Exception:  # noqa: BLE001
            raw_citations = None
    return Message(
        id=row.id,
        tenant_id=row.tenant_id,
        conversation_id=row.conversation_id,
        role=row.role,
        content=row.content,
        created_at=row.created_at,
        turn_id=getattr(row, "turn_id", None),
        visibility_state=getattr(row, "visibility_state", "visible"),
        citations=raw_citations,
    )


def _to_qa_turn(row: models.QaTurn) -> QaTurn:
    return QaTurn(
        turn_id=row.turn_id,
        request_id=row.request_id,
        tenant_id=row.tenant_id,
        actor_id=row.actor_id,
        conversation_id=row.conversation_id,
        assistant_message_id=row.assistant_message_id,
        status=row.status,
        stream_version=row.stream_version,
        last_seq=row.last_seq,
        first_visible_at=row.first_visible_at,
        cancel_requested_at=row.cancel_requested_at,
        finish_reason=row.finish_reason,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def _to_audit_log(row: models.AuditLog) -> AuditLog:
    return AuditLog(
        id=row.id,
        tenant_id=row.tenant_id,
        actor_id=row.actor_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        result=row.result,
        trace_id=row.trace_id,
        metadata_redacted=dict(row.metadata_redacted or {}),
        created_at=row.created_at,
        ip_hash=row.ip_hash,
        user_agent_hash=row.user_agent_hash,
    )


def _to_kb_membership(row: models.KbMembership) -> KbMembership:
    return KbMembership(
        id=row.id,
        tenant_id=row.tenant_id,
        kb_id=row.kb_id,
        user_id=row.user_id,
        role=KbRole(row.role),
        granted_by=row.granted_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_review_item(row: models.ReviewItem) -> ReviewItem:
    from ekb_api.domain import ReviewStatus

    return ReviewItem(
        id=row.id,
        tenant_id=row.tenant_id,
        conversation_id=row.conversation_id,
        message_id=row.message_id,
        question_preview=row.question_preview,
        answer_preview=row.answer_preview,
        confidence=row.confidence,
        evidence_summary=list(row.evidence_summary or []),
        status=ReviewStatus(row.status or "PENDING"),
        resolution=row.resolution,
        resolved_by=row.resolved_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_document_version(row: models.DocumentVersion) -> DocumentVersion:
    return DocumentVersion(
        id=row.id,
        tenant_id=row.tenant_id,
        doc_id=row.doc_id,
        version=row.version,
        checksum=row.checksum,
        chunk_count=row.chunk_count,
        content_snapshot=list(row.content_snapshot or []),
        created_at=row.created_at,
    )


def _to_sync_source(row: models.SyncSource) -> SyncSource:
    return SyncSource(
        id=row.id,
        tenant_id=row.tenant_id,
        kb_id=row.kb_id,
        name=row.name,
        source_type=row.source_type,
        source_url=row.source_url,
        cursor=row.cursor,
        status=row.status,
        last_sync_at=row.last_sync_at,
        last_sync_count=row.last_sync_count,
        error_message=row.error_message,
        retry_count=row.retry_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )

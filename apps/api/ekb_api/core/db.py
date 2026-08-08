from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# 默认使用本地 SQLite 文件，零外部依赖即可跑通 M1 tracer；
# 生产环境通过 EKB_DATABASE_URL 指向 PostgreSQL（推荐启用 pgvector 扩展）。
DEFAULT_DATABASE_URL = "sqlite:///./ekb_dev.db"
DATABASE_URL = os.getenv("EKB_DATABASE_URL", DEFAULT_DATABASE_URL)

_engine = None
_SessionLocal = None
Base = declarative_base()


def _build_engine():
    connect_args: dict = {}
    if DATABASE_URL.startswith("sqlite"):
        # SQLite 单文件模式下允许跨线程复用连接（store 方法在请求线程内打开短会话）。
        connect_args = {"check_same_thread": False}
    return create_engine(DATABASE_URL, connect_args=connect_args, future=True, pool_pre_ping=True)


def get_engine():
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_local() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)
    return _SessionLocal


def init_db() -> None:
    """创建表结构（幂等）并在空库时种子化 dev 租户/用户/示例知识库。"""
    # 延迟导入，避免 models -> db 的顶层循环依赖。
    from ekb_api import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_user_columns(engine)
    _migrate_tenant_columns(engine)
    _migrate_feedback_columns(engine)
    _seed_if_empty()


def _migrate_user_columns(engine) -> None:
    """M1-2 向后兼容：为已存在的 users 表补加 password_hash/tenant_id/role 列。

    SQLite 的 ALTER 仅支持 ADD COLUMN，分段补齐即可，避免老库启动即报错。
    新列为可空，旧行的 role/tenant_id 为 NULL，由重种子或登录逻辑兜底。
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("users")}
    needed = {
        "password_hash": "VARCHAR(128)",
        "tenant_id": "VARCHAR(36)",
        "role": "VARCHAR(32)",
    }
    with engine.begin() as conn:
        for name, ddl in needed.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))


def _migrate_tenant_columns(engine) -> None:
    """M2-7/M3-5 向后兼容：为已存在的 tenants 表补加路由/出域/配额列。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("tenants")}
    # 每个列独立给出正确类型与缺省值；注意 quota_daily_qa 为整型，不得误用 'default' 字符串缺省。
    needed = {
        "model_routing_key": "VARCHAR(64) DEFAULT 'default'",
        "egress_policy": "VARCHAR(32) DEFAULT 'allow'",
        "quota_daily_qa": "INTEGER DEFAULT 0",
        "quota_storage_docs": "INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        for name, ddl in needed.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE tenants ADD COLUMN {name} {ddl}"))


def _migrate_feedback_columns(engine) -> None:
    """M3-2 向后兼容：为已存在的 feedback 表补加 status/annotation 列。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "feedback" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("feedback")}
    needed = {
        "status": "VARCHAR(32) DEFAULT 'PENDING'",
        "annotation": "TEXT",
    }
    with engine.begin() as conn:
        for name, ddl in needed.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE feedback ADD COLUMN {name} {ddl}"))


def _seed_if_empty() -> None:
    from ekb_api import models
    from ekb_api.core.config import get_settings
    from ekb_api.core.security import hash_password
    from ekb_api.domain import (
        DocumentStatus,
        KbRole,
        KbVisibility,
        SourceType,
        TenantRole,
        new_id,
        utc_now,
    )

    SessionLocal = get_session_local()
    settings = get_settings()
    with SessionLocal() as session:
        if session.query(models.Tenant).first():
            return

        now = utc_now()
        tenant = models.Tenant(
            id=new_id(),
            name=f"{settings.dev_user_name} 工作区",
            role=TenantRole.OWNER.value,
            policy_version=1,
            created_at=now,
            updated_at=now,
        )
        user = models.User(
            id=new_id(),
            name=settings.dev_user_name,
            email=settings.dev_user_email,
            tenant_id=tenant.id,
            role=TenantRole.OWNER.value,
            # M1-2 真实鉴权：以加盐 PBKDF2-SHA256 哈希存储口令，明文不落库。
            password_hash=hash_password(settings.dev_password) if settings.dev_password else None,
            created_at=now,
            updated_at=now,
        )
        session.add_all([tenant, user])
        session.flush()

        kb = models.KnowledgeBase(
            id=new_id(),
            tenant_id=tenant.id,
            name="运维 SOP",
            description="M1 内置示例知识库（脱敏，供评估集使用）",
            visibility=KbVisibility.PRIVATE.value,
            role=KbRole.OWNER.value,
            document_count=len(_SEED_SOPS),
            created_at=now,
            updated_at=now,
        )
        session.add(kb)
        session.flush()

        # M2-2：为种子知识库授予创建者（dev 用户）OWNER 成员，保证 ACL 可见性判定对 dev 账号成立。
        session.add(
            models.KbMembership(
                id=new_id(),
                tenant_id=tenant.id,
                kb_id=kb.id,
                user_id=user.id,
                role=KbRole.OWNER.value,
                granted_by=user.id,
                created_at=now,
                updated_at=now,
            )
        )

        all_chunks: list[models.Chunk] = []
        seed_chunk_contents: list[str] = []
        seed_chunk_refs: list[models.Chunk] = []
        for sop in _SEED_SOPS:
            doc = models.Document(
                id=new_id(),
                tenant_id=tenant.id,
                kb_id=kb.id,
                title=sop["title"],
                status=DocumentStatus.READY.value,
                version=1,
                mime_type="text/plain",
                checksum=f"sha256:seed-{sop['title']}",
                chunk_count=len(sop["chunks"]),
                source_type=SourceType.UPLOAD.value,
                failure_reason=None,
                idempotency_key=None,
                created_at=now,
                updated_at=now,
            )
            session.add(doc)
            session.flush()
            for section_path, content in sop["chunks"]:
                chunk = models.Chunk(
                    id=new_id(),
                    tenant_id=tenant.id,
                    kb_id=kb.id,
                    doc_id=doc.id,
                    doc_version=doc.version,
                    title=doc.title,
                    section_path=section_path,
                    content=content,
                    content_hash=None,
                    token_count=None,
                    embedding=None,
                    created_at=now,
                    updated_at=now,
                )
                all_chunks.append(chunk)
                seed_chunk_refs.append(chunk)
                seed_chunk_contents.append(content)
        session.add_all(all_chunks)

        # 仅在配置外部 Embedding API 时为种子 chunk 生成真实语义向量；
        # 未配置时 embedding=None，检索降级到关键词打分（保持 M1 评估基线不回归）。
        try:
            from ekb_api.core.config import get_settings
            from ekb_api.embedding import embed_batch

            if get_settings().embedding_enabled:
                embed_inputs = [
                    " / ".join(chunk_ref.section_path) + " " + content
                    for chunk_ref, content in zip(seed_chunk_refs, seed_chunk_contents)
                ]
                embeddings = embed_batch(embed_inputs)
                for chunk_ref, embedding in zip(seed_chunk_refs, embeddings):
                    chunk_ref.embedding = embedding
        except Exception:  # noqa: BLE001
            # embedding 失败不应阻断种子化；检索会降级到关键词 fallback。
            pass

        session.commit()


# 脱敏运维 SOP 种子数据：每篇含标题和多个 (章节路径, 内容) 片段。
# 内容为虚构示例，用于支撑 M1 评估集；不含真实生产信息。
_SEED_SOPS: list[dict] = [
    {
        "title": "数据库故障 SOP",
        "chunks": [
            (
                ["故障处理", "连接池耗尽"],
                (
                    "数据库连接池耗尽时，先检查连接池使用率、等待队列长度、慢查询和应用实例数。"
                    "其中慢查询会长期占用连接不释放，是连接池耗尽的主要诱因；"
                    "等待队列长度持续增长说明连接请求已超出连接池上限，应用拿不到空闲连接而开始排队。"
                ),
            ),
            (
                ["恢复步骤"],
                (
                    "若连接池持续耗尽，先限流非关键流量，再扩容应用实例，并保留 trace_id 供复盘。"
                    "扩容应用实例是通用的恢复手段（与 Redis 内存溢出时拆分大 key 或扩容同源），"
                    "其目标是将连接池使用率降至安全水位、恢复等待队列可控。"
                ),
            ),
        ],
    },
    {
        "title": "Redis 内存溢出 SOP",
        "chunks": [
            (
                ["故障处理", "内存诊断"],
                (
                    "Redis 内存溢出时，先执行 info memory 查看 used_memory_rss 和 "
                    "fragmentation ratio（内存碎片率），再排查大 key。"
                    "fragmentation ratio 过高说明存在明显的内存碎片，实际可用内存被碎片挤占。"
                ),
            ),
            (
                ["恢复步骤", "淘汰策略"],
                (
                    "确认 maxmemory-policy 配置；若为 allkeys-lru 可短期缓解，"
                    "长期需拆分大 key 或扩容。"
                ),
            ),
            (
                ["恢复步骤", "持久化影响"],
                (
                    "若开启 RDB/AOF，内存溢出可能触发 fork 失败，需评估 "
                    "repl-backlog-size 和 aof-rewrite-incremental-fsync。"
                    "aof-rewrite-incremental-fsync 用于评估 fork 阻塞/磁盘 IO 抖动场景，"
                    "开启后可将 AOF 重写期间的磁盘写入峰值摊薄，降低对主线程的阻塞。"
                ),
            ),
        ],
    },
    {
        "title": "网络延迟排查 SOP",
        "chunks": [
            (
                ["故障处理", "延迟定位"],
                (
                    "网络延迟突增时，先按 mtr 分段定位拥塞节点，"  # noqa: E501
                    "再检查交换机队列深度和网卡丢包计数。"
                    "其中网卡丢包计数升高说明该网卡存在收发异常，可能由链路质量下降、网卡硬件或驱动故障、"
                    "或上游拥塞导致；它是定位丢包来源与区分网络侧与主机侧问题的重要指标，"
                    "若仅该网卡丢包而同机其他链路正常，则指向本机网卡或直连链路故障。"
                ),
            ),
            (
                ["故障处理", "DNS 排查"],
                "若延迟仅在首请求出现，排查 DNS 解析耗时和本地 resolver 缓存命中率。"
                "本地 resolver 缓存命中率低的影响：首请求仍需向上游递归解析，"
                "DNS 解析耗时升高，表现为延迟仅在首次请求出现。",
            ),
            (
                ["恢复步骤"],
                "恢复顺序为：先确认是单链路或全网卡问题，再切换备用链路，"
                "然后通知上游限流，最后保留 pcap 供复盘。"
                "单链路问题和全网卡问题如何区分：单链路仅特定目标不可达、其余链路正常；"
                "全网卡则全部目标连通性普遍下降，需结合多目标探测判断。",
            ),
        ],
    },
    {
        "title": "部署回滚 SOP",
        "chunks": [
            (
                ["故障处理", "回滚判定"],
                (
                    "发布后核心指标（错误率、延迟、可用性）超阈值 5 分钟未恢复时，"
                    "立即触发回滚，不等根因定位。"
                ),
            ),
            (
                ["恢复步骤", "回滚执行"],
                (
                    "回滚时先切上一版本镜像，再迁移数据库变更；"
                    "破坏性迁移需先评估 forward-compatibility。"
                ),
            ),
        ],
    },
    {
        "title": "安全事件响应 SOP",
        "chunks": [
            (
                ["故障处理", "入侵隔离"],
                "确认主机被入侵后，先断网隔离保留现场，禁止直接重启以免丢失内存证据。",
            ),
            (
                ["恢复步骤", "取证流程"],
                "按顺序采集内存镜像、磁盘镜像和进程网络连接，全程记录操作时间和操作人，交接安全团队。",
            ),
            (
                ["恢复步骤", "复盘改进"],
                "事件闭环后 48 小时内输出复盘报告，更新检测规则和访问策略，并通知受影响用户。",
            ),
        ],
    },
]

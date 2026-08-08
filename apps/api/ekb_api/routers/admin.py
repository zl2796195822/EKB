from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request
from starlette import status

from ekb_api.core.audit import RESULT_FAILURE, RESULT_SUCCESS, extract_fingerprints, redact_metadata
from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.authorization import (
    CAP_AUDIT_READ,
    CAP_KB_READ,
    CAP_KB_WRITE,
    CAP_TENANT_PROVISION,
    assert_capability,
)
from ekb_api.core.config import get_settings
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext
from ekb_api.schemas import (
    OpsDashboardResponse,
    ReviewItemListResponse,
    ReviewItemResponse,
    ReviewItemUpdate,
    SyncRunResponse,
    SyncSourceCreate,
    SyncSourceResponse,
    TenantCreate,
    TenantResponse,
    UserInvite,
    UserInviteResponse,
)
from ekb_api.store import SqlStore

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/audit")
def list_audit_logs(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    tenant_id: Annotated[Optional[str], Query()] = None,
    actor_id: Annotated[Optional[str], Query()] = None,
    action: Annotated[Optional[str], Query()] = None,
    result: Annotated[Optional[str], Query()] = None,
    trace_id: Annotated[Optional[str], Query()] = None,
    created_after: Annotated[Optional[str], Query()] = None,
    created_before: Annotated[Optional[str], Query()] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[Optional[str], Query()] = None,
) -> dict[str, object]:
    assert_capability(auth, CAP_AUDIT_READ)

    # 非平台管理员只能查看本租户审计；平台管理员（platform_role != NONE）可跨租户。
    effective_tenant = tenant_id if auth.platform_role != "NONE" and tenant_id else auth.tenant_id

    logs, next_cursor = store.list_audit_logs(
        tenant_id=effective_tenant,
        actor_id=actor_id,
        action=action,
        result=result,
        trace_id=trace_id,
        created_after=created_after,
        created_before=created_before,
        page_size=page_size,
        cursor=cursor,
        # 平台级事件（如失败登录，tenant_id 为空）对审计读者可见，
        # 否则未认证事件将无法被任何管理员检索到。
        include_platform_events=True,
    )

    # 审计查询本身也要审计（Spec 行 254："访问本身也需审计"）。
    ip_hash, ua_hash = extract_fingerprints(request)
    store.write_audit_log(
        action="audit.query",
        target_type="audit_log",
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(
            {
                "filters": {
                    "tenant_id": tenant_id,
                    "actor_id": actor_id,
                    "action": action,
                    "result": result,
                    "trace_id": trace_id,
                },
                "page_size": page_size,
                "returned_count": len(logs),
            }
        ),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )

    return {
        "results": [
            {
                "id": log.id,
                "tenant_id": log.tenant_id,
                "actor_id": log.actor_id,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "result": log.result,
                "trace_id": log.trace_id,
                "metadata": log.metadata_redacted,
                "ip_hash": log.ip_hash,
                "user_agent_hash": log.user_agent_hash,
                "created_at": log.created_at,
            }
            for log in logs
        ],
        "next_cursor": next_cursor,
        "trace_id": auth.trace_id,
    }


# ---- 租户开通（M2-6）----


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> TenantResponse:
    assert_capability(auth, CAP_TENANT_PROVISION)
    tenant, _owner = store.create_tenant_with_owner(
        payload.name,
        payload.owner_email,
        payload.owner_name,
        payload.owner_password,
        model_routing_key=payload.model_routing_key,
        egress_policy=payload.egress_policy,
        quota_daily_qa=payload.quota_daily_qa,
        quota_storage_docs=payload.quota_storage_docs,
    )
    ip_hash, ua_hash = extract_fingerprints(request)
    store.write_audit_log(
        action="tenant.create",
        target_type="tenant",
        target_id=tenant.id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=tenant.id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(
            {"name": payload.name, "owner_email": payload.owner_email}
        ),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        role=tenant.role.value,
        model_routing_key=tenant.model_routing_key,
        egress_policy=tenant.egress_policy,
        quota_daily_qa=tenant.quota_daily_qa,
        quota_storage_docs=tenant.quota_storage_docs,
    )


@router.get("/tenants", response_model=list[TenantResponse])
def list_tenants(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[TenantResponse]:
    assert_capability(auth, CAP_TENANT_PROVISION)
    return [
        TenantResponse(
            id=t.id,
            name=t.name,
            role=t.role.value,
            model_routing_key=t.model_routing_key,
            egress_policy=t.egress_policy,
            quota_daily_qa=t.quota_daily_qa,
            quota_storage_docs=t.quota_storage_docs,
        )
        for t in store.list_tenants()
    ]


# ---- 租户内成员邀请（M2-2 团队/成员授权）----


@router.post("/users", response_model=UserInviteResponse, status_code=status.HTTP_201_CREATED)
def invite_user(
    payload: UserInvite,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> UserInviteResponse:
    assert_capability(auth, CAP_KB_WRITE)
    user = store.create_user(
        auth.tenant_id, payload.email, payload.name, payload.password, payload.role
    )
    ip_hash, ua_hash = extract_fingerprints(request)
    store.write_audit_log(
        action="user.invite",
        target_type="user",
        target_id=user.id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata({"email": payload.email, "role": payload.role}),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    return UserInviteResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        tenant_id=user.tenant_id,
    )


# ---- M3-1 运营看板 ----


@router.get("/ops/dashboard", response_model=OpsDashboardResponse)
def get_ops_dashboard(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> OpsDashboardResponse:
    """运营看板：问答量、准确率、拒答率、满意度、覆盖盲区。"""
    assert_capability(auth, CAP_AUDIT_READ)
    data = store.get_ops_dashboard(auth, days=days)
    return OpsDashboardResponse(**data)


# ---- M3-3 审核队列 ----


@router.get("/reviews", response_model=ReviewItemListResponse)
def list_review_items(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    status_filter: Annotated[Optional[str], Query(alias="status")] = None,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[Optional[str], Query()] = None,
) -> ReviewItemListResponse:
    """列出低置信度审核队列条目。"""
    assert_capability(auth, CAP_AUDIT_READ)
    items, next_cursor = store.list_review_items(
        auth, status=status_filter, page_size=page_size, cursor=cursor
    )
    return ReviewItemListResponse(
        results=[_to_review_response(item) for item in items],
        next_cursor=next_cursor,
    )


@router.get("/reviews/{item_id}", response_model=ReviewItemResponse)
def get_review_item(
    item_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> ReviewItemResponse:
    assert_capability(auth, CAP_AUDIT_READ)
    item = store.get_review_item(auth, item_id)
    if item is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    return _to_review_response(item)


@router.patch("/reviews/{item_id}", response_model=ReviewItemResponse)
def update_review_item(
    item_id: str,
    payload: ReviewItemUpdate,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> ReviewItemResponse:
    """流转审核项状态（PENDING → REVIEWED → RESOLVED）。"""
    assert_capability(auth, CAP_AUDIT_READ)
    item = store.update_review_item(
        auth, item_id, status=payload.status, resolution=payload.resolution
    )
    if item is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    return _to_review_response(item)


def _to_review_response(item) -> ReviewItemResponse:
    return ReviewItemResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        message_id=item.message_id,
        question_preview=item.question_preview,
        answer_preview=item.answer_preview,
        confidence=item.confidence,
        evidence_summary=item.evidence_summary,
        status=item.status.value if hasattr(item.status, "value") else str(item.status),
        resolution=item.resolution,
        resolved_by=item.resolved_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ---- M3-6 文档版本 ----


@router.get("/kb/{kb_id}/docs/{doc_id}/versions")
def list_document_versions(
    kb_id: str,
    doc_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[dict]:
    """列出文档版本快照。"""
    assert_capability(auth, CAP_KB_READ)
    versions = store.list_document_versions(auth, doc_id)
    return [
        {
            "id": v.id,
            "doc_id": v.doc_id,
            "version": v.version,
            "checksum": v.checksum,
            "chunk_count": v.chunk_count,
            "content_snapshot": v.content_snapshot,
            "created_at": v.created_at,
        }
        for v in versions
    ]


@router.get("/kb/{kb_id}/docs/{doc_id}/diff")
def get_document_diff(
    kb_id: str,
    doc_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    from_version: Annotated[int, Query(ge=1)] = 1,
    to_version: Annotated[int, Query(ge=1)] = 2,
) -> dict:
    """对比两个文档版本的 chunk 差异。"""
    assert_capability(auth, CAP_KB_READ)
    return store.get_document_diff(auth, doc_id, from_version, to_version)


# ---- M3-7 同步源 ----


@router.post(
    "/sync/sources",
    response_model=SyncSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sync_source(
    payload: SyncSourceCreate,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> SyncSourceResponse:
    """创建网盘/工单只读增量同步源。"""
    assert_capability(auth, CAP_KB_WRITE)
    src = store.create_sync_source(
        auth, payload.kb_id, payload.name, payload.source_type, payload.source_url
    )
    return _to_sync_response(src)


@router.get("/sync/sources", response_model=list[SyncSourceResponse])
def list_sync_sources(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    kb_id: Annotated[Optional[str], Query()] = None,
) -> list[SyncSourceResponse]:
    assert_capability(auth, CAP_KB_WRITE)
    sources = store.list_sync_sources(auth, kb_id=kb_id)
    return [_to_sync_response(s) for s in sources]


@router.post("/sync/sources/{source_id}/run", response_model=SyncRunResponse)
def run_sync_source(
    source_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> SyncRunResponse:
    """触发一次增量同步（M3-7 MVP：记录游标推进，实际拉取由外部适配器完成）。"""
    assert_capability(auth, CAP_KB_WRITE)
    sources = store.list_sync_sources(auth)
    src = next((s for s in sources if s.id == source_id), None)
    if src is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    # MVP：标记一次成功同步，游标推进到当前时间。
    from ekb_api.domain import utc_now

    updated = store.record_sync_result(
        auth,
        source_id,
        success=True,
        new_cursor=utc_now()[:10],
        synced_count=0,
    )
    if updated is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    return SyncRunResponse(
        source_id=source_id,
        status=updated.status,
        synced_count=updated.last_sync_count,
        error_message=updated.error_message,
    )


def _to_sync_response(src) -> SyncSourceResponse:
    return SyncSourceResponse(
        id=src.id,
        tenant_id=src.tenant_id,
        kb_id=src.kb_id,
        name=src.name,
        source_type=src.source_type,
        source_url=src.source_url,
        cursor=src.cursor,
        status=src.status,
        last_sync_at=src.last_sync_at,
        last_sync_count=src.last_sync_count,
        error_message=src.error_message,
        retry_count=src.retry_count,
        created_at=src.created_at,
        updated_at=src.updated_at,
    )


# ---- M4-6 备份恢复 ----


@router.post("/backup")
def trigger_backup(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict[str, object]:
    """触发数据库一致性快照备份（平台管理员能力）。

    用 VACUUM INTO 在线生成一致性快照，不阻塞写入。
    返回备份元数据（路径/大小/sha256/表行数），用于审计和恢复校验。
    """
    assert_capability(auth, CAP_TENANT_PROVISION)

    import sys
    from pathlib import Path

    # scripts/ 目录在 ekb_api 的上级，加入 path 以复用 backup.py。
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from backup import backup as do_backup  # noqa: E402

    settings = get_settings()
    out_dir = Path("./backups")
    ip_hash, ua_hash = extract_fingerprints(request)

    try:
        result = do_backup(settings.database_url, out_dir, keep=7)
    except Exception as exc:
        store.write_audit_log(
            action="admin.backup",
            target_type="database",
            target_id="sqlite",
            result=RESULT_FAILURE,
            trace_id=auth.trace_id,
            tenant_id=auth.tenant_id,
            actor_id=auth.actor_id,
            metadata_redacted=redact_metadata({"error": str(exc)[:200]}),
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
        )
        raise ApiError(500, "BACKUP_FAILED", f"备份失败: {exc}") from exc

    store.write_audit_log(
        action="admin.backup",
        target_type="database",
        target_id="sqlite",
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(
            {
                "backup_path": result.get("backup_path", ""),
                "backup_size_bytes": result.get("backup_size_bytes", 0),
                "backup_sha256": result.get("backup_sha256", "")[:16],
                "elapsed_seconds": result.get("elapsed_seconds", 0),
                "tables": len(result.get("tables", [])),
            }
        ),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )
    return result

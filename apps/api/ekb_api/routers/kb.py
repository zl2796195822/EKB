from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Request, UploadFile
from starlette import status

from ekb_api.core.audit import RESULT_SUCCESS, extract_fingerprints, redact_metadata
from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.authorization import (
    CAP_KB_WRITE,
    assert_capability,
    assert_kb_manager,
)
from ekb_api.core.errors import ApiError
from ekb_api.domain import AuthContext, SourceType
from ekb_api.schemas import (
    DocumentResponse,
    KbMemberCreate,
    KbMemberResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    UploadAcceptedResponse,
)
from ekb_api.store import SqlStore

router = APIRouter(prefix="/kb", tags=["kb"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
ALLOWED_MIME_PREFIXES = {
    "text/",
    "application/pdf",
    "application/msword",
    "application/vnd.",
}


@router.get("", response_model=list[KnowledgeBaseResponse])
def list_knowledge_bases(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[KnowledgeBaseResponse]:
    return [KnowledgeBaseResponse(**kb.__dict__) for kb in store.list_knowledge_bases(auth)]


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> KnowledgeBaseResponse:
    assert_capability(auth, CAP_KB_WRITE)
    kb = store.create_knowledge_base(auth, payload.name, payload.description, payload.visibility)
    _audit(request, auth, store, "kb.create", "knowledge_base", kb.id, {"name": payload.name})
    return KnowledgeBaseResponse(**kb.__dict__)


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(
    kb_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> KnowledgeBaseResponse:
    kb = store.get_knowledge_base(auth, kb_id)
    if not kb:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    return KnowledgeBaseResponse(**kb.__dict__)


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
def update_knowledge_base(
    kb_id: str,
    payload: KnowledgeBaseUpdate,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> KnowledgeBaseResponse:
    assert_capability(auth, CAP_KB_WRITE)
    kb = store.update_knowledge_base(
        auth,
        kb_id,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility,
    )
    if not kb:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    _audit(
        request,
        auth,
        store,
        "kb.update",
        "knowledge_base",
        kb_id,
        {
            "name": payload.name,
            "visibility": payload.visibility.value if payload.visibility else None,
        },
    )
    return KnowledgeBaseResponse(**kb.__dict__)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    kb_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> None:
    assert_capability(auth, CAP_KB_WRITE)
    if not store.delete_knowledge_base(auth, kb_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    _audit(request, auth, store, "kb.delete", "knowledge_base", kb_id)


@router.get("/{kb_id}/docs", response_model=list[DocumentResponse])
def list_documents(
    kb_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[DocumentResponse]:
    if not store.get_knowledge_base(auth, kb_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    return [_doc_response(doc) for doc in store.list_documents(auth, kb_id)]


@router.post(
    "/{kb_id}/docs",
    response_model=UploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    kb_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
    file: Annotated[UploadFile, File(...)],
    title: Annotated[Optional[str], Form()] = None,
    source_type: Annotated[SourceType, Form()] = SourceType.UPLOAD,
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
) -> UploadAcceptedResponse:
    assert_capability(auth, CAP_KB_WRITE)
    if not store.get_knowledge_base(auth, kb_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")

    # M3-5 存储配额：超额拒绝上传（429），不泄露已有文档数。
    if not store.check_storage_quota(auth.tenant_id):
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "STORAGE_QUOTA_EXCEEDED",
            "该租户文档数已达存储配额上限",
        )

    _validate_upload_metadata(file)
    raw_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ApiError(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "PAYLOAD_TOO_LARGE",
            "文件超过当前租户限制",
            {"max_bytes": MAX_UPLOAD_BYTES},
        )

    filename = title or file.filename or "未命名文档"
    mime_type = file.content_type or "application/octet-stream"
    doc, job_id = store.create_document_from_text(
        auth=auth,
        kb_id=kb_id,
        title=filename,
        mime_type=mime_type,
        raw_bytes=raw_bytes,
        source_type=source_type,
        idempotency_key=idempotency_key,
    )

    # 异步执行解析+切分，不阻塞 HTTP 响应。
    background_tasks.add_task(
        store.ingest_document,
        auth=auth,
        kb_id=kb_id,
        doc_id=doc.id,
        raw_bytes=raw_bytes,
        mime_type=mime_type,
        filename=filename,
    )

    _audit(
        request,
        auth,
        store,
        "doc.upload",
        "document",
        doc.id,
        {"kb_id": kb_id, "title": filename, "source_type": source_type.value},
    )
    return UploadAcceptedResponse(
        doc_id=doc.id,
        job_id=job_id,
        status=doc.status.value,
        trace_id=auth.trace_id,
    )


@router.get("/{kb_id}/docs/{doc_id}", response_model=DocumentResponse)
def get_document(
    kb_id: str,
    doc_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> DocumentResponse:
    doc = store.get_document(auth, kb_id, doc_id)
    if not doc:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    return _doc_response(doc)


@router.post("/{kb_id}/docs/{doc_id}/retry")
def retry_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict[str, str]:
    assert_capability(auth, CAP_KB_WRITE)
    # 先确认文档存在（404 与 409 区分，不泄露资源是否存在给无权限主体）。
    if not store.get_document(auth, kb_id, doc_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    retried = store.retry_document(auth, kb_id, doc_id)
    if retried is None:
        # 非 FAILED 状态不可重试（AC-US02-03：失败可恢复，但不应滥用重试覆盖正常状态）。
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "NOT_RETRYABLE",
            "仅 FAILED 状态文档可重试",
            {"kb_id": kb_id, "doc_id": doc_id},
        )
    _audit(request, auth, store, "doc.retry", "document", doc_id, {"kb_id": kb_id})
    return {"status": "accepted", "trace_id": auth.trace_id}


@router.delete("/{kb_id}/docs/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> None:
    assert_capability(auth, CAP_KB_WRITE)
    if not store.delete_document(auth, kb_id, doc_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    _audit(request, auth, store, "doc.delete", "document", doc_id, {"kb_id": kb_id})


# ---- 知识库成员（M2-2 ACL / 团队授权）----


def _kb_manager_or_403(auth: AuthContext, store: SqlStore, kb_id: str) -> None:
    """成员管理端点前置：要求 kb:write 且为 KB OWNER/ADMIN 成员。"""
    assert_capability(auth, CAP_KB_WRITE)
    membership = store.get_kb_membership(auth, kb_id)
    kb_role = membership.role if membership else None
    assert_kb_manager(auth, kb_role)


@router.get("/{kb_id}/members", response_model=list[KbMemberResponse])
def list_kb_members(
    kb_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> list[KbMemberResponse]:
    _kb_manager_or_403(auth, store, kb_id)
    return [
        KbMemberResponse(
            id=m.id,
            kb_id=m.kb_id,
            user_id=m.user_id,
            role=m.role.value,
            granted_by=m.granted_by,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
        for m in store.list_kb_members(kb_id)
    ]


@router.post(
    "/{kb_id}/members",
    response_model=KbMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_kb_member(
    kb_id: str,
    payload: KbMemberCreate,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> KbMemberResponse:
    _kb_manager_or_403(auth, store, kb_id)
    member = store.grant_kb_access(auth, kb_id, payload.email, payload.role)
    if member is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    _audit(
        request,
        auth,
        store,
        "kb.member.grant",
        "kb_membership",
        member.id,
        {"kb_id": kb_id, "user_email": payload.email, "role": payload.role.value},
    )
    return KbMemberResponse(
        id=member.id,
        kb_id=member.kb_id,
        user_id=member.user_id,
        role=member.role.value,
        granted_by=member.granted_by,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.delete("/{kb_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_kb_member(
    kb_id: str,
    user_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> None:
    _kb_manager_or_403(auth, store, kb_id)
    if not store.revoke_kb_access(auth, kb_id, user_id):
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "当前授权范围内不存在")
    _audit(
        request,
        auth,
        store,
        "kb.member.revoke",
        "kb_membership",
        user_id,
        {"kb_id": kb_id},
    )


def _validate_upload_metadata(file: UploadFile) -> None:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ApiError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "UNSUPPORTED_MEDIA_TYPE",
            "文件格式不支持",
            {"allowed_extensions": sorted(ALLOWED_EXTENSIONS)},
        )

    content_type = file.content_type or ""
    if not any(content_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES):
        raise ApiError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "UNSUPPORTED_MEDIA_TYPE",
            "文件类型不支持",
            {"content_type": content_type},
        )


def _doc_response(doc) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        kb_id=doc.kb_id,
        title=doc.title,
        status=doc.status,
        version=doc.version,
        mime_type=doc.mime_type,
        checksum=doc.checksum,
        chunk_count=doc.chunk_count,
        failure_reason=doc.failure_reason,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _audit(
    request: Request,
    auth: AuthContext,
    store: SqlStore,
    action: str,
    target_type: str,
    target_id: str,
    metadata: Optional[dict] = None,
) -> None:
    """统一的审计写入辅助：脱敏 metadata 后追加审计记录。"""
    ip_hash, ua_hash = extract_fingerprints(request)
    store.write_audit_log(
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=RESULT_SUCCESS,
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(metadata or {}),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )

"""Tenant/knowledge-base scoped folder tree with bounded cycle checks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette import status

from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.domain import utc_now

MAX_DEPTH = 32
_UNSET = object()


def _bad(message: str, code: str = "INVALID_ARGUMENT") -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, code, message)


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "文件夹不存在或无权访问")


@dataclass(frozen=True)
class FolderView:
    id: str
    tenant_id: str
    kb_id: str
    parent_id: Optional[str]
    name: str
    deleted_at: Optional[str]
    created_by: str
    created_at: str
    updated_at: str
    child_count: int = 0
    document_count: int = 0


def _row_view(row) -> FolderView:
    return FolderView(
        id=str(row[0]),
        tenant_id=str(row[1]),
        kb_id=str(row[2]),
        parent_id=None if row[3] is None else str(row[3]),
        name=str(row[4]),
        deleted_at=None if row[5] is None else str(row[5]),
        created_by=str(row[6]),
        created_at=str(row[7]),
        updated_at=str(row[8]),
        child_count=int(row[9] or 0),
        document_count=int(row[10] or 0),
    )


def _assert_kb(session, tenant_id: str, kb_id: str) -> None:
    found = session.execute(
        text(
            "SELECT 1 FROM knowledge_bases "
            "WHERE id=:kb AND tenant_id=:tenant AND deleted_at IS NULL"
        ),
        {"kb": kb_id, "tenant": tenant_id},
    ).first()
    if found is None:
        raise _not_found()


def _assert_parent(session, tenant_id: str, kb_id: str, parent_id: Optional[str]) -> None:
    if parent_id is None:
        return
    found = session.execute(
        text(
            "SELECT 1 FROM folders "
            "WHERE id=:id AND tenant_id=:tenant AND kb_id=:kb AND deleted_at IS NULL"
        ),
        {"id": parent_id, "tenant": tenant_id, "kb": kb_id},
    ).first()
    if found is None:
        raise _not_found()


def _folder_rows(
    session, tenant_id: str, kb_id: str, parent_id: Optional[str], include_deleted: bool
):
    filters = ["f.tenant_id=:tenant", "f.kb_id=:kb"]
    params = {"tenant": tenant_id, "kb": kb_id}
    if parent_id is None:
        filters.append("f.parent_id IS NULL")
    else:
        filters.append("f.parent_id=:parent")
        params["parent"] = parent_id
    if not include_deleted:
        filters.append("f.deleted_at IS NULL")
    return session.execute(
        text(
            "SELECT f.id,f.tenant_id,f.kb_id,f.parent_id,f.name,f.deleted_at,"
            "f.created_by,f.created_at,f.updated_at,"
            "(SELECT COUNT(*) FROM folders c WHERE c.tenant_id=f.tenant_id "
            "AND c.parent_id=f.id AND c.deleted_at IS NULL),"
            "(SELECT COUNT(*) FROM document_folder_links l JOIN documents d ON d.id=l.document_id "
            "WHERE l.tenant_id=f.tenant_id AND l.folder_id=f.id AND d.status <> 'DELETED') "
            "FROM folders f WHERE "
            + " AND ".join(filters)
            + " ORDER BY f.name COLLATE NOCASE, f.id"
        ),
        params,
    ).all()


def list_folders(
    tenant_id: str, kb_id: str, *, parent_id: Optional[str] = None, include_deleted: bool = False
) -> list[FolderView]:
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        _assert_kb(session, tenant_id, kb_id)
        _assert_parent(session, tenant_id, kb_id, parent_id)
        return [
            _row_view(row)
            for row in _folder_rows(session, tenant_id, kb_id, parent_id, include_deleted)
        ]


def create_folder(
    tenant_id: str, actor_id: str, kb_id: str, name: str, parent_id: Optional[str] = None
) -> FolderView:
    clean = " ".join(name.split())
    if not clean or len(clean) > 255:
        raise _bad("文件夹名称不能为空且不能超过 255 个字符")
    SessionLocal = get_session_local()
    now = utc_now()
    folder_id = str(uuid.uuid4())
    with SessionLocal() as session:
        _assert_kb(session, tenant_id, kb_id)
        _assert_parent(session, tenant_id, kb_id, parent_id)
        try:
            session.execute(
                text(
                    "INSERT INTO folders (id,tenant_id,kb_id,parent_id,name,deleted_at,"
                    "deleted_by,created_by,created_at,updated_at) "
                    "VALUES (:id,:tenant,:kb,:parent,:name,NULL,NULL,:actor,:now,:now)"
                ),
                {
                    "id": folder_id,
                    "tenant": tenant_id,
                    "kb": kb_id,
                    "parent": parent_id,
                    "name": clean,
                    "actor": actor_id,
                    "now": now,
                },
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ApiError(
                status.HTTP_409_CONFLICT, "FOLDER_NAME_CONFLICT", "同级文件夹名称已存在"
            ) from None
        row = session.execute(
            text(
                "SELECT id,tenant_id,kb_id,parent_id,name,deleted_at,created_by,"
                "created_at,updated_at,0,0 FROM folders WHERE id=:id"
            ),
            {"id": folder_id},
        ).first()
    return _row_view(row)


def _descendant_ids(session, tenant_id: str, kb_id: str, folder_id: str) -> set[str]:
    seen: set[str] = set()
    current = folder_id
    for _ in range(MAX_DEPTH + 1):
        if current in seen:
            raise _bad("检测到文件夹环路", "FOLDER_CYCLE")
        seen.add(current)
        row = session.execute(
            text(
                "SELECT id,parent_id FROM folders WHERE id=:id AND tenant_id=:tenant AND kb_id=:kb"
            ),
            {"id": current, "tenant": tenant_id, "kb": kb_id},
        ).first()
        if row is None or row[1] is None:
            return seen
        current = str(row[1])
    raise _bad("文件夹层级超过 32 层", "FOLDER_DEPTH_EXCEEDED")


def update_folder(
    tenant_id: str,
    folder_id: str,
    *,
    name: Optional[str] = None,
    parent_id: str | None | object = _UNSET,
) -> FolderView:
    clean = None if name is None else " ".join(name.split())
    if clean is not None and (not clean or len(clean) > 255):
        raise _bad("文件夹名称不能为空且不能超过 255 个字符")
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id,tenant_id,kb_id,parent_id,name,deleted_at,created_by,"
                "created_at,updated_at FROM folders WHERE id=:id AND tenant_id=:tenant"
            ),
            {"id": folder_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise _not_found()
        kb_id = str(row[2])
        requested_parent = row[3] if parent_id is _UNSET else parent_id
        if requested_parent == folder_id:
            raise _bad("文件夹不能移动到自身", "FOLDER_CYCLE")
        _assert_parent(session, tenant_id, kb_id, requested_parent)
        if requested_parent is not None and folder_id in _descendant_ids(
            session, tenant_id, kb_id, requested_parent
        ):
            raise _bad("文件夹不能移动到自己的后代", "FOLDER_CYCLE")
        try:
            session.execute(
                text(
                    "UPDATE folders SET name=COALESCE(:name,name), parent_id=:parent, "
                    "updated_at=:now WHERE id=:id AND tenant_id=:tenant"
                ),
                {
                    "name": clean,
                    "parent": requested_parent,
                    "now": now,
                    "id": folder_id,
                    "tenant": tenant_id,
                },
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            raise ApiError(
                status.HTTP_409_CONFLICT, "FOLDER_NAME_CONFLICT", "同级文件夹名称已存在"
            ) from None
        updated = session.execute(
            text(
                "SELECT id,tenant_id,kb_id,parent_id,name,deleted_at,created_by,"
                "created_at,updated_at,0,0 FROM folders WHERE id=:id"
            ),
            {"id": folder_id},
        ).first()
    return _row_view(updated)


def delete_folder(tenant_id: str, actor_id: str, folder_id: str) -> FolderView:
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id,tenant_id,kb_id,parent_id,name,deleted_at,created_by,"
                "created_at,updated_at FROM folders "
                "WHERE id=:id AND tenant_id=:tenant AND deleted_at IS NULL"
            ),
            {"id": folder_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise _not_found()
        session.execute(
            text(
                "UPDATE folders SET deleted_at=:now,deleted_by=:actor,updated_at=:now "
                "WHERE id=:id AND tenant_id=:tenant"
            ),
            {"now": now, "actor": actor_id, "id": folder_id, "tenant": tenant_id},
        )
        session.commit()
    return FolderView(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        None if row[3] is None else str(row[3]),
        str(row[4]),
        now,
        str(row[6]),
        str(row[7]),
        now,
    )

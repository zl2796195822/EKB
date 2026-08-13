"""Recycle bin service over the ``trash_items`` projection (v3_002_content).

The projection is never the source of truth.  ``knowledge_bases``,
``documents`` and ``conversations`` remain authoritative; this module keeps the
projection in sync and writes restores back to the owning table inside one
transaction.

Column names here are pinned to the DDL locked in ``v3_002_content``.  The
migration checksum cannot change, so any mismatch must be fixed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text
from starlette import status

from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.domain import utc_now
from ekb_api.migrations.v3_002_content import TRASH_RETENTION_DAYS, trash_id_for

RESOURCE_TYPES = ("KB", "DOCUMENT", "CONVERSATION")

#: Restoring a document is refused while its knowledge base is still deleted —
#: otherwise the document would come back invisible.
_ORPHAN_PARENT = "parent_deleted"


@dataclass(frozen=True)
class TrashItem:
    id: str
    resource_type: str
    resource_id: str
    title: str
    parent_id: Optional[str]
    parent_title: Optional[str]
    deleted_by: Optional[str]
    deleted_at: str
    expires_at: str
    restorable: bool
    blocked_reason: Optional[str]


@dataclass(frozen=True)
class TrashPage:
    items: list[TrashItem]
    total: int
    counts: dict[str, int]


@dataclass(frozen=True)
class _TrashRow:
    id: str
    tenant_id: str
    resource_type: str
    resource_id: str
    title: str
    parent_id: Optional[str]
    parent_title: Optional[str]
    deleted_by: Optional[str]
    deleted_at: str
    expires_at: str
    restored_at: Optional[str]
    purged_at: Optional[str]
    deletion_generation: int
    purge_state: str
    claim_fencing_token: int
    metadata: dict[str, Any]


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "回收站条目不存在或已被清理")


def _bad_request(message: str) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_ARGUMENT", message)


def _retention_expired() -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        "RETENTION_EXPIRED",
        "回收站保留期已结束，资源不可恢复或手动永久删除",
    )


def _purge_in_progress(state: str) -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        "PURGE_IN_PROGRESS",
        f"回收站条目正在清理，当前状态为 {state}，请稍后重试",
        {"purge_state": state},
    )


def _restore_stale() -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        "RESTORE_CONFLICT",
        "资源状态已变化，恢复操作未提交，请刷新回收站后重试",
    )


def _expires_after(deleted_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    moment = (parsed + timedelta(days=TRASH_RETENTION_DAYS)).replace(microsecond=0)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_expired(expires_at: Any, now: str) -> bool:
    expires = _parse_utc(expires_at)
    current = _parse_utc(now)
    # Invalid retention metadata fails closed: it cannot be restored or manually purged.
    return expires is None or current is None or expires <= current


def _loads(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Projection writes (called from store.py soft-delete paths)
# ---------------------------------------------------------------------------

def record_deletion(
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    *,
    title: str = "",
    deleted_by: Optional[str] = None,
    deleted_at: Optional[str] = None,
    parent_id: Optional[str] = None,
    parent_title: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Record one deletion generation without rewriting retention history.

    A live row is deliberately idempotent: a retry of the same soft-delete
    must not reset its clock or overwrite a purge claim.  Once that row is
    restored or purged, the next deletion receives a new row and generation.
    """
    resource_type = str(resource_type).upper()
    if resource_type not in RESOURCE_TYPES:
        raise _bad_request(f"不支持的资源类型：{resource_type}")
    moment = deleted_at or utc_now()
    expires_at = _expires_after(moment)
    payload = {
        "tenant_id": tenant_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "title": title or "",
        "parent_id": parent_id,
        "parent_title": parent_title,
        "deleted_by": deleted_by,
        "deleted_at": moment,
        "expires_at": expires_at,
        "metadata": json.dumps(metadata or {}, separators=(",", ":"), ensure_ascii=False),
        "now": moment,
    }
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text(
                "SELECT id, purge_state FROM trash_items "
                "WHERE tenant_id = :tenant_id AND resource_type = :resource_type "
                "AND resource_id = :resource_id AND restored_at IS NULL AND purged_at IS NULL"
            ),
            {
                "tenant_id": tenant_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        ).first()
        if existing is not None:
            if str(existing[1] or "ELIGIBLE") != "ELIGIBLE":
                raise _purge_in_progress(str(existing[1]))
            # Projection retries must retain both the original generation and
            # its retention window.  Returning the existing id is idempotent.
            session.commit()
            return str(existing[0])

        generation = int(
            session.execute(
                text(
                    "SELECT COALESCE(MAX(deletion_generation), 0) FROM trash_items "
                    "WHERE tenant_id=:tenant_id AND resource_type=:resource_type "
                    "AND resource_id=:resource_id"
                ),
                {
                    "tenant_id": tenant_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                },
            ).scalar_one()
        ) + 1
        row_id = (
            trash_id_for(resource_type, resource_id)
            if generation == 1
            else str(uuid4())
        )
        payload.update({"id": row_id, "generation": generation})
        session.execute(
            text(
                """
                INSERT INTO trash_items (
                  id, tenant_id, resource_type, resource_id, title,
                  parent_id, parent_title, deleted_by, deleted_at, expires_at,
                  restored_at, purged_at, metadata, deletion_generation,
                  purge_state, claim_fencing_token, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :resource_type, :resource_id, :title,
                  :parent_id, :parent_title, :deleted_by, :deleted_at, :expires_at,
                  NULL, NULL, :metadata, :generation, 'ELIGIBLE', 0, :now, :now
                )
                """
            ),
            payload,
        )

        # Keep the authoritative source row tied to the same generation.  This
        # is what makes a later purge unable to remove a newer incarnation.
        source_table = {
            "KB": "knowledge_bases",
            "DOCUMENT": "documents",
            "CONVERSATION": "conversations",
        }[resource_type]
        source_fields = (
            "expires_at=:expires_at, deleted_by=:deleted_by, "
            "deletion_generation=:generation, updated_at=:now"
        )
        session.execute(
            text(
                f"UPDATE {source_table} SET {source_fields} "
                "WHERE id=:resource_id AND tenant_id=:tenant_id"
            ),
            {
                **payload,
                "generation": generation,
            },
        )
        session.commit()
    return row_id


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _deleted_kb_ids(session, tenant_id: str) -> set:
    rows = session.execute(
        text(
            "SELECT id FROM knowledge_bases WHERE tenant_id = :tenant_id "
            "AND deleted_at IS NOT NULL"
        ),
        {"tenant_id": tenant_id},
    ).all()
    return {str(row[0]) for row in rows}


def _kb_titles(session, kb_ids: set) -> dict[str, str]:
    """Resolve KB names for rows whose ``parent_title`` was never captured.

    The ``v3_002_content`` backfill could only see ``documents.kb_id`` and wrote
    ``parent_title = NULL``. That migration is checksum-locked, so the fallback
    lives here instead of in the DDL.
    """
    if not kb_ids:
        return {}
    ordered = sorted(kb_ids)
    keys = [f"kb{index}" for index in range(len(ordered))]
    placeholders = ", ".join(f":{key}" for key in keys)
    rows = session.execute(
        text(f"SELECT id, name FROM knowledge_bases WHERE id IN ({placeholders})"),
        dict(zip(keys, ordered)),
    ).all()
    return {str(row[0]): str(row[1]) for row in rows}


def list_trash(
    tenant_id: str,
    *,
    resource_type: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> TrashPage:
    """Active (not restored, not purged) recycle-bin entries for a tenant."""
    if resource_type is not None and resource_type not in RESOURCE_TYPES:
        raise _bad_request(f"不支持的资源类型：{resource_type}")
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    where = ["tenant_id = :tenant_id", "restored_at IS NULL", "purged_at IS NULL"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if resource_type:
        where.append("resource_type = :resource_type")
        params["resource_type"] = resource_type
    if query:
        where.append("LOWER(title) LIKE :query")
        params["query"] = f"%{query.strip().lower()}%"
    clause = " AND ".join(where)

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        total = int(
            session.execute(
                text("SELECT COUNT(*) FROM trash_items WHERE " + clause), params
            ).scalar_one()
        )
        count_rows = session.execute(
            text(
                "SELECT resource_type, COUNT(*) FROM trash_items "
                "WHERE tenant_id = :tenant_id AND restored_at IS NULL AND purged_at IS NULL "
                "GROUP BY resource_type"
            ),
            {"tenant_id": tenant_id},
        ).all()
        counts = {key: 0 for key in RESOURCE_TYPES}
        for row in count_rows:
            counts[str(row[0])] = int(row[1])

        page_params = dict(params)
        page_params["limit"] = limit
        page_params["offset"] = offset
        rows = session.execute(
            text(
                "SELECT id, resource_type, resource_id, title, parent_id, parent_title, "
                "deleted_by, deleted_at, expires_at, purge_state "
                "FROM trash_items WHERE " + clause + " "
                "ORDER BY deleted_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            page_params,
        ).all()
        deleted_kbs = _deleted_kb_ids(session, tenant_id) if rows else set()
        missing_titles = {
            str(row[4]) for row in rows if row[4] is not None and row[5] is None
        }
        fallback_titles = _kb_titles(session, missing_titles)

    items = []
    for row in rows:
        kind = str(row[1])
        parent_id = None if row[4] is None else str(row[4])
        blocked = None
        if kind == "DOCUMENT" and parent_id and parent_id in deleted_kbs:
            blocked = _ORPHAN_PARENT
        parent_title = str(row[5]) if row[5] is not None else fallback_titles.get(parent_id or "")
        purge_state = str(row[9] or "ELIGIBLE")
        expired = _is_expired(row[8], utc_now())
        blocked_reason = blocked
        if blocked_reason is None and expired:
            blocked_reason = "retention_expired"
        elif blocked_reason is None and purge_state != "ELIGIBLE":
            blocked_reason = "purge_in_progress"
        items.append(
            TrashItem(
                id=str(row[0]),
                resource_type=kind,
                resource_id=str(row[2]),
                title=str(row[3] or ""),
                parent_id=parent_id,
                parent_title=parent_title,
                deleted_by=None if row[6] is None else str(row[6]),
                deleted_at=str(row[7]),
                expires_at=str(row[8]),
                restorable=blocked_reason is None,
                blocked_reason=blocked_reason,
            )
        )
    return TrashPage(items=items, total=total, counts=counts)


# ---------------------------------------------------------------------------
# Restore / purge
# ---------------------------------------------------------------------------

def _load_row(session, tenant_id: str, item_id: str) -> _TrashRow:
    row = session.execute(
        text(
            "SELECT id, tenant_id, resource_type, resource_id, title, parent_id, "
            "parent_title, deleted_by, deleted_at, expires_at, restored_at, purged_at, "
            "deletion_generation, purge_state, claim_fencing_token, metadata "
            "FROM trash_items WHERE id=:id AND tenant_id=:tenant_id"
        ),
        {"id": item_id, "tenant_id": tenant_id},
    ).first()
    if row is None or row[10] is not None or row[11] is not None:
        raise _not_found()
    return _TrashRow(
        id=str(row[0]),
        tenant_id=str(row[1]),
        resource_type=str(row[2]).upper(),
        resource_id=str(row[3]),
        title=str(row[4] or ""),
        parent_id=None if row[5] is None else str(row[5]),
        parent_title=None if row[6] is None else str(row[6]),
        deleted_by=None if row[7] is None else str(row[7]),
        deleted_at=str(row[8]),
        expires_at=str(row[9]),
        restored_at=None,
        purged_at=None,
        deletion_generation=int(row[12] or 0),
        purge_state=str(row[13] or "ELIGIBLE"),
        claim_fencing_token=int(row[14] or 0),
        metadata=_loads(row[15]),
    )


def _assert_purge_eligible(row: _TrashRow, now: str) -> None:
    if row.purge_state != "ELIGIBLE":
        raise _purge_in_progress(row.purge_state)
    if _is_expired(row.expires_at, now):
        raise _retention_expired()


def _assert_purge_due(row: _TrashRow, now: str) -> None:
    if row.purge_state != "ELIGIBLE":
        raise _purge_in_progress(row.purge_state)
    if not _is_expired(row.expires_at, now):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "RETENTION_NOT_EXPIRED",
            "回收站条目尚在 30 天保留期内，不能永久删除",
        )


def _source_generation_clause() -> str:
    return (
        "(deletion_generation=:generation OR deletion_generation=0 "
        "OR deletion_generation IS NULL)"
    )


def _document_restore_status(row: _TrashRow, source: Any) -> str:
    metadata_status = str(
        row.metadata.get("previous_status") or row.metadata.get("document_status") or ""
    ).upper()
    if metadata_status in {"PROCESSING", "READY", "FAILED"}:
        return metadata_status
    if str(source.failure_reason or "").strip():
        return "FAILED"
    # A legacy delete did not persist the prior status.  READY is only the
    # fallback when no status signal exists; it never overwrites a known state.
    return "READY"


def _restore_source(session, row: _TrashRow, now: str) -> None:
    generation_clause = _source_generation_clause()
    common = (
        "expires_at=NULL, deleted_by=NULL, deletion_batch_id=NULL, "
        "deletion_reason=NULL, purged_at=NULL, updated_at=:now"
    )
    params = {
        "rid": row.resource_id,
        "tenant_id": row.tenant_id,
        "generation": row.deletion_generation,
        "now": now,
    }
    if row.resource_type == "KB":
        changed = session.execute(
            text(
                "UPDATE knowledge_bases SET deleted_at=NULL, "
                + common
                + " WHERE id=:rid AND tenant_id=:tenant_id AND deleted_at IS NOT NULL AND "
                + generation_clause
            ),
            params,
        )
        if changed.rowcount != 1:
            raise _restore_stale()
        # KB deletion cascades document visibility without creating child trash
        # rows.  Only generation-zero children belong to that cascade; a direct
        # document deletion has its own generation and remains untouched.
        session.execute(
            text(
                "UPDATE documents SET status=CASE WHEN failure_reason IS NOT NULL "
                "THEN 'FAILED' ELSE 'READY' END, deleted_at=NULL, "
                + common
                + " WHERE tenant_id=:tenant_id AND kb_id=:rid AND status='DELETED' "
                "AND (deletion_generation=0 OR deletion_generation IS NULL)"
            ),
            params,
        )
        return
    if row.resource_type == "DOCUMENT":
        source = session.execute(
            text(
                "SELECT status, failure_reason FROM documents WHERE id=:rid "
                "AND tenant_id=:tenant_id AND "
                + generation_clause
            ),
            params,
        ).first()
        if source is None or str(source[0]).upper() != "DELETED":
            raise _restore_stale()
        status_value = _document_restore_status(row, source)
        changed = session.execute(
            text(
                "UPDATE documents SET status=:status, deleted_at=NULL, "
                + common
                + " WHERE id=:rid AND tenant_id=:tenant_id AND status='DELETED' AND "
                + generation_clause
            ),
            {**params, "status": status_value},
        )
        if changed.rowcount != 1:
            raise _restore_stale()
        return
    if row.resource_type == "CONVERSATION":
        changed = session.execute(
            text(
                "UPDATE conversations SET deleted_at=NULL, "
                + common
                + " WHERE id=:rid AND tenant_id=:tenant_id AND deleted_at IS NOT NULL AND "
                + generation_clause
            ),
            params,
        )
        if changed.rowcount != 1:
            raise _restore_stale()
        return
    raise _bad_request(f"不支持的资源类型：{row.resource_type}")


def _check_parent(session, row: _TrashRow) -> None:
    if row.resource_type != "DOCUMENT" or not row.parent_id:
        return
    parent = session.execute(
        text(
            "SELECT 1 FROM knowledge_bases WHERE id=:id AND tenant_id=:tenant "
            "AND deleted_at IS NOT NULL"
        ),
        {"id": row.parent_id, "tenant": row.tenant_id},
    ).first()
    if parent is not None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PARENT_DELETED",
            "所属知识库仍在回收站，请先恢复知识库",
        )


def _as_item(row: _TrashRow, *, restorable: bool, blocked_reason: Optional[str]) -> TrashItem:
    return TrashItem(
        id=row.id,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        title=row.title,
        parent_id=row.parent_id,
        parent_title=row.parent_title,
        deleted_by=row.deleted_by,
        deleted_at=row.deleted_at,
        expires_at=row.expires_at,
        restorable=restorable,
        blocked_reason=blocked_reason,
    )


def restore_item(tenant_id: str, item_id: str) -> TrashItem:
    """Restore one eligible row using a tenant-scoped purge/restore CAS."""
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = _load_row(session, tenant_id, item_id)
        _assert_purge_eligible(row, now)
        _check_parent(session, row)

        # Claim restore before touching the authoritative source.  If a purge
        # wins first, this CAS is zero and no resource can become visible again.
        restored = session.execute(
            text(
                "UPDATE trash_items SET restored_at=:now, updated_at=:now "
                "WHERE id=:id AND tenant_id=:tenant AND restored_at IS NULL "
                "AND purged_at IS NULL AND purge_state='ELIGIBLE' AND expires_at>:now"
            ),
            {"id": item_id, "tenant": tenant_id, "now": now},
        )
        if restored.rowcount != 1:
            raise _restore_stale()
        _restore_source(session, row, now)
        session.commit()
    return _as_item(row, restorable=False, blocked_reason=None)


def _claim_for_purge(session, row: _TrashRow, now: str) -> int:
    fence = row.claim_fencing_token + 1
    claimed = session.execute(
        text(
            "UPDATE trash_items SET purge_state='PURGING_DB', "
            "claim_fencing_token=:fence, updated_at=:now "
            "WHERE id=:id AND tenant_id=:tenant AND restored_at IS NULL "
            "AND purged_at IS NULL AND purge_state='ELIGIBLE' AND expires_at<=:now"
        ),
        {
            "fence": fence,
            "now": now,
            "id": row.id,
            "tenant": row.tenant_id,
        },
    )
    if claimed.rowcount != 1:
        raise _purge_in_progress("CLAIMED")
    return fence


def _finish_purge(session, row: _TrashRow, now: str, fence: int) -> None:
    finished = session.execute(
        text(
            "UPDATE trash_items SET purge_state='SUCCEEDED', purged_at=:now, "
            "updated_at=:now WHERE id=:id AND tenant_id=:tenant "
            "AND purge_state='PURGING_DB' AND restored_at IS NULL AND purged_at IS NULL "
            "AND claim_fencing_token=:fence"
        ),
        {"id": row.id, "tenant": row.tenant_id, "now": now, "fence": fence},
    )
    if finished.rowcount != 1:
        raise _purge_in_progress("PURGING_DB")


def purge_item(tenant_id: str, item_id: str) -> dict[str, Any]:
    """Permanently drop an expired resource through the purge state machine."""
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = _load_row(session, tenant_id, item_id)
        _assert_purge_due(row, now)
        fence = _claim_for_purge(session, row, now)
        try:
            removed = _purge_resource(
                session,
                tenant_id,
                row.resource_type,
                row.resource_id,
                generation=row.deletion_generation,
            )
            _finish_purge(session, row, now, fence)
            session.commit()
        except ApiError:
            session.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - convert to a retryable contract error
            session.rollback()
            with SessionLocal() as retry_session:
                retry_session.execute(
                    text(
                        "UPDATE trash_items SET purge_state='RETRY_WAIT', updated_at=:now "
                        "WHERE id=:id AND tenant_id=:tenant AND purge_state='PURGING_DB' "
                        "AND claim_fencing_token=:fence"
                    ),
                    {"now": now, "id": row.id, "tenant": tenant_id, "fence": fence},
                )
                retry_session.commit()
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "PURGE_RETRY_WAIT",
                "永久删除暂时失败，条目已进入可重试状态",
            ) from exc
    return {
        "id": item_id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "removed": removed,
    }


def purge_all(tenant_id: str, *, resource_type: Optional[str] = None) -> dict[str, Any]:
    """Purge only expired eligible entries; never bypass retention or CAS."""
    if resource_type is not None:
        resource_type = str(resource_type).upper()
    if resource_type is not None and resource_type not in RESOURCE_TYPES:
        raise _bad_request(f"不支持的资源类型：{resource_type}")
    now = utc_now()
    params: dict[str, Any] = {"tenant": tenant_id}
    clause = "tenant_id=:tenant AND restored_at IS NULL AND purged_at IS NULL"
    if resource_type:
        clause += " AND resource_type=:resource_type"
        params["resource_type"] = resource_type

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        ids = session.execute(text("SELECT id FROM trash_items WHERE " + clause), params).all()
        rows = [_load_row(session, tenant_id, str(item[0])) for item in ids]
        for row in rows:
            _assert_purge_due(row, now)
        purged = 0
        for row in rows:
            fence = _claim_for_purge(session, row, now)
            _purge_resource(
                session,
                tenant_id,
                row.resource_type,
                row.resource_id,
                generation=row.deletion_generation,
            )
            _finish_purge(session, row, now, fence)
            purged += 1
        session.commit()
    return {"purged": purged}


def _purge_resource(
    session,
    tenant_id: str,
    kind: str,
    resource_id: str,
    *,
    generation: Optional[int] = None,
) -> dict[str, int]:
    """Hard-delete one generation and its dependent rows. Caller commits."""
    kind = str(kind).upper()
    removed = {}
    if kind == "KB":
        doc_rows = session.execute(
            text(
                "SELECT id, deletion_generation FROM documents "
                "WHERE kb_id = :kid AND tenant_id = :tenant_id"
            ),
            {"kid": resource_id, "tenant_id": tenant_id},
        ).all()
        for doc in doc_rows:
            _purge_resource(
                session,
                tenant_id,
                "DOCUMENT",
                str(doc[0]),
                generation=int(doc[1] or 0),
            )
        removed["documents"] = len(doc_rows)
        session.execute(
            text("DELETE FROM kb_memberships WHERE kb_id = :kid AND tenant_id=:tenant_id"),
            {"kid": resource_id, "tenant_id": tenant_id},
        )
        session.execute(
            text(
                "DELETE FROM knowledge_bases WHERE id = :kid AND tenant_id = :tenant_id "
                "AND " + _source_generation_clause()
            ),
            {
                "kid": resource_id,
                "tenant_id": tenant_id,
                "generation": generation,
            },
        )
        removed["knowledge_bases"] = 1
    elif kind == "DOCUMENT":
        # chunks / document_versions / ingest_jobs key the document as ``doc_id``.
        dependent_params = {"did": resource_id, "tenant_id": tenant_id}
        session.execute(
            text("DELETE FROM chunks WHERE doc_id=:did AND tenant_id=:tenant_id"),
            dependent_params,
        )
        session.execute(
            text("DELETE FROM document_versions WHERE doc_id=:did AND tenant_id=:tenant_id"),
            dependent_params,
        )
        session.execute(
            text("DELETE FROM ingest_jobs WHERE doc_id=:did AND tenant_id=:tenant_id"),
            dependent_params,
        )
        session.execute(
            text(
                "DELETE FROM documents WHERE id=:did AND tenant_id=:tenant_id AND "
                + _source_generation_clause()
            ),
            {"did": resource_id, "tenant_id": tenant_id, "generation": generation},
        )
        removed["documents"] = 1
    elif kind == "CONVERSATION":
        # feedback hangs off messages, so it must go before the messages themselves.
        session.execute(
            text(
                "DELETE FROM feedback WHERE message_id IN "
                "(SELECT id FROM messages WHERE conversation_id=:cid AND tenant_id=:tenant_id) "
                "AND tenant_id=:tenant_id"
            ),
            {"cid": resource_id, "tenant_id": tenant_id},
        )
        session.execute(
            text("DELETE FROM review_items WHERE conversation_id=:cid AND tenant_id=:tenant_id"),
            {"cid": resource_id, "tenant_id": tenant_id},
        )
        session.execute(
            text("DELETE FROM qa_turns WHERE conversation_id=:cid AND tenant_id=:tenant_id"),
            {"cid": resource_id, "tenant_id": tenant_id},
        )
        session.execute(
            text("DELETE FROM messages WHERE conversation_id=:cid AND tenant_id=:tenant_id"),
            {"cid": resource_id, "tenant_id": tenant_id},
        )
        session.execute(
            text(
                "DELETE FROM conversations WHERE id=:cid AND tenant_id=:tenant_id AND "
                + _source_generation_clause()
            ),
            {"cid": resource_id, "tenant_id": tenant_id, "generation": generation},
        )
        removed["conversations"] = 1
    return removed

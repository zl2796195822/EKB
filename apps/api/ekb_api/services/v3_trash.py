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
from typing import Any, Dict, List, Optional, Tuple

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
    items: List[TrashItem]
    total: int
    counts: Dict[str, int]


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "回收站条目不存在或已被清理")


def _bad_request(message: str) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_ARGUMENT", message)


def _expires_after(deleted_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    moment = (parsed + timedelta(days=TRASH_RETENTION_DAYS)).replace(microsecond=0)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _loads(raw: Any) -> Dict[str, Any]:
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
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Upsert the recycle-bin projection for a soft-deleted resource.

    Re-deleting a previously restored resource reuses the same stable row and
    clears ``restored_at`` / ``purged_at`` so it reappears in the bin.
    """
    if resource_type not in RESOURCE_TYPES:
        raise _bad_request("不支持的资源类型：{0}".format(resource_type))
    moment = deleted_at or utc_now()
    row_id = trash_id_for(resource_type, resource_id)
    payload = {
        "id": row_id,
        "tenant_id": tenant_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "title": title or "",
        "parent_id": parent_id,
        "parent_title": parent_title,
        "deleted_by": deleted_by,
        "deleted_at": moment,
        "expires_at": _expires_after(moment),
        "metadata": json.dumps(metadata or {}, separators=(",", ":"), ensure_ascii=False),
        "now": moment,
    }
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        existing = session.execute(
            text(
                "SELECT id FROM trash_items WHERE resource_type = :resource_type "
                "AND resource_id = :resource_id"
            ),
            {"resource_type": resource_type, "resource_id": resource_id},
        ).first()
        if existing is None:
            session.execute(
                text(
                    """
                    INSERT INTO trash_items (
                      id, tenant_id, resource_type, resource_id, title,
                      parent_id, parent_title, deleted_by, deleted_at, expires_at,
                      restored_at, purged_at, metadata, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :resource_type, :resource_id, :title,
                      :parent_id, :parent_title, :deleted_by, :deleted_at, :expires_at,
                      NULL, NULL, :metadata, :now, :now
                    )
                    """
                ),
                payload,
            )
        else:
            row_id = str(existing[0])
            payload["id"] = row_id
            session.execute(
                text(
                    """
                    UPDATE trash_items
                       SET tenant_id = :tenant_id,
                           title = :title,
                           parent_id = :parent_id,
                           parent_title = :parent_title,
                           deleted_by = :deleted_by,
                           deleted_at = :deleted_at,
                           expires_at = :expires_at,
                           restored_at = NULL,
                           purged_at = NULL,
                           metadata = :metadata,
                           updated_at = :now
                     WHERE id = :id
                    """
                ),
                payload,
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


def _kb_titles(session, kb_ids: set) -> Dict[str, str]:
    """Resolve KB names for rows whose ``parent_title`` was never captured.

    The ``v3_002_content`` backfill could only see ``documents.kb_id`` and wrote
    ``parent_title = NULL``. That migration is checksum-locked, so the fallback
    lives here instead of in the DDL.
    """
    if not kb_ids:
        return {}
    ordered = sorted(kb_ids)
    keys = ["kb{0}".format(index) for index in range(len(ordered))]
    placeholders = ", ".join(":{0}".format(key) for key in keys)
    rows = session.execute(
        text("SELECT id, name FROM knowledge_bases WHERE id IN ({0})".format(placeholders)),
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
        raise _bad_request("不支持的资源类型：{0}".format(resource_type))
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    where = ["tenant_id = :tenant_id", "restored_at IS NULL", "purged_at IS NULL"]
    params: Dict[str, Any] = {"tenant_id": tenant_id}
    if resource_type:
        where.append("resource_type = :resource_type")
        params["resource_type"] = resource_type
    if query:
        where.append("LOWER(title) LIKE :query")
        params["query"] = "%{0}%".format(query.strip().lower())
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
                "deleted_by, deleted_at, expires_at "
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
                restorable=blocked is None,
                blocked_reason=blocked,
            )
        )
    return TrashPage(items=items, total=total, counts=counts)


# ---------------------------------------------------------------------------
# Restore / purge
# ---------------------------------------------------------------------------

def _load_active(session, tenant_id: str, item_id: str) -> Tuple[str, str, Optional[str]]:
    row = session.execute(
        text(
            "SELECT resource_type, resource_id, parent_id FROM trash_items "
            "WHERE id = :id AND tenant_id = :tenant_id "
            "AND restored_at IS NULL AND purged_at IS NULL"
        ),
        {"id": item_id, "tenant_id": tenant_id},
    ).first()
    if row is None:
        raise _not_found()
    return str(row[0]), str(row[1]), None if row[2] is None else str(row[2])


def restore_item(tenant_id: str, item_id: str) -> TrashItem:
    """Undo the soft delete on the owning table and close the projection row."""
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        kind, resource_id, parent_id = _load_active(session, tenant_id, item_id)

        if kind == "KB":
            session.execute(
                text(
                    "UPDATE knowledge_bases SET deleted_at = NULL, updated_at = :now "
                    "WHERE id = :rid AND tenant_id = :tenant_id"
                ),
                {"rid": resource_id, "tenant_id": tenant_id, "now": now},
            )
        elif kind == "DOCUMENT":
            if parent_id:
                kb_deleted = session.execute(
                    text(
                        "SELECT 1 FROM knowledge_bases WHERE id = :kid AND tenant_id = :tenant_id "
                        "AND deleted_at IS NOT NULL"
                    ),
                    {"kid": parent_id, "tenant_id": tenant_id},
                ).first()
                if kb_deleted:
                    raise ApiError(
                        status.HTTP_409_CONFLICT,
                        "CONFLICT",
                        "所属知识库仍在回收站，请先恢复知识库",
                    )
            session.execute(
                text(
                    "UPDATE documents SET status = 'READY', updated_at = :now "
                    "WHERE id = :rid AND tenant_id = :tenant_id"
                ),
                {"rid": resource_id, "tenant_id": tenant_id, "now": now},
            )
        elif kind == "CONVERSATION":
            session.execute(
                text(
                    "UPDATE conversations SET deleted_at = NULL, updated_at = :now "
                    "WHERE id = :rid AND tenant_id = :tenant_id"
                ),
                {"rid": resource_id, "tenant_id": tenant_id, "now": now},
            )
        else:  # pragma: no cover - guarded by _load_active
            raise _bad_request("不支持的资源类型：{0}".format(kind))

        session.execute(
            text(
                "UPDATE trash_items SET restored_at = :now, updated_at = :now WHERE id = :id"
            ),
            {"id": item_id, "now": now},
        )
        session.commit()

    return TrashItem(
        id=item_id,
        resource_type=kind,
        resource_id=resource_id,
        title="",
        parent_id=parent_id,
        parent_title=None,
        deleted_by=None,
        deleted_at=now,
        expires_at=now,
        restorable=False,
        blocked_reason=None,
    )


def purge_item(tenant_id: str, item_id: str) -> Dict[str, Any]:
    """Permanently drop the underlying rows and mark the projection purged."""
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        kind, resource_id, _ = _load_active(session, tenant_id, item_id)
        removed = _purge_resource(session, tenant_id, kind, resource_id)
        session.execute(
            text("UPDATE trash_items SET purged_at = :now, updated_at = :now WHERE id = :id"),
            {"id": item_id, "now": now},
        )
        session.commit()
    return {"id": item_id, "resource_type": kind, "resource_id": resource_id, "removed": removed}


def purge_all(tenant_id: str, *, resource_type: Optional[str] = None) -> Dict[str, Any]:
    """Empty the recycle bin (optionally limited to one resource type)."""
    if resource_type is not None and resource_type not in RESOURCE_TYPES:
        raise _bad_request("不支持的资源类型：{0}".format(resource_type))
    now = utc_now()
    params: Dict[str, Any] = {"tenant_id": tenant_id}
    clause = "tenant_id = :tenant_id AND restored_at IS NULL AND purged_at IS NULL"
    if resource_type:
        clause += " AND resource_type = :resource_type"
        params["resource_type"] = resource_type

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text("SELECT id, resource_type, resource_id FROM trash_items WHERE " + clause),
            params,
        ).all()
        purged = 0
        for row in rows:
            _purge_resource(session, tenant_id, str(row[1]), str(row[2]))
            purged += 1
        if rows:
            update_params = dict(params)
            update_params["now"] = now
            session.execute(
                text(
                    "UPDATE trash_items SET purged_at = :now, updated_at = :now WHERE " + clause
                ),
                update_params,
            )
        session.commit()
    return {"purged": purged}


def _purge_resource(session, tenant_id: str, kind: str, resource_id: str) -> Dict[str, int]:
    """Hard-delete a resource and its dependent rows. Caller commits."""
    removed = {}
    if kind == "KB":
        doc_rows = session.execute(
            text("SELECT id FROM documents WHERE kb_id = :kid AND tenant_id = :tenant_id"),
            {"kid": resource_id, "tenant_id": tenant_id},
        ).all()
        for doc in doc_rows:
            _purge_resource(session, tenant_id, "DOCUMENT", str(doc[0]))
        removed["documents"] = len(doc_rows)
        session.execute(
            text("DELETE FROM kb_memberships WHERE kb_id = :kid"), {"kid": resource_id}
        )
        session.execute(
            text("DELETE FROM knowledge_bases WHERE id = :kid AND tenant_id = :tenant_id"),
            {"kid": resource_id, "tenant_id": tenant_id},
        )
        removed["knowledge_bases"] = 1
    elif kind == "DOCUMENT":
        # chunks / document_versions / ingest_jobs key the document as ``doc_id``.
        session.execute(text("DELETE FROM chunks WHERE doc_id = :did"), {"did": resource_id})
        session.execute(
            text("DELETE FROM document_versions WHERE doc_id = :did"), {"did": resource_id}
        )
        session.execute(text("DELETE FROM ingest_jobs WHERE doc_id = :did"), {"did": resource_id})
        session.execute(
            text("DELETE FROM documents WHERE id = :did AND tenant_id = :tenant_id"),
            {"did": resource_id, "tenant_id": tenant_id},
        )
        removed["documents"] = 1
    elif kind == "CONVERSATION":
        # feedback hangs off messages, so it must go before the messages themselves.
        session.execute(
            text(
                "DELETE FROM feedback WHERE message_id IN "
                "(SELECT id FROM messages WHERE conversation_id = :cid)"
            ),
            {"cid": resource_id},
        )
        session.execute(
            text("DELETE FROM review_items WHERE conversation_id = :cid"), {"cid": resource_id}
        )
        session.execute(
            text("DELETE FROM qa_turns WHERE conversation_id = :cid"), {"cid": resource_id}
        )
        session.execute(
            text("DELETE FROM messages WHERE conversation_id = :cid"), {"cid": resource_id}
        )
        session.execute(
            text("DELETE FROM conversations WHERE id = :cid AND tenant_id = :tenant_id"),
            {"cid": resource_id, "tenant_id": tenant_id},
        )
        removed["conversations"] = 1
    return removed

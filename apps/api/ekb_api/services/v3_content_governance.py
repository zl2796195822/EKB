"""Content governance service: favorites projection (P2 · v4 content governance).

Exposes three primitives over the ``favorites`` projection created by
``v3_007_content_governance_compat``:

* ``toggle_favorite`` — idempotent star toggle.  Returns the NEW favourited
  state so the caller can update the icon without a follow-up fetch.
* ``list_favorites`` — paginated per-subject list, newest first.  Resolves
  each row's title via a single cross-resource SELECT … UNION ALL query so
  there are **no N+1** round-trips, and deleted resources are excluded from
  the rendered list (their projection row is preserved for auditability).
* ``check_favorite`` — cheap existence check by composite key; used by list
  pages to pre-fill star state without doing a full listing.

The projection enforces composite uniqueness on ``(tenant_id, subject_id,
resource_type, resource_id)``.  ``toggle_favorite`` therefore resolves the
current state first and executes either INSERT or DELETE.  A concurrent
toggle raced by the same subject is harmless — both INSERTs lose to the
UNIQUE constraint (caught by IntegrityError → re-read), and DELETEs with
matched rowcount 0 are equivalent to a concurrent un-favourite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette import status

from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.domain import utc_now

RESOURCE_TYPES = ("KB", "DOCUMENT", "CONVERSATION")


def _bad_request(message: str) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_ARGUMENT", message)


@dataclass(frozen=True)
class FavoriteItem:
    id: str
    resource_type: str
    resource_id: str
    title: str
    parent_id: Optional[str]
    parent_title: Optional[str]
    favorited_at: str


@dataclass(frozen=True)
class FavoritesPage:
    items: List[FavoriteItem]
    total: int
    counts: Dict[str, int]


@dataclass(frozen=True)
class ToggleResult:
    resource_type: str
    resource_id: str
    favorited: bool
    favorited_at: Optional[str]


def _validate_resource_type(resource_type: str) -> None:
    if resource_type not in RESOURCE_TYPES:
        raise _bad_request("不支持的资源类型：{0}".format(resource_type))


def _validate_resource_ownership(session, tenant_id: str, resource_type: str, resource_id: str) -> None:
    """Guard against cross-tenant favourites.

    A missing row means either the resource was hard-deleted or the caller
    handed us an id they don't own — both collapse into a 404 so we never
    leak the existence of other tenants' resources.
    """
    if resource_type == "KB":
        sql = "SELECT 1 FROM knowledge_bases WHERE id = :rid AND tenant_id = :tid"
    elif resource_type == "DOCUMENT":
        sql = "SELECT 1 FROM documents WHERE id = :rid AND tenant_id = :tid"
    elif resource_type == "CONVERSATION":
        sql = "SELECT 1 FROM conversations WHERE id = :rid AND tenant_id = :tid"
    else:  # pragma: no cover
        raise _bad_request("不支持的资源类型：{0}".format(resource_type))
    exists = session.execute(text(sql), {"rid": resource_id, "tid": tenant_id}).scalar()
    if not exists:
        raise ApiError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "资源不存在或无权操作")


def _resolve_titles_bulk(session, tenant_id: str, rows: list) -> Dict[str, Dict[str, Any]]:
    """Resolve titles + parent info for every listed favourite in one pass.

    Returns a mapping ``{resource_type:resource_id -> {title, parent_id, parent_title}}``.
    Missing entries are returned with ``title = ""``; callers decide whether to
    drop them from the rendered page.
    """
    if not rows:
        return {}
    kb_ids: list[str] = []
    doc_ids: list[str] = []
    conv_ids: list[str] = []
    for row in rows:
        kind = str(row[2])  # resource_type column
        rid = str(row[3])
        if kind == "KB":
            kb_ids.append(rid)
        elif kind == "DOCUMENT":
            doc_ids.append(rid)
        elif kind == "CONVERSATION":
            conv_ids.append(rid)

    out: Dict[str, Dict[str, Any]] = {}

    if kb_ids:
        ordered = sorted(set(kb_ids))
        keys = ["kb{0}".format(i) for i in range(len(ordered))]
        placeholders = ", ".join(":{0}".format(k) for k in keys)
        qrows = session.execute(
            text(
                "SELECT id, name AS title, NULL AS parent_id, NULL AS parent_title "
                "FROM knowledge_bases WHERE id IN ({0}) AND tenant_id = :tid".format(placeholders)
            ),
            dict(zip(keys, ordered), tid=tenant_id),
        ).all()
        for qr in qrows:
            out["KB:" + str(qr[0])] = {
                "title": str(qr[1] or ""),
                "parent_id": None,
                "parent_title": None,
            }

    if doc_ids:
        ordered = sorted(set(doc_ids))
        keys = ["doc{0}".format(i) for i in range(len(ordered))]
        placeholders = ", ".join(":{0}".format(k) for k in keys)
        qrows = session.execute(
            text(
                "SELECT d.id, d.title AS title, d.kb_id AS parent_id, kb.name AS parent_title "
                "FROM documents d LEFT JOIN knowledge_bases kb ON kb.id = d.kb_id "
                "WHERE d.id IN ({0}) AND d.tenant_id = :tid".format(placeholders)
            ),
            dict(zip(keys, ordered), tid=tenant_id),
        ).all()
        for qr in qrows:
            out["DOCUMENT:" + str(qr[0])] = {
                "title": str(qr[1] or ""),
                "parent_id": None if qr[2] is None else str(qr[2]),
                "parent_title": None if qr[3] is None else str(qr[3]),
            }

    if conv_ids:
        ordered = sorted(set(conv_ids))
        keys = ["c{0}".format(i) for i in range(len(ordered))]
        placeholders = ", ".join(":{0}".format(k) for k in keys)
        qrows = session.execute(
            text(
                "SELECT id, COALESCE(title, '') AS title, NULL AS parent_id, "
                "  NULL AS parent_title "
                "FROM conversations WHERE id IN ({0}) AND tenant_id = :tid".format(placeholders)
            ),
            dict(zip(keys, ordered), tid=tenant_id),
        ).all()
        for qr in qrows:
            out["CONVERSATION:" + str(qr[0])] = {
                "title": str(qr[1] or ""),
                "parent_id": None if qr[2] is None else str(qr[2]),
                "parent_title": None if qr[3] is None else str(qr[3]),
            }

    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def toggle_favorite(
    tenant_id: str,
    subject_id: str,
    *,
    resource_type: str,
    resource_id: str,
) -> ToggleResult:
    """Toggle the starred state for a resource the subject has permission to see."""
    _validate_resource_type(resource_type)
    now = utc_now()
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        _validate_resource_ownership(session, tenant_id, resource_type, resource_id)
        existing = session.execute(
            text(
                "SELECT id, favorited_at FROM favorites "
                "WHERE tenant_id = :tid AND subject_id = :sid "
                "AND resource_type = :rtype AND resource_id = :rid"
            ),
            {"tid": tenant_id, "sid": subject_id, "rtype": resource_type, "rid": resource_id},
        ).first()
        if existing is None:
            row_id = uuid.uuid4().hex
            try:
                session.execute(
                    text(
                        "INSERT INTO favorites (id, tenant_id, subject_id, resource_type, resource_id, favorited_at) "
                        "VALUES (:id, :tid, :sid, :rtype, :rid, :fat)"
                    ),
                    {"id": row_id, "tid": tenant_id, "sid": subject_id, "rtype": resource_type, "rid": resource_id, "fat": now},
                )
                session.commit()
            except IntegrityError:
                # Raced another insert — we lost, so the current state is "favourited".
                session.rollback()
                reread = session.execute(
                    text(
                        "SELECT favorited_at FROM favorites "
                        "WHERE tenant_id = :tid AND subject_id = :sid "
                        "AND resource_type = :rtype AND resource_id = :rid"
                    ),
                    {"tid": tenant_id, "sid": subject_id, "rtype": resource_type, "rid": resource_id},
                ).scalar()
                return ToggleResult(resource_type, resource_id, True, str(reread) if reread else now)
            return ToggleResult(resource_type, resource_id, True, now)
        session.execute(
            text(
                "DELETE FROM favorites "
                "WHERE tenant_id = :tid AND subject_id = :sid "
                "AND resource_type = :rtype AND resource_id = :rid"
            ),
            {"tid": tenant_id, "sid": subject_id, "rtype": resource_type, "rid": resource_id},
        )
        session.commit()
        return ToggleResult(resource_type, resource_id, False, None)


def list_favorites(
    tenant_id: str,
    subject_id: str,
    *,
    resource_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> FavoritesPage:
    """List the subject's favourited resources, newest first, with titles resolved."""
    if resource_type is not None:
        _validate_resource_type(resource_type)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    where = ["tenant_id = :tid", "subject_id = :sid"]
    params: Dict[str, Any] = {"tid": tenant_id, "sid": subject_id}
    if resource_type:
        where.append("resource_type = :rtype")
        params["rtype"] = resource_type
    clause = " AND ".join(where)

    SessionLocal = get_session_local()
    with SessionLocal() as session:
        total = int(
            session.execute(
                text("SELECT COUNT(*) FROM favorites WHERE " + clause), params
            ).scalar_one()
        )
        count_rows = session.execute(
            text(
                "SELECT resource_type, COUNT(*) FROM favorites "
                "WHERE tenant_id = :tid AND subject_id = :sid GROUP BY resource_type"
            ),
            {"tid": tenant_id, "sid": subject_id},
        ).all()
        counts = {key: 0 for key in RESOURCE_TYPES}
        for row in count_rows:
            counts[str(row[0])] = int(row[1])

        page_params = dict(params)
        page_params["limit"] = limit
        page_params["offset"] = offset
        rows = session.execute(
            text(
                "SELECT id, tenant_id, resource_type, resource_id, subject_id, favorited_at "
                "FROM favorites WHERE " + clause + " "
                "ORDER BY favorited_at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            page_params,
        ).all()
        title_map = _resolve_titles_bulk(session, tenant_id, rows)

    items: List[FavoriteItem] = []
    for row in rows:
        rid = str(row[3])
        kind = str(row[2])
        meta = title_map.get("{0}:{1}".format(kind, rid))
        if meta is None:
            # Either hard-deleted or cross-tenant; skip so the page stays
            # consistent with actual permissions.  We don't drop the row from
            # the projection — audit trails need it.
            continue
        items.append(
            FavoriteItem(
                id=str(row[0]),
                resource_type=kind,
                resource_id=rid,
                title=meta["title"],
                parent_id=meta.get("parent_id"),
                parent_title=meta.get("parent_title"),
                favorited_at=str(row[5]),
            )
        )
    return FavoritesPage(items=items, total=total, counts=counts)


def check_favorite(
    tenant_id: str,
    subject_id: str,
    *,
    resource_type: str,
    resource_id: str,
) -> Dict[str, Any]:
    """Cheap existence probe used to pre-fill star state on list pages."""
    _validate_resource_type(resource_type)
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT favorited_at FROM favorites "
                "WHERE tenant_id = :tid AND subject_id = :sid "
                "AND resource_type = :rtype AND resource_id = :rid"
            ),
            {"tid": tenant_id, "sid": subject_id, "rtype": resource_type, "rid": resource_id},
        ).first()
    if row is None:
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "favorited": False,
            "favorited_at": None,
        }
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "favorited": True,
        "favorited_at": str(row[0]),
    }

"""Analytics service over the ``resource_access_events`` projection (v3_003).

Everything here is computed from real rows.  When a metric has no rows the
service returns an explicit zero-filled series (so the chart can draw a real
"nothing happened" line) rather than omitting the field — the frontend must be
able to tell "0 accesses" apart from "endpoint missing".

Column names are pinned to the DDL locked in ``v3_003_analytics``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import text
from starlette import status

from ekb_api.core.db import get_session_local
from ekb_api.core.errors import ApiError
from ekb_api.domain import utc_now
from ekb_api.migrations.v3_003_analytics import (
    access_event_id_for,
    access_kind_for,
    resource_type_for,
)

#: Matches the router's Query(ge=1, le=90) bound.
MIN_DAYS = 1
MAX_DAYS = 90

#: Distribution/activity list caps so a large tenant cannot blow up a response.
MAX_DISTRIBUTION = 20
MAX_ACTIVITY = 50


@dataclass(frozen=True)
class TrendPoint:
    day: str
    accesses: int
    visitors: int


@dataclass(frozen=True)
class DistributionSlice:
    kb_id: str
    name: str
    documents: int
    accesses: int


@dataclass(frozen=True)
class ActivityEntry:
    id: str
    resource_type: str
    resource_id: str
    title: str
    access_kind: str
    actor_id: Optional[str]
    actor_name: Optional[str]
    occurred_at: str


@dataclass(frozen=True)
class AnalyticsOverview:
    days: int
    since: str
    accesses: int
    visitors: int
    qa_volume: int
    kb_count: int
    doc_count: int
    member_count: int
    accesses_today: int
    qa_today: int


def _bad_request(message: str) -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, "INVALID_ARGUMENT", message)


def normalize_days(days: int) -> int:
    if days < MIN_DAYS or days > MAX_DAYS:
        raise _bad_request("days 必须在 {0}–{1} 之间".format(MIN_DAYS, MAX_DAYS))
    return days


def _day_strings(days: int) -> List[str]:
    """Inclusive UTC day axis ending today, oldest first."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]


def _since_day(days: int) -> str:
    return _day_strings(days)[0]


# ---------------------------------------------------------------------------
# Runtime projection write (called from request handlers)
# ---------------------------------------------------------------------------

def record_access(
    tenant_id: str,
    resource_type: str,
    resource_id: str,
    *,
    action: str,
    actor_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
    source_ref: Optional[str] = None,
) -> Optional[str]:
    """Append one access event.  Returns the row id, or None when skipped.

    Mapping reuses the migration's locked rules so backfilled history and live
    traffic classify identically.  ``resource_type`` accepts either the audit
    ``target_type`` spelling (``knowledge_base``) or the projection spelling
    (``KB``).

    When ``source_ref`` (the originating ``audit_logs.id``) is supplied the row
    id is derived with the migration's uuid5 strategy, so a later re-run of the
    backfill collides on the unique ``(source, source_ref)`` index instead of
    double-counting the same event.
    """
    from uuid import uuid4

    normalized = (resource_type or "").strip()
    kind = normalized.upper()
    if kind not in ("KB", "DOCUMENT", "CONVERSATION"):
        kind = resource_type_for(normalized) or ""
    if not kind or not tenant_id or not resource_id:
        return None

    moment = occurred_at or utc_now()
    event_id = access_event_id_for(source_ref) if source_ref else str(uuid4())
    params = {
        "id": event_id,
        "tenant_id": str(tenant_id),
        "resource_type": kind,
        "resource_id": str(resource_id),
        "actor_id": None if actor_id is None else str(actor_id),
        "access_kind": access_kind_for(action),
        "occurred_at": moment,
        "occurred_day": moment[:10],
        "source_ref": source_ref,
        "trace_id": trace_id,
        "created_at": utc_now(),
    }
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        dialect = session.bind.dialect.name if session.bind else "sqlite"
        if dialect == "postgresql":
            # PostgreSQL uses standard ON CONFLICT … DO NOTHING.  Conflict is
            # either the PK (runtime writes) or ux_resource_access_events_source_ref
            # (audit-derived writes with a shared source_ref).
            insert_sql = """
                INSERT INTO resource_access_events (
                  id, tenant_id, resource_type, resource_id, actor_id,
                  access_kind, occurred_at, occurred_day, source, source_ref,
                  trace_id, metadata, created_at
                ) VALUES (
                  :id, :tenant_id, :resource_type, :resource_id, :actor_id,
                  :access_kind, :occurred_at, :occurred_day, 'runtime', :source_ref,
                  :trace_id, NULL, :created_at
                ) ON CONFLICT DO NOTHING
            """
        else:
            # SQLite's long-standing INSERT OR IGNORE covers any uniqueness
            # violation (PK or the source/source_ref unique index) in one go.
            insert_sql = """
                INSERT OR IGNORE INTO resource_access_events (
                  id, tenant_id, resource_type, resource_id, actor_id,
                  access_kind, occurred_at, occurred_day, source, source_ref,
                  trace_id, metadata, created_at
                ) VALUES (
                  :id, :tenant_id, :resource_type, :resource_id, :actor_id,
                  :access_kind, :occurred_at, :occurred_day, 'runtime', :source_ref,
                  :trace_id, NULL, :created_at
                )
            """
        session.execute(text(insert_sql), params)
        session.commit()
    return event_id


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_overview(tenant_id: str, days: int) -> AnalyticsOverview:
    window = normalize_days(days)
    since = _since_day(window)
    today = _day_strings(1)[0]
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        totals = session.execute(
            text(
                "SELECT COUNT(*), COUNT(DISTINCT actor_id) FROM resource_access_events "
                "WHERE tenant_id = :tenant AND occurred_day >= :since"
            ),
            {"tenant": tenant_id, "since": since},
        ).first()
        qa_volume = session.execute(
            text(
                "SELECT COUNT(*) FROM resource_access_events "
                "WHERE tenant_id = :tenant AND occurred_day >= :since AND access_kind = 'ASK'"
            ),
            {"tenant": tenant_id, "since": since},
        ).scalar_one()
        accesses_today = session.execute(
            text(
                "SELECT COUNT(*) FROM resource_access_events "
                "WHERE tenant_id = :tenant AND occurred_day = :day"
            ),
            {"tenant": tenant_id, "day": today},
        ).scalar_one()
        qa_today = session.execute(
            text(
                "SELECT COUNT(*) FROM resource_access_events "
                "WHERE tenant_id = :tenant AND occurred_day = :day AND access_kind = 'ASK'"
            ),
            {"tenant": tenant_id, "day": today},
        ).scalar_one()
        kb_count = session.execute(
            text(
                "SELECT COUNT(*) FROM knowledge_bases "
                "WHERE tenant_id = :tenant AND deleted_at IS NULL"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
        doc_count = session.execute(
            text(
                "SELECT COUNT(*) FROM documents "
                "WHERE tenant_id = :tenant AND status <> 'DELETED'"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
        member_count = session.execute(
            text("SELECT COUNT(*) FROM users WHERE tenant_id = :tenant"),
            {"tenant": tenant_id},
        ).scalar_one()

    return AnalyticsOverview(
        days=window,
        since=since,
        accesses=int(totals[0] or 0) if totals else 0,
        visitors=int(totals[1] or 0) if totals else 0,
        qa_volume=int(qa_volume or 0),
        kb_count=int(kb_count or 0),
        doc_count=int(doc_count or 0),
        member_count=int(member_count or 0),
        accesses_today=int(accesses_today or 0),
        qa_today=int(qa_today or 0),
    )


def get_access_trend(tenant_id: str, days: int) -> List[TrendPoint]:
    """Daily accesses + unique visitors, zero-filled across the whole window."""
    window = normalize_days(days)
    axis = _day_strings(window)
    since = axis[0]
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT occurred_day, COUNT(*), COUNT(DISTINCT actor_id) "
                "FROM resource_access_events "
                "WHERE tenant_id = :tenant AND occurred_day >= :since "
                "GROUP BY occurred_day"
            ),
            {"tenant": tenant_id, "since": since},
        ).all()

    by_day: Dict[str, TrendPoint] = {
        str(row[0]): TrendPoint(str(row[0]), int(row[1] or 0), int(row[2] or 0)) for row in rows
    }
    return [by_day.get(day, TrendPoint(day, 0, 0)) for day in axis]


def get_kb_distribution(tenant_id: str, days: int, limit: int = MAX_DISTRIBUTION) -> List[DistributionSlice]:
    """Per-knowledge-base document count and access count in the window.

    Access counts join through ``documents`` so a document view is attributed to
    its owning knowledge base, and direct KB events are attributed as-is.
    """
    window = normalize_days(days)
    since = _since_day(window)
    capped = max(1, min(limit, MAX_DISTRIBUTION))
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        kb_rows = session.execute(
            text(
                "SELECT id, name FROM knowledge_bases "
                "WHERE tenant_id = :tenant AND deleted_at IS NULL ORDER BY created_at"
            ),
            {"tenant": tenant_id},
        ).all()
        if not kb_rows:
            return []

        doc_rows = session.execute(
            text(
                "SELECT kb_id, COUNT(*) FROM documents "
                "WHERE tenant_id = :tenant AND status <> 'DELETED' GROUP BY kb_id"
            ),
            {"tenant": tenant_id},
        ).all()
        kb_access_rows = session.execute(
            text(
                "SELECT resource_id, COUNT(*) FROM resource_access_events "
                "WHERE tenant_id = :tenant AND occurred_day >= :since AND resource_type = 'KB' "
                "GROUP BY resource_id"
            ),
            {"tenant": tenant_id, "since": since},
        ).all()
        doc_access_rows = session.execute(
            text(
                "SELECT d.kb_id, COUNT(*) FROM resource_access_events e "
                "JOIN documents d ON d.id = e.resource_id "
                "WHERE e.tenant_id = :tenant AND e.occurred_day >= :since "
                "AND e.resource_type = 'DOCUMENT' GROUP BY d.kb_id"
            ),
            {"tenant": tenant_id, "since": since},
        ).all()

    documents = {str(row[0]): int(row[1] or 0) for row in doc_rows}
    accesses: Dict[str, int] = {}
    for row in kb_access_rows:
        accesses[str(row[0])] = accesses.get(str(row[0]), 0) + int(row[1] or 0)
    for row in doc_access_rows:
        if row[0] is None:
            continue
        accesses[str(row[0])] = accesses.get(str(row[0]), 0) + int(row[1] or 0)

    slices = [
        DistributionSlice(
            kb_id=str(row[0]),
            name=str(row[1] or ""),
            documents=documents.get(str(row[0]), 0),
            accesses=accesses.get(str(row[0]), 0),
        )
        for row in kb_rows
    ]
    slices.sort(key=lambda item: (-item.accesses, -item.documents, item.name))
    return slices[:capped]


def get_recent_activity(tenant_id: str, limit: int = 10) -> List[ActivityEntry]:
    """Newest access events with resolved titles and actor names."""
    capped = max(1, min(limit, MAX_ACTIVITY))
    SessionLocal = get_session_local()
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT id, resource_type, resource_id, actor_id, access_kind, occurred_at "
                "FROM resource_access_events WHERE tenant_id = :tenant "
                "ORDER BY occurred_at DESC, id DESC LIMIT :limit"
            ),
            {"tenant": tenant_id, "limit": capped},
        ).all()
        if not rows:
            return []

        titles = _resolve_titles(session, rows)
        actor_ids = {str(row[3]) for row in rows if row[3] is not None}
        actors: Dict[str, str] = {}
        if actor_ids:
            placeholders = ",".join(":a{0}".format(index) for index in range(len(actor_ids)))
            params = {"a{0}".format(index): value for index, value in enumerate(sorted(actor_ids))}
            actor_rows = session.execute(
                text("SELECT id, name FROM users WHERE id IN ({0})".format(placeholders)),
                params,
            ).all()
            actors = {str(row[0]): str(row[1] or "") for row in actor_rows}

    return [
        ActivityEntry(
            id=str(row[0]),
            resource_type=str(row[1]),
            resource_id=str(row[2]),
            title=titles.get((str(row[1]), str(row[2])), ""),
            access_kind=str(row[4]),
            actor_id=None if row[3] is None else str(row[3]),
            actor_name=actors.get(str(row[3])) if row[3] is not None else None,
            occurred_at=str(row[5]),
        )
        for row in rows
    ]


def _resolve_titles(session, rows) -> Dict[tuple, str]:
    """Look up display titles per resource type in one query each."""
    buckets: Dict[str, set] = {"KB": set(), "DOCUMENT": set(), "CONVERSATION": set()}
    for row in rows:
        kind = str(row[1])
        if kind in buckets:
            buckets[kind].add(str(row[2]))

    sources = {
        "KB": ("knowledge_bases", "name"),
        "DOCUMENT": ("documents", "title"),
        "CONVERSATION": ("conversations", "title"),
    }
    resolved: Dict[tuple, str] = {}
    for kind, ids in buckets.items():
        if not ids:
            continue
        table, column = sources[kind]
        placeholders = ",".join(":i{0}".format(index) for index in range(len(ids)))
        params = {"i{0}".format(index): value for index, value in enumerate(sorted(ids))}
        found = session.execute(
            text(
                "SELECT id, {0} FROM {1} WHERE id IN ({2})".format(column, table, placeholders)
            ),
            params,
        ).all()
        for row in found:
            resolved[(kind, str(row[0]))] = str(row[1] or "")
    return resolved

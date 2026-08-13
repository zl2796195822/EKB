"""Retention purge scheduler for the unified trash (PH2 · T05).

The retention contract lives in ``v4_005``: deleting a resource writes a
``deletion_batch`` whose ``expires_at`` is exactly ``deleted_at + 30 days``
and a ``trash_items`` row in ``ELIGIBLE`` state.  This service is the
operational counterpart — it permanently purges trash entries whose
30-day window has elapsed and that have not been restored.

Every transition is guarded by a compare-and-set on ``purge_state`` so a
concurrent purge or a restore cannot race.  Resource rows are hard-deleted by
``(tenant_id, resource_type, resource_id, deletion_generation)`` so a
re-created resource with a later generation is never clobbered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Engine, text

from ekb_api.core.db import get_engine
from ekb_api.domain import utc_now

# resource_type -> (table, primary_key_column)
_RESOURCE_TABLES = {
    "knowledge_base": ("knowledge_bases", "id"),
    "KB": ("knowledge_bases", "id"),
    "document": ("documents", "id"),
    "DOCUMENT": ("documents", "id"),
    "document_version": ("document_versions", "id"),
    "DOCUMENT_VERSION": ("document_versions", "id"),
    "conversation": ("conversations", "id"),
    "CONVERSATION": ("conversations", "id"),
}

_TERMINAL_PURGED = "SUCCEEDED"
_ELIGIBLE = "ELIGIBLE"


@dataclass
class PurgeReport:
    evaluated: int = 0
    purged: int = 0
    skipped_restored: int = 0
    skipped_not_due: int = 0
    failed: int = 0
    purged_resource_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _resource_delete_sql(resource_type: str) -> Optional[str]:
    mapping = _RESOURCE_TABLES.get(resource_type)
    if mapping is None:
        return None
    table, pk = mapping
    return (
        f"DELETE FROM {table} WHERE tenant_id=:tenant AND {pk}=:resource_id "
        f"AND (deletion_generation=:generation OR deletion_generation=0)"
    )


def purge_expired_trash(
    engine: Optional[Engine] = None,
    *,
    tenant_id: Optional[str] = None,
    now: Optional[str] = None,
    batch_size: int = 200,
    dry_run: bool = False,
) -> PurgeReport:
    """Permanently purge eligible trash past its 30-day retention window.

    Returns a :class:`PurgeReport`.  When ``dry_run`` is true no rows are
    mutated and ``purged`` stays ``0`` while ``evaluated``/``skipped_*`` still
    report what *would* happen.
    """
    engine = engine or get_engine()
    current = now or utc_now()
    report = PurgeReport()

    with engine.begin() as connection:
        tenant_clause = "" if tenant_id is None else " AND t.tenant_id=:tenant"
        candidate_params = {"eligible": _ELIGIBLE, "limit": max(1, min(batch_size, 2000))}
        if tenant_id is not None:
            candidate_params["tenant"] = tenant_id
        candidates = connection.execute(
            text(
                "SELECT t.id, t.tenant_id, t.resource_type, t.resource_id, "
                "t.deletion_generation, t.deletion_batch_id, t.restored_at, t.expires_at "
                "FROM trash_items AS t "
                "WHERE t.purge_state=:eligible AND t.restored_at IS NULL "
                "AND t.purged_at IS NULL AND t.expires_at IS NOT NULL "
                + tenant_clause
                + " ORDER BY t.expires_at ASC LIMIT :limit"
            ),
            candidate_params,
        ).all()

        for row in candidates:
            report.evaluated += 1
            if row.restored_at is not None:
                report.skipped_restored += 1
                continue
            if row.expires_at is None or str(row.expires_at) > current:
                report.skipped_not_due += 1
                continue
            # If the entry belongs to a deletion batch, the batch must also be
            # past its window before we destroy anything.
            if row.deletion_batch_id is not None:
                due = connection.execute(
                    text(
                        "SELECT 1 FROM deletion_batches WHERE id=:bid "
                        "AND expires_at <= :now"
                    ),
                    {"bid": row.deletion_batch_id, "now": current},
                ).first()
                if due is None:
                    report.skipped_not_due += 1
                    continue

            if dry_run:
                continue

            # Claim the item for purging (CAS guards against restore/race).
            claimed = connection.execute(
                text(
                    "UPDATE trash_items SET purge_state='PURGING_DB', "
                    "claim_fencing_token=claim_fencing_token+1, updated_at=:now "
                    "WHERE id=:id AND purge_state=:eligible"
                ),
                {"id": str(row.id), "eligible": _ELIGIBLE, "now": current},
            )
            if claimed.rowcount != 1:
                report.skipped_not_due += 1
                continue

            try:
                delete_sql = _resource_delete_sql(str(row.resource_type))
                if delete_sql is not None:
                    connection.execute(
                        text(delete_sql),
                        {
                            "tenant": str(row.tenant_id),
                            "resource_id": str(row.resource_id),
                            "generation": int(row.deletion_generation),
                        },
                    )
                connection.execute(
                    text(
                        "UPDATE trash_items SET purge_state=:terminal, "
                        "purged_at=:now, updated_at=:now WHERE id=:id"
                    ),
                    {
                        "terminal": _TERMINAL_PURGED,
                        "now": current,
                        "id": str(row.id),
                    },
                )
                report.purged += 1
                report.purged_resource_ids.append(str(row.resource_id))
            except Exception as exc:  # noqa: BLE001 - record and continue batch
                report.failed += 1
                report.errors.append(f"{row.id}: {exc}")
                connection.execute(
                    text(
                        "UPDATE trash_items SET purge_state='RETRY_WAIT', "
                        "updated_at=:now WHERE id=:id"
                    ),
                    {"now": current, "id": str(row.id)},
                )

    return report


def schedule_purge_job(
    *,
    engine: Optional[Engine] = None,
    tenant_id: str,
    batch_size: int = 200,
    now: Optional[str] = None,
) -> str:
    """Enqueue a tenant purge job via the v4 job queue (operational hook).

    The purge itself still runs through :func:`purge_expired_trash`; this only
    records the intent so the Jobs Center can show/observe it.  Returns the
    job id.
    """
    from ekb_api.services.jobs import get_job_service

    service = get_job_service(engine)
    schedule_time = now or utc_now()
    # A scheduler tick may run more than once per hour; idempotency keeps one
    # durable cleanup job per tenant/hour while still allowing the next window.
    bucket = schedule_time[:13]
    result = service.enqueue(
        tenant_id=tenant_id,
        job_type="retention_purge",
        idempotency_key=f"retention_purge:{tenant_id}:{bucket}",
        payload={"batch_size": batch_size},
        now=schedule_time,
    )
    return result.job.id


__all__ = ["PurgeReport", "purge_expired_trash", "schedule_purge_job"]

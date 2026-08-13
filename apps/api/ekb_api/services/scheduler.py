"""Small DB-leased scheduler tick for local and single-process deployments.

The scheduler only claims a short lease and delegates deletion to the existing
CAS-protected retention service.  It never becomes the source of truth and it
does not run in production until the deployment explicitly enables the worker
process topology.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from sqlalchemy import Engine, text

from ekb_api.core.db import get_engine
from ekb_api.domain import utc_now
from ekb_api.services.retention import PurgeReport, purge_expired_trash, schedule_purge_job


@dataclass(frozen=True)
class SchedulerTick:
    acquired: bool
    enqueued: int
    purged: int
    report: PurgeReport


def _acquire_lease(engine: Engine, *, owner_id: str, now: str, lease_seconds: int) -> bool:
    from ekb_api.services.jobs import _after

    expires = _after(now, lease_seconds)
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            row = connection.execute(
                text(
                    "SELECT owner_id, lease_expires_at FROM scheduler_leases "
                    "WHERE schedule_name=:name FOR UPDATE"
                ),
                {"name": "retention_purge"},
            ).first()
        else:
            row = connection.execute(
                text(
                    "SELECT owner_id, lease_expires_at FROM scheduler_leases "
                    "WHERE schedule_name=:name"
                ),
                {"name": "retention_purge"},
            ).first()
        if row is not None and str(row.owner_id) != owner_id and str(row.lease_expires_at) > now:
            return False
        if row is None:
            connection.execute(
                text(
                    "INSERT INTO scheduler_leases "
                    "(schedule_name, owner_id, lease_expires_at, fencing_token) "
                    "VALUES ('retention_purge',:owner,:expires,1)"
                ),
                {"owner": owner_id, "expires": expires},
            )
        else:
            connection.execute(
                text(
                    "UPDATE scheduler_leases SET owner_id=:owner, lease_expires_at=:expires, "
                    "fencing_token=fencing_token+1 WHERE schedule_name='retention_purge'"
                ),
                {"owner": owner_id, "expires": expires},
            )
        return True


def run_retention_tick(
    engine: Optional[Engine] = None,
    *,
    owner_id: Optional[str] = None,
    now: Optional[str] = None,
    lease_seconds: int = 55,
    batch_size: int = 200,
) -> SchedulerTick:
    """Claim the retention schedule and purge due rows once."""
    engine = engine or get_engine()
    current = now or utc_now()
    owner = owner_id or f"local-scheduler-{uuid4().hex}"
    if not _acquire_lease(engine, owner_id=owner, now=current, lease_seconds=lease_seconds):
        return SchedulerTick(False, 0, 0, PurgeReport())

    with engine.connect() as connection:
        tenants = [str(row[0]) for row in connection.execute(text("SELECT id FROM tenants"))]
    enqueued = 0
    for tenant_id in tenants:
        schedule_purge_job(
            engine=engine,
            tenant_id=tenant_id,
            batch_size=batch_size,
            now=current,
        )
        enqueued += 1

    report = PurgeReport()
    from ekb_api.services.jobs import get_job_service

    job_service = get_job_service(engine)
    for tenant_id in tenants:
        claimed = job_service.claim_next(
            worker_id=owner,
            tenant_id=tenant_id,
            job_type="retention_purge",
            now=current,
            lease_seconds=lease_seconds,
        )
        if claimed is None:
            continue
        try:
            tenant_report = purge_expired_trash(
                engine,
                tenant_id=tenant_id,
                now=current,
                batch_size=batch_size,
            )
            for field in ("evaluated", "purged", "skipped_restored", "skipped_not_due", "failed"):
                setattr(report, field, getattr(report, field) + getattr(tenant_report, field))
            report.purged_resource_ids.extend(tenant_report.purged_resource_ids)
            report.errors.extend(tenant_report.errors)
            job_service.complete(
                tenant_id=tenant_id,
                job_id=claimed.job.id,
                worker_id=owner,
                now=current,
            )
        except Exception as exc:  # noqa: BLE001 - worker error is sanitized by JobService
            report.failed += 1
            report.errors.append(f"retention_purge:{tenant_id}:{type(exc).__name__}")
            job_service.fail(
                tenant_id=tenant_id,
                job_id=claimed.job.id,
                worker_id=owner,
                error_code="RETENTION_PURGE_FAILED",
                sanitized_error={"code": "RETENTION_PURGE_FAILED", "retryable": True},
                now=current,
            )
    return SchedulerTick(True, enqueued, report.purged, report)


__all__ = ["SchedulerTick", "run_retention_tick"]

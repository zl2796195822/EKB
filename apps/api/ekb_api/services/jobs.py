"""Database-backed runtime job primitives for PH1.

The service deliberately uses the v4 tables through parameterized SQL instead
of ORM ``create_all``.  A returned object is a snapshot only; every state
change is guarded by a database compare-and-set predicate.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import Engine, text

from ekb_api.core.db import get_engine
from ekb_api.domain import utc_now

_TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "DEAD"})


class JobStateConflict(RuntimeError):
    """The requested transition lost its database CAS race or lease."""


class JobNotFound(LookupError):
    """The requested tenant-scoped job does not exist."""


@dataclass(frozen=True)
class JobView:
    id: str
    tenant_id: str
    job_type: str
    idempotency_key: str
    state: str
    priority: int
    max_attempts: int
    payload: dict[str, Any]
    available_at: str
    lease_owner: Optional[str]
    lease_expires_at: Optional[str]
    heartbeat_at: Optional[str]
    error_code: Optional[str]
    sanitized_error: Optional[dict[str, Any]]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EnqueueResult:
    job: JobView
    created: bool


@dataclass(frozen=True)
class ClaimedJob:
    job: JobView
    attempt_id: str
    attempt_no: int
    worker_id: str


@dataclass(frozen=True)
class OutboxEvent:
    id: str
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload: dict[str, Any]
    published_at: Optional[str]
    created_at: str


@dataclass(frozen=True)
class CleanupProjection:
    """Read-only aggregate view of a tenant's cleanup/purge jobs (PH2 · T05).

    Powers the Jobs Center "cleanup projection": how many ``retention_purge``
    jobs sit in each state, plus the most recent ones for quick inspection.
    Counts are computed over the whole tenant+job_type window, not just the
    ``recent`` slice, so the dashboard totals are exact.
    """

    job_type: str
    total: int
    by_state: dict[str, int]
    recent: list[JobView]


@dataclass(frozen=True)
class WorkerHeartbeatView:
    worker_id: str
    worker_type: str
    queues: list[str]
    version: str
    heartbeat_at: str
    started_at: str


@dataclass(frozen=True)
class SchedulerLeaseView:
    schedule_name: str
    owner_id: str
    lease_expires_at: str
    fencing_token: int


@dataclass(frozen=True)
class SchedulerRunView:
    id: str
    schedule_name: str
    scope_type: str
    started_at: str
    ended_at: Optional[str]
    status: str


@dataclass(frozen=True)
class RuntimeSnapshot:
    status: str
    workers: list[WorkerHeartbeatView]
    leases: list[SchedulerLeaseView]
    recent_runs: list[SchedulerRunView]
    checked_at: str


# Job types the Jobs Center surfaces as "cleanup" operations.
CLEANUP_JOB_TYPES = ("retention_purge",)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_json(value: Any, *, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _now(value: Optional[str] = None) -> str:
    if value:
        return value
    return utc_now()


def _after(now: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _sanitized_error(error: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if error is None:
        return None
    if not isinstance(error, dict):
        raise ValueError("sanitized_error must be an object")
    allowed = {"code", "category", "retryable", "message"}
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in error:
            continue
        value = error[key]
        if key == "message":
            result[key] = str(value)[:512]
        elif key == "retryable":
            result[key] = bool(value)
        else:
            result[key] = str(value)[:128]
    return result


def _row_to_job(row: Any) -> JobView:
    return JobView(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        job_type=str(row.job_type),
        idempotency_key=str(row.idempotency_key),
        state=str(row.state),
        priority=int(row.priority),
        max_attempts=int(row.max_attempts),
        payload=_as_json(row.payload, fallback={}),
        available_at=str(row.available_at),
        lease_owner=None if row.lease_owner is None else str(row.lease_owner),
        lease_expires_at=None if row.lease_expires_at is None else str(row.lease_expires_at),
        heartbeat_at=None if row.heartbeat_at is None else str(row.heartbeat_at),
        error_code=None if row.error_code is None else str(row.error_code),
        sanitized_error=_as_json(row.sanitized_error, fallback=None),
        created_at=str(row.created_at),
        updated_at=str(row.updated_at),
    )


def _row_to_outbox(row: Any) -> OutboxEvent:
    return OutboxEvent(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        aggregate_type=str(row.aggregate_type),
        aggregate_id=str(row.aggregate_id),
        event_type=str(row.event_type),
        payload=_as_json(row.payload, fallback={}),
        published_at=None if row.published_at is None else str(row.published_at),
        created_at=str(row.created_at),
    )


def _row_to_worker_heartbeat(row: Any) -> WorkerHeartbeatView:
    raw_queues = _as_json(row.queues, fallback=[])
    queues = raw_queues if isinstance(raw_queues, list) else []
    return WorkerHeartbeatView(
        worker_id=str(row.worker_id),
        worker_type=str(row.worker_type),
        queues=[str(queue) for queue in queues],
        version=str(row.version),
        heartbeat_at=str(row.heartbeat_at),
        started_at=str(row.started_at),
    )


def _row_to_scheduler_lease(row: Any) -> SchedulerLeaseView:
    return SchedulerLeaseView(
        schedule_name=str(row.schedule_name),
        owner_id=str(row.owner_id),
        lease_expires_at=str(row.lease_expires_at),
        fencing_token=int(row.fencing_token),
    )


def _row_to_scheduler_run(row: Any) -> SchedulerRunView:
    return SchedulerRunView(
        id=str(row.id),
        schedule_name=str(row.schedule_name),
        scope_type=str(row.scope_type),
        started_at=str(row.started_at),
        ended_at=None if row.ended_at is None else str(row.ended_at),
        status=str(row.status),
    )


class JobService:
    """A tenant-scoped DB job queue with lease and outbox semantics."""

    def __init__(self, engine: Optional[Engine] = None):
        self.engine = engine or get_engine()

    def _require_job(self, connection, tenant_id: str, job_id: str) -> Any:
        row = connection.execute(
            text("SELECT * FROM background_jobs WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": tenant_id, "id": job_id},
        ).first()
        if row is None:
            raise JobNotFound(f"job not found for tenant: {job_id}")
        return row

    def _write_outbox(
        self,
        connection,
        *,
        tenant_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        params = {
            "id": str(uuid4()),
            "tenant": tenant_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "event_type": event_type,
            "payload": _json(payload),
            "created_at": now,
        }
        if self.engine.dialect.name == "postgresql":
            statement = (
                "INSERT INTO job_outbox "
                "(id, tenant_id, aggregate_type, aggregate_id, event_type, payload, created_at) "
                "VALUES (:id, :tenant, :aggregate_type, :aggregate_id, :event_type, "
                "CAST(:payload AS JSONB), :created_at) ON CONFLICT DO NOTHING"
            )
        else:
            statement = (
                "INSERT OR IGNORE INTO job_outbox "
                "(id, tenant_id, aggregate_type, aggregate_id, event_type, payload, created_at) "
                "VALUES (:id, :tenant, :aggregate_type, :aggregate_id, :event_type, "
                ":payload, :created_at)"
            )
        connection.execute(text(statement), params)

    def enqueue(
        self,
        *,
        tenant_id: str,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        priority: int = 0,
        available_at: Optional[str] = None,
        now: Optional[str] = None,
    ) -> EnqueueResult:
        with self.engine.begin() as connection:
            return self.enqueue_in(
                connection,
                tenant_id=tenant_id,
                job_type=job_type,
                idempotency_key=idempotency_key,
                payload=payload,
                max_attempts=max_attempts,
                priority=priority,
                available_at=available_at,
                now=now,
            )

    def enqueue_in(
        self,
        connection,
        *,
        tenant_id: str,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        priority: int = 0,
        available_at: Optional[str] = None,
        now: Optional[str] = None,
    ) -> EnqueueResult:
        """Enqueue on a caller-owned connection so the write joins its transaction.

        Callers that already hold a transaction (for example the PH3 ingestion
        version writer) must use this variant: opening a second connection from
        the pool would deadlock SQLite and break atomicity on PostgreSQL.
        """

        if not tenant_id or not job_type or not idempotency_key:
            raise ValueError("tenant_id, job_type and idempotency_key are required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        current = _now(now)
        available = available_at or current
        job_id = str(uuid4())
        params = {
            "id": job_id,
            "tenant": tenant_id,
            "job_type": job_type,
            "idempotency": idempotency_key,
            "state": "QUEUED",
            "priority": int(priority),
            "max_attempts": int(max_attempts),
            "payload": _json(payload),
            "available_at": available,
            "created_at": current,
            "updated_at": current,
        }
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            statement = (
                "INSERT INTO background_jobs "
                "(id, tenant_id, job_type, idempotency_key, state, priority, max_attempts, "
                "payload, available_at, created_at, updated_at) VALUES "
                "(:id, :tenant, :job_type, :idempotency, :state, :priority, :max_attempts, "
                "CAST(:payload AS JSONB), :available_at, :created_at, :updated_at) "
                "ON CONFLICT (tenant_id, job_type, idempotency_key) DO NOTHING"
            )
        else:
            statement = (
                "INSERT OR IGNORE INTO background_jobs "
                "(id, tenant_id, job_type, idempotency_key, state, priority, max_attempts, "
                "payload, available_at, created_at, updated_at) VALUES "
                "(:id, :tenant, :job_type, :idempotency, :state, :priority, :max_attempts, "
                ":payload, :available_at, :created_at, :updated_at)"
            )
        result = connection.execute(text(statement), params)
        created = result.rowcount == 1
        row = connection.execute(
            text(
                "SELECT * FROM background_jobs WHERE tenant_id=:tenant AND job_type=:job_type "
                "AND idempotency_key=:idempotency"
            ),
            {"tenant": tenant_id, "job_type": job_type, "idempotency": idempotency_key},
        ).first()
        if row is None:
            raise RuntimeError("job insert did not produce a durable row")
        job = _row_to_job(row)
        if created:
            self._write_outbox(
                connection,
                tenant_id=tenant_id,
                aggregate_type="job",
                aggregate_id=job.id,
                event_type="job.created",
                payload={"job_id": job.id, "job_type": job.job_type},
                now=current,
            )
        return EnqueueResult(job, created)

    def get(self, *, tenant_id: str, job_id: str) -> JobView:
        with self.engine.connect() as connection:
            return _row_to_job(self._require_job(connection, tenant_id, job_id))

    def _recover_expired(self, connection, *, now: str) -> None:
        error_expression = (
            "CAST(:error AS JSONB)"
            if self.engine.dialect.name == "postgresql"
            else ":error"
        )
        expired = connection.execute(
            text(
                "SELECT j.id, j.tenant_id, j.max_attempts, a.id AS attempt_id, "
                "a.attempt_no FROM background_jobs AS j "
                "JOIN background_job_attempts AS a ON a.job_id=j.id "
                "AND a.state='RUNNING' "
                "WHERE j.state='RUNNING' AND j.lease_expires_at IS NOT NULL "
                "AND j.lease_expires_at <= :now"
                + (
                    " FOR UPDATE OF j, a SKIP LOCKED"
                    if self.engine.dialect.name == "postgresql"
                    else ""
                )
            ),
            {"now": now},
        ).all()
        for row in expired:
            attempt_result = connection.execute(
                text(
                    "UPDATE background_job_attempts SET state='LEASE_EXPIRED', ended_at=:now, "
                    f"sanitized_error={error_expression} "
                    "WHERE id=:attempt AND state='RUNNING'"
                ),
                {
                    "attempt": str(row.attempt_id),
                    "now": now,
                    "error": _json(
                        {
                            "code": "LEASE_EXPIRED",
                            "retryable": int(row.attempt_no) < int(row.max_attempts),
                        }
                    ),
                },
            )
            if attempt_result.rowcount != 1:
                continue
            dead = int(row.attempt_no) >= int(row.max_attempts)
            next_state = "DEAD" if dead else "RETRY_WAIT"
            job_result = connection.execute(
                text(
                    "UPDATE background_jobs SET state=:state, available_at=:available, "
                    "lease_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL, "
                    f"error_code='LEASE_EXPIRED', sanitized_error={error_expression}, "
                    "updated_at=:now "
                    "WHERE id=:job AND state='RUNNING' AND lease_expires_at <= :now"
                ),
                {
                    "job": str(row.id),
                    "state": next_state,
                    "available": now,
                    "error": _json({"code": "LEASE_EXPIRED", "retryable": not dead}),
                    "now": now,
                },
            )
            if job_result.rowcount != 1:
                raise JobStateConflict("lease recovery CAS rejected")
            self._write_outbox(
                connection,
                tenant_id=str(row.tenant_id),
                aggregate_type="job",
                aggregate_id=str(row.id),
                event_type="job.dead" if dead else "job.retry",
                payload={
                    "job_id": str(row.id),
                    "error_code": "LEASE_EXPIRED",
                    "attempt_no": int(row.attempt_no),
                },
                now=now,
            )
            if dead:
                self._write_outbox(
                    connection,
                    tenant_id=str(row.tenant_id),
                    aggregate_type="job",
                    aggregate_id=str(row.id),
                    event_type="job.dlq",
                    payload={
                        "job_id": str(row.id),
                        "error_code": "LEASE_EXPIRED",
                        "attempt_no": int(row.attempt_no),
                    },
                    now=now,
                )

    def claim_next(
        self,
        *,
        worker_id: str,
        tenant_id: Optional[str] = None,
        job_type: Optional[str] = None,
        now: Optional[str] = None,
        lease_seconds: int = 60,
    ) -> Optional[ClaimedJob]:
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        current = _now(now)
        expiry = _after(current, lease_seconds)
        with self.engine.begin() as connection:
            self._recover_expired(connection, now=current)
            clauses = ["state IN ('QUEUED','RETRY_WAIT')", "available_at <= :now"]
            query_params: dict[str, Any] = {"now": current}
            if tenant_id is not None:
                clauses.append("tenant_id=:tenant")
                query_params["tenant"] = tenant_id
            if job_type is not None:
                clauses.append("job_type=:job_type")
                query_params["job_type"] = job_type
            query = (
                "SELECT * FROM background_jobs WHERE "
                + " AND ".join(clauses)
                + " ORDER BY priority DESC, available_at ASC, created_at ASC LIMIT 1"
            )
            if self.engine.dialect.name == "postgresql":
                query += " FOR UPDATE SKIP LOCKED"
            candidate = connection.execute(text(query), query_params).first()
            if candidate is None:
                return None
            updated = connection.execute(
                text(
                    "UPDATE background_jobs SET state='RUNNING', lease_owner=:worker, "
                    "lease_expires_at=:expiry, heartbeat_at=:now, updated_at=:now "
                    "WHERE id=:id AND state IN ('QUEUED','RETRY_WAIT') AND available_at <= :now"
                ),
                {"worker": worker_id, "expiry": expiry, "now": current, "id": candidate.id},
            )
            if updated.rowcount != 1:
                return None
            attempt_no = int(
                connection.execute(
                    text(
                        "SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM background_job_attempts "
                        "WHERE job_id=:job"
                    ),
                    {"job": candidate.id},
                ).scalar_one()
            )
            attempt_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO background_job_attempts "
                    "(id, tenant_id, job_id, attempt_no, worker_id, state, started_at) "
                    "VALUES (:id, :tenant, :job, :attempt_no, :worker, 'RUNNING', :started)"
                ),
                {
                    "id": attempt_id,
                    "tenant": candidate.tenant_id,
                    "job": candidate.id,
                    "attempt_no": attempt_no,
                    "worker": worker_id,
                    "started": current,
                },
            )
            row = connection.execute(
                text("SELECT * FROM background_jobs WHERE id=:id"), {"id": candidate.id}
            ).first()
            return ClaimedJob(_row_to_job(row), attempt_id, attempt_no, worker_id)

    def heartbeat(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        now: Optional[str] = None,
        lease_seconds: int = 60,
    ) -> JobView:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        current = _now(now)
        expiry = _after(current, lease_seconds)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE background_jobs SET heartbeat_at=:now, lease_expires_at=:expiry, "
                    "updated_at=:now WHERE tenant_id=:tenant AND id=:id AND state='RUNNING' "
                    "AND lease_owner=:worker AND lease_expires_at > :now"
                ),
                {
                    "tenant": tenant_id,
                    "id": job_id,
                    "worker": worker_id,
                    "now": current,
                    "expiry": expiry,
                },
            )
            if result.rowcount != 1:
                raise JobStateConflict("heartbeat CAS rejected: owner or lease is invalid")
            return _row_to_job(self._require_job(connection, tenant_id, job_id))

    def complete(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        now: Optional[str] = None,
    ) -> JobView:
        current = _now(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE background_jobs SET state='SUCCEEDED', lease_owner=NULL, "
                    "lease_expires_at=NULL, heartbeat_at=NULL, updated_at=:now "
                    "WHERE tenant_id=:tenant AND id=:id AND state='RUNNING' "
                    "AND lease_owner=:worker AND lease_expires_at > :now"
                ),
                {"tenant": tenant_id, "id": job_id, "worker": worker_id, "now": current},
            )
            if result.rowcount != 1:
                raise JobStateConflict("complete CAS rejected: owner, state or lease is invalid")
            connection.execute(
                text(
                    "UPDATE background_job_attempts SET state='SUCCEEDED', ended_at=:now "
                    "WHERE job_id=:job AND worker_id=:worker AND state='RUNNING'"
                ),
                {"job": job_id, "worker": worker_id, "now": current},
            )
            self._write_outbox(
                connection,
                tenant_id=tenant_id,
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.succeeded",
                payload={"job_id": job_id},
                now=current,
            )
            return _row_to_job(self._require_job(connection, tenant_id, job_id))

    def fail(
        self,
        *,
        tenant_id: str,
        job_id: str,
        worker_id: str,
        error_code: str,
        sanitized_error: Optional[dict[str, Any]] = None,
        retryable: bool = True,
        retry_delay_seconds: int = 0,
        now: Optional[str] = None,
    ) -> JobView:
        if not error_code:
            raise ValueError("error_code is required")
        current = _now(now)
        safe_error = _sanitized_error(sanitized_error) or {
            "code": error_code,
            "retryable": retryable,
        }
        error_expression = (
            "CAST(:sanitized_error AS JSONB)"
            if self.engine.dialect.name == "postgresql"
            else ":sanitized_error"
        )
        attempt_error_expression = (
            "CAST(:error AS JSONB)"
            if self.engine.dialect.name == "postgresql"
            else ":error"
        )
        with self.engine.begin() as connection:
            current_row = self._require_job(connection, tenant_id, job_id)
            if str(current_row.state) != "RUNNING" or str(current_row.lease_owner) != worker_id:
                raise JobStateConflict("fail CAS rejected: owner or state is invalid")
            attempts = int(
                connection.execute(
                    text(
                        "SELECT COUNT(*) FROM background_job_attempts "
                        "WHERE job_id=:job AND state IN ('RUNNING','FAILED','LEASE_EXPIRED')"
                    ),
                    {"job": job_id},
                ).scalar_one()
            )
            dead = (not retryable) or attempts >= int(current_row.max_attempts)
            next_state = "DEAD" if dead else "RETRY_WAIT"
            next_available = current if dead else _after(current, max(0, retry_delay_seconds))
            result = connection.execute(
                text(
                    "UPDATE background_jobs SET state=:state, available_at=:available, "
                    "lease_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL, "
                    f"error_code=:error_code, sanitized_error={error_expression}, "
                    "updated_at=:now "
                    "WHERE tenant_id=:tenant AND id=:id AND state='RUNNING' "
                    "AND lease_owner=:worker AND lease_expires_at > :now"
                ),
                {
                    "state": next_state,
                    "available": next_available,
                    "tenant": tenant_id,
                    "id": job_id,
                    "worker": worker_id,
                    "error_code": error_code[:128],
                    "sanitized_error": _json(safe_error),
                    "now": current,
                },
            )
            if result.rowcount != 1:
                raise JobStateConflict("fail CAS rejected: lease expired")
            connection.execute(
                text(
                    "UPDATE background_job_attempts SET state='FAILED', ended_at=:now, "
                    f"sanitized_error={attempt_error_expression} "
                    "WHERE job_id=:job AND worker_id=:worker "
                    "AND state='RUNNING'"
                ),
                {"now": current, "error": _json(safe_error), "job": job_id, "worker": worker_id},
            )
            self._write_outbox(
                connection,
                tenant_id=tenant_id,
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.dead" if dead else "job.retry",
                payload={"job_id": job_id, "error_code": error_code[:128]},
                now=current,
            )
            if dead:
                self._write_outbox(
                    connection,
                    tenant_id=tenant_id,
                    aggregate_type="job",
                    aggregate_id=job_id,
                    event_type="job.dlq",
                    payload={"job_id": job_id, "error_code": error_code[:128]},
                    now=current,
                )
            return _row_to_job(self._require_job(connection, tenant_id, job_id))

    def heartbeat_worker(
        self,
        *,
        worker_id: str,
        worker_type: str,
        queues: Iterable[str],
        version: str,
        now: Optional[str] = None,
    ) -> None:
        current = _now(now)
        with self.engine.begin() as connection:
            params = {
                "worker": worker_id,
                "worker_type": worker_type,
                "queues": _json(list(queues)),
                "version": version,
                "now": current,
            }
            if self.engine.dialect.name == "postgresql":
                statement = (
                    "INSERT INTO worker_heartbeats "
                    "(worker_id, worker_type, queues, version, heartbeat_at, started_at) "
                    "VALUES (:worker, :worker_type, CAST(:queues AS JSONB), :version, :now, :now) "
                    "ON CONFLICT(worker_id) DO UPDATE SET worker_type=EXCLUDED.worker_type, "
                    "queues=EXCLUDED.queues, version=EXCLUDED.version, "
                    "heartbeat_at=EXCLUDED.heartbeat_at"
                )
            else:
                statement = (
                    "INSERT OR REPLACE INTO worker_heartbeats "
                    "(worker_id, worker_type, queues, version, heartbeat_at, started_at) "
                    "VALUES (:worker, :worker_type, :queues, :version, :now, "
                    "COALESCE((SELECT started_at FROM worker_heartbeats "
                    "WHERE worker_id=:worker), :now))"
                )
            connection.execute(text(statement), params)

    def pending_outbox(self, *, tenant_id: str, limit: int = 100) -> list[OutboxEvent]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM job_outbox WHERE tenant_id=:tenant AND published_at IS NULL "
                    "ORDER BY created_at ASC LIMIT :limit"
                ),
                {"tenant": tenant_id, "limit": max(1, min(limit, 1000))},
            ).all()
            return [_row_to_outbox(row) for row in rows]

    def mark_outbox_published(
        self, *, tenant_id: str, event_id: str, now: Optional[str] = None
    ) -> OutboxEvent:
        current = _now(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE job_outbox SET published_at=:now WHERE id=:id AND tenant_id=:tenant "
                    "AND published_at IS NULL"
                ),
                {"now": current, "id": event_id, "tenant": tenant_id},
            )
            if result.rowcount != 1:
                raise JobStateConflict("outbox publish CAS rejected")
            row = connection.execute(
                text("SELECT * FROM job_outbox WHERE id=:id AND tenant_id=:tenant"),
                {"id": event_id, "tenant": tenant_id},
            ).first()
            return _row_to_outbox(row)


    def list_for_tenant(
        self,
        *,
        tenant_id: str,
        states: Optional[Iterable[str]] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[JobView]:
        """Tenant-scoped job listing for the Jobs Center UI.

        The ``tenant_id`` predicate is mandatory and cannot be disabled, so a
        caller can only ever observe jobs within their own tenant.
        """
        clauses = ["tenant_id=:tenant"]
        params: dict[str, Any] = {
            "tenant": tenant_id,
            "limit": max(1, min(limit, 500)),
        }
        if states:
            distinct = [str(s) for s in states if s]
            if distinct:
                placeholders = [f":state_{i}" for i in range(len(distinct))]
                clauses.append(f"state IN ({', '.join(placeholders)})")
                for i, value in enumerate(distinct):
                    params[f"state_{i}"] = value
        if job_type:
            clauses.append("job_type=:job_type")
            params["job_type"] = str(job_type)
        statement = (
            "SELECT * FROM background_jobs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, priority DESC LIMIT :limit"
        )
        with self.engine.connect() as connection:
            rows = connection.execute(text(statement), params).all()
            return [_row_to_job(row) for row in rows]

    def cleanup_projection(
        self,
        *,
        tenant_id: str,
        job_type: str = "retention_purge",
        recent_limit: int = 10,
    ) -> CleanupProjection:
        """Tenant-scoped read-only projection of cleanup jobs for the Jobs Center.

        Counts the tenant's jobs of ``job_type`` grouped by state (exact totals,
        not truncated by ``recent_limit``) and returns the most recent ones as
        :class:`JobView` for inline inspection.  The ``tenant_id`` predicate is
        mandatory — this can never observe another tenant's jobs.
        """
        with self.engine.connect() as connection:
            counts = connection.execute(
                text(
                    "SELECT state, COUNT(*) AS n FROM background_jobs "
                    "WHERE tenant_id=:tenant AND job_type=:jt GROUP BY state"
                ),
                {"tenant": tenant_id, "jt": job_type},
            ).all()
            by_state = {str(row.state): int(row.n) for row in counts}
            recent_rows = connection.execute(
                text(
                    "SELECT * FROM background_jobs WHERE tenant_id=:tenant "
                    "AND job_type=:jt ORDER BY created_at DESC LIMIT :lim"
                ),
                {
                    "tenant": tenant_id,
                    "jt": job_type,
                    "lim": max(1, min(recent_limit, 100)),
                },
            ).all()
        recent = [_row_to_job(row) for row in recent_rows]
        return CleanupProjection(
            job_type=job_type,
            total=sum(by_state.values()),
            by_state=by_state,
            recent=recent,
        )

    def runtime_snapshot(
        self,
        *,
        tenant_id: str,
        worker_limit: int = 50,
        lease_limit: int = 10,
        recent_runs_limit: int = 20,
    ) -> RuntimeSnapshot:
        """Return a redacted, tenant-safe snapshot of runtime coordination state.

        Worker heartbeats and scheduler leases are platform coordination rows.
        Scheduler runs are visible when they are platform-wide or belong to the
        caller's tenant.  Payloads, counters, errors, and tenant identifiers
        are intentionally never selected into this projection.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")
        worker_limit = max(1, min(worker_limit, 100))
        lease_limit = max(1, min(lease_limit, 50))
        recent_runs_limit = max(1, min(recent_runs_limit, 100))
        with self.engine.connect() as connection:
            workers = connection.execute(
                text(
                    "SELECT worker_id, worker_type, queues, version, heartbeat_at, started_at "
                    "FROM worker_heartbeats "
                    "WHERE worker_type <> 'local-scheduler' OR "
                    "worker_id IN (SELECT owner_id FROM scheduler_leases) "
                    "ORDER BY heartbeat_at DESC LIMIT :limit"
                ),
                {"limit": worker_limit},
            ).all()
            leases = connection.execute(
                text(
                    "SELECT schedule_name, owner_id, lease_expires_at, fencing_token "
                    "FROM scheduler_leases WHERE schedule_name='retention_purge' "
                    "ORDER BY schedule_name ASC LIMIT :limit"
                ),
                {"limit": lease_limit},
            ).all()
            runs = connection.execute(
                text(
                    "SELECT id, schedule_name, scope_type, started_at, ended_at, status "
                    "FROM scheduler_runs "
                    "WHERE scope_type='PLATFORM' OR "
                    "(scope_type='TENANT' AND tenant_id=:tenant) "
                    "ORDER BY started_at DESC LIMIT :limit"
                ),
                {"tenant": tenant_id, "limit": recent_runs_limit},
            ).all()
        snapshot_workers = [_row_to_worker_heartbeat(row) for row in workers]
        snapshot_leases = [_row_to_scheduler_lease(row) for row in leases]
        snapshot_runs = [_row_to_scheduler_run(row) for row in runs]
        status = (
            "available"
            if snapshot_workers or snapshot_leases or snapshot_runs
            else "unavailable"
        )
        return RuntimeSnapshot(
            status=status,
            workers=snapshot_workers,
            leases=snapshot_leases,
            recent_runs=snapshot_runs,
            checked_at=utc_now(),
        )

    def cancel(
        self,
        *,
        tenant_id: str,
        job_id: str,
        actor_id: str,
        now: Optional[str] = None,
    ) -> JobView:
        """Tenant-admin cancel of a non-terminal job.

        Marks the job CANCELLED, releases any held lease and writes a
        ``job.cancelled`` outbox event.  Already-terminal jobs are returned
        unchanged (idempotent).  The transition is guarded by a CAS on the
        previously-observed state so a concurrent worker finish cannot be
        clobbered.
        """
        if not actor_id:
            raise ValueError("actor_id is required")
        current = _now(now)
        with self.engine.begin() as connection:
            row = self._require_job(connection, tenant_id, job_id)
            current_state = str(row.state)
            if current_state in _TERMINAL_STATES:
                return _row_to_job(row)
            result = connection.execute(
                text(
                    "UPDATE background_jobs SET state='CANCELLED', lease_owner=NULL, "
                    "lease_expires_at=NULL, heartbeat_at=NULL, updated_at=:now "
                    "WHERE tenant_id=:tenant AND id=:id AND state=:expected"
                ),
                {"tenant": tenant_id, "id": job_id, "expected": current_state, "now": current},
            )
            if result.rowcount != 1:
                raise JobStateConflict("cancel CAS rejected: state changed concurrently")
            connection.execute(
                text(
                    "UPDATE background_job_attempts SET state='CANCELLED', ended_at=:now "
                    "WHERE job_id=:job AND state='RUNNING'"
                ),
                {"job": job_id, "now": current},
            )
            self._write_outbox(
                connection,
                tenant_id=tenant_id,
                aggregate_type="job",
                aggregate_id=job_id,
                event_type="job.cancelled",
                payload={"job_id": job_id, "actor_id": actor_id},
                now=current,
            )
            return _row_to_job(self._require_job(connection, tenant_id, job_id))


def get_job_service(engine: Optional[Engine] = None) -> JobService:
    return JobService(engine or get_engine())


__all__ = [
    "ClaimedJob",
    "EnqueueResult",
    "JobNotFound",
    "JobService",
    "JobStateConflict",
    "JobView",
    "OutboxEvent",
    "RuntimeSnapshot",
    "SchedulerLeaseView",
    "SchedulerRunView",
    "WorkerHeartbeatView",
    "get_job_service",
]

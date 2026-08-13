"""PH3 ingestion pipeline: version creation, attempt lifecycle and stage ledger.

Contract (spec `06-ingestion.md`):

* A completed upload item becomes a ``document_versions`` row plus exactly one
  ``ingest_jobs`` row (DB-enforced by ``uq_ingest_job_version``).
* Content dedup is per document: re-uploading identical bytes to the same
  normalized path returns the existing version instead of forking a new one.
* Each processing run is an ``ingest_job_attempts`` row. At most one attempt per
  job may be non-terminal (DB-enforced by ``uq_ingest_one_active_attempt``), and
  the stage order is fixed:
  ``VALIDATING -> CONVERTING -> PARSING -> CHUNKING -> EMBEDDING -> INDEXING``.
* Concurrent writers are serialised with compare-and-swap on
  ``ingest_jobs.state_version``; a stale writer gets ``IngestStateConflict``.
* Stage results are recorded in ``ingest_stages`` keyed by
  ``(attempt_id, stage, input_hash)`` so a resumed attempt skips stages whose
  input did not change.
* Errors are stored as stable codes from ``services.parsers.registry`` with a
  sanitized detail payload — never a raw provider trace.

Every statement binds ``tenant_id``: tenant isolation is enforced by
construction, not by a caller-supplied filter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.services.parsers.registry import is_retryable, sanitize_detail

STAGES = (
    "VALIDATING",
    "CONVERTING",
    "PARSING",
    "CHUNKING",
    "EMBEDDING",
    "INDEXING",
)
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
ACTIVE_STATES = frozenset({"WAITING", *STAGES})
INGEST_JOB_TYPE = "document_ingest"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 300


def _sync_promotion_status(
    connection,
    *,
    tenant_id: str,
    ingest_job_id: str,
    status: str,
    from_statuses: tuple[str, ...],
) -> None:
    """Project a real PH3 job transition onto its optional PH5 promotion row."""
    if status not in {"PROCESSING", "SUCCEEDED", "FAILED"}:
        raise ValueError(f"unsupported promotion status: {status}")
    if not inspect(connection).has_table("attachment_promotions"):
        return
    placeholders = ",".join(f":from_{index}" for index in range(len(from_statuses)))
    params: dict[str, Any] = {
        "tenant": tenant_id,
        "job": ingest_job_id,
        "status": status,
    }
    params.update({f"from_{index}": value for index, value in enumerate(from_statuses)})
    connection.execute(
        text(
            "UPDATE attachment_promotions SET status=:status "
            "WHERE tenant_id=:tenant AND ingest_job_id=:job "
            f"AND status IN ({placeholders})"
        ),
        params,
    )


def sync_promotion_status_for_job(
    engine: Engine,
    *,
    tenant_id: str,
    ingest_job_id: str,
    status: str,
) -> None:
    """Best-effort projection for failures that occur before an ingest attempt."""
    with engine.begin() as connection:
        _sync_promotion_status(
            connection,
            tenant_id=tenant_id,
            ingest_job_id=ingest_job_id,
            status=status,
            from_statuses=("QUEUED", "PROCESSING", "FAILED"),
        )

_NEXT_STATE = {"WAITING": STAGES[0]}
for _i, _stage in enumerate(STAGES):
    _NEXT_STATE[_stage] = STAGES[_i + 1] if _i + 1 < len(STAGES) else "SUCCEEDED"


class IngestStateConflict(RuntimeError):
    """Raised when a compare-and-swap on the ingest job loses the race."""


class IngestNotFound(LookupError):
    """Raised when a job/attempt does not exist inside the bound tenant."""


class IngestTransitionError(RuntimeError):
    """Raised when a caller requests a stage order the state machine forbids."""


@dataclass(frozen=True)
class VersionOutcome:
    version_id: str
    document_id: str
    ingest_job_id: str
    is_new: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "document_id": self.document_id,
            "ingest_job_id": self.ingest_job_id,
            "is_new": self.is_new,
        }


@dataclass(frozen=True)
class AttemptView:
    id: str
    ingest_job_id: str
    attempt_no: int
    state: str
    current_stage: Optional[str]
    progress_current: int
    progress_total: Optional[int]
    error_code: Optional[str]
    sanitized_error: Optional[dict[str, Any]]
    lease_owner: Optional[str]
    lease_expires_at: Optional[str]


@dataclass(frozen=True)
class IngestProjection:
    ingest_job_id: str
    document_id: str
    document_version_id: str
    status: str
    attempts: int
    max_attempts: int
    state_version: int
    active_attempt: Optional[AttemptView]
    stages: list[dict[str, Any]] = field(default_factory=list)


def _json_load(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _stage_input_hash(*parts: Any) -> str:
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_expr(dialect: str, parameter: str) -> str:
    """Render a JSON bind parameter. PostgreSQL needs an explicit cast."""

    return f"CAST(:{parameter} AS JSONB)" if dialect == "postgresql" else f":{parameter}"


def _stage_insert(dialect: str) -> str:
    """Idempotent stage ledger insert. The unique key is (attempt_id, stage, input_hash)."""

    columns = (
        "INSERT INTO ingest_stages (id, tenant_id, attempt_id, stage, status, input_hash, "
        "metrics, started_at, ended_at) VALUES (:id, :tenant, :attempt, :stage, 'SUCCEEDED', "
        ":hash, {metrics}, :now, :now)"
    )
    if dialect == "postgresql":
        return columns.format(metrics="CAST(:metrics AS JSONB)") + (
            " ON CONFLICT (attempt_id, stage, input_hash) DO NOTHING"
        )
    return columns.format(metrics=":metrics").replace(
        "INSERT INTO ingest_stages", "INSERT OR IGNORE INTO ingest_stages"
    )


def _leaf_title(normalized_relative_path: str) -> str:
    leaf = normalized_relative_path.rstrip("/").rsplit("/", 1)[-1]
    return leaf or normalized_relative_path or "untitled"


def create_version_for_item(
    connection,
    *,
    tenant_id: str,
    kb_id: str,
    normalized_relative_path: str,
    display_path: str,
    sha256: str,
    byte_size: int,
    detected_mime: str,
    source_object_id: str,
    job_service,
    owner_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Promote an uploaded object into a document version + queued ingest job.

    Runs entirely on the caller's ``connection`` so the version, the ingest job
    and the background job row commit atomically with the upload completion.
    """

    now = utc_now()
    dialect = connection.engine.dialect.name
    kb = connection.execute(
        text(
            "SELECT id FROM knowledge_bases WHERE id=:kb AND tenant_id=:tenant "
            "AND purged_at IS NULL"
        ),
        {"kb": kb_id, "tenant": tenant_id},
    ).first()
    if kb is None:
        raise IngestNotFound("knowledge base not found in tenant")

    document = connection.execute(
        text(
            "SELECT id, version FROM documents WHERE tenant_id=:tenant AND kb_id=:kb "
            "AND normalized_relative_path=:path AND purged_at IS NULL"
        ),
        {"tenant": tenant_id, "kb": kb_id, "path": normalized_relative_path},
    ).first()

    if document is None:
        document_id = str(uuid4())
        connection.execute(
            text(
                "INSERT INTO documents (id, tenant_id, kb_id, title, status, version, "
                "mime_type, checksum, chunk_count, source_type, created_at, updated_at, "
                "normalized_relative_path) VALUES (:id, :tenant, :kb, :title, 'PENDING', 0, "
                ":mime, :checksum, 0, 'UPLOAD', :now, :now, :path)"
            ),
            {
                "id": document_id,
                "tenant": tenant_id,
                "kb": kb_id,
                "title": _leaf_title(display_path or normalized_relative_path),
                "mime": detected_mime,
                "checksum": sha256,
                "now": now,
                "path": normalized_relative_path,
            },
        )
        current_version = 0
    else:
        document_id = str(document.id)
        current_version = int(document.version or 0)

        existing = connection.execute(
            text(
                "SELECT id FROM document_versions WHERE tenant_id=:tenant AND doc_id=:doc "
                "AND checksum=:sha AND purged_at IS NULL ORDER BY version DESC LIMIT 1"
            ),
            {"tenant": tenant_id, "doc": document_id, "sha": sha256},
        ).first()
        if existing is not None:
            version_id = str(existing.id)
            job = connection.execute(
                text(
                    "SELECT id FROM ingest_jobs WHERE tenant_id=:tenant "
                    "AND document_version_id=:dv LIMIT 1"
                ),
                {"tenant": tenant_id, "dv": version_id},
            ).first()
            return VersionOutcome(
                version_id=version_id,
                document_id=document_id,
                ingest_job_id=str(job.id) if job is not None else "",
                is_new=False,
            ).as_dict()

    next_version = current_version + 1
    version_id = str(uuid4())
    connection.execute(
        text(
            "INSERT INTO document_versions (id, tenant_id, doc_id, version, checksum, "
            "chunk_count, content_snapshot, created_at, source_object_id, ingest_status) "
            "VALUES (:id, :tenant, :doc, :version, :sha, 0, "
            f"{_json_expr(dialect, 'snapshot')}, :now, :src, 'UPLOADED')"
        ),
        {
            "id": version_id,
            "tenant": tenant_id,
            "doc": document_id,
            "version": next_version,
            "sha": sha256,
            "snapshot": json.dumps(
                {
                    "display_path": display_path,
                    "byte_size": int(byte_size),
                    "detected_mime": detected_mime,
                },
                sort_keys=True,
            ),
            "now": now,
            "src": source_object_id,
        },
    )
    connection.execute(
        text(
            "UPDATE documents SET version=:version, status='PROCESSING', mime_type=:mime, "
            "checksum=:sha, updated_at=:now WHERE id=:id AND tenant_id=:tenant"
        ),
        {
            "version": next_version,
            "mime": detected_mime,
            "sha": sha256,
            "now": now,
            "id": document_id,
            "tenant": tenant_id,
        },
    )

    ingest_job_id = str(uuid4())
    idempotency_key = f"ingest:{version_id}"
    connection.execute(
        text(
            "INSERT INTO ingest_jobs (id, tenant_id, kb_id, doc_id, status, attempts, "
            "max_attempts, idempotency_key, created_at, updated_at, document_version_id, "
            "state_version) VALUES (:id, :tenant, :kb, :doc, 'QUEUED', 0, :max, :idem, "
            ":now, :now, :dv, 0)"
        ),
        {
            "id": ingest_job_id,
            "tenant": tenant_id,
            "kb": kb_id,
            "doc": document_id,
            "max": DEFAULT_MAX_ATTEMPTS,
            "idem": idempotency_key,
            "now": now,
            "dv": version_id,
        },
    )
    connection.execute(
        text(
            "UPDATE source_objects SET ref_count = ref_count + 1 "
            "WHERE id=:src AND tenant_id=:tenant"
        ),
        {"src": source_object_id, "tenant": tenant_id},
    )

    job_service.enqueue_in(
        connection,
        tenant_id=tenant_id,
        job_type=INGEST_JOB_TYPE,
        idempotency_key=idempotency_key,
        payload={
            "ingest_job_id": ingest_job_id,
            "document_version_id": version_id,
            "document_id": document_id,
            "kb_id": kb_id,
            "mime": detected_mime,
            "owner_user_id": owner_user_id,
        },
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        now=now,
    )

    return VersionOutcome(
        version_id=version_id,
        document_id=document_id,
        ingest_job_id=ingest_job_id,
        is_new=True,
    ).as_dict()


def _row_to_attempt(row: Any) -> AttemptView:
    return AttemptView(
        id=str(row.id),
        ingest_job_id=str(row.ingest_job_id),
        attempt_no=int(row.attempt_no),
        state=str(row.state),
        current_stage=str(row.current_stage) if row.current_stage else None,
        progress_current=int(row.progress_current or 0),
        progress_total=int(row.progress_total) if row.progress_total is not None else None,
        error_code=str(row.error_code) if row.error_code else None,
        sanitized_error=_json_load(row.sanitized_error, None),
        lease_owner=str(row.lease_owner) if row.lease_owner else None,
        lease_expires_at=str(row.lease_expires_at) if row.lease_expires_at else None,
    )


class IngestService:
    """Attempt lifecycle for PH3. All methods are tenant-bound and CAS-guarded."""

    def __init__(self, engine: Engine):
        self.engine = engine

    # -- internals --

    def _require_job(self, connection, tenant_id: str, ingest_job_id: str) -> Any:
        row = connection.execute(
            text("SELECT * FROM ingest_jobs WHERE id=:id AND tenant_id=:tenant"),
            {"id": ingest_job_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise IngestNotFound("ingest job not found in tenant")
        return row

    def _require_attempt(self, connection, tenant_id: str, attempt_id: str) -> Any:
        row = connection.execute(
            text("SELECT * FROM ingest_job_attempts WHERE id=:id AND tenant_id=:tenant"),
            {"id": attempt_id, "tenant": tenant_id},
        ).first()
        if row is None:
            raise IngestNotFound("ingest attempt not found in tenant")
        return row

    def _bump(self, connection, *, tenant_id: str, job_row: Any, status: str, now: str) -> None:
        """Compare-and-swap ``state_version`` so concurrent writers cannot interleave."""

        expected = int(job_row.state_version or 0)
        result = connection.execute(
            text(
                "UPDATE ingest_jobs SET status=:status, state_version=:next, updated_at=:now "
                "WHERE id=:id AND tenant_id=:tenant AND state_version=:expected"
            ),
            {
                "status": status,
                "next": expected + 1,
                "now": now,
                "id": str(job_row.id),
                "tenant": tenant_id,
                "expected": expected,
            },
        )
        if result.rowcount != 1:
            raise IngestStateConflict(
                f"ingest job {job_row.id} changed concurrently (expected v{expected})"
            )

    # -- attempt lifecycle --

    def start_attempt(
        self,
        *,
        tenant_id: str,
        ingest_job_id: str,
        lease_owner: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: Optional[str] = None,
    ) -> AttemptView:
        current = now or utc_now()
        with self.engine.begin() as connection:
            job = self._require_job(connection, tenant_id, ingest_job_id)
            if str(job.status) == "CANCELLED":
                raise IngestTransitionError("cancelled job cannot start a new attempt")
            active = connection.execute(
                text(
                    "SELECT id FROM ingest_job_attempts WHERE ingest_job_id=:job "
                    "AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')"
                ),
                {"job": ingest_job_id},
            ).first()
            if active is not None:
                raise IngestStateConflict("an attempt is already in flight for this job")
            used = int(job.attempts or 0)
            if used >= int(job.max_attempts or DEFAULT_MAX_ATTEMPTS):
                raise IngestTransitionError("attempt budget exhausted")

            attempt_id = str(uuid4())
            connection.execute(
                text(
                    "INSERT INTO ingest_job_attempts (id, tenant_id, ingest_job_id, attempt_no, "
                    "state, current_stage, progress_current, lease_owner, lease_expires_at, "
                    "heartbeat_at, created_at, updated_at) VALUES (:id, :tenant, :job, :no, "
                    "'WAITING', NULL, 0, :owner, :lease, :now, :now, :now)"
                ),
                {
                    "id": attempt_id,
                    "tenant": tenant_id,
                    "job": ingest_job_id,
                    "no": used + 1,
                    "owner": lease_owner,
                    "lease": _shift(current, lease_seconds),
                    "now": current,
                },
            )
            self._bump(connection, tenant_id=tenant_id, job_row=job, status="RUNNING", now=current)
            connection.execute(
                text(
                    "UPDATE ingest_jobs SET attempts=:attempts, active_attempt_id=:attempt "
                    "WHERE id=:id AND tenant_id=:tenant"
                ),
                {
                    "attempts": used + 1,
                    "attempt": attempt_id,
                    "id": ingest_job_id,
                    "tenant": tenant_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET ingest_status='PROCESSING' "
                    "WHERE id=:dv AND tenant_id=:tenant"
                ),
                {"dv": str(job.document_version_id), "tenant": tenant_id},
            )
            _sync_promotion_status(
                connection,
                tenant_id=tenant_id,
                ingest_job_id=ingest_job_id,
                status="PROCESSING",
                from_statuses=("QUEUED", "FAILED"),
            )
            return _row_to_attempt(self._require_attempt(connection, tenant_id, attempt_id))

    def run_stage(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        stage: str,
        input_hash: Optional[str] = None,
        metrics: Optional[dict[str, Any]] = None,
        progress_current: int = 0,
        progress_total: Optional[int] = None,
        now: Optional[str] = None,
    ) -> AttemptView:
        """Record a stage transition. Rejects any order other than ``STAGES``."""

        if stage not in STAGES:
            raise IngestTransitionError(f"unknown stage {stage!r}")
        current = now or utc_now()
        with self.engine.begin() as connection:
            attempt = self._require_attempt(connection, tenant_id, attempt_id)
            state = str(attempt.state)
            if state in TERMINAL_STATES:
                raise IngestTransitionError(f"attempt already terminal ({state})")
            if _NEXT_STATE.get(state) != stage:
                raise IngestTransitionError(
                    f"illegal transition {state} -> {stage}; expected {_NEXT_STATE.get(state)}"
                )
            job = self._require_job(connection, tenant_id, str(attempt.ingest_job_id))
            digest = input_hash or _stage_input_hash(attempt_id, stage, job.document_version_id)
            connection.execute(
                text(_stage_insert(self.engine.dialect.name)),
                {
                    "id": str(uuid4()),
                    "tenant": tenant_id,
                    "attempt": attempt_id,
                    "stage": stage,
                    "hash": digest,
                    "metrics": json.dumps(metrics or {}, sort_keys=True),
                    "now": current,
                },
            )
            connection.execute(
                text(
                    "UPDATE ingest_job_attempts SET state=:stage, current_stage=:stage, "
                    "progress_current=:cur, progress_total=:total, heartbeat_at=:now, "
                    "updated_at=:now WHERE id=:id AND tenant_id=:tenant"
                ),
                {
                    "stage": stage,
                    "cur": int(progress_current),
                    "total": progress_total,
                    "now": current,
                    "id": attempt_id,
                    "tenant": tenant_id,
                },
            )
            return _row_to_attempt(self._require_attempt(connection, tenant_id, attempt_id))

    def succeed(
        self, *, tenant_id: str, attempt_id: str, now: Optional[str] = None
    ) -> IngestProjection:
        current = now or utc_now()
        with self.engine.begin() as connection:
            attempt = self._require_attempt(connection, tenant_id, attempt_id)
            if str(attempt.state) != STAGES[-1]:
                raise IngestTransitionError(
                    f"cannot succeed from {attempt.state}; {STAGES[-1]} must complete first"
                )
            job = self._require_job(connection, tenant_id, str(attempt.ingest_job_id))
            connection.execute(
                text(
                    "UPDATE ingest_job_attempts SET state='SUCCEEDED', lease_owner=NULL, "
                    "lease_expires_at=NULL, updated_at=:now WHERE id=:id AND tenant_id=:tenant"
                ),
                {"now": current, "id": attempt_id, "tenant": tenant_id},
            )
            self._bump(
                connection, tenant_id=tenant_id, job_row=job, status="SUCCEEDED", now=current
            )
            version_id = str(job.document_version_id)
            connection.execute(
                text(
                    "UPDATE document_versions SET ingest_status='SUCCEEDED', activated_at=:now "
                    "WHERE id=:dv AND tenant_id=:tenant"
                ),
                {"now": current, "dv": version_id, "tenant": tenant_id},
            )
            connection.execute(
                text(
                    "UPDATE documents SET status='READY', active_version_id=:dv, updated_at=:now "
                    "WHERE id=:doc AND tenant_id=:tenant"
                ),
                {"dv": version_id, "now": current, "doc": str(job.doc_id), "tenant": tenant_id},
            )
            _sync_promotion_status(
                connection,
                tenant_id=tenant_id,
                ingest_job_id=str(job.id),
                status="SUCCEEDED",
                from_statuses=("QUEUED", "PROCESSING"),
            )
            return self._projection(connection, tenant_id, str(job.id))

    def fail(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        error_code: str,
        detail: Optional[dict[str, Any]] = None,
        now: Optional[str] = None,
    ) -> IngestProjection:
        current = now or utc_now()
        with self.engine.begin() as connection:
            attempt = self._require_attempt(connection, tenant_id, attempt_id)
            if str(attempt.state) in TERMINAL_STATES:
                raise IngestTransitionError(f"attempt already terminal ({attempt.state})")
            job = self._require_job(connection, tenant_id, str(attempt.ingest_job_id))
            detail_expr = _json_expr(self.engine.dialect.name, "detail")
            connection.execute(
                text(
                    "UPDATE ingest_job_attempts SET state='FAILED', error_code=:code, "
                    f"sanitized_error={detail_expr}, lease_owner=NULL, lease_expires_at=NULL, "
                    "updated_at=:now WHERE id=:id AND tenant_id=:tenant"
                ),
                {
                    "code": error_code,
                    "detail": json.dumps(sanitize_detail(detail), sort_keys=True),
                    "now": current,
                    "id": attempt_id,
                    "tenant": tenant_id,
                },
            )
            budget_left = int(job.attempts or 0) < int(job.max_attempts or DEFAULT_MAX_ATTEMPTS)
            retry = is_retryable(error_code) and budget_left
            self._bump(
                connection,
                tenant_id=tenant_id,
                job_row=job,
                status="QUEUED" if retry else "FAILED",
                now=current,
            )
            _sync_promotion_status(
                connection,
                tenant_id=tenant_id,
                ingest_job_id=str(job.id),
                status="FAILED",
                from_statuses=("QUEUED", "PROCESSING", "FAILED"),
            )
            connection.execute(
                text(
                    "UPDATE ingest_jobs SET active_attempt_id=NULL, error_code=:code "
                    "WHERE id=:id AND tenant_id=:tenant"
                ),
                {"code": error_code, "id": str(job.id), "tenant": tenant_id},
            )
            if not retry:
                connection.execute(
                    text(
                        "UPDATE document_versions SET ingest_status='FAILED' "
                        "WHERE id=:dv AND tenant_id=:tenant"
                    ),
                    {"dv": str(job.document_version_id), "tenant": tenant_id},
                )
                connection.execute(
                    text(
                        "UPDATE documents SET status='FAILED', failure_reason=:code, "
                        "updated_at=:now WHERE id=:doc AND tenant_id=:tenant"
                    ),
                    {
                        "code": error_code,
                        "now": current,
                        "doc": str(job.doc_id),
                        "tenant": tenant_id,
                    },
                )
            return self._projection(connection, tenant_id, str(job.id))

    def cancel(
        self, *, tenant_id: str, ingest_job_id: str, now: Optional[str] = None
    ) -> IngestProjection:
        current = now or utc_now()
        with self.engine.begin() as connection:
            job = self._require_job(connection, tenant_id, ingest_job_id)
            if str(job.status) == "SUCCEEDED":
                raise IngestTransitionError("succeeded job cannot be cancelled")
            connection.execute(
                text(
                    "UPDATE ingest_job_attempts SET state='CANCELLED', lease_owner=NULL, "
                    "lease_expires_at=NULL, updated_at=:now WHERE ingest_job_id=:job "
                    "AND tenant_id=:tenant AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')"
                ),
                {"now": current, "job": ingest_job_id, "tenant": tenant_id},
            )
            self._bump(
                connection, tenant_id=tenant_id, job_row=job, status="CANCELLED", now=current
            )
            _sync_promotion_status(
                connection,
                tenant_id=tenant_id,
                ingest_job_id=ingest_job_id,
                status="FAILED",
                from_statuses=("QUEUED", "PROCESSING", "FAILED"),
            )
            connection.execute(
                text("UPDATE ingest_jobs SET active_attempt_id=NULL WHERE id=:id AND tenant_id=:t"),
                {"id": ingest_job_id, "t": tenant_id},
            )
            connection.execute(
                text(
                    "UPDATE document_versions SET ingest_status='CANCELLED' "
                    "WHERE id=:dv AND tenant_id=:tenant"
                ),
                {"dv": str(job.document_version_id), "tenant": tenant_id},
            )
            return self._projection(connection, tenant_id, ingest_job_id)

    def retry(
        self,
        *,
        tenant_id: str,
        ingest_job_id: str,
        lease_owner: str,
        now: Optional[str] = None,
    ) -> AttemptView:
        with self.engine.connect() as connection:
            job = self._require_job(connection, tenant_id, ingest_job_id)
            if str(job.status) not in ("QUEUED", "FAILED"):
                raise IngestTransitionError(f"cannot retry a {job.status} job")
        return self.start_attempt(
            tenant_id=tenant_id,
            ingest_job_id=ingest_job_id,
            lease_owner=lease_owner,
            now=now,
        )

    def heartbeat(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        lease_owner: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: Optional[str] = None,
    ) -> AttemptView:
        current = now or utc_now()
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    "UPDATE ingest_job_attempts SET heartbeat_at=:now, lease_expires_at=:lease, "
                    "updated_at=:now WHERE id=:id AND tenant_id=:tenant AND lease_owner=:owner "
                    "AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED')"
                ),
                {
                    "now": current,
                    "lease": _shift(current, lease_seconds),
                    "id": attempt_id,
                    "tenant": tenant_id,
                    "owner": lease_owner,
                },
            )
            if result.rowcount != 1:
                raise IngestStateConflict("lease lost or attempt terminal")
            return _row_to_attempt(self._require_attempt(connection, tenant_id, attempt_id))

    def reconcile(self, *, tenant_id: str, now: Optional[str] = None) -> list[str]:
        """Release expired leases so a fresh worker can pick the job up."""

        current = now or utc_now()
        recovered: list[str] = []
        with self.engine.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, ingest_job_id FROM ingest_job_attempts WHERE tenant_id=:tenant "
                    "AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED') "
                    "AND lease_expires_at IS NOT NULL AND lease_expires_at < :now"
                ),
                {"tenant": tenant_id, "now": current},
            ).all()
            for row in rows:
                connection.execute(
                    text(
                        "UPDATE ingest_job_attempts SET state='FAILED', error_code='LEASE_LOST', "
                        "lease_owner=NULL, lease_expires_at=NULL, updated_at=:now "
                        "WHERE id=:id AND tenant_id=:tenant"
                    ),
                    {"now": current, "id": str(row.id), "tenant": tenant_id},
                )
                job = self._require_job(connection, tenant_id, str(row.ingest_job_id))
                self._bump(
                    connection, tenant_id=tenant_id, job_row=job, status="QUEUED", now=current
                )
                _sync_promotion_status(
                    connection,
                    tenant_id=tenant_id,
                    ingest_job_id=str(row.ingest_job_id),
                    status="FAILED",
                    from_statuses=("QUEUED", "PROCESSING", "FAILED"),
                )
                connection.execute(
                    text(
                        "UPDATE ingest_jobs SET active_attempt_id=NULL WHERE id=:id "
                        "AND tenant_id=:tenant"
                    ),
                    {"id": str(row.ingest_job_id), "tenant": tenant_id},
                )
                recovered.append(str(row.ingest_job_id))
        return recovered

    # -- projection --

    def projection(self, *, tenant_id: str, ingest_job_id: str) -> IngestProjection:
        with self.engine.connect() as connection:
            return self._projection(connection, tenant_id, ingest_job_id)

    def _projection(self, connection, tenant_id: str, ingest_job_id: str) -> IngestProjection:
        job = self._require_job(connection, tenant_id, ingest_job_id)
        # Legacy upload path (the compatibility `/kb/{id}/docs` endpoint) can
        # finish before the PH3 attempt worker creates an attempt.  Projection
        # must remain readable against that schema and return a valid empty
        # attempt, rather than turning a completed upload into a 500.
        attempt_table = "ingest_job_attempts"
        try:
            attempt_row = connection.execute(
                text(
                    "SELECT * FROM ingest_job_attempts WHERE ingest_job_id=:job "
                    "AND tenant_id=:tenant "
                    "ORDER BY attempt_no DESC LIMIT 1"
                ),
                {"job": ingest_job_id, "tenant": tenant_id},
            ).first()
        except Exception as exc:  # noqa: BLE001 - compatibility schema probe
            if "no such table" not in str(exc).lower():
                raise
            attempt_table = ""
            attempt_row = None
        stages: list[dict[str, Any]] = []
        if attempt_row is not None and attempt_table:
            stage_rows = connection.execute(
                text(
                    "SELECT stage, status, metrics, started_at, ended_at FROM ingest_stages "
                    "WHERE attempt_id=:attempt AND tenant_id=:tenant"
                ),
                {"attempt": str(attempt_row.id), "tenant": tenant_id},
            ).all()
            order = {name: index for index, name in enumerate(STAGES)}
            stages = sorted(
                (
                    {
                        "stage": str(r.stage),
                        "status": str(r.status),
                        "metrics": _json_load(r.metrics, {}),
                        "started_at": str(r.started_at) if r.started_at else None,
                        "ended_at": str(r.ended_at) if r.ended_at else None,
                    }
                    for r in stage_rows
                ),
                key=lambda item: order.get(item["stage"], 99),
            )
        return IngestProjection(
            ingest_job_id=str(job.id),
            document_id=str(job.doc_id),
            document_version_id=str(getattr(job, "document_version_id", None) or ""),
            status=str(job.status),
            attempts=int(getattr(job, "attempts", 0) or 0),
            max_attempts=int(
                getattr(job, "max_attempts", DEFAULT_MAX_ATTEMPTS) or DEFAULT_MAX_ATTEMPTS
            ),
            state_version=int(getattr(job, "state_version", 0) or 0),
            active_attempt=_row_to_attempt(attempt_row) if attempt_row is not None else None,
            stages=stages,
        )


def _shift(now: str, seconds: int) -> str:
    try:
        base = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return now
    shifted = base + timedelta(seconds=int(seconds))
    return shifted.isoformat().replace("+00:00", "Z")


def get_ingest_service(engine: Engine) -> IngestService:
    return IngestService(engine)

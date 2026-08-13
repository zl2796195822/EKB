"""Document ingestion worker for the local/runtime job queue (PH3).

The worker reads the tenant-scoped source object, parses and chunks it, then
uses only the configured remote embedding provider. A missing provider is a
stable terminal failure; it never creates local vectors or lexical-only READY
documents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Engine, text

from ekb_api.chunking import split_sections
from ekb_api.domain import utc_now
from ekb_api.services.embedding import (
    EmbeddingError,
    EmbeddingUnavailable,
    build_embedding_client,
    resolve_profile,
)
from ekb_api.services.ingestion import (
    STAGES,
    IngestService,
    sync_promotion_status_for_job,
)
from ekb_api.services.jobs import ClaimedJob, JobService
from ekb_api.services.parsers import (
    ParserError,
    get_default_registry,
    is_retryable,
    sanitize_detail,
)
from ekb_api.services.storage import StorageClient, build_storage_client


@dataclass(frozen=True)
class IngestWorkResult:
    job_id: str
    status: str
    error_code: Optional[str] = None


def _stage(
    service: IngestService,
    *,
    tenant_id: str,
    attempt_id: str,
    name: str,
    current: int,
    total: int,
    metrics: Optional[dict[str, Any]] = None,
) -> None:
    service.run_stage(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        stage=name,
        progress_current=current,
        progress_total=total,
        metrics=metrics or {},
    )


def _source(engine: Engine, *, tenant_id: str, version_id: str) -> tuple[str, str, str, str, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT dv.source_object_id, d.kb_id, d.id, d.title, dv.checksum "
                "FROM document_versions dv JOIN documents d ON d.id=dv.doc_id "
                "WHERE dv.id=:version AND dv.tenant_id=:tenant"
            ),
            {"version": version_id, "tenant": tenant_id},
        ).first()
    if row is None or row.source_object_id is None:
        raise ParserError("OBJECT_CHECKSUM_MISMATCH", "摄取版本缺少源对象")
    return str(row.source_object_id), str(row.kb_id), str(row.id), str(row.title), str(row.checksum)


def _object_key(engine: Engine, *, tenant_id: str, source_id: str) -> str:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT object_key FROM source_objects WHERE id=:id AND tenant_id=:tenant"),
            {"id": source_id, "tenant": tenant_id},
        ).first()
    if row is None:
        raise ParserError("OBJECT_CHECKSUM_MISMATCH", "源对象不存在")
    return str(row.object_key)


def _active_profile(
    engine: Engine, *, tenant_id: str, kb_id: str, owner_user_id: str | None
):
    if not owner_user_id:
        raise EmbeddingUnavailable("embedding actor scope is missing")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT embedding_profile_id, active_index_generation_id "
                "FROM knowledge_bases WHERE id=:kb AND tenant_id=:tenant "
                "AND purged_at IS NULL"
            ),
            {"kb": kb_id, "tenant": tenant_id},
        ).first()
        if row is None or not row.embedding_profile_id or not row.active_index_generation_id:
            raise EmbeddingUnavailable("embedding profile is not active for this knowledge base")
        generation = connection.execute(
            text(
                "SELECT embedding_profile_id, state FROM index_generations "
                "WHERE id=:generation AND knowledge_base_id=:kb AND tenant_id=:tenant"
            ),
            {
                "generation": row.active_index_generation_id,
                "kb": kb_id,
                "tenant": tenant_id,
            },
        ).first()
    if (
        generation is None
        or str(generation.state) != "ACTIVE"
        or str(generation.embedding_profile_id) != str(row.embedding_profile_id)
    ):
        raise EmbeddingUnavailable("embedding index generation is not active")
    return resolve_profile(engine, tenant_id=tenant_id, profile_id=str(row.embedding_profile_id))


def _write_chunks(
    engine: Engine,
    *,
    tenant_id: str,
    version_id: str,
    kb_id: str,
    doc_id: str,
    title: str,
    chunks: list[Any],
    vectors: list[list[float]],
) -> int:
    now = utc_now()
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM chunks WHERE tenant_id=:tenant AND document_version_id=:version"),
            {"tenant": tenant_id, "version": version_id},
        )
        for index, chunk in enumerate(chunks):
            vector = vectors[index]
            connection.execute(
                text(
                    "INSERT INTO chunks (id, tenant_id, kb_id, doc_id, doc_version, chunk_index, "
                    "title, section_path, content, content_hash, token_count, embedding, "
                    "created_at, "
                    "updated_at, document_version_id, locator) VALUES "
                    "(:id,:tenant,:kb,:doc,1,:index,:title,:path,:content,:hash,:tokens,:embedding,"
                    ":created,:updated,:version,:locator)"
                ),
                {
                    "id": f"{version_id}:{index}",
                    "tenant": tenant_id,
                    "kb": kb_id,
                    "doc": doc_id,
                    "index": int(chunk.chunk_index),
                    "title": title,
                    "path": json.dumps(list(chunk.section_path), ensure_ascii=False),
                    "content": chunk.content,
                    "hash": chunk.content_hash,
                    "tokens": int(chunk.token_count),
                    "embedding": json.dumps(vector),
                    "created": now,
                    "updated": now,
                    "version": version_id,
                    "locator": json.dumps({"section_path": list(chunk.section_path)}),
                },
            )
        connection.execute(
            text(
                "UPDATE document_versions SET chunk_count=:count, parser_id=:parser, "
                "parser_version=:parser_version WHERE id=:version AND tenant_id=:tenant"
            ),
            {
                "count": len(chunks),
                "parser": "registry",
                "parser_version": "registry-v1",
                "version": version_id,
                "tenant": tenant_id,
            },
        )
    return len(chunks)


def _set_upload_item_status(
    engine: Engine, *, tenant_id: str, source_id: str, status: str, error_code: Optional[str] = None
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE upload_items SET status=:status, error_code=:error_code "
                "WHERE source_object_id=:source AND EXISTS ("
                "SELECT 1 FROM upload_batches WHERE upload_batches.id=upload_items.batch_id "
                "AND upload_batches.tenant_id=:tenant)"
            ),
            {
                "status": status,
                "error_code": error_code,
                "source": source_id,
                "tenant": tenant_id,
            },
        )


def process_claimed_job(
    engine: Engine,
    claimed: ClaimedJob,
    *,
    storage: Optional[StorageClient] = None,
    worker_id: Optional[str] = None,
) -> IngestWorkResult:
    """Process one claimed ``document_ingest`` job and close its DB lease."""

    job = claimed.job
    tenant_id = job.tenant_id
    owner = worker_id or claimed.worker_id
    jobs = JobService(engine)
    ingest = IngestService(engine)
    try:
        version_id = str(job.payload["document_version_id"])
        source_id, kb_id, doc_id, title, expected_checksum = _source(
            engine, tenant_id=tenant_id, version_id=version_id
        )
        object_key = _object_key(engine, tenant_id=tenant_id, source_id=source_id)
        client = storage or build_storage_client()
        raw = client.get_object_bytes(tenant_id=tenant_id, object_key=object_key)
        head = client.head_object(tenant_id=tenant_id, object_key=object_key)
        if expected_checksum and expected_checksum.removeprefix("sha256:") != head.sha256:
            raise ParserError(
                "OBJECT_CHECKSUM_MISMATCH",
                "源对象校验和与版本记录不一致",
            )

        attempt = ingest.start_attempt(
            tenant_id=tenant_id,
            ingest_job_id=str(job.payload["ingest_job_id"]),
            lease_owner=owner,
        )
        _stage(
            ingest,
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            name=STAGES[0],
            current=0,
            total=6,
            metrics={"byte_size": head.byte_size},
        )
        _stage(
            ingest, tenant_id=tenant_id, attempt_id=attempt.id, name=STAGES[1], current=1, total=6
        )
        parsed = get_default_registry().parse(
            raw, filename=title, mime=str(job.payload.get("mime", "text/plain"))
        )
        _stage(
            ingest,
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            name=STAGES[2],
            current=2,
            total=6,
            metrics=parsed.metadata,
        )
        chunks = split_sections(parsed.sections)
        _stage(
            ingest,
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            name=STAGES[3],
            current=3,
            total=6,
            metrics={"chunk_count": len(chunks)},
        )
        inputs = [" / ".join(chunk.section_path) + " " + chunk.content for chunk in chunks]
        owner_user_id = job.payload.get("owner_user_id")
        profile = _active_profile(
            engine,
            tenant_id=tenant_id,
            kb_id=kb_id,
            owner_user_id=str(owner_user_id) if owner_user_id else None,
        )
        embedding_client = build_embedding_client(
            engine=engine,
            tenant_id=tenant_id,
            user_id=str(owner_user_id),
            profile=profile,
        )
        vectors = (
            embedding_client.embed(texts=inputs, profile=profile)
            if inputs
            else []
        )
        if len(vectors) != len(chunks):
            raise EmbeddingError("embedding response count does not match chunks")
        _stage(
            ingest,
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            name=STAGES[4],
            current=4,
            total=6,
            metrics={"vector_count": len(vectors)},
        )
        count = _write_chunks(
            engine,
            tenant_id=tenant_id,
            version_id=version_id,
            kb_id=kb_id,
            doc_id=doc_id,
            title=title,
            chunks=chunks,
            vectors=vectors,
        )
        _stage(
            ingest,
            tenant_id=tenant_id,
            attempt_id=attempt.id,
            name=STAGES[5],
            current=6,
            total=6,
            metrics={"vector_count": count},
        )
        ingest.succeed(tenant_id=tenant_id, attempt_id=attempt.id)
        _set_upload_item_status(
            engine, tenant_id=tenant_id, source_id=source_id, status="COMPLETED"
        )
        jobs.complete(tenant_id=tenant_id, job_id=job.id, worker_id=owner)
        return IngestWorkResult(job.id, "SUCCEEDED")
    except ParserError as exc:
        _set_upload_item_status(
            engine,
            tenant_id=tenant_id,
            source_id=locals().get("source_id", ""),
            status="FAILED",
            error_code=exc.code,
        )
        _fail_claim(engine, claimed, jobs, ingest, str(exc.code), exc.detail, owner)
        return IngestWorkResult(job.id, "FAILED", exc.code)
    except EmbeddingError as exc:
        code = getattr(exc, "code", "EMBEDDING_UNAVAILABLE")
        _set_upload_item_status(
            engine,
            tenant_id=tenant_id,
            source_id=locals().get("source_id", ""),
            status="FAILED",
            error_code=code,
        )
        _fail_claim(engine, claimed, jobs, ingest, code, {"stage": "EMBEDDING"}, owner)
        return IngestWorkResult(job.id, "FAILED", code)
    except Exception as exc:  # noqa: BLE001 - worker records only sanitized state
        _set_upload_item_status(
            engine,
            tenant_id=tenant_id,
            source_id=locals().get("source_id", ""),
            status="FAILED",
            error_code="INGEST_INTERNAL_ERROR",
        )
        _fail_claim(
            engine,
            claimed,
            jobs,
            ingest,
            "INGEST_INTERNAL_ERROR",
            {"reason": type(exc).__name__},
            owner,
        )
        return IngestWorkResult(job.id, "FAILED", "INGEST_INTERNAL_ERROR")


def _fail_claim(
    engine: Engine,
    claimed: ClaimedJob,
    jobs: JobService,
    ingest: IngestService,
    code: str,
    detail: dict[str, Any],
    owner: str,
) -> None:
    try:
        with engine.connect() as connection:
            attempt = connection.execute(
                text(
                    "SELECT id FROM ingest_job_attempts WHERE ingest_job_id=:job "
                    "AND tenant_id=:tenant AND state NOT IN "
                    "('SUCCEEDED','FAILED','CANCELLED') "
                    "ORDER BY attempt_no DESC LIMIT 1"
                ),
                {"job": claimed.job.payload.get("ingest_job_id"), "tenant": claimed.job.tenant_id},
            ).first()
        if attempt is not None:
            ingest.fail(
                tenant_id=claimed.job.tenant_id,
                attempt_id=str(attempt.id),
                error_code=code,
                detail=sanitize_detail(detail),
            )
    finally:
        try:
            jobs.fail(
                tenant_id=claimed.job.tenant_id,
                job_id=claimed.job.id,
                worker_id=owner,
                error_code=code,
                sanitized_error={"code": code, "retryable": is_retryable(code)},
                retryable=is_retryable(code),
            )
        finally:
            # A failure can happen before IngestService.start_attempt (for
            # example, the source object is missing), so the projection must
            # also be closed by job id rather than only by active attempt.
            sync_promotion_status_for_job(
                engine,
                tenant_id=claimed.job.tenant_id,
                ingest_job_id=str(claimed.job.payload.get("ingest_job_id", "")),
                status="FAILED",
            )


def run_ingest_tick(
    engine: Engine, *, worker_id: str = "local-ingest-worker"
) -> Optional[IngestWorkResult]:
    jobs = JobService(engine)
    claimed = jobs.claim_next(worker_id=worker_id, job_type="document_ingest")
    if claimed is None:
        return None
    return process_claimed_job(engine, claimed, worker_id=worker_id)


__all__ = ["IngestWorkResult", "process_claimed_job", "run_ingest_tick"]

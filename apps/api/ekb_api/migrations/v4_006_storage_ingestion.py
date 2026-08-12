"""Storage & ingestion migration for PH3 (Reliable Knowledge-Base Upload & Index).

Per spec `10-database-changes.md` section 4 this migration owns the storage and
ingestion shape:

* ``source_objects`` — tenant-scoped dedup of uploaded raw bytes (content hash).
* ``upload_batches`` / ``upload_items`` / ``upload_sessions`` / ``upload_parts`` —
  the resumable multipart upload protocol with tenant scoping on every row.
* ``embedding_profiles`` / ``index_generations`` — immutable embedding config and
  atomic index generations.
* ``ingest_job_attempts`` / ``ingest_stages`` — the per-attempt state machine and
  idempotent stage ledger (history is immutable, exactly one active attempt).
* ALTERs on ``documents`` / ``document_versions`` / ``chunks`` / ``ingest_jobs`` /
  ``knowledge_bases`` adding the ingestion columns and partial unique indexes
  that guarantee document identity, single active attempt and single active
  index generation.

The module structure (``VERSION`` / ``V4_006_CHECKSUM`` evaluated at import,
``apply_v4_006`` / ``verify_v4_006`` / ``rollback_v4_006_dry_run``) is copied
verbatim from ``v4_005_retention_governance``.  DDL uses the ``__TIME__`` and
``JSON`` placeholders so it is accepted by both SQLite and PostgreSQL; the
PostgreSQL-only table-level FK constraints are applied conditionally.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_005_retention_governance import V4_005_CHECKSUM

VERSION = "v4_006_storage_ingestion"
V4_006_OWNED_TABLES = (
    "source_objects",
    "upload_batches",
    "upload_items",
    "upload_sessions",
    "upload_parts",
    "embedding_profiles",
    "ingest_job_attempts",
    "ingest_stages",
    "index_generations",
)
V4_006_ROLLBACK_POLICY = (
    "dry-run only; source objects, upload sessions, ingest attempts and index "
    "generations are retained for audit; use a forward-fix to evolve the "
    "storage/ingestion contract"
)

# ---- Canonical DDL (PostgreSQL contract with __TIME__ / JSON placeholders) ----

_SOURCE_OBJECTS_DDL = (
    "CREATE TABLE IF NOT EXISTS source_objects ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " object_key text NOT NULL,"
    " sha256 char(64) NOT NULL,"
    " byte_size bigint NOT NULL CHECK (byte_size >= 0),"
    " detected_mime text NOT NULL,"
    " ref_count bigint NOT NULL DEFAULT 0 CHECK (ref_count >= 0),"
    " created_at __TIME__ NOT NULL,"
    " UNIQUE (tenant_id, object_key),"
    " UNIQUE (tenant_id, sha256, byte_size)"
    ")"
)

_UPLOAD_BATCHES_DDL = (
    "CREATE TABLE IF NOT EXISTS upload_batches ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),"
    " created_by text NOT NULL REFERENCES users(id),"
    " mode text NOT NULL CHECK (mode IN ('FILE','MULTI_FILE','DIRECTORY')),"
    " status text NOT NULL,"
    " client_request_id text NOT NULL,"
    " item_count integer NOT NULL CHECK (item_count BETWEEN 0 AND 1000),"
    " total_bytes bigint NOT NULL CHECK (total_bytes BETWEEN 0 AND 5368709120),"
    " created_at __TIME__ NOT NULL,"
    " updated_at __TIME__ NOT NULL,"
    " UNIQUE (tenant_id, created_by, client_request_id)"
    ")"
)

_UPLOAD_ITEMS_DDL = (
    "CREATE TABLE IF NOT EXISTS upload_items ("
    " id text PRIMARY KEY,"
    " batch_id text NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,"
    " client_item_id text NOT NULL,"
    " display_path text NOT NULL,"
    " normalized_relative_path text NOT NULL,"
    " byte_size bigint NOT NULL CHECK (byte_size BETWEEN 0 AND 104857600),"
    " expected_sha256 char(64),"
    " source_object_id text REFERENCES source_objects(id),"
    " status text NOT NULL,"
    " uploaded_bytes bigint NOT NULL DEFAULT 0 CHECK (uploaded_bytes >= 0),"
    " error_code text,"
    " error_detail json,"
    " UNIQUE (batch_id, client_item_id)"
    ")"
)

_UPLOAD_SESSIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS upload_sessions ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " upload_item_id text NOT NULL REFERENCES upload_items(id) ON DELETE CASCADE,"
    " method text NOT NULL CHECK (method IN ('SINGLE','MULTIPART','API_STREAM')),"
    " provider_upload_id text,"
    " state text NOT NULL CHECK (state IN "
    "('ACTIVE','COMPLETING','COMPLETED','ABORTED','EXPIRED','FAILED')),"
    " part_size bigint,"
    " expires_at __TIME__ NOT NULL,"
    " created_at __TIME__ NOT NULL,"
    " updated_at __TIME__ NOT NULL,"
    " UNIQUE (upload_item_id)"
    ")"
)

_UPLOAD_PARTS_DDL = (
    "CREATE TABLE IF NOT EXISTS upload_parts ("
    " session_id text NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,"
    " part_no integer NOT NULL CHECK (part_no > 0),"
    " etag text NOT NULL,"
    " byte_size bigint NOT NULL CHECK (byte_size > 0),"
    " checksum text,"
    " confirmed_at __TIME__ NOT NULL,"
    " PRIMARY KEY (session_id, part_no)"
    ")"
)

_EMBEDDING_PROFILES_DDL = (
    "CREATE TABLE IF NOT EXISTS embedding_profiles ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " llm_provider_id text NOT NULL REFERENCES llm_providers(id),"
    " model_id text NOT NULL REFERENCES llm_models(id),"
    " dimensions integer NOT NULL CHECK (dimensions > 0),"
    " tokenizer text NOT NULL,"
    " chunker_id text NOT NULL,"
    " chunker_version text NOT NULL,"
    " config json NOT NULL,"
    " fingerprint char(64) NOT NULL,"
    " created_at __TIME__ NOT NULL,"
    " UNIQUE (tenant_id, fingerprint)"
    ")"
)

_INGEST_JOB_ATTEMPTS_DDL = (
    "CREATE TABLE IF NOT EXISTS ingest_job_attempts ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " ingest_job_id text NOT NULL REFERENCES ingest_jobs(id) ON DELETE CASCADE,"
    " attempt_no integer NOT NULL CHECK (attempt_no > 0),"
    " state text NOT NULL CHECK (state IN ("
    "'WAITING','VALIDATING','CONVERTING','PARSING','CHUNKING',"
    "'EMBEDDING','INDEXING','SUCCEEDED','FAILED','CANCELLED')),"
    " current_stage text,"
    " progress_current bigint NOT NULL DEFAULT 0,"
    " progress_total bigint,"
    " lease_owner text,"
    " lease_expires_at __TIME__,"
    " heartbeat_at __TIME__,"
    " error_code text,"
    " sanitized_error json,"
    " created_at __TIME__ NOT NULL,"
    " updated_at __TIME__ NOT NULL,"
    " UNIQUE (ingest_job_id, attempt_no)"
    ")"
)

_INGEST_STAGES_DDL = (
    "CREATE TABLE IF NOT EXISTS ingest_stages ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " attempt_id text NOT NULL REFERENCES ingest_job_attempts(id) ON DELETE CASCADE,"
    " stage text NOT NULL,"
    " status text NOT NULL,"
    " input_hash char(64) NOT NULL,"
    " metrics json NOT NULL DEFAULT '{}',"
    " started_at __TIME__,"
    " ended_at __TIME__,"
    " UNIQUE (attempt_id, stage, input_hash)"
    ")"
)

_INDEX_GENERATIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS index_generations ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),"
    " embedding_profile_id text NOT NULL REFERENCES embedding_profiles(id),"
    " generation_no integer NOT NULL,"
    " state text NOT NULL CHECK (state IN "
    "('BUILDING','READY','ACTIVE','FAILED','RETIRED')),"
    " vector_count bigint NOT NULL DEFAULT 0,"
    " created_at __TIME__ NOT NULL,"
    " activated_at __TIME__,"
    " UNIQUE (knowledge_base_id, embedding_profile_id, generation_no)"
    ")"
)

# ALTER column additions (dialect-neutral; normalized at apply time).
_DOCUMENT_COLUMNS = {
    "normalized_relative_path": "text",
    "active_version_id": "text REFERENCES document_versions(id)",
}
_DOCUMENT_VERSION_COLUMNS = {
    "source_object_id": "text REFERENCES source_objects(id)",
    "ingest_status": "text NOT NULL DEFAULT 'LEGACY' "
    "CHECK (ingest_status IN "
    "('LEGACY','UPLOADED','PROCESSING','SUCCEEDED','FAILED','CANCELLED'))",
    "parser_id": "text",
    "parser_version": "text",
    "embedding_profile_id": "text REFERENCES embedding_profiles(id)",
    "index_generation_id": "text REFERENCES index_generations(id)",
    "activated_at": "__TIME__",
}
_CHUNK_COLUMNS = {
    "document_version_id": "text REFERENCES document_versions(id)",
    "index_generation_id": "text REFERENCES index_generations(id)",
    "locator": "json NOT NULL DEFAULT '{}'",
}
_INGEST_JOB_COLUMNS = {
    "document_version_id": "text REFERENCES document_versions(id)",
    "active_attempt_id": "text",
    "state_version": "integer NOT NULL DEFAULT 0",
}
_KB_COLUMNS = {
    "embedding_profile_id": "text REFERENCES embedding_profiles(id)",
    "active_index_generation_id": "text REFERENCES index_generations(id)",
}

# Index DDL (partial unique indexes use WHERE clauses, supported by both engines).
_INDEX_DDL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_live_path "
    "ON documents (kb_id, normalized_relative_path) WHERE purged_at IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_document_versions_doc_version "
    "ON document_versions (doc_id, version)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_document_versions_doc_checksum "
    "ON document_versions (doc_id, checksum)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_chunks_version_ordinal "
    "ON chunks (document_version_id, chunk_index) "
    "WHERE document_version_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ingest_job_version "
    "ON ingest_jobs (tenant_id, document_version_id) "
    "WHERE document_version_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ingest_one_active_attempt "
    "ON ingest_job_attempts (ingest_job_id) "
    "WHERE state IN ('WAITING','VALIDATING','CONVERTING','PARSING','CHUNKING',"
    "'EMBEDDING','INDEXING')",
    "CREATE INDEX IF NOT EXISTS ix_upload_sessions_expiry "
    "ON upload_sessions (expires_at) WHERE state IN ('ACTIVE','COMPLETING')",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_one_active_generation "
    "ON index_generations (knowledge_base_id) WHERE state = 'ACTIVE'",
]

# Table-level FK constraints are PostgreSQL-only (SQLite cannot ADD CONSTRAINT).
_FK_DDL = [
    "ALTER TABLE ingest_jobs ADD CONSTRAINT fk_ingest_active_attempt "
    "FOREIGN KEY (active_attempt_id) REFERENCES ingest_job_attempts(id)",
    "ALTER TABLE document_versions ADD CONSTRAINT fk_document_version_generation "
    "FOREIGN KEY (index_generation_id) REFERENCES index_generations(id)",
    "ALTER TABLE chunks ADD CONSTRAINT fk_chunks_index_generation "
    "FOREIGN KEY (index_generation_id) REFERENCES index_generations(id)",
]

# Canonical DDL used to derive the migration checksum (module-load invariant).
_CANONICAL_DDL = "\n".join(
    [
        _SOURCE_OBJECTS_DDL,
        _UPLOAD_BATCHES_DDL,
        _UPLOAD_ITEMS_DDL,
        _UPLOAD_SESSIONS_DDL,
        _UPLOAD_PARTS_DDL,
        _EMBEDDING_PROFILES_DDL,
        _INGEST_JOB_ATTEMPTS_DDL,
        _INGEST_STAGES_DDL,
        _INDEX_GENERATIONS_DDL,
    ]
    + [
        f"ALTER TABLE {t} ADD COLUMN {name} {definition}"
        for t, cols in (
            ("documents", _DOCUMENT_COLUMNS),
            ("document_versions", _DOCUMENT_VERSION_COLUMNS),
            ("chunks", _CHUNK_COLUMNS),
            ("ingest_jobs", _INGEST_JOB_COLUMNS),
            ("knowledge_bases", _KB_COLUMNS),
        )
        for name, definition in cols.items()
    ]
    + _INDEX_DDL
    + _FK_DDL
)

V4_006_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + " ".join(_CANONICAL_DDL.split())
        + "\nrollback="
        + V4_006_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB,time=sqlite:VARCHAR,postgresql:TIMESTAMPTZ"
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: Optional[str] = None


def _normalize_ddl(statement: str, dialect: str) -> str:
    if dialect == "postgresql":
        return statement.replace(" JSON", " JSONB").replace("__TIME__", "TIMESTAMPTZ")
    if dialect == "sqlite":
        return statement.replace("__TIME__", "VARCHAR(64)")
    raise RuntimeError(f"{VERSION} unsupported database dialect: {dialect}")


def _create_tables_ddl() -> list[str]:
    return [
        _SOURCE_OBJECTS_DDL,
        _UPLOAD_BATCHES_DDL,
        _UPLOAD_ITEMS_DDL,
        _UPLOAD_SESSIONS_DDL,
        _UPLOAD_PARTS_DDL,
        _EMBEDDING_PROFILES_DDL,
        _INGEST_JOB_ATTEMPTS_DDL,
        _INGEST_STAGES_DDL,
        _INDEX_GENERATIONS_DDL,
    ]


def _alter_columns() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for table, cols in (
        ("documents", _DOCUMENT_COLUMNS),
        ("document_versions", _DOCUMENT_VERSION_COLUMNS),
        ("chunks", _CHUNK_COLUMNS),
        ("ingest_jobs", _INGEST_JOB_COLUMNS),
        ("knowledge_bases", _KB_COLUMNS),
    ):
        for name, definition in cols.items():
            out.append((table, name, definition))
    return out


def _ensure_column(connection: Connection, table: str, name: str, definition: str) -> None:
    existing = {column["name"] for column in inspect(connection).get_columns(table)}
    if name in existing:
        return
    definition = _normalize_ddl(definition, connection.dialect.name)
    if connection.dialect.name == "postgresql":
        definition = definition.replace(" JSON", " JSONB")
    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def _ledger(connection: Connection, version: str) -> Any:
    return connection.execute(
        text("SELECT checksum, manifest_position FROM migration_provenance WHERE version=:version"),
        {"version": version},
    ).first()


def _audit(connection: Connection, action: str, outcome: str, detail: dict[str, Any]) -> None:
    from uuid import uuid4

    expression = "CAST(:detail AS JSONB)" if connection.dialect.name == "postgresql" else ":detail"
    connection.execute(
        text(
            "INSERT INTO migration_audit "
            "(id, version, action, outcome, detail, recorded_at) "
            f"VALUES (:id,:version,:action,:outcome,{expression},:recorded_at)"
        ),
        {
            "id": str(uuid4()),
            "version": VERSION,
            "action": action,
            "outcome": outcome,
            "detail": json.dumps(detail, sort_keys=True, separators=(",", ":")),
            "recorded_at": utc_now(),
        },
    )


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table in V4_006_OWNED_TABLES:
        if table not in tables:
            raise RuntimeError(f"{VERSION} verification failed: missing table={table}")
    owned = set(V4_006_OWNED_TABLES)
    for table in ("documents", "document_versions", "chunks", "ingest_jobs", "knowledge_bases"):
        if table not in tables:
            raise RuntimeError(f"{VERSION} verification failed: missing table={table}")
        owned.add(table)
    indexes: set[str] = set()
    for table in owned:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                indexes.add(str(name))
    expected_indexes = {
        "uq_documents_live_path",
        "uq_document_versions_doc_version",
        "uq_document_versions_doc_checksum",
        "uq_chunks_version_ordinal",
        "uq_ingest_job_version",
        "uq_ingest_one_active_attempt",
        "ix_upload_sessions_expiry",
        "uq_kb_one_active_generation",
    }
    missing = sorted(expected_indexes - indexes)
    if missing:
        raise RuntimeError(
            f"{VERSION} verification failed: missing indexes={','.join(missing)}"
        )
    return tuple(sorted(indexes))


def apply_v4_006(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v4_005_retention_governance")
        if previous is None or str(previous[0]) != V4_005_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v4_005 retention governance")
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) == V4_006_CHECKSUM:
            _audit(connection, "APPLY", "NO_OP", {"storage_ingestion": "schema_only"})
            return MigrationResult(
                VERSION, V4_006_CHECKSUM, False, {"storage_ingestion": "schema_only"}
            )

        dialect = connection.dialect.name
        for statement in _create_tables_ddl():
            connection.execute(text(_normalize_ddl(statement, dialect)))
        for table, name, definition in _alter_columns():
            _ensure_column(connection, table, name, definition)
        for statement in _INDEX_DDL:
            connection.execute(text(_normalize_ddl(statement, dialect)))
        if dialect == "postgresql":
            for statement in _FK_DDL:
                connection.execute(text(statement))

        _catalog(connection)

        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"storage_ingestion": "schema_only"})
            return MigrationResult(
                VERSION, V4_006_CHECKSUM, False, {"storage_ingestion": "schema_only"}
            )

        position = int(previous[1]) + 1 if previous is not None else 0
        now = utc_now()
        expression = (
            "CAST(:provenance AS JSONB)" if dialect == "postgresql" else ":provenance"
        )
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version, owner, checksum, applied_at, verify_status, provenance,"
                "manifest_position, created_at) "
                "VALUES (:version, :owner, :checksum, :applied_at, 'VERIFIED',"
                f"{expression}, :position, :created_at)"
            ),
            {
                "version": VERSION,
                "owner": "storage_ingestion",
                "checksum": V4_006_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {"tables": list(V4_006_OWNED_TABLES)}, sort_keys=True
                ),
                "position": position,
                "created_at": now,
            },
        )
        _audit(
            connection,
            "APPLY",
            "PASS",
            {"tables": "source_objects+upload_*/embedding_profiles+ingest_*+index_generations"},
        )
    return MigrationResult(
        VERSION,
        V4_006_CHECKSUM,
        True,
        {"tables": "source_objects+upload_*/embedding_profiles+ingest_*+index_generations"},
    )


def verify_v4_006(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_006_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {"storage_ingestion": "schema_only"})
    return VerificationResult(
        "PASS", VERSION, V4_006_CHECKSUM, V4_006_OWNED_TABLES, indexes
    )


def rollback_v4_006_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), tuple(V4_006_OWNED_TABLES))
        if str(row[0]) != V4_006_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_006_ROLLBACK_POLICY})
    return RollbackPlan(True, (), tuple(V4_006_OWNED_TABLES), V4_006_ROLLBACK_POLICY)

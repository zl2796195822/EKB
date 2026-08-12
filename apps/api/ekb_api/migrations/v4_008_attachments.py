"""Attachment, OCR & Vision migration for PH5 (Chat Attachments / Files / Images).

Per spec `10-database-changes.md` section 4 (``v4_008_attachments``) this migration
owns the attachment shape introduced by `07-file-and-image.md`:

* ``attachments`` — tenant/owner-scoped upload with its own lifecycle and the full
  retention field set (deleted_at/expires_at/deleted_by/deletion_batch_id/
  deletion_reason/deletion_generation/purged_at).
* ``attachment_artifacts`` — parse output (text object, metadata, token estimate).
* ``attachment_chunks`` — attachment-scoped retrieval chunks (NOT KB citation space).
* ``message_attachments`` — the explicit cross-resource binding between a message
  and an attachment with the resolved ``usage_mode`` (INLINE/RETRIEVAL/VISION/
  OCR_FALLBACK). The composite PK is ``(message_id, attachment_id)``.
* ``image_artifacts`` — derived image, vision/OCR method, caption and confidence.
* ``attachment_promotions`` — promotion of an attachment into a target knowledge
  base (reuses PH3 ingestion by reference, never copies UI labels).

Every table carries the retention field set so the same soft-delete / 30-day
generation contract used by PH2 trash applies to attachments.

The module structure (``VERSION`` / ``V4_008_CHECKSUM`` evaluated at import,
``apply_v4_008`` / ``verify_v4_008`` / ``rollback_v4_008_dry_run``) and the
``__TIME__`` / ``JSON`` dialect placeholders are copied verbatim from
``v4_006_storage_ingestion`` so the migration is accepted by both SQLite and
PostgreSQL.

The previous migration in the append order is ``v4_007_chat_graph`` (PH4). Because
PH3/PH4 may be applied by other agents in parallel, the required previous version is
resolved dynamically: if ``v4_007`` is importable its checksum is required,
otherwise the chain falls back to ``v4_006``. This keeps PH5 independently
applicable and testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v4_004_postgres_cutover import V4_004_CHECKSUM
from ekb_api.migrations.v4_005_retention_governance import V4_005_CHECKSUM
from ekb_api.migrations.v4_006_storage_ingestion import V4_006_CHECKSUM

VERSION = "v4_008_attachments"
V4_008_OWNED_TABLES = (
    "attachments",
    "attachment_artifacts",
    "attachment_chunks",
    "message_attachments",
    "image_artifacts",
    "attachment_promotions",
)
V4_008_ROLLBACK_POLICY = (
    "dry-run only; attachment artifacts, chunks, image artifacts and promotions are "
    "retained for audit; use a forward-fix to evolve the attachment contract"
)

# ---- Canonical DDL (PostgreSQL contract with __TIME__ / JSON placeholders) ----

# Retention field set appended to every PH5 table.
_RETENTION_COLUMNS = (
    " deleted_at __TIME__,"
    " expires_at __TIME__,"
    " deleted_by text REFERENCES users(id),"
    " deletion_batch_id text REFERENCES deletion_batches(id),"
    " deletion_reason text,"
    " deletion_generation integer NOT NULL DEFAULT 0,"
    " purged_at __TIME__"
)

_ATTACHMENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS attachments ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " owner_user_id text NOT NULL REFERENCES users(id),"
    " conversation_id text REFERENCES conversations(id),"
    " source_object_id text NOT NULL REFERENCES source_objects(id),"
    " client_request_id text NOT NULL,"
    " status text NOT NULL,"
    " detected_mime text NOT NULL,"
    " byte_size bigint NOT NULL CHECK (byte_size >= 0),"
    " created_at __TIME__ NOT NULL,"
    " updated_at __TIME__ NOT NULL,"
    " expires_at __TIME__,"
    " deleted_at __TIME__,"
    " deleted_by text REFERENCES users(id),"
    " deletion_batch_id text REFERENCES deletion_batches(id),"
    " deletion_reason text,"
    " deletion_generation integer NOT NULL DEFAULT 0,"
    " purged_at __TIME__,"
    " UNIQUE (tenant_id, owner_user_id, client_request_id)"
    ")"
)

_ATTACHMENT_ARTIFACTS_DDL = (
    "CREATE TABLE IF NOT EXISTS attachment_artifacts ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " attachment_id text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,"
    " parser_version text NOT NULL,"
    " text_object_id text REFERENCES source_objects(id),"
    " metadata json NOT NULL DEFAULT '{}',"
    " token_estimate integer,"
    " status text NOT NULL,"
    " UNIQUE (attachment_id, parser_version)"
    ")"
)

_ATTACHMENT_CHUNKS_DDL = (
    "CREATE TABLE IF NOT EXISTS attachment_chunks ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " attachment_id text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,"
    " artifact_id text NOT NULL REFERENCES attachment_artifacts(id) ON DELETE CASCADE,"
    " ordinal integer NOT NULL,"
    " text_content text NOT NULL,"
    " metadata json NOT NULL,"
    " embedding_profile_id text REFERENCES embedding_profiles(id),"
    " UNIQUE (attachment_id, artifact_id, ordinal)"
    ")"
)

_MESSAGE_ATTACHMENTS_DDL = (
    "CREATE TABLE IF NOT EXISTS message_attachments ("
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,"
    " attachment_id text NOT NULL REFERENCES attachments(id),"
    " ordinal integer NOT NULL,"
    " usage_mode text NOT NULL CHECK (usage_mode IN "
    "('INLINE','RETRIEVAL','VISION','OCR_FALLBACK')),"
    " PRIMARY KEY (message_id, attachment_id),"
    " UNIQUE (message_id, ordinal)"
    ")"
)

_IMAGE_ARTIFACTS_DDL = (
    "CREATE TABLE IF NOT EXISTS image_artifacts ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " attachment_id text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,"
    " derived_object_id text REFERENCES source_objects(id),"
    " width integer NOT NULL CHECK (width > 0),"
    " height integer NOT NULL CHECK (height > 0),"
    " method text NOT NULL CHECK (method IN ('NATIVE_VISION','OCR','OCR_CAPTION')),"
    " ocr_text_object_id text REFERENCES source_objects(id),"
    " caption text,"
    " confidence numeric,"
    " provider_id text REFERENCES llm_providers(id),"
    " model_id text REFERENCES llm_models(id)"
    ")"
)

_ATTACHMENT_PROMOTIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS attachment_promotions ("
    " id text PRIMARY KEY,"
    " tenant_id text NOT NULL REFERENCES tenants(id),"
    " attachment_id text NOT NULL REFERENCES attachments(id),"
    " target_knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),"
    " normalized_relative_path text NOT NULL,"
    " client_request_id text NOT NULL,"
    " document_version_id text REFERENCES document_versions(id),"
    " ingest_job_id text REFERENCES ingest_jobs(id),"
    " status text NOT NULL,"
    " UNIQUE (tenant_id, attachment_id, target_knowledge_base_id, client_request_id)"
    ")"
)

# Required columns per table (used by verify; retention superset checked loosely).
_REQUIRED_COLUMNS = {
    "attachments": {
        "id", "tenant_id", "owner_user_id", "conversation_id", "source_object_id",
        "client_request_id", "status", "detected_mime", "byte_size", "created_at",
        "updated_at", "expires_at",
        "deleted_at", "deleted_by", "deletion_batch_id", "deletion_reason",
        "deletion_generation", "purged_at",
    },
    "attachment_artifacts": {
        "id", "tenant_id", "attachment_id", "parser_version", "text_object_id",
        "metadata", "token_estimate", "status",
    },
    "attachment_chunks": {
        "id", "tenant_id", "attachment_id", "artifact_id", "ordinal",
        "text_content", "metadata", "embedding_profile_id",
    },
    "message_attachments": {
        "tenant_id", "message_id", "attachment_id", "ordinal", "usage_mode",
    },
    "image_artifacts": {
        "id", "tenant_id", "attachment_id", "derived_object_id", "width",
        "height", "method", "ocr_text_object_id", "caption", "confidence",
        "provider_id", "model_id",
    },
    "attachment_promotions": {
        "id", "tenant_id", "attachment_id", "target_knowledge_base_id",
        "normalized_relative_path", "client_request_id", "document_version_id",
        "ingest_job_id", "status",
    },
}

# Canonical DDL used to derive the migration checksum (module-load invariant).
_CANONICAL_DDL = "\n".join(
    [
        _ATTACHMENTS_DDL,
        _ATTACHMENT_ARTIFACTS_DDL,
        _ATTACHMENT_CHUNKS_DDL,
        _MESSAGE_ATTACHMENTS_DDL,
        _IMAGE_ARTIFACTS_DDL,
        _ATTACHMENT_PROMOTIONS_DDL,
    ]
)

V4_008_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + " ".join(_CANONICAL_DDL.split())
        + "\nrollback="
        + V4_008_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB,time=sqlite:VARCHAR,postgresql:TIMESTAMPTZ"
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict[str, Any]


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
        _ATTACHMENTS_DDL,
        _ATTACHMENT_ARTIFACTS_DDL,
        _ATTACHMENT_CHUNKS_DDL,
        _MESSAGE_ATTACHMENTS_DDL,
        _IMAGE_ARTIFACTS_DDL,
        _ATTACHMENT_PROMOTIONS_DDL,
    ]


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


def _resolved_previous() -> tuple[str, str]:
    """Return (previous_version, expected_checksum) for the chain append order.

    PH5 appends after PH4 (``v4_007_chat_graph``).  When PH4 is not yet present
    (parallel agent work) the chain falls back to requiring PH3 (``v4_006``).
    """
    try:
        from ekb_api.migrations.v4_007_chat_graph import (  # type: ignore
            V4_007_CHECKSUM as _V4_007_CHECKSUM,
        )

        return "v4_007_chat_graph", _V4_007_CHECKSUM
    except Exception:  # noqa: BLE001
        return "v4_006_storage_ingestion", V4_006_CHECKSUM


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table in V4_008_OWNED_TABLES:
        if table not in tables:
            raise RuntimeError(f"{VERSION} verification failed: missing table={table}")
    indexes: set[str] = set()
    for table in V4_008_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            name = index.get("name")
            if name:
                indexes.add(str(name))
    return tuple(sorted(indexes))


def apply_v4_008(engine: Engine) -> MigrationResult:
    previous_version, expected_previous = _resolved_previous()
    with engine.begin() as connection:
        # Guard against the immutable migration history.
        for required in ("v4_004_postgres_cutover", "v4_005_retention_governance"):
            if required == "v4_004_postgres_cutover":
                required_cs = V4_004_CHECKSUM
            else:
                required_cs = V4_005_CHECKSUM
            row = _ledger(connection, required)
            if row is None or str(row[0]) != required_cs:
                raise RuntimeError(f"{VERSION} requires verified {required}")
        previous = _ledger(connection, previous_version)
        if previous is None or str(previous[0]) != expected_previous:
            raise RuntimeError(
                f"{VERSION} requires verified {previous_version} "
                f"(got={None if previous is None else previous[0]})"
            )
        ledger = _ledger(connection, VERSION)
        if ledger is not None and str(ledger[0]) == V4_008_CHECKSUM:
            _audit(connection, "APPLY", "NO_OP", {"attachments": "schema_only"})
            return MigrationResult(VERSION, V4_008_CHECKSUM, False, {"attachments": "schema_only"})

        dialect = connection.dialect.name
        for statement in _create_tables_ddl():
            connection.execute(text(_normalize_ddl(statement, dialect)))

        _catalog(connection)

        if ledger is not None:
            _audit(connection, "APPLY", "NO_OP", {"attachments": "schema_only"})
            return MigrationResult(VERSION, V4_008_CHECKSUM, False, {"attachments": "schema_only"})

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
                "owner": "attachments",
                "checksum": V4_008_CHECKSUM,
                "applied_at": now,
                "provenance": json.dumps(
                    {"tables": list(V4_008_OWNED_TABLES)}, sort_keys=True
                ),
                "position": position,
                "created_at": now,
            },
        )
        _audit(
            connection,
            "APPLY",
            "PASS",
            {
                "tables": "attachments+artifacts+chunks+message_attachments"
                "+image_artifacts+promotions"
            },
        )
    return MigrationResult(
        VERSION,
        V4_008_CHECKSUM,
        True,
        {"tables": "attachments+artifacts+chunks+message_attachments+image_artifacts+promotions"},
    )


def verify_v4_008(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None or str(row[0]) != V4_008_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table in V4_008_OWNED_TABLES:
            if table not in tables:
                raise RuntimeError(f"{VERSION} verification failed: missing table={table}")
        for table, required in _REQUIRED_COLUMNS.items():
            if table not in tables:
                continue
            actual = {column["name"] for column in inspector.get_columns(table)}
            missing = required - actual
            if missing:
                raise RuntimeError(
                    f"{VERSION} verification failed: {table} missing columns={sorted(missing)}"
                )
        indexes = _catalog(connection)
        _audit(connection, "VERIFY", "PASS", {"attachments": "schema_only"})
    return VerificationResult(
        "PASS", VERSION, V4_008_CHECKSUM, V4_008_OWNED_TABLES, indexes
    )


def rollback_v4_008_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        row = _ledger(connection, VERSION)
        if row is None:
            return RollbackPlan(False, (), tuple(V4_008_OWNED_TABLES))
        if str(row[0]) != V4_008_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        _audit(connection, "ROLLBACK_DRY_RUN", "BLOCKED", {"reason": V4_008_ROLLBACK_POLICY})
    return RollbackPlan(
        True, (), tuple(V4_008_OWNED_TABLES), "additive migration not reversible"
    )

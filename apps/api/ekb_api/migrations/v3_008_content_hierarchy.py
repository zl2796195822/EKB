"""Append-only content hierarchy migration.

The original ``v3_002_content`` checksum is already applied in existing
databases and therefore remains immutable.  Folder structure is added here as
a forward compensation migration so the Knowledge page can use a durable
tenant/KB-scoped tree without rewriting the old trash projection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now
from ekb_api.migrations.v3_005_llm import V3_005_CHECKSUM

VERSION = "v3_008_content_hierarchy"
OWNED_TABLES = ("folders", "document_folder_links")
ROLLBACK_POLICY = (
    "dry-run only by default; folder structure is user-authored state and must "
    "be preserved with a forward fix"
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS folders (
      id VARCHAR(36) PRIMARY KEY,
      tenant_id VARCHAR(36) NOT NULL,
      kb_id VARCHAR(36) NOT NULL,
      parent_id VARCHAR(36),
      name VARCHAR(255) NOT NULL,
      deleted_at VARCHAR(32),
      deleted_by VARCHAR(36),
      created_by VARCHAR(36) NOT NULL,
      created_at VARCHAR(32) NOT NULL,
      updated_at VARCHAR(32) NOT NULL,
      UNIQUE (tenant_id, kb_id, parent_id, name),
      UNIQUE (tenant_id, id),
      UNIQUE (tenant_id, kb_id, id),
      FOREIGN KEY (tenant_id) REFERENCES tenants (id),
      FOREIGN KEY (kb_id) REFERENCES knowledge_bases (id),
      FOREIGN KEY (tenant_id, kb_id, parent_id)
        REFERENCES folders (tenant_id, kb_id, id),
      FOREIGN KEY (tenant_id, deleted_by)
        REFERENCES tenant_memberships (tenant_id, user_id),
      FOREIGN KEY (tenant_id, created_by)
        REFERENCES tenant_memberships (tenant_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS document_folder_links (
      tenant_id VARCHAR(36) NOT NULL,
      document_id VARCHAR(36) NOT NULL,
      folder_id VARCHAR(36) NOT NULL,
      created_at VARCHAR(32) NOT NULL,
      PRIMARY KEY (tenant_id, document_id),
      FOREIGN KEY (document_id) REFERENCES documents (id),
      FOREIGN KEY (tenant_id, folder_id) REFERENCES folders (tenant_id, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_folders_tenant_kb_parent "
    "ON folders (tenant_id, kb_id, parent_id, deleted_at, name)",
    "CREATE INDEX IF NOT EXISTS ix_doc_folder_tenant_folder "
    "ON document_folder_links (tenant_id, folder_id, document_id)",
)

V3_008_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\nrollback="
        + ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
    ).encode()
).hexdigest()


@dataclass(frozen=True)
class MigrationResult:
    version: str
    checksum: str
    applied: bool
    backfill_counts: dict


@dataclass(frozen=True)
class VerificationResult:
    status: str
    version: str
    checksum: str
    tables: tuple[str, ...]
    indexes: tuple[str, ...]
    backfill_counts: dict


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: Optional[str] = None


def _ledger(connection: Connection, version: str) -> Optional[str]:
    row = connection.execute(
        text("SELECT checksum FROM schema_migrations WHERE version = :version"),
        {"version": version},
    ).first()
    return None if row is None else str(row[0])


def _catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    missing = sorted(set(OWNED_TABLES) - tables)
    if missing:
        raise RuntimeError(f"{VERSION} verification failed: missing tables={missing}")
    required = {
        "folders": {"id", "tenant_id", "kb_id", "parent_id", "name", "deleted_at", "created_by"},
        "document_folder_links": {"tenant_id", "document_id", "folder_id", "created_at"},
    }
    for table, columns in required.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing_columns = sorted(columns - actual)
        if missing_columns:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} missing columns={missing_columns}"
            )
    indexes = {
        str(index["name"])
        for table in OWNED_TABLES
        for index in inspector.get_indexes(table)
        if index.get("name")
    }
    missing_indexes = {
        "ix_folders_tenant_kb_parent",
        "ix_doc_folder_tenant_folder",
    } - indexes
    if missing_indexes:
        raise RuntimeError(
            f"{VERSION} verification failed: missing indexes={sorted(missing_indexes)}"
        )
    return tuple(sorted(indexes))


def apply_v3_008(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        previous = _ledger(connection, "v3_005_llm")
        if previous != V3_005_CHECKSUM:
            raise RuntimeError(f"{VERSION} requires verified v3_005_llm")
        applied = _ledger(connection, VERSION)
        if applied is not None and applied != V3_008_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch: applied={applied}")
        for statement in _DDL:
            connection.execute(text(statement))
        _catalog(connection)
        if applied is None:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, applied_at, checksum) "
                    "VALUES (:version, :applied_at, :checksum)"
                ),
                {"version": VERSION, "applied_at": utc_now(), "checksum": V3_008_CHECKSUM},
            )
    return MigrationResult(VERSION, V3_008_CHECKSUM, applied is None, {"folders": 0})


def verify_v3_008(engine: Engine) -> VerificationResult:
    with engine.connect() as connection:
        if _ledger(connection, VERSION) != V3_008_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        indexes = _catalog(connection)
        folder_count = int(connection.execute(text("SELECT COUNT(*) FROM folders")).scalar_one())
    return VerificationResult(
        "PASS", VERSION, V3_008_CHECKSUM, OWNED_TABLES, indexes, {"folders": folder_count}
    )


def rollback_v3_008_dry_run(engine: Engine) -> RollbackPlan:
    with engine.connect() as connection:
        applied = _ledger(connection, VERSION)
        if applied is None:
            return RollbackPlan(False, (), ("schema_migrations", "knowledge_bases"))
        if applied != V3_008_CHECKSUM:
            raise RuntimeError(f"{VERSION} checksum mismatch")
        count = int(connection.execute(text("SELECT COUNT(*) FROM folders")).scalar_one())
    if count:
        return RollbackPlan(
            True,
            (),
            ("schema_migrations", "knowledge_bases", "folders"),
            "folders_rows_present — preserve user-authored hierarchy",
        )
    return RollbackPlan(
        True,
        ("DROP TABLE document_folder_links", "DROP TABLE folders"),
        ("schema_migrations", "knowledge_bases"),
    )

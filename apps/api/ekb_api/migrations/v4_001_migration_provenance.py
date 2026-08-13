"""Immutable provenance ledger for the additive v4 migration chain.

The historical v3 ``schema_migrations`` table remains the source of truth for
the applied v3 contract.  This migration adds a separate, append-only ledger
which records the observed v3 order and the immutable v4 manifest.  It never
rewrites historical v3 rows and does not use ORM metadata ownership.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import Connection, Engine, inspect, text

from ekb_api.domain import utc_now

VERSION = "v4_001_migration_provenance"
V4_001_OWNED_TABLES = ("migration_provenance", "migration_audit")
V4_001_ROLLBACK_POLICY = (
    "dry-run only; migration provenance and audit are retained and immutable; "
    "use a forward-fix for any post-apply correction"
)

# This is the only ordered v4 manifest accepted by this local slice.  Later
# phases append versions in their own migrations; they must not be inserted
# ahead of an applied version.
V4_MANIFEST = (
    ("v4_001_migration_provenance", "schema-governance"),
    ("v4_002_runtime_jobs", "runtime"),
)

_HISTORICAL_V3_VERSIONS = (
    "v3_001_identity",
    "v3_002_content",
    "v3_003_analytics",
    "v3_004_apps",
    "v3_005_analytics_compat",
    "v3_006_apps_compat",
    "v3_007_content_governance_compat",
    "v3_005_llm",
    "v3_008_content_hierarchy",
)

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS migration_provenance (
      version VARCHAR(128) PRIMARY KEY,
      owner VARCHAR(128) NOT NULL,
      checksum VARCHAR(128) NOT NULL,
      applied_at VARCHAR(64) NOT NULL,
      verify_status VARCHAR(32) NOT NULL,
      provenance JSON NOT NULL,
      manifest_position INTEGER NOT NULL,
      created_at VARCHAR(64) NOT NULL,
      CHECK (verify_status IN ('APPLIED', 'VERIFIED', 'LEGACY_OBSERVED')),
      UNIQUE (manifest_position)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS migration_audit (
      id VARCHAR(36) PRIMARY KEY,
      version VARCHAR(128) NOT NULL,
      action VARCHAR(32) NOT NULL,
      outcome VARCHAR(32) NOT NULL,
      detail JSON NOT NULL,
      recorded_at VARCHAR(64) NOT NULL,
      CHECK (action IN ('APPLY', 'VERIFY', 'ROLLBACK_DRY_RUN')),
      CHECK (outcome IN ('PASS', 'FAIL', 'BLOCKED', 'NO_OP')),
      FOREIGN KEY (version) REFERENCES migration_provenance(version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_migration_audit_version_time "
    "ON migration_audit (version, recorded_at DESC)",
)

_REQUIRED_COLUMNS = {
    "migration_provenance": {
        "version",
        "owner",
        "checksum",
        "applied_at",
        "verify_status",
        "provenance",
        "manifest_position",
        "created_at",
    },
    "migration_audit": {
        "id",
        "version",
        "action",
        "outcome",
        "detail",
        "recorded_at",
    },
}

V4_001_CHECKSUM = hashlib.sha256(
    (
        VERSION
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _DDL)
        + "\n"
        + json.dumps(V4_MANIFEST, separators=(",", ":"))
        + "\n"
        + json.dumps(_HISTORICAL_V3_VERSIONS, separators=(",", ":"))
        + "\nrollback="
        + V4_001_ROLLBACK_POLICY
        + "\njson=sqlite:JSON,postgresql:JSONB"
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
    backfill_counts: dict[str, int | str]


@dataclass(frozen=True)
class RollbackPlan:
    applied: bool
    objects: tuple[str, ...]
    retained: tuple[str, ...]
    blocked_reason: Optional[str] = None


def _set_sqlite_foreign_keys(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        connection.execute(text("PRAGMA foreign_keys=ON"))


def _ddl_for_connection(connection: Connection) -> tuple[str, ...]:
    if connection.dialect.name == "postgresql":
        return tuple(statement.replace(" JSON ", " JSONB ") for statement in _DDL)
    if connection.dialect.name == "sqlite":
        return _DDL
    raise RuntimeError(f"{VERSION} unsupported database dialect: {connection.dialect.name}")


def _install_immutability_guards(connection: Connection) -> None:
    """Reject edits to the provenance/audit rows at the database boundary."""
    if connection.dialect.name == "sqlite":
        statements = (
            """
            CREATE TRIGGER IF NOT EXISTS trg_migration_provenance_immutable_update
            BEFORE UPDATE ON migration_provenance
            BEGIN SELECT RAISE(ABORT, 'migration provenance is immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_migration_provenance_immutable_delete
            BEFORE DELETE ON migration_provenance
            BEGIN SELECT RAISE(ABORT, 'migration provenance is immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_migration_audit_immutable_update
            BEFORE UPDATE ON migration_audit
            BEGIN SELECT RAISE(ABORT, 'migration audit is append-only'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_migration_audit_immutable_delete
            BEFORE DELETE ON migration_audit
            BEGIN SELECT RAISE(ABORT, 'migration audit is append-only'); END
            """,
        )
    elif connection.dialect.name == "postgresql":
        statements = (
            """
            CREATE OR REPLACE FUNCTION ekb_reject_migration_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'migration provenance or audit is immutable'; END;
            $$
            """,
            """
            DROP TRIGGER IF EXISTS trg_migration_provenance_immutable ON migration_provenance
            """,
            """
            CREATE TRIGGER trg_migration_provenance_immutable
            BEFORE UPDATE OR DELETE ON migration_provenance
            FOR EACH ROW EXECUTE FUNCTION ekb_reject_migration_mutation()
            """,
            """
            DROP TRIGGER IF EXISTS trg_migration_audit_immutable ON migration_audit
            """,
            """
            CREATE TRIGGER trg_migration_audit_immutable
            BEFORE UPDATE OR DELETE ON migration_audit
            FOR EACH ROW EXECUTE FUNCTION ekb_reject_migration_mutation()
            """,
        )
    else:
        raise RuntimeError(f"{VERSION} unsupported database dialect: {connection.dialect.name}")
    for statement in statements:
        connection.execute(text(statement))


def _ledger_checksum(connection: Connection) -> Optional[str]:
    row = connection.execute(
        text("SELECT checksum FROM migration_provenance WHERE version = :version"),
        {"version": VERSION},
    ).first()
    return None if row is None else str(row[0])


def _legacy_rows(connection: Connection) -> list[dict[str, Any]]:
    tables = set(inspect(connection).get_table_names())
    if "schema_migrations" not in tables:
        return []
    rows = list(
        connection.execute(
            text(
                "SELECT version, checksum, applied_at FROM schema_migrations "
                "ORDER BY applied_at ASC"
            )
        ).mappings()
    )
    positions = {version: index for index, version in enumerate(_HISTORICAL_V3_VERSIONS)}
    # Timestamp ties are resolved by the canonical manifest, never by a
    # physical row locator.  Validation below rejects contradictory timestamps.
    return [
        dict(row)
        for row in sorted(
            rows,
            key=lambda row: (
                str(row.get("applied_at") or ""),
                positions.get(str(row.get("version")), len(positions)),
            ),
        )
    ]


def _validate_legacy_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    observed = list(rows)
    versions = [str(row["version"]) for row in observed]
    unknown = sorted(set(versions) - set(_HISTORICAL_V3_VERSIONS))
    if unknown:
        raise RuntimeError(f"{VERSION} unknown historical migration(s): {','.join(unknown)}")
    positions = {version: index for index, version in enumerate(_HISTORICAL_V3_VERSIONS)}
    observed_positions = [positions[version] for version in versions]
    if observed_positions != sorted(observed_positions):
        raise RuntimeError(f"{VERSION} historical migration order drift detected")
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"{VERSION} duplicate historical migration version detected")
    if tuple(versions) != _HISTORICAL_V3_VERSIONS:
        missing = [version for version in _HISTORICAL_V3_VERSIONS if version not in versions]
        raise RuntimeError(
            f"{VERSION} incomplete historical migration facts: missing={','.join(missing)}"
        )
    timestamps = [str(row.get("applied_at") or "") for row in observed]
    if any(not timestamp for timestamp in timestamps):
        raise RuntimeError(f"{VERSION} missing historical applied_at")
    if timestamps != sorted(timestamps):
        raise RuntimeError(f"{VERSION} historical migration timestamp order drift detected")
    for row in observed:
        checksum = str(row.get("checksum") or "")
        if not checksum:
            raise RuntimeError(f"{VERSION} missing historical checksum")
    return observed


def _catalog_index_names(connection: Connection) -> set[str]:
    names = set()
    inspector = inspect(connection)
    for table in V4_001_OWNED_TABLES:
        for index in inspector.get_indexes(table):
            if index.get("name"):
                names.add(str(index["name"]))
    return names


def _verify_catalog(connection: Connection) -> tuple[str, ...]:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing_tables = sorted(set(V4_001_OWNED_TABLES) - table_names)
    if missing_tables:
        raise RuntimeError(
            f"{VERSION} verification failed: missing tables={','.join(missing_tables)}"
        )
    for table, required in _REQUIRED_COLUMNS.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(required - actual)
        if missing:
            raise RuntimeError(
                f"{VERSION} verification failed: {table} missing columns={','.join(missing)}"
            )
    indexes = _catalog_index_names(connection)
    if "ix_migration_audit_version_time" not in indexes:
        raise RuntimeError(f"{VERSION} verification failed: audit index missing")
    return tuple(sorted(indexes))


def _record_audit(
    connection: Connection,
    *,
    action: str,
    outcome: str,
    detail: dict[str, Any],
) -> None:
    from uuid import uuid4

    detail_json = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    detail_expression = (
        "CAST(:detail AS JSONB)"
        if connection.dialect.name == "postgresql"
        else ":detail"
    )
    connection.execute(
        text(
            "INSERT INTO migration_audit "
            "(id, version, action, outcome, detail, recorded_at) "
            f"VALUES (:id, :version, :action, :outcome, {detail_expression}, :recorded_at)"
        ),
        {
            "id": str(uuid4()),
            "version": VERSION,
            "action": action,
            "outcome": outcome,
            "detail": detail_json,
            "recorded_at": utc_now(),
        },
    )


def apply_v4_001(engine: Engine) -> MigrationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        legacy_rows = _validate_legacy_rows(_legacy_rows(connection))
        for statement in _ddl_for_connection(connection):
            connection.execute(text(statement))
        _install_immutability_guards(connection)
        applied_checksum = _ledger_checksum(connection)
        if applied_checksum is not None and applied_checksum != V4_001_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={applied_checksum} "
                f"expected={V4_001_CHECKSUM}"
            )
        if applied_checksum is not None:
            _verify_catalog(connection)
            _record_audit(
                connection,
                action="APPLY",
                outcome="NO_OP",
                detail={"legacy_count": len(legacy_rows)},
            )
            return MigrationResult(VERSION, V4_001_CHECKSUM, False, {"legacy_observed": 0})

        now = utc_now()
        provenance_expression = (
            "CAST(:provenance AS JSONB)"
            if connection.dialect.name == "postgresql"
            else ":provenance"
        )
        provenance_rows = [
            {
                "version": version,
                "owner": "legacy-v3",
                "checksum": str(row["checksum"]),
                "applied_at": str(row["applied_at"]),
                "verify_status": "LEGACY_OBSERVED",
                "provenance": json.dumps(
                    {"source": "schema_migrations", "observed_position": index},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "manifest_position": index,
                "created_at": now,
            }
            for index, (version, row) in enumerate(
                zip((str(row["version"]) for row in legacy_rows), legacy_rows)
            )
        ]
        for row in provenance_rows:
            connection.execute(
                text(
                    "INSERT INTO migration_provenance "
                    "(version, owner, checksum, applied_at, verify_status, provenance, "
                    "manifest_position, created_at) VALUES "
                    "(:version, :owner, :checksum, :applied_at, :verify_status, "
                    f"{provenance_expression}, "
                    ":manifest_position, :created_at)"
                ),
                row,
            )
        connection.execute(
            text(
                "INSERT INTO migration_provenance "
                "(version, owner, checksum, applied_at, verify_status, provenance, "
                "manifest_position, created_at) VALUES "
                "(:version, :owner, :checksum, :applied_at, :verify_status, "
                f"{provenance_expression}, "
                ":manifest_position, :created_at)"
            ),
            {
                "version": VERSION,
                "owner": "schema-governance",
                "checksum": V4_001_CHECKSUM,
                "applied_at": now,
                "verify_status": "VERIFIED",
                "provenance": json.dumps(
                    {
                        "manifest": list(V4_MANIFEST),
                        "legacy_versions": list(_HISTORICAL_V3_VERSIONS),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "manifest_position": len(_HISTORICAL_V3_VERSIONS),
                "created_at": now,
            },
        )
        _verify_catalog(connection)
        _record_audit(
            connection,
            action="APPLY",
            outcome="PASS",
            detail={"legacy_observed": len(legacy_rows), "manifest_entries": len(V4_MANIFEST)},
        )
    return MigrationResult(
        VERSION,
        V4_001_CHECKSUM,
        True,
        {"legacy_observed": len(legacy_rows), "manifest_entries": len(V4_MANIFEST)},
    )


def verify_v4_001(engine: Engine) -> VerificationResult:
    with engine.begin() as connection:
        _set_sqlite_foreign_keys(connection)
        checksum = _ledger_checksum(connection)
        if checksum != V4_001_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={checksum} expected={V4_001_CHECKSUM}"
            )
        observed = _validate_legacy_rows(_legacy_rows(connection))
        indexes = _verify_catalog(connection)
        rows = connection.execute(
            text(
                "SELECT version, manifest_position, checksum FROM migration_provenance "
                "ORDER BY manifest_position"
            )
        ).mappings().all()
        expected_versions = [str(row["version"]) for row in rows]
        expected_prefix = [str(row["version"]) for row in observed]
        expected_prefix.extend(version for version, _owner in V4_MANIFEST)
        # The local-slice manifest is the *authoritative prefix* of the full
        # v4 chain.  ``verify_v4_001`` runs both mid-chain (when v4_002+ are not
        # yet applied) and at the end of the full chain (when v4_003+ have
        # appended their own provenance rows).  We therefore only assert that
        # the known prefix is preserved **up to what is currently present** and
        # that no reordering/drift occurred.  The exact checksum of this
        # migration depends only on the module constants, never on verify logic.
        n = min(len(expected_prefix), len(expected_versions))
        append_order_matches = expected_versions[:n] == expected_prefix[:n]
        if not append_order_matches or VERSION not in expected_versions:
            raise RuntimeError(f"{VERSION} verification failed: provenance append order drift")
        for row in rows:
            if not row["checksum"]:
                raise RuntimeError(f"{VERSION} verification failed: missing provenance checksum")
        _record_audit(
            connection,
            action="VERIFY",
            outcome="PASS",
            detail={"legacy_observed": len(observed), "provenance_rows": len(rows)},
        )
    return VerificationResult(
        "PASS",
        VERSION,
        V4_001_CHECKSUM,
        V4_001_OWNED_TABLES,
        indexes,
        {"legacy_observed": len(observed), "provenance_rows": len(rows)},
    )


def rollback_v4_001_dry_run(engine: Engine) -> RollbackPlan:
    with engine.begin() as connection:
        table_names = set(inspect(connection).get_table_names())
        if "migration_provenance" not in table_names:
            return RollbackPlan(False, (), ("migration_provenance", "migration_audit"))
        checksum = _ledger_checksum(connection)
        if checksum is None:
            return RollbackPlan(False, (), ("migration_provenance", "migration_audit"))
        if checksum != V4_001_CHECKSUM:
            raise RuntimeError(
                f"{VERSION} checksum mismatch: applied={checksum} expected={V4_001_CHECKSUM}"
            )
        _record_audit(
            connection,
            action="ROLLBACK_DRY_RUN",
            outcome="BLOCKED",
            detail={"reason": "immutable_provenance_retained"},
        )
    return RollbackPlan(
        True,
        (),
        ("migration_provenance", "migration_audit", "schema_migrations"),
        blocked_reason="immutable_provenance_retained",
    )

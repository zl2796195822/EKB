from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v3_fullstack import KNOWN_VERSIONS as KNOWN_V3_VERSIONS
from ekb_api.migrations.v4_001_migration_provenance import (
    _HISTORICAL_V3_VERSIONS,
    V4_001_CHECKSUM,
    _validate_legacy_rows,
)
from ekb_api.migrations.v4_002_runtime_jobs import V4_002_CHECKSUM
from ekb_api.migrations.v4_fullstack import KNOWN_VERSIONS

API_ROOT = Path(__file__).resolve().parents[2]


def test_canonical_v3_ledger_order_is_shared_by_runners() -> None:
    expected = (
        "v3_001_identity",
        "v3_002_content",
        "v3_003_analytics",
        "v3_004_apps",
        "v3_005_analytics_compat",
        "v3_006_apps_compat",
        "v3_007_content_governance_compat",
        "v3_005_llm",
    )
    assert KNOWN_V3_VERSIONS == expected
    assert tuple(KNOWN_VERSIONS[: len(expected)]) == expected
    assert tuple(_HISTORICAL_V3_VERSIONS) == expected


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _run_migration(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "ekb_api.migrations.v4_fullstack",
        "--database-url",
        _database_url(path),
        *extra,
    ]
    return subprocess.run(
        command,
        cwd=API_ROOT,
        env={"PYTHONPATH": str(API_ROOT), "EKB_ENV": "test"},
        capture_output=True,
        text=True,
        check=False,
    )


def _prepare_legacy_fixture(path: Path) -> None:
    """Explicitly create the legacy fixture before invoking the v4 runner."""
    engine = build_engine(_database_url(path))
    prepare_legacy_schema(engine, seed=False)


def test_v4_apply_verify_repeat_and_rollback_keep_ledger(tmp_path: Path) -> None:
    database = tmp_path / "v4.db"
    _prepare_legacy_fixture(database)

    first = _run_migration(
        database,
        "--verify",
        "--expect-versions",
        ",".join(KNOWN_VERSIONS),
    )
    second = _run_migration(database, "--verify")
    rollback_without_dry_run = _run_migration(database, "--rollback")
    rollback = _run_migration(database, "--rollback", "--dry-run")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert rollback_without_dry_run.returncode != 0
    assert "requires --dry-run" in rollback_without_dry_run.stderr
    assert rollback.returncode == 2, rollback.stdout + rollback.stderr
    assert "retain=schema_migrations,migration_provenance,migration_audit" in rollback.stdout

    engine = build_engine(_database_url(database))
    with engine.connect() as connection:
        provenance_rows = connection.execute(
            text(
                "SELECT version, checksum FROM migration_provenance "
                "ORDER BY manifest_position"
            )
        ).all()
        provenance_versions = [row[0] for row in provenance_rows]
        provenance = {row[0]: row[1] for row in provenance_rows}
        # The chain tail must match the known v4 append order; v4_003..v4_005
        # are now part of KNOWN_VERSIONS.
        assert provenance_versions[-len(KNOWN_VERSIONS) :] == list(KNOWN_VERSIONS)
        # Historical v4 checksums remain stable and immutable.
        assert provenance["v4_001_migration_provenance"] == V4_001_CHECKSUM
        assert provenance["v4_002_runtime_jobs"] == V4_002_CHECKSUM
        assert connection.execute(text("SELECT COUNT(*) FROM migration_audit")).scalar_one() >= 6


def test_provenance_checksum_drift_is_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "drift.db"
    _prepare_legacy_fixture(database)
    first = _run_migration(database)
    assert first.returncode == 0, first.stderr

    # Simulate a pre-existing ledger written by an older/broken binary.  The
    # production trigger intentionally prevents an in-band mutation, so this
    # fixture removes only its own temporary trigger before corrupting the row.
    connection = sqlite3.connect(str(database))
    connection.execute("DROP TRIGGER trg_migration_provenance_immutable_update")
    connection.execute(
        "UPDATE migration_provenance SET checksum='drifted' "
        "WHERE version='v4_001_migration_provenance'"
    )
    connection.commit()
    connection.close()

    failed = _run_migration(database, "--verify")
    assert failed.returncode != 0
    assert "checksum mismatch" in failed.stderr


def test_provenance_rows_are_database_immutable(tmp_path: Path) -> None:
    database = tmp_path / "immutable.db"
    _prepare_legacy_fixture(database)
    result = _run_migration(database)
    assert result.returncode == 0, result.stderr
    engine = build_engine(_database_url(database))

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "UPDATE migration_provenance SET owner='changed' "
                    "WHERE version='v4_001_migration_provenance'"
                )
            )


def test_runner_rejects_non_append_expect_versions(tmp_path: Path) -> None:
    _prepare_legacy_fixture(tmp_path / "versions.db")
    result = _run_migration(
        tmp_path / "versions.db",
        "--through",
        "v4_001_migration_provenance",
        "--expect-versions",
        "v4_001_migration_provenance",
    )
    assert result.returncode != 0
    assert "version contract mismatch" in result.stderr


def test_provenance_rejects_unknown_or_missing_historical_facts() -> None:
    complete = [
        {"version": version, "checksum": f"checksum-{index}", "applied_at": str(index)}
        for index, version in enumerate(_HISTORICAL_V3_VERSIONS)
    ]
    with pytest.raises(RuntimeError, match="unknown historical"):
        _validate_legacy_rows([*complete[:-1], {**complete[-1], "version": "v3_unknown"}])
    with pytest.raises(RuntimeError, match="incomplete historical"):
        _validate_legacy_rows(complete[:-1])


def test_runtime_catalog_has_tenant_fks_partial_claim_and_outbox_delivery_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.db"
    _prepare_legacy_fixture(database)
    result = _run_migration(database)
    assert result.returncode == 0, result.stderr
    engine = build_engine(_database_url(database))
    inspector = inspect(engine)

    foreign_keys = {
        (
            table,
            fk["constrained_columns"][0],
            fk["referred_table"],
            fk["referred_columns"][0],
        )
        for table in (
            "background_jobs",
            "background_job_attempts",
            "scheduler_runs",
            "job_outbox",
        )
        for fk in inspector.get_foreign_keys(table)
        if len(fk["constrained_columns"]) == 1 and len(fk["referred_columns"]) == 1
    }
    assert {
        ("background_jobs", "tenant_id", "tenants", "id"),
        ("background_job_attempts", "tenant_id", "tenants", "id"),
        ("background_job_attempts", "job_id", "background_jobs", "id"),
        ("scheduler_runs", "tenant_id", "tenants", "id"),
        ("job_outbox", "tenant_id", "tenants", "id"),
    } <= foreign_keys

    with engine.connect() as connection:
        claim_ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name='ix_jobs_claim'")
        ).scalar_one()
        assert "where state in ('queued','retry_wait')" in str(claim_ddl).lower()
        outbox_ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='job_outbox'")
        ).scalar_one()
        assert "UNIQUE (tenant_id, event_type, aggregate_type, aggregate_id)" not in outbox_ddl
        jobs_ddl = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='background_jobs'")
        ).scalar_one()
        attempts_ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='background_job_attempts'"
            )
        ).scalar_one()
        assert "UNIQUE (tenant_id, job_type, idempotency_key)" in jobs_ddl
        assert "UNIQUE (job_id, attempt_no)" in attempts_ddl


def test_runner_never_bootstraps_orm_schema_implicitly(tmp_path: Path) -> None:
    database = tmp_path / "unprepared.db"
    result = _run_migration(database)

    assert result.returncode != 0
    assert "implicit ORM bootstrap is disabled" in result.stderr
    assert (
        not database.exists()
        or inspect(build_engine(_database_url(database))).get_table_names() == []
    )


def test_production_runner_never_bootstraps_orm_schema(tmp_path: Path) -> None:
    database = tmp_path / "production-unprepared.db"
    command = [
        sys.executable,
        "-m",
        "ekb_api.migrations.v4_fullstack",
        "--database-url",
        _database_url(database),
    ]
    result = subprocess.run(
        command,
        cwd=API_ROOT,
        env={"PYTHONPATH": str(API_ROOT), "EKB_ENV": "production"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "implicit ORM bootstrap is disabled" in result.stderr
    assert (
        not database.exists()
        or inspect(build_engine(_database_url(database))).get_table_names() == []
    )


def test_provenance_timestamp_order_contradiction_is_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "timestamp-drift.db"
    _prepare_legacy_fixture(database)
    engine = build_engine(_database_url(database))
    from ekb_api.migrations.v3_fullstack import CHAIN

    for step in CHAIN:
        step.apply(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE schema_migrations SET applied_at='2026-08-11T00:00:00Z' "
                "WHERE version='v3_005_llm'"
            )
        )

    with pytest.raises(RuntimeError, match="historical migration (timestamp )?order drift"):
        from ekb_api.migrations.v4_001_migration_provenance import apply_v4_001

        apply_v4_001(engine)

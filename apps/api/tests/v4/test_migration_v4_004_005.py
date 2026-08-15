"""Targeted verification for the v4_004 (cutover ledger) and v4_005
(retention governance) migrations.

These migrations only establish durable bookkeeping schema; the actual
SQLite->PostgreSQL data move and the 30-day purge scheduler are operational
steps that remain BLOCKED in this environment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_004_postgres_cutover import V4_004_CHECKSUM
from ekb_api.migrations.v4_005_retention_governance import (
    V4_005_CHECKSUM,
    _has_valid_30_day_check,
)
from ekb_api.migrations.v4_fullstack import KNOWN_VERSIONS

API_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "sqltext",
    [
        "expires_at = deleted_at + interval '30 days'",
        "((expires_at) = ((deleted_at) + ('30 days'::interval)))",
        "expires_at = deleted_at + CAST('30 days' AS interval)",
        "(expires_at) = (deleted_at + CAST('30 days' AS pg_catalog.interval))",
    ],
)
def test_v4_005_accepts_postgresql_constraint_rendering(sqltext: str) -> None:
    assert _has_valid_30_day_check([{"sqltext": sqltext}]) is True


@pytest.mark.parametrize(
    "sqltext",
    [
        "expires_at = deleted_at + '31 days'::interval",
        "expires_at = deleted_at + interval '30 hours'",
        "expires_at = deleted_at + '30 days'::text",
        "expires_at = deleted_at + '30 days'::interval OR deleted_at IS NULL",
    ],
)
def test_v4_005_rejects_constraint_with_invalid_or_extra_predicate(sqltext: str) -> None:
    assert _has_valid_30_day_check([{"sqltext": sqltext}]) is False


def _run(database: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ekb_api.migrations.v4_fullstack",
            "--database-url",
            f"sqlite:///{database}",
            *extra,
        ],
        cwd=API_ROOT,
        env={"PYTHONPATH": str(API_ROOT), "EKB_ENV": "test"},
        capture_output=True,
        text=True,
        check=False,
    )


def _prepare(database: Path) -> None:
    engine = build_engine(f"sqlite:///{database}")
    prepare_legacy_schema(engine, seed=False)


def test_v4_004_005_apply_verify_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "retention.db"
    _prepare(database)

    first = _run(database, "--verify", "--expect-versions", ",".join(KNOWN_VERSIONS))
    assert first.returncode == 0, first.stderr

    repeat = _run(database, "--verify")
    assert repeat.returncode == 0, repeat.stderr

    engine = build_engine(f"sqlite:///{database}")
    inspector = inspect(engine)

    assert "cutover_ledger" in inspector.get_table_names()
    assert "deletion_batches" in inspector.get_table_names()
    assert "trash_items" in inspector.get_table_names()

    trash_cols = {c["name"] for c in inspector.get_columns("trash_items")}
    for column in (
        "deletion_generation",
        "deletion_batch_id",
        "purge_state",
        "claim_token",
        "claim_fencing_token",
        "claim_expires_at",
        "purge_job_id",
    ):
        assert column in trash_cols, column

    kb_cols = {c["name"] for c in inspector.get_columns("knowledge_bases")}
    assert {"expires_at", "deleted_by", "deletion_batch_id", "purged_at"} <= kb_cols
    doc_cols = {c["name"] for c in inspector.get_columns("documents")}
    assert {"deleted_at", "expires_at", "purged_at"} <= doc_cols
    conv_cols = {c["name"] for c in inspector.get_columns("conversations")}
    assert {"expires_at", "purged_at"} <= conv_cols

    trash_indexes = {i["name"] for i in inspector.get_indexes("trash_items")}
    assert "ux_trash_items_resource_generation" in trash_indexes
    assert "ux_trash_claim_token" in trash_indexes
    assert "ix_trash_expiry_claim" in trash_indexes

    with engine.connect() as connection:
        provenance = connection.execute(
            text("SELECT version, checksum FROM migration_provenance ORDER BY manifest_position")
        ).all()
        versions = [row[0] for row in provenance]
        # The chain is append-only, so assert relative order and checksum binding
        # rather than tail position (later phases append after v4_005).
        position_004 = versions.index("v4_004_postgres_cutover")
        position_005 = versions.index("v4_005_retention_governance")
        assert position_005 == position_004 + 1
        assert provenance[position_004][1] == V4_004_CHECKSUM
        assert provenance[position_005][1] == V4_005_CHECKSUM


def test_v4_004_005_rollback_is_dry_run_blocked(tmp_path: Path) -> None:
    database = tmp_path / "retention-rollback.db"
    _prepare(database)
    assert _run(database).returncode == 0, "initial apply failed"

    without_dry = _run(database, "--rollback")
    assert without_dry.returncode != 0
    assert "requires --dry-run" in without_dry.stderr

    dry = _run(database, "--rollback", "--dry-run")
    assert dry.returncode == 2, dry.stdout + dry.stderr
    assert "v4_004_postgres_cutover rollback=blocked" in dry.stdout
    assert "v4_005_retention_governance rollback=blocked" in dry.stdout


def test_v4_005_requires_v4_004_first(tmp_path: Path) -> None:
    database = tmp_path / "retention-order.db"
    _prepare(database)
    engine = build_engine(f"sqlite:///{database}")
    # Apply only up to v4_003, then attempt to apply v4_005 directly.
    from ekb_api.migrations.v3_fullstack import CHAIN as V3_CHAIN
    from ekb_api.migrations.v4_001_migration_provenance import apply_v4_001, verify_v4_001
    from ekb_api.migrations.v4_002_runtime_jobs import apply_v4_002, verify_v4_002
    from ekb_api.migrations.v4_003_provider_security import apply_v4_003, verify_v4_003
    from ekb_api.migrations.v4_005_retention_governance import apply_v4_005

    for step in V3_CHAIN:
        step.apply(engine)
    apply_v4_001(engine)
    verify_v4_001(engine)
    apply_v4_002(engine)
    verify_v4_002(engine)
    apply_v4_003(engine)
    verify_v4_003(engine)


    # v4_005 must refuse to apply without verified v4_004.
    with pytest.raises(RuntimeError, match="requires verified v4_004"):
        apply_v4_005(engine)

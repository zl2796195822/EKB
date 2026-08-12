"""CLI for the immutable v3-compatible plus additive v4 migration chain."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import inspect

from ekb_api.core.db import _init_pgvector, build_engine
from ekb_api.migrations import (
    v3_001_identity,
    v3_002_content,
    v3_003_analytics,
    v3_004_apps,
    v3_005_analytics_compat,
    v3_005_llm,
    v3_006_apps_compat,
    v3_007_content_governance_compat,
    v4_001_migration_provenance,
    v4_002_runtime_jobs,
    v4_003_provider_security,
    v4_004_postgres_cutover,
    v4_005_retention_governance,
    v4_006_storage_ingestion,
    v4_007_chat_graph,
    v4_008_attachments,
)


@dataclass(frozen=True)
class MigrationStep:
    version: str
    apply: Callable
    verify: Callable
    rollback_dry_run: Callable


class _PostgresV3Inspector:
    """Normalize PostgreSQL's equivalent ANY check rendering for immutable v3 verify."""

    _STATUS_CHECKS = {
        "tenant_memberships": "STATUS IN ('ACTIVE', 'INVITED', 'SUSPENDED')",
        "api_keys": "STATUS IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
        "auth_sessions": "STATUS IN ('ACTIVE', 'REVOKED', 'EXPIRED')",
    }

    def __init__(self, delegate) -> None:
        self._delegate = delegate

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def get_check_constraints(self, table_name, **kwargs):
        constraints = self._delegate.get_check_constraints(table_name, **kwargs)
        expected = self._STATUS_CHECKS.get(table_name)
        if expected is None:
            return constraints
        normalized = " ".join(expected.split()).upper()
        for constraint in constraints:
            sqltext = " ".join(str(constraint.get("sqltext") or "").split()).upper()
            if "STATUS" in sqltext and all(
                value in sqltext for value in ("ACTIVE", "INVITED", "SUSPENDED")
                if table_name == "tenant_memberships"
            ) and (
                table_name == "tenant_memberships"
                or all(value in sqltext for value in ("ACTIVE", "REVOKED", "EXPIRED"))
            ):
                return [{**constraint, "sqltext": normalized}]
        return constraints


class _PostgresV3Connection:
    """Adapt known historical PG syntax/bind casts without changing checksums."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self.dialect = delegate.dialect

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def execute(self, statement, parameters=None, *args, **kwargs):
        sql = str(getattr(statement, "text", statement))
        original_sql = sql
        if "INSERT OR IGNORE INTO" in sql:
            sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            if "ON CONFLICT" not in sql.upper():
                sql = f"{sql.rstrip()} ON CONFLICT DO NOTHING"
        if ":capabilities::JSONB" in sql:
            sql = sql.replace(":capabilities::JSONB", "CAST(:capabilities AS JSONB)")
        if sql != original_sql:
            from sqlalchemy import text

            statement = text(sql)
        return self._delegate.execute(statement, parameters, *args, **kwargs)


@contextmanager
def _v3_postgresql_catalog_compat(module, engine) -> Iterator[None]:
    supported_modules = (
        "v3_001_identity",
        "v3_003_analytics",
        "v3_004_apps",
        "v3_006_apps_compat",
    )
    if engine.dialect.name != "postgresql" or not any(
        module.__name__.endswith(name) for name in supported_modules
    ):
        yield
        return
    original_inspect = module.inspect
    original_engine_begin = engine.begin

    def compat_begin():
        context = original_engine_begin()

        class ConnectionContext:
            def __enter__(self):
                self.connection = _PostgresV3Connection(context.__enter__())
                return self.connection

            def __exit__(self, exc_type, exc_value, traceback):
                return context.__exit__(exc_type, exc_value, traceback)

        return ConnectionContext()

    module.inspect = lambda bind: _PostgresV3Inspector(
        original_inspect(getattr(bind, "_delegate", bind))
    )
    engine.begin = compat_begin
    try:
        yield
    finally:
        module.inspect = original_inspect
        engine.begin = original_engine_begin


def _compat_call(module, function: Callable) -> Callable:
    def call(engine):
        with _v3_postgresql_catalog_compat(module, engine):
            return function(engine)

    return call


def _step(version: str, module, *, apply_name: str, verify_name: str, rollback_name: str):
    return MigrationStep(
        version,
        _compat_call(module, getattr(module, apply_name)),
        _compat_call(module, getattr(module, verify_name)),
        _compat_call(module, getattr(module, rollback_name)),
    )


CHAIN = (
    _step(
        v3_001_identity.VERSION,
        v3_001_identity,
        apply_name="apply_v3_001",
        verify_name="verify_v3_001",
        rollback_name="rollback_v3_001_dry_run",
    ),
    _step(
        v3_002_content.VERSION,
        v3_002_content,
        apply_name="apply_v3_002",
        verify_name="verify_v3_002",
        rollback_name="rollback_v3_002_dry_run",
    ),
    _step(
        v3_003_analytics.VERSION,
        v3_003_analytics,
        apply_name="apply_v3_003",
        verify_name="verify_v3_003",
        rollback_name="rollback_v3_003_dry_run",
    ),
    _step(
        v3_004_apps.VERSION,
        v3_004_apps,
        apply_name="apply_v3_004",
        verify_name="verify_v3_004",
        rollback_name="rollback_v3_004_dry_run",
    ),
    _step(
        v3_005_analytics_compat.VERSION,
        v3_005_analytics_compat,
        apply_name="apply_v3_005",
        verify_name="verify_v3_005",
        rollback_name="rollback_v3_005_dry_run",
    ),
    _step(
        v3_006_apps_compat.VERSION,
        v3_006_apps_compat,
        apply_name="apply_v3_006",
        verify_name="verify_v3_006",
        rollback_name="rollback_v3_006_dry_run",
    ),
    _step(
        v3_007_content_governance_compat.VERSION,
        v3_007_content_governance_compat,
        apply_name="apply_v3_007",
        verify_name="verify_v3_007",
        rollback_name="rollback_v3_007_dry_run",
    ),
    _step(
        v3_005_llm.VERSION,
        v3_005_llm,
        apply_name="apply_v3_005",
        verify_name="verify_v3_005",
        rollback_name="rollback_v3_005_dry_run",
    ),
    _step(
        v4_001_migration_provenance.VERSION,
        v4_001_migration_provenance,
        apply_name="apply_v4_001",
        verify_name="verify_v4_001",
        rollback_name="rollback_v4_001_dry_run",
    ),
    _step(
        v4_002_runtime_jobs.VERSION,
        v4_002_runtime_jobs,
        apply_name="apply_v4_002",
        verify_name="verify_v4_002",
        rollback_name="rollback_v4_002_dry_run",
    ),
    _step(
        v4_003_provider_security.VERSION,
        v4_003_provider_security,
        apply_name="apply_v4_003",
        verify_name="verify_v4_003",
        rollback_name="rollback_v4_003_dry_run",
    ),
    _step(
        v4_004_postgres_cutover.VERSION,
        v4_004_postgres_cutover,
        apply_name="apply_v4_004",
        verify_name="verify_v4_004",
        rollback_name="rollback_v4_004_dry_run",
    ),
    _step(
        v4_005_retention_governance.VERSION,
        v4_005_retention_governance,
        apply_name="apply_v4_005",
        verify_name="verify_v4_005",
        rollback_name="rollback_v4_005_dry_run",
    ),
    _step(
        v4_006_storage_ingestion.VERSION,
        v4_006_storage_ingestion,
        apply_name="apply_v4_006",
        verify_name="verify_v4_006",
        rollback_name="rollback_v4_006_dry_run",
    ),
    _step(
        v4_007_chat_graph.VERSION,
        v4_007_chat_graph,
        apply_name="apply_v4_007",
        verify_name="verify_v4_007",
        rollback_name="rollback_v4_007_dry_run",
    ),
    _step(
        v4_008_attachments.VERSION,
        v4_008_attachments,
        apply_name="apply_v4_008",
        verify_name="verify_v4_008",
        rollback_name="rollback_v4_008_dry_run",
    ),
)

KNOWN_VERSIONS = tuple(step.version for step in CHAIN)
LATEST_VERSION = KNOWN_VERSIONS[-1]


def _steps_through(target: str) -> tuple[MigrationStep, ...]:
    if target not in KNOWN_VERSIONS:
        raise RuntimeError(
            "unsupported migration target: {} (known={})".format(
                target, ",".join(KNOWN_VERSIONS)
            )
        )
    return CHAIN[: KNOWN_VERSIONS.index(target) + 1]


def _check_versions(expected: Optional[str], steps: tuple[MigrationStep, ...]) -> None:
    if not expected:
        return
    versions = [item.strip() for item in expected.split(",") if item.strip()]
    planned = [step.version for step in steps]
    if versions != planned:
        raise RuntimeError(
            "migration version contract mismatch: got={} planned={}".format(
                ",".join(versions), ",".join(planned)
            )
        )


_REQUIRED_LEGACY_SCHEMA_TABLES = frozenset(
    {
        "audit_logs",
        "chunks",
        "conversations",
        "document_versions",
        "documents",
        "feedback",
        "ingest_jobs",
        "kb_memberships",
        "knowledge_bases",
        "messages",
        "qa_turns",
        "review_items",
        "sync_sources",
        "tenant_daily_usage",
        "tenants",
        "users",
    }
)


def _require_imported_legacy_schema(engine) -> None:
    """Require the imported/base schema before any v4-chain DDL.

    The v4 runner is a migration/cutover gate, not a legacy ORM bootstrapper.
    Fresh local databases must prepare this fixture explicitly before invoking
    the runner; production must arrive here with an imported legacy schema.
    """
    existing = set(inspect(engine).get_table_names())
    missing = sorted(_REQUIRED_LEGACY_SCHEMA_TABLES - existing)
    if missing:
        raise RuntimeError(
            "v4 runner requires an imported legacy schema; implicit ORM bootstrap "
            "is disabled (missing={})".format(",".join(missing))
        )


def _result_value(result, key: str, default=None):
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run immutable EKB v3/v4 migrations")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--through", default=LATEST_VERSION)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--expect-versions")
    args = parser.parse_args(argv)

    steps = _steps_through(args.through)
    _check_versions(args.expect_versions, steps)
    engine = build_engine(args.database_url)
    if args.database_url.startswith("postgresql"):
        _init_pgvector(engine)
    _require_imported_legacy_schema(engine)

    if args.rollback:
        if not args.dry_run:
            raise RuntimeError("v4 rollback requires --dry-run")
        blocked = False
        for step in reversed(steps):
            plan = step.rollback_dry_run(engine)
            if args.reverse:
                print(f"version={step.version} rollback=reverse dry_run=true")
            if not _result_value(plan, "applied", False):
                print(f"version={step.version} down_plan=none")
                continue
            reason = _result_value(plan, "blocked_reason")
            if reason:
                print(f"version={step.version} rollback=blocked reason={reason}")
                blocked = True
                continue
            for object_name in _result_value(plan, "objects", ()):
                print(f"version={step.version} would_drop={object_name}")
        print("retain=schema_migrations,migration_provenance,migration_audit")
        return 2 if blocked else 0

    for step in steps:
        result = step.apply(engine)
        print(
            "version={} checksum={} applied={} backfill={}".format(
                _result_value(result, "version", step.version),
                _result_value(result, "checksum", ""),
                _result_value(result, "applied", False),
                _result_value(result, "backfill_counts", {}),
            )
        )
        if args.verify:
            verification = step.verify(engine)
            status = _result_value(verification, "status", "FAIL")
            print(
                "version={} checksum={} tables={} indexes={} status={}".format(
                    _result_value(verification, "version", step.version),
                    _result_value(verification, "checksum", ""),
                    len(_result_value(verification, "tables", ())),
                    len(_result_value(verification, "indexes", ())),
                    status,
                )
            )
            if status != "PASS":
                raise RuntimeError(f"{step.version} verify failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

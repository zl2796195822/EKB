"""CLI runner for the immutable EKB v3 migration chain.

Migrations are ordered and checksum-locked.  ``--through <version>`` applies
every migration up to and including that version; already-applied versions are
no-ops as long as their checksum still matches.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable, Optional

from ekb_api.core.db import _init_pgvector, build_engine, prepare_legacy_schema
from ekb_api.migrations import (
    v3_001_identity,
    v3_002_content,
    v3_003_analytics,
    v3_004_apps,
    v3_005_analytics_compat,
    v3_005_llm,
    v3_006_apps_compat,
    v3_007_content_governance_compat,
    v3_008_content_hierarchy,
)


@dataclass(frozen=True)
class MigrationStep:
    version: str
    apply: Callable
    verify: Callable
    rollback_dry_run: Callable


# ---- Contract wrappers -----------------------------------------------------
# Some legacy migrations (e.g. v3_004_apps) return plain dicts instead of the
# frozen dataclass objects used by the rest of the chain.  Rather than edit
# their applied-on-disk source we normalise at the aggregator boundary — the
# ledger/checksum lives inside each migration module and is untouched here.

def _wrap_apply_result(raw, fallback_version: str):
    if not isinstance(raw, dict):
        return raw
    status = raw.get("status", "")
    applied = True if status == "applied" else status != "skip" and raw.get("applied", False)
    backfill = {
        key: value for key, value in raw.items()
        if key not in {"status", "checksum", "applied", "reason"}
    }
    return SimpleNamespace(
        version=str(raw.get("version") or fallback_version),
        checksum=str(raw.get("checksum") or "0" * 64),
        applied=bool(applied),
        backfill_counts=backfill,
    )


def _wrap_verify_result(raw, fallback_version: str):
    if not isinstance(raw, dict):
        return raw
    tables = tuple(raw.get("tables") or ())
    indexes = tuple(raw.get("indexes") or ())
    backfill = {
        key: value for key, value in raw.items()
        if key not in {"status", "checksum", "expected_checksum", "reason", "tables", "indexes"}
    }
    return SimpleNamespace(
        status=str(raw.get("status") or "FAIL"),
        version=str(raw.get("version") or fallback_version),
        checksum=str(raw.get("checksum") or raw.get("expected_checksum") or "0" * 64),
        tables=tables,
        indexes=indexes,
        backfill_counts=backfill,
    )


def _wrap_rollback_result(raw):
    if not isinstance(raw, dict):
        return raw
    blocked = raw.get("action") == "BLOCKED" or bool(raw.get("blocked_reason"))
    objects = ()
    if raw.get("action") and raw["action"] != "BLOCKED" and "DROP" in str(raw["action"]):
        objects = (str(raw["action"]),)
    retained = ("schema_migrations", "audit_logs")
    return SimpleNamespace(
        applied=raw.get("status") == "dry-run" or raw.get("applied", True),
        objects=objects,
        retained=retained,
        blocked_reason="active_installs_present" if blocked else None,
    )


#: Ordered chain. Append new migrations at the end; never reorder or edit.
CHAIN = (
    MigrationStep(
        v3_001_identity.VERSION,
        v3_001_identity.apply_v3_001,
        v3_001_identity.verify_v3_001,
        v3_001_identity.rollback_v3_001_dry_run,
    ),
    MigrationStep(
        v3_002_content.VERSION,
        v3_002_content.apply_v3_002,
        v3_002_content.verify_v3_002,
        v3_002_content.rollback_v3_002_dry_run,
    ),
    MigrationStep(
        v3_003_analytics.VERSION,
        v3_003_analytics.apply_v3_003,
        v3_003_analytics.verify_v3_003,
        v3_003_analytics.rollback_v3_003_dry_run,
    ),
    MigrationStep(
        v3_004_apps.VERSION,
        v3_004_apps.apply_v3_004,
        v3_004_apps.verify_v3_004,
        v3_004_apps.rollback_v3_004_dry_run,
    ),
    MigrationStep(
        v3_005_analytics_compat.VERSION,
        v3_005_analytics_compat.apply_v3_005,
        v3_005_analytics_compat.verify_v3_005,
        v3_005_analytics_compat.rollback_v3_005_dry_run,
    ),
    MigrationStep(
        v3_006_apps_compat.VERSION,
        v3_006_apps_compat.apply_v3_006,
        v3_006_apps_compat.verify_v3_006,
        v3_006_apps_compat.rollback_v3_006_dry_run,
    ),
    MigrationStep(
        v3_007_content_governance_compat.VERSION,
        v3_007_content_governance_compat.apply_v3_007,
        v3_007_content_governance_compat.verify_v3_007,
        v3_007_content_governance_compat.rollback_v3_007_dry_run,
    ),
    MigrationStep(
        v3_005_llm.VERSION,
        v3_005_llm.apply_v3_005,
        v3_005_llm.verify_v3_005,
        v3_005_llm.rollback_v3_005_dry_run,
    ),
    MigrationStep(
        v3_008_content_hierarchy.VERSION,
        v3_008_content_hierarchy.apply_v3_008,
        v3_008_content_hierarchy.verify_v3_008,
        v3_008_content_hierarchy.rollback_v3_008_dry_run,
    ),
)

KNOWN_VERSIONS = tuple(step.version for step in CHAIN)
LATEST_VERSION = KNOWN_VERSIONS[-1]


def _steps_through(target: str) -> tuple:
    if target not in KNOWN_VERSIONS:
        raise RuntimeError(
            f"unsupported migration target: {target} (known={','.join(KNOWN_VERSIONS)})"
        )
    index = KNOWN_VERSIONS.index(target)
    return CHAIN[: index + 1]


def _check_versions(expected: Optional[str], steps: tuple) -> None:
    if not expected:
        return
    versions = [item.strip() for item in expected.split(",") if item.strip()]
    planned = [step.version for step in steps]
    if versions != planned:
        raise RuntimeError(
            f"unsupported v3 migration versions: got={','.join(versions)} "
            f"planned={','.join(planned)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run immutable EKB v3 migrations")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--through", default=LATEST_VERSION)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--expect-versions")
    args = parser.parse_args()

    steps = _steps_through(args.through)
    _check_versions(args.expect_versions, steps)
    engine = build_engine(args.database_url)
    if args.database_url.startswith("postgresql"):
        _init_pgvector(engine)
    prepare_legacy_schema(engine, seed=False)

    if args.rollback:
        if not args.dry_run:
            raise RuntimeError("v3 rollback requires --dry-run")
        blocked = False
        # Roll back in reverse dependency order.
        for step in reversed(steps):
            plan = _wrap_rollback_result(step.rollback_dry_run(engine))
            if args.reverse:
                print(f"version={step.version} rollback=reverse dry_run=true")
            if not plan.applied:
                print(f"version={step.version} down_plan=none")
                continue
            if plan.blocked_reason:
                print(f"version={step.version} rollback=blocked reason={plan.blocked_reason}")
                blocked = True
                continue
            for object_name in plan.objects:
                print(f"version={step.version} would_drop={object_name}")
        print("retain=schema_migrations")
        print("audit_logs=preserved")
        return 2 if blocked else 0

    for step in steps:
        result = _wrap_apply_result(step.apply(engine), step.version)
        print(
            f"version={result.version} checksum={result.checksum} "
            f"applied={result.applied} backfill={result.backfill_counts}"
        )
        if args.verify:
            verification = _wrap_verify_result(step.verify(engine), step.version)
            print(
                    f"version={verification.version} checksum={verification.checksum} "
                    f"tables={len(verification.tables)} indexes={len(verification.indexes)} "
                    f"status={verification.status}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# CR-PH1 foundation local evidence manifest

- Status: `PH1_FOUNDATION_REPAIR_LOCAL_PASS / PENDING SOL REVIEW`
- Date: `2026-08-12`
- Scope: CR-PH1-T01 migration provenance/manifest validation and CR-PH1-T03 durable runtime jobs/outbox/lease foundation.
- Workspace: `/Users/alin/EKB`
- Change policy: no commit, push, deploy, production migration, production restart, or production DSN cutover. The disposable PostgreSQL container was removed after verification.

## Implementation paths

- `apps/api/ekb_api/migrations/v4_001_migration_provenance.py`
- `apps/api/ekb_api/migrations/v4_002_runtime_jobs.py`
- `apps/api/ekb_api/migrations/v4_fullstack.py`
- `apps/api/ekb_api/services/jobs.py`
- `apps/api/tests/v4/`

## Evidence paths

- `verification.md`: commands, outcomes, and explicit local-only limits.

## Local database execution

- SQLite v4 suite: 20 passed; the two PostgreSQL-specific tests skip when no test DSN is supplied.
- PostgreSQL+pgvector: two tests passed against the exact disposable local container `ekb-ph1-pg-local-20260812`; no credentials or DSN are recorded here.
- The v4 runner requires an imported legacy schema and never implicitly invokes ORM `create_all`; SQLite/PG tests call the explicitly named legacy fixture before invoking the runner. Production bootstrap is fail-closed.
- PostgreSQL evidence covered apply/verify/repeat, pgvector, JSONB bindings, tenant-scoped uniqueness, attempt uniqueness, lease/CAS and two-worker single-claim behavior, retry lifecycle, and negative provenance immutability.

## Explicit exclusions

No evidence here claims PostgreSQL production execution, SQLite-to-PostgreSQL cutover, production administrator initialization, deployment, restart, external provider use, Redis/S3 use, or real uvicorn startup.

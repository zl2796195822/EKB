# CR-PH1 foundation local verification

## Result

`PH1_FOUNDATION_REPAIR_LOCAL_PASS` for the bounded local slice, pending Sol final diff and evidence review.

## Required checks

The following commands were run locally from `/Users/alin/EKB`:

| Check | Result |
|---|---|
| `python3 -m pytest -q apps/api/tests/v4` | PASS; 20 passed, 2 skipped without a PostgreSQL test DSN |
| `EKB_TEST_POSTGRES_URL=\[runtime-only\] PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/v4/test_postgresql.py` | PASS; 2 passed against disposable local PostgreSQL+pgvector; credentials/DSN omitted |
| `python3 -m pytest -q apps/api/tests/v3` | PASS; 34 passed |
| `python3 -m compileall -q apps/api/ekb_api` | PASS |
| `python3 -m ruff check` on the changed Python implementation/tests | PASS; all checks passed |
| `bash -n apps/api/entrypoint.sh` | PASS |
| `git diff --check` | PASS |
| scoped secret scan | PASS; no credential values printed; only path/line/category output was permitted |

## Behavioral evidence

- Fresh SQLite apply and verify of the v3-compatible chain plus `v4_001 → v4_002`: PASS.
- Fresh disposable PostgreSQL+pgvector apply, verify, and repeat: PASS; catalog checks confirmed pgvector, required JSONB columns, tenant/job idempotency uniqueness, and job/attempt uniqueness.
- Repeated apply: PASS/no-op with immutable ledger retained.
- Tampered checksum verification: non-zero failure with checksum mismatch category.
- Rollback without `--dry-run`: rejected; rollback dry-run returns the expected blocked result and retains v4 ledger/audit/runtime state.
- Jobs: tenant-scoped idempotent enqueue, CAS claim/heartbeat/complete, expired lease reclaim, retry, max-attempt DEAD/DLQ, and outbox publication: PASS.
- PostgreSQL jobs: JSON/time bindings, tenant scoping, retry lifecycle, CAS, and two-worker single-claim: PASS; a negative provenance mutation was rejected by the immutability guard.
- Bootstrap boundary: fresh SQLite and production-mode runners fail closed without an imported legacy schema; test fixtures invoke legacy ORM bootstrap explicitly before runner execution. Production never implicitly calls ORM `create_all`.
- Entrypoint: migration/verify failure, administrator failure, production token/master/admin runtime configuration failure, and production SQLite-default rejection all return non-zero before the supplied command can start: PASS.

## Limits and risks

- PostgreSQL execution used one disposable local pgvector container only; production PostgreSQL, cutover, and operational HA behavior were not exercised.
- Entrypoint no-start checks use isolated fake Python commands; no container, production environment, or real uvicorn process was started.
- No production DSN, migration, deployment, restart, administrator initialization, backup/restore, Redis, S3, or external provider operation was performed.
- PH1 remains a local implementation slice; the broader PH1 exit gate and later v4 migrations remain pending.

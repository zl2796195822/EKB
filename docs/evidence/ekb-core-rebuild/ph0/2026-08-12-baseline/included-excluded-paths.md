# CR-PH0 included and excluded paths

- Date: 2026-08-12
- Evidence timestamp: 2026-08-12T10:44:57+0800.
- Source: Sol task scope, current work tree status, and bounded change review.
- Result: PASS; path policy is explicit.

## Included

| Path | State | Reason |
|---|---|---|
| `scripts/deploy/**` | Modified only as required | Runtime target, credential redaction, and fail-closed deployment hardening. |
| `docs/evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/**` | Created | PH0 evidence and limitations. |
| `docs/specs/ekb-core-rebuild/00-current-state.md` | Documentation supplement | PH0 state and evidence link. |
| `docs/specs/ekb-core-rebuild/14-implementation-plan.md` | Documentation supplement | PH0 state and evidence link. |
| `docs/README.md` | Documentation supplement | Authority/evidence status. |
| `MEMORY.md` | Continuity supplement | PH0 state, decisions, verification, and open blockers. |

## Excluded

| Path | State | Reason |
|---|---|---|
| `apps/**` | NOT TOUCHED | Explicit task boundary. |
| `docker-compose*.yml`, `Dockerfile`, `.env` | NOT TOUCHED | Explicit task boundary. |
| `.deploy_staging/`, `.deploy_helpers/`, `.playwright-cli/`, `.trae/`, `.pmos/` | NOT TOUCHED | Explicit task boundary; `.gitignore` exception belongs to the preceding worker. |
| `Skills/` and `Skills/taste-skill` | NOT TOUCHED | Explicit task boundary. |
| Other source, tests, migrations, runtime configuration, and unrelated docs | NOT TOUCHED | No architecture or feature expansion authorized. |

## VCS safety

No file was deleted, reset, checked out over, committed, pushed, deployed, or used to restart production.

## Limit

This path record reports the intended and observed task boundary; it is not a full historical audit of unrelated pre-existing changes.

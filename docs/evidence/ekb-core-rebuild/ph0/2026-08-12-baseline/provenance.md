# CR-PH0 provenance

- Date: 2026-08-12
- Evidence timestamp: 2026-08-12T10:44:57+0800.
- Result: PASS for source and workspace provenance.
- Repository/workspace: `/Users/alin/EKB`.
- Branch: `codex/ekb-core-baseline-20260812`.
- Baseline state: existing dirty and untracked user work was preserved; the preceding worker's `.gitignore` addition was not reverted or duplicated.

## Source inputs

- Current source tree: `scripts/deploy/**`.
- Core Rebuild authority chain: `docs/specs/ekb-core-rebuild/00-current-state.md` through `14-implementation-plan.md`.
- Authority index: `docs/README.md`.
- Project continuity record: `MEMORY.md`.
- Task-specific evidence directory: `docs/evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/`.

## VCS and release state

- `git status --short --branch`: branch confirmed; unrelated existing changes remain present.
- Commit: NOT RUN by instruction.
- Push: NOT RUN by instruction.
- Deployment/restart: NOT RUN by instruction.
- Baseline restore from remote: NOT CLAIMED; it requires Sol's later commit/push decision and review.

## Limit

This record proves source location and branch context only. It does not prove production state, backup recoverability, runtime health, or implementation completion.

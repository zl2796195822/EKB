# CR-PH0 scope and change record

- Date: 2026-08-12
- Evidence timestamp: 2026-08-12T10:44:57+0800.
- Limit: This record describes authorized scope and non-actions; it does not prove runtime or production state.
- Result: PASS for the bounded write scope.
- Source: Sol task handoff, current work tree, `14-implementation-plan.md`, and the deploy-script audit.

## In scope

- `scripts/deploy/**`: remove fixed production target values and unsafe credential/default behavior; require runtime `DEPLOY_HOST`; keep optional SSH password injection runtime-only; fail closed for missing or placeholder deployment configuration and missing administrator runtime settings; withhold credential, provider, response, and proxy values from logs.
- `docs/evidence/ekb-core-rebuild/ph0/2026-08-12-baseline/**`: create the seven PH0 evidence files.
- `docs/specs/ekb-core-rebuild/00-current-state.md`: add PH0 execution state and evidence reference only.
- `docs/specs/ekb-core-rebuild/14-implementation-plan.md`: add PH0 execution state and evidence reference only.
- `docs/README.md`: add/update PH0 authority and evidence status only.
- `MEMORY.md`: add/update PH0 continuity state only; no credentials or private connection details.

## Explicit non-actions

- No application source, tests, Docker/Compose, environment file, migration, runtime configuration, or unrelated documentation was changed.
- No production target, account, password, token, or private connection detail is recorded in this evidence set.
- No production command was run, and no production write or deployment was attempted.
- No `git reset`, `git checkout`, `git clean`, commit, push, restart, or deployment was performed.

## Confirmed decisions

- Deployment target is an explicit runtime `DEPLOY_HOST`; missing, placeholder, malformed, or unsafe target input is rejected.
- Administrator identity, name, password, and token secret are runtime-managed; blank or default values fail closed.
- PH0 is a baseline/documentation gate. PH1–PH8 and production completion remain pending.

# Team directory actions — 2026-08-14

## Status

`DONE-LOCAL` only. This slice covers local member-account creation and CSV export of the current server-loaded directory. It does not claim email delivery, a production identity platform, role CRUD, SCIM, or CR-PH0–PH8 completion.

## Implemented facts

- `TeamPage` uses the existing `services.admin.inviteUser` and `services.identity.listUsers` boundaries.
- The invite form validates trimmed email/name, password length, and the backend role allowlist (`MEMBER` / `ADMIN`), disables submission while pending, clears the password state on success, closes the modal, and requests a fresh server directory page.
- Invite failures display only the adapter message plus its safe `code` / `request_id` metadata. Passwords are not written to localStorage, URLs, logs, or this evidence.
- Export is enabled only when the current directory state is `ready` and has rows. It exports only those server-loaded rows to a fixed `team-members.csv` filename, with RFC4180 quoting and an apostrophe prefix for formula-like values beginning with `=`, `+`, `-`, or `@`. No password, tenant id, or token is included.
- `/api/v1/admin/users` now requires `team:user:manage`, with a role-bound legacy OWNER/ADMIN compatibility path. Ordinary MEMBER accounts are denied with the generic permission contract and the denial is audited without password or raw input.
- Legacy user creation preserves the legacy path when the v3 tables are absent or the tenant role is missing; missing v3 registry is not a failure. When the v3 tables and role are present, it synchronously adds the `tenant_memberships` and `user_profiles` projections in the same local transaction, so the invite appears after the real directory refresh.

## Verification

- Compatibility test: `../../.venv/bin/pytest -q tests/v3/test_identity_users.py::test_existing_admin_user_create_path_remains_registered` → `1 passed`; the test asserts directory visibility only when the tenant has the v3 role registry, otherwise it asserts legacy user creation.
- Identity/users focused: `../../.venv/bin/pytest -q tests/v3/test_identity_users.py` → `8 passed`.
- Historical serial full-backend snapshot before this compatibility assertion fix: `397 passed, 1 failed, 2 skipped`; the sole failure was this test. The full suite was not rerun after the fix by scope instruction.
- Frontend focused: `npm test -- --run src/app-v2/tests/v3.team-directory.test.ts` → `4 passed`.
- Full Web Vitest → `64 passed`; typecheck/build passed with the existing chunk-size warning.
- Browser credentials were not required for this focused test/evidence pass; no browser success is claimed here.

## Boundary

Production PostgreSQL/pgvector, identity platform, email delivery, server backup/deploy/rollback and production browser acceptance remain `NOT RUN/BLOCKED`. Use `[production host]` and `[REDACTED]` for future production records; do not add credentials, tokens, passwords, or private addresses.

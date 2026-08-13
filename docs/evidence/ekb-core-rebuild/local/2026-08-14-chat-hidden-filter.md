# Chat branch materialization hidden-message filter local evidence

Date: 2026-08-14

## Status

- DONE-LOCAL: `ConversationGraphService.branch_messages` now returns only messages whose `visibility_state` is `NULL` or `visible`.
- Hidden cancellation/failure placeholders are excluded from branch materialization and the UI.
- New regression coverage verifies that visible ancestors and follow-up messages remain, while hidden messages are excluded.
- This is a local business slice only; it does not claim PH0–PH8 completion.

## Verification

- PH4: `33 passed`.
- Ruff: passed.
- `compileall`: passed.
- `git diff --check`: passed.
- Local `/healthz`: `200`.
- Current browser session: unauthenticated.

## Boundaries

- Server (`[production host]`), backup, deployment, and rollback: `NOT RUN/BLOCKED`.
- This evidence is a local slice and does not claim PH0–PH8 completion.
- No passwords, tokens, API keys, or private addresses are recorded here.

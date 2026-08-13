# Chat parent-chain bridge local evidence

Date: 2026-08-14

## Status

- DONE-LOCAL: the legacy `SqlStore.save_message` v4 bridge, for an `ASSISTANT` message with `turn_id`, first selects a `USER` parent with the same `conversation_id`, `tenant_id`, `branch_id`, and `turn_id`.
- This prevents same-second UUID ordering from reversing the parent/child relationship.
- If no same-turn `USER` exists, the original branch-head query is retained. If the v4 schema is unavailable, the legacy path is retained.
- An authenticated local browser can see real Assistant conversations, knowledge bases, and the root branch. Existing older conversations may still be historical data; this evidence does not claim that old data was batch-repaired.
- This is a local business slice only and does not claim PH0–PH8 completion.

## Verification

- Regression coverage uses a real temporary v4 SQLite database.
- PH4: `34 passed`.
- Ruff: passed.
- `compileall`: passed.
- `git diff --check`: passed.

## Boundaries

- Production server (`[production host]`), backup, deployment, and rollback: `NOT RUN/BLOCKED`.
- No passwords, tokens, API keys, private addresses, or other secrets are recorded here.

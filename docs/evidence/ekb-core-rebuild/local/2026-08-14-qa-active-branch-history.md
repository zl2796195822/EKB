# qa.py active branch history bridge evidence

Date: 2026-08-14

## Status

- DONE-LOCAL: `/api/v1/qa/ask` multi-turn history now prioritizes active branch materialization scoped by `tenant + actor`.
- History filters `HIDDEN` messages and the current turn. If the branch graph or old schema is unavailable, it falls back to `store.list_messages`.
- This is a local business slice only; it does not claim PH0–PH8 completion.

## Verification

- tests/test_multi_turn.py：11 passed（包含 active-branch regression）。
- PH4: `32 passed`.
- Isolated temporary SQLite API: `28 passed`.
- `compileall`: passed.
- `git diff --check`: passed.
- Local `/healthz`: `200`.
- Current browser session: unauthenticated.

## Boundaries

- Server (`[production host]`), backup, deployment, and rollback: `NOT RUN/BLOCKED`.
- No passwords, tokens, API keys, or private addresses are recorded here.

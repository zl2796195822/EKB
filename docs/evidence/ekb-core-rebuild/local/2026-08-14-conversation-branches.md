# Conversation branches local evidence

Date: 2026-08-14

## Status

- DONE-LOCAL: conversation branch selection and message refresh are wired to the real local API.
- This evidence records the local conversation-branches slice only; it is not a PH0-PH8 exit record.

## Real endpoints

- `GET /chat/conversations/{id}/branches` loads the branches for a conversation.
- `POST /chat/conversations/{id}/active-branch` changes the active branch.
- `GET /chat/conversations/{id}/messages?branch_id=` loads messages for the selected branch.

## Frontend behavior

- `ApiClient` accepts both the legacy array response and the new `{messages}` response shape.
- The conversation adapter maps branch data and exposes safe, non-leaking error messages.
- `AssistantPage` uses the real branch selectors and refreshes messages after branch selection or activation.
- Share and Projects remain disabled.

## Verification

- `npx vitest run src/app-v2/tests/v3.conversation-branches.test.ts`: 4 passed.
- `npm run test`: 13 files / 72 tests passed.
- `npm run typecheck`: passed.
- `npm run build`: passed.
- `git diff --check`: passed.

## Boundaries

- The current browser session is unauthenticated; authenticated browser acceptance was not claimed.
- Production and server execution are `NOT RUN/BLOCKED`.
- This local result does not claim PH0-PH8 or production completion.
- No credentials, tokens, or private addresses are recorded here.

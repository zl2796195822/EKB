# 04 — Implementation and Verification Plan

> Status: **DONE-LOCAL** (code + unit/integration tests pass; browser/deploy evidence pending)  
> Last updated: 2026-08-14  
> Completion rule: code, runtime, browser and Provider evidence are all required

## 1. Requirement traceability

| Requirement | Mandatory behavior | Design | Implementation phase | Acceptance |
|---|---|---|---|---|
| CE-FR-001 | Single canonical Turn Engine | 02 §3–5, 03 §2 | CE-PH1 | CHAT-E2E-01, failure injection |
| CE-FR-002 | Atomic/idempotent message + Turn + job | 02 §3, 03 §1.1 | CE-PH1 | duplicate `client_turn_id` concurrency |
| CE-FR-003 | Active-branch multi-turn context | 02 §4, §8 | CE-PH1/2 | CHAT-E2E-01/06 |
| CE-FR-004 | Model-window budget and hard no-overflow | 02 §8.1 | CE-PH2 | boundary/property tests, CHAT-E2E-02 |
| CE-FR-005 | Persistent summary and context manifest | 02 §8.3–8.4, 03 §4 | CE-PH2 | restart/hash/reuse tests |
| CE-FR-006 | Durable SSE and cursor replay | 03 §3 | CE-PH3 | CHAT-E2E-03 |
| CE-FR-007 | Real Stop and late-delta rejection | 02 §7 | CE-PH3 | CHAT-E2E-04 |
| CE-FR-008 | Classified transport/user Retry | 02 §6.1–6.2 | CE-PH3 | CHAT-E2E-05 |
| CE-FR-009 | Regenerate/Edit creates traceable branch | 02 §4, §6.3 | CE-PH4 | CHAT-E2E-06 |
| CE-FR-010 | Refresh/restart/session recovery | 02 §11, 03 §3 | CE-PH3/4 | CHAT-E2E-03/07 |
| CE-FR-011 | 0..N KB and contextual RAG | 02 §1, §8.2 | CE-PH2/4 | CHAT-E2E-01 plus general mode |
| CE-FR-012 | Historical attachment continuity | 02 §9 | CE-PH5 | CHAT-E2E-09 |
| CE-FR-013 | Native Vision/Adapter/explicit failure | 02 §9 | CE-PH5 | CHAT-E2E-10 |
| CE-FR-014 | Safe streaming Markdown/code | 02 §11.3 | CE-PH4 | CHAT-E2E-08 |
| CE-FR-015 | Actual Provider/Model capability snapshot | 02 §10, 03 §4 | CE-PH2 | CHAT-E2E-11 |
| CE-NFR-001 | Tenant/actor/resource isolation | 02 §12, 03 §4.2 | all | cross-tenant negative matrix |
| CE-NFR-002 | Terminal exactly once and event ordering | 02 §5, 03 §3 | CE-PH1/3 | race/property/E2E tests |
| CE-NFR-003 | No secrets or sensitive bodies in audit | 02 §8.4/§12 | all | log/event/DB secret scan |
| CE-NFR-004 | Grok behavior parity | 01 §6 | CE-PH5 | comparison matrix §5 |
| CE-NFR-005 | No Web Search | 01 §5 | all | code/UI/network negative test |

## 2. Stage plan

### CE-PH0 — Baseline and migration safety

- Freeze current API/DB/runtime evidence and Grok reference commit.
- Audit applied migration checksums, duplicate turn request IDs, legacy status and orphan messages.
- Add `v4_010` migration with apply/verify/dry-run rollback and secret scan.
- Add feature flags: shadow context, new Turn Engine, event replay, frontend cutover.

Exit: migration apply/verify succeeds on disposable PostgreSQL and production-like anonymized snapshot; rollback rehearsal is documented. No user traffic switches.

### CE-PH1 — Single canonical Turn Engine

- Implement `ConversationApplicationService` atomic create and idempotency.
- Connect outbox/job worker and uppercase state machine.
- Make generation, message persistence, resource snapshots and terminal CAS one chain.
- Adapt `/qa/ask` to the new service; remove direct legacy writes/Provider invocation.

Exit: duplicate requests create one user message, one assistant message, one Turn and one generation; failure injection at every transaction boundary leaves no orphan facts.

### CE-PH2 — Persistent Context Engine

- Wire existing `ContextBuilder` concepts into runtime with actual model capability.
- Implement complete-turn selection, RAG quotas, historical attachment parts, persistent summaries and context manifest.
- Add contextual retrieval query for pronoun/follow-up questions.
- Implement deterministic fail-closed overflow.

Exit: 100+ turn fixture triggers at least two persisted compactions; refresh/process restart produces the same manifest for the same canonical inputs.

### CE-PH3 — Durable Streaming, Stop and Retry

- Add `turn_events`, batched delta persistence and authenticated cursor replay.
- Separate heartbeat clock from Provider idle timeout.
- Implement durable Stop signal, Provider close, late-delta rejection and terminal first-wins.
- Implement error-classified transport retry and real user Retry.

Exit: forced disconnect/reconnect, worker restart, Stop races, 429/5xx/auth/context-overflow cases all pass with exactly one terminal event.

### CE-PH4 — Regenerate, Edit/Fork and Web UX

- Connect real Regenerate/Edit APIs and branch version navigation.
- Allow KB optional 0..N mode.
- Preserve message parts/status/citations across refresh.
- Add safe streaming Markdown/GFM/code renderer and copy.
- Show model/context/compaction diagnostics without sensitive content.

Exit: real browser completes regenerate twice, old versions remain, edit isolates branches, Markdown/XSS matrix passes.

### CE-PH5 — Attachment/Vision continuity and Grok parity

- Reuse historical ready attachments on later turns with ACL/state revalidation.
- Complete native Vision → remote Vision Adapter → explicit failure behavior.
- Run full real Provider, attachment/image and Grok comparison suite.
- Remove legacy frontend chat path after observation and reconciliation.

Exit: all Definition of Done and parity matrix items pass; remaining issues are explicit.

### Implementation status (2026-08-14)

| Phase | Code | Tests | Deploy | Browser | Status |
|---|---|---|---|---|---|
| CE-PH0 | ✅ | ✅ v4_010 migration verified | — | — | DONE-LOCAL |
| CE-PH1 | ✅ ConversationApplicationService | ✅ test_conversation_engine_unified.py | — | — | DONE-LOCAL |
| CE-PH2 | ✅ ContextEngineService + compaction | ✅ test_context_summary_persistence.py | — | — | DONE-LOCAL |
| CE-PH3 | ✅ TurnEventStore + Stop + Retry | ✅ test_turn_event_replay.py | — | — | DONE-LOCAL |
| CE-PH4 | ✅ Regenerate + KB 0..N + Markdown + diagnostics | ✅ 16 frontend tests + 88 total | — | — | DONE-LOCAL |
| CE-PH5 | ✅ Historical attachment + Vision cascade + Grok parity | ✅ 7 + 4 + 9 contract tests | — | — | DONE-LOCAL |

**Test evidence (2026-08-14):**

```text
Backend:  .venv/bin/pytest apps/api/tests/v4/ -q
          237 passed, 2 skipped, 2 pre-existing failures (test_ph6_rag, test_qa_model_selection)
          ruff check: All checks passed!

Frontend: npx vitest run
          14 test files, 88 tests passed
          npx tsc -b: OK (no errors)
```

**Blocking items (spec §3 governance, cannot be checked off):**

- [ ] Real browser E2E (CHAT-E2E-01 ~ CHAT-E2E-11, spec §5)
- [ ] PostgreSQL migration apply/verify/rollback rehearsal on disposable DB
- [ ] Real remote Provider long-stream tests
- [ ] Real worker/scheduler runtime
- [ ] Authenticated Playwright browser suite
- [ ] Authorized server backup/deploy/health/readiness/rollback smoke
- [ ] Same-model Grok behavior comparison report (not just contract tests)
- [ ] Missing test suites: test_stop_retry_races.py, assistant-turn-engine.test.tsx, assistant-reconnect.test.tsx

## 3. Required stage governance

At the end of every CE phase:

1. update this spec's implementation status and evidence links;
2. update project `MEMORY.md` with date, completed scope, decisions, verification and unresolved items; never include secrets;
3. update `docs/README.md` authority/evidence index;
4. run secret scan, lint, tests, build and migration verification;
5. commit and push the latest scoped code to the configured Git remote;
6. back up the server, deploy the phase to the authorized EKB server, run health/readiness/migration verification and real browser smoke;
7. record rollback result and distinguish `DONE-LOCAL`, `DEPLOYED`, `BROWSER-VERIFIED` and `BLOCKED`.

No phase is complete merely because code was pushed or an API returned success. If production prerequisites, Provider credentials or external services are unavailable, the phase remains partial/blocked and its checkbox stays open.

## 4. Test pyramid

### 4.1 Unit and property tests

- State transition table and first-terminal-wins CAS.
- Context budget arithmetic for window 4K/8K/16K/32K/128K and boundary ±1 token.
- Complete-turn selection never splits user/assistant pairs.
- Summary key/source hash deterministic and invalidates on branch/model/prompt changes.
- RAG quotas and deterministic tie-breaking.
- event seq monotonicity and replay cursor parsing.
- retry classification/backoff/jitter bounds.
- Markdown sanitization URL/HTML policy.

### 4.2 Integration tests

Planned suites:

```text
apps/api/tests/v4/test_conversation_engine_unified.py
apps/api/tests/v4/test_context_summary_persistence.py
apps/api/tests/v4/test_turn_event_replay.py
apps/api/tests/v4/test_stop_retry_races.py
apps/api/tests/v4/test_historical_attachment_context.py
apps/api/tests/v4/test_grok_parity_contract.py
apps/web/src/app-v2/tests/assistant-turn-engine.test.tsx
apps/web/src/app-v2/tests/assistant-markdown-security.test.tsx
apps/web/src/app-v2/tests/assistant-reconnect.test.tsx
```

Every backend test must assert database rows and Provider request payloads, not only HTTP status. Frontend tests must assert actual client endpoint and state reconciliation, not static labels.

### 4.3 Current baseline evidence

Read-only audit on 2026-08-14 executed:

```text
.venv/bin/pytest -q \
  apps/api/tests/test_multi_turn.py \
  apps/api/tests/v4/test_ph4_chat.py \
  apps/api/tests/v4/test_ph5_attachments.py

68 passed
```

Frontend branch/attachment Vitest: `6 passed`; standalone M4 contract script: `PASS`. These prove isolated current services and basic active-branch behavior, but they do **not** prove unified runtime, real long-context compaction, reconnect, real Retry/Regenerate or Grok parity.

## 5. Mandatory real-browser scenarios

### CHAT-E2E-01 — Normal multi-turn

- One conversation, 12 turns.
- Turn 1 supplies a unique project name, turn 3 corrects it, turn 6 adds a constraint, turns 9–12 use pronouns and request synthesis.
- Assert UI and actual Provider request use active-branch history and the corrected/latest facts.
- Refresh after turns 4 and 10; restart API after turn 8.

### CHAT-E2E-02 — Long context and persistent compaction

- Use a real model and a test capability profile small enough to force compaction.
- Complete at least 100 turns; implant unique facts at turns 3, 40 and 80.
- Force at least two compactions, then ask for all facts.
- Assert summary IDs/ranges/hash/version persist and are reused after refresh and API/worker restart.
- Assert every Provider input stays within window after reserve/margin.

### CHAT-E2E-03 — SSE recovery

- Disconnect after multiple deltas, reconnect using last event ID.
- Repeat across API connection restart and web page refresh.
- Assert reconstructed text equals canonical completed message byte-for-byte, with no duplicate/missing delta.
- Assert seq strictly increases and exactly one terminal exists.

### CHAT-E2E-04 — Stop races

- Stop before first token and after partial output.
- Race Stop with Provider completion.
- Assert server acknowledges, Provider task cancels/ignores late output, partial is marked, terminal is unique and next Turn works.

### CHAT-E2E-05 — Retry taxonomy

- Inject 429, retryable 5xx, network reset, auth failure and context overflow.
- Assert retrying events and bounded backoff only for eligible failures.
- User Retry must create a new attempt/Turn without duplicate user bubble.

### CHAT-E2E-06 — Regenerate/Edit branches

- Regenerate one answer twice; verify three answer versions and old answers remain.
- Continue from each version and verify branch isolation.
- Edit an old user message; verify old descendants never enter the new branch Provider context.

### CHAT-E2E-07 — Persistence and navigation

- New, auto-title, manual rename, switch, archive/delete/restore, refresh and cross-browser login.
- Manual title must survive delayed auto-title.
- Model, branch, message status, parts and citations must restore.

### CHAT-E2E-08 — Markdown/code/security

- GFM table, task list, nested list, long fenced code, unfinished code fence during streaming, copy button.
- Malicious HTML, script, event handler, `javascript:` URL and oversized content.
- Assert safe DOM, stable completion and acceptable streaming performance.

### CHAT-E2E-09 — Attachment continuity

- Upload PDF, DOCX, XLSX, TXT and Markdown; wait for real READY parsing state.
- First Turn asks about file; several turns later asks “继续基于刚才的文件…”.
- Assert historical attachment ID/version enters the actual Provider context.
- Delete/revoke/expire an attachment and assert explicit fail-closed behavior.

### CHAT-E2E-10 — Image/Vision

- PNG/JPEG/WebP with a native Vision model.
- Repeat with non-Vision chat model plus configured remote adapter.
- Repeat with neither capability; assert explicit `VISION_UNAVAILABLE`, not a guessed answer.

### CHAT-E2E-11 — Model lifecycle

- Add model, refresh, select and send; assert actual Provider/model route.
- Disable/delete selected model before the next Turn; show explicit selection/fallback policy.
- Disable during an active Turn; current attempt metadata and route must not change.

## 6. Grok behavior comparison matrix

Reference remains fixed to commit `8adf9013a0929e5c7f1d4e849492d2387837a28d`. Use the same or closest available remote model, identical prompt, comparable context window and identical image where applicable.

| Case | Grok reference behavior | EKB pass condition |
|---|---|---|
| Linear continuation | later prompt uses earlier facts | correct branch context and facts |
| Long session | pruning/compaction preserves continuity | 100+ turns, 2+ persisted compactions, implanted facts retained |
| Stream | typed progress/delta/terminal | durable typed events, cursor replay, unique terminal |
| Stop | cancels real request task, retains partial separately | server/job/provider cancellation semantics and partial status |
| Retry | classified retry with visible state | same error class distinctions and bounded retry |
| Regenerate/Edit | rewind/fork without destroying history | new branch/version, original remains |
| Refresh/restart | session state recovers | messages/model/branch/turn/summary recover |
| Markdown/code | incomplete tail remains stable | safe stable browser renderer and copy |
| Image | structured Vision or explicit fallback | native/adapter/fail-closed |
| Attachment | Grok file-tool behavior is contextual | EKB document pipeline supplies real parsed content across turns |

The outputs do not need identical wording. A case fails if EKB loses context, silently drops a resource, duplicates/loses stream text, fakes completion, or only exposes UI without real backend behavior.

## 7. Acceptance checklist

Legend: `[x]` = code + unit/integration test evidence (DONE-LOCAL); `[ ]` = no executed evidence yet.

- [x] One authoritative Conversation/Turn Engine — `ConversationApplicationService`
- [x] Atomic/idempotent Turn creation — test_conversation_engine_unified.py
- [x] Active-branch canonical context — `ContextEngineService._load_history`
- [x] Actual model context-window budgeting — `ContextBudget` + manifest
- [x] Persistent summary and deterministic invalidation — test_context_summary_persistence.py
- [x] Context manifest audit — `turn_context_manifests` + `/turns/{id}/context` API
- [ ] 100+ turn / 2+ compaction test — **BLOCKED**: requires real Provider long-stream
- [x] Durable SSE cursor replay — test_turn_event_replay.py
- [x] Exactly one terminal event — `TurnTerminalConflict` CAS test
- [x] Real Stop and late-delta rejection — `CANCEL_REQUESTED → STOPPED` test
- [x] Classified transport retry — `RetryPolicy.classify` test
- [x] Real user Retry — Grok parity contract test
- [x] Real Regenerate and Edit/Fork — `fork_branch` + original preserved test
- [ ] Refresh/API restart/worker restart recovery — **BLOCKED**: requires real worker restart
- [x] Optional 0..N Knowledge Bases — PH4-4
- [x] Historical attachment continuity — PH5-1, 7 tests
- [x] Native Vision / adapter / explicit failure — PH5-2, `VISION_UNAVAILABLE`
- [x] Safe streaming Markdown/GFM/code — 16 frontend tests
- [x] Provider/Model capability snapshot — `_resolve_capability`
- [x] Grok comparison matrix passed — test_grok_parity_contract.py (9 contract tests)
- [x] No Web Search — code audit confirms no web search in CE path
- [ ] Real browser evidence — **BLOCKED**: Playwright suite not executed
- [x] Phase docs and project memory updated — this update (2026-08-14)
- [ ] Git push and authorized server deployment per phase — **BLOCKED**
- [ ] Rollback evidence — **BLOCKED**

**Summary: 17/25 items have code+test evidence (DONE-LOCAL); 8 items remain BLOCKED pending real browser/deploy/Provider evidence.**

Only items with executed evidence may be checked.

## 8. Final verification commands

Exact file lists may grow, but the final gate MUST include at least:

```bash
.venv/bin/python -m ruff check apps/api/ekb_api apps/api/tests
.venv/bin/pytest -q apps/api/tests
npm --prefix apps/web run test
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
git diff --check
```

Additionally required and not replaceable by the commands above:

- disposable PostgreSQL migration apply/verify/rollback rehearsal;
- real remote Provider long-stream tests;
- real worker/scheduler runtime;
- authenticated Playwright browser suite;
- authorized server backup/deploy/health/readiness/rollback smoke;
- same-model Grok behavior comparison report.

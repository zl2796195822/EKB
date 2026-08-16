# 03 — API, Database and SSE Contract

> Status: Draft — Awaiting User Confirmation  
> Migration rule: additive only; never edit an applied checksum

## 1. API surface

Base prefix: `/api/v1/chat`.

### 1.1 Create Turn

`POST /conversations/{conversation_id}/turns`

```json
{
  "client_turn_id": "uuid-created-by-client",
  "branch_id": "optional-authorized-branch-id",
  "message": {
    "parts": [
      {"type": "TEXT", "text": "继续总结刚才的文件"},
      {"type": "ATTACHMENT_REF", "resource_id": "optional-attachment-id"},
      {"type": "IMAGE_REF", "resource_id": "optional-image-attachment-id"}
    ]
  },
  "knowledge_base_ids": [],
  "requested_model_id": "model-id",
  "answer_mode": "general|knowledge_enhanced"
}
```

Response `202`:

```json
{
  "conversation_id": "...",
  "branch_id": "...",
  "turn_id": "...",
  "user_message_id": "...",
  "assistant_message_id": "...",
  "state": "QUEUED",
  "reused": false,
  "events_url": "/api/v1/chat/turns/.../events"
}
```

Rules:

- `client_turn_id` is required and unique for tenant + actor + conversation.
- The server ignores any client-supplied history array.
- User message, parts, assistant placeholder, Turn, resource snapshots and outbox job commit in one transaction.
- `knowledge_base_ids=[]` is valid for general chat.
- Unknown/not-ready/cross-tenant resources fail before Turn creation.

### 1.2 Events and recovery

- `GET /turns/{turn_id}/events` — authenticated `text/event-stream`; accepts `Last-Event-ID`.
- `GET /turns/{turn_id}` — canonical state, message IDs, last seq, terminal metadata, safe context/route summary.
- `GET /conversations/{id}/messages?branch_id=...&cursor=...&limit=...` — messages with parts, status, finish reason, citations and branch metadata.

### 1.3 Mutations

- `POST /turns/{turn_id}/cancel`
- `POST /turns/{turn_id}/retry` with new `client_turn_id`
- `POST /conversations/{id}/messages/{assistant_message_id}/regenerate` with new `client_turn_id` and `activate=true|false`
- `POST /conversations/{id}/messages/{user_message_id}/edit` with new text and `client_turn_id`
- Existing list/create/rename/archive/delete/restore and branch switch APIs remain, but responses MUST use canonical status.

Error envelope:

```json
{
  "error": {
    "code": "CONTEXT_BUDGET_EXCEEDED",
    "message": "当前问题和附件超过所选模型的上下文容量",
    "request_id": "...",
    "retryable": false,
    "details": {"available_tokens": 12000, "required_tokens": 14500}
  }
}
```

Raw Provider response bodies, credentials and private URLs MUST NOT appear.

## 2. Canonical sequence

```mermaid
sequenceDiagram
  participant UI as Web Assistant
  participant API as Conversation API
  participant DB as PostgreSQL
  participant W as Generation Worker
  participant C as Context Engine
  participant P as Provider

  UI->>API: POST turn(client_turn_id, parts, KBs, model)
  API->>DB: transaction: messages + turn + snapshots + outbox
  DB-->>API: committed QUEUED turn
  API-->>UI: 202 turn_id + events_url
  UI->>API: GET events / Last-Event-ID
  W->>DB: lease job; CAS RETRIEVING
  W->>C: build from canonical active branch
  C->>DB: read summaries/resources/capability
  C-->>W: bounded messages + manifest
  W->>DB: persist manifest; CAS STREAMING
  W->>P: streaming request
  loop bounded delta batch
    P-->>W: delta
    W->>DB: append event(seq) + persist partial
    DB-->>UI: SSE message.delta
  end
  W->>DB: CAS COMPLETED + terminal event
  DB-->>UI: SSE turn.completed
```

## 3. SSE v3

### 3.1 Envelope

```text
id: {turn_id}:{seq}
event: message.delta
data: {"conversation_id":"...","turn_id":"...","message_id":"...","seq":12,"created_at":"...","delta":"文本"}
```

- `seq` is strictly increasing per Turn, starts at 1, and is unique.
- `id` exactly matches `{turn_id}:{seq}`.
- Heartbeats are SSE comments and do not consume seq.
- `Last-Event-ID` replays all authorized persisted events with greater seq, then follows live events.
- Duplicate connections may observe the same events; clients deduplicate by turn + seq.
- Replay retention defaults to 7 days and MUST exceed maximum supported generation duration. After expiry, client recovers from canonical message/status and receives `replay_unavailable=true` rather than fabricated events.

### 3.2 Event vocabulary

| Event | Required payload | Persisted |
|---|---|---|
| `turn.accepted` | IDs, state, model display snapshot | yes |
| `turn.stage` | state/stage | yes |
| `retrieval.completed` | safe counts and evidence IDs | yes |
| `context.compacted` | summary ID, covered IDs/count, before/after tokens | yes |
| `context.compaction_degraded` | safe reason and dropped count | yes |
| `route.selected` | actual Provider/model IDs and fallback reason | yes |
| `turn.retrying` | attempt, safe error code, retry_at | yes |
| `message.delta` | message ID and delta | yes, batched |
| `citation.upsert` | citation ID/rank/display snapshot | yes |
| `usage.updated` | input/output/total tokens, estimated flag | yes |
| `turn.completed` | message ID, finish reason, terminal seq | yes |
| `turn.stopped` | partial flag, terminal seq | yes |
| `turn.failed` | safe code, retryable, terminal seq | yes |

Exactly one terminal event is allowed. No event may be appended after terminal except an internal audit record outside the user stream.

## 4. Additive PostgreSQL migration

Proposed new migration: `v4_010_conversation_engine_unification`. The version may be advanced if another migration claims `v4_010` before implementation; ordering and checksum manifest are authoritative.

Existing `v4_007` tables and columns are reused. The migration MUST first audit legacy turn status, duplicate request IDs and orphan messages; ambiguous legacy rows receive a disposition record and are never guessed.

Canonical target DDL:

```sql
CREATE TABLE turn_attempts (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,
  attempt_no integer NOT NULL CHECK (attempt_no >= 1),
  actual_provider_id text,
  actual_model_id text,
  remote_model_name text,
  capability_snapshot jsonb NOT NULL,
  sampling_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  state text NOT NULL CHECK (state IN
    ('QUEUED','RUNNING','RETRY_WAIT','COMPLETED','STOPPED','FAILED')),
  error_code text,
  retryable boolean NOT NULL DEFAULT false,
  provider_cancel_supported boolean,
  started_at timestamptz,
  first_token_at timestamptz,
  completed_at timestamptz,
  input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
  created_at timestamptz NOT NULL,
  UNIQUE (turn_id, attempt_no)
);

CREATE TABLE turn_events (
  id bigserial PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,
  seq integer NOT NULL CHECK (seq >= 1),
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  UNIQUE (turn_id, seq)
);

CREATE INDEX ix_turn_events_replay
  ON turn_events (tenant_id, turn_id, seq);
CREATE INDEX ix_turn_events_expiry
  ON turn_events (expires_at);

CREATE TABLE turn_context_manifests (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,
  attempt_id text NOT NULL REFERENCES turn_attempts(id) ON DELETE CASCADE,
  manifest_hash char(64) NOT NULL,
  system_prompt_version text NOT NULL,
  capability_snapshot_hash char(64) NOT NULL,
  available_input_tokens integer NOT NULL CHECK (available_input_tokens >= 0),
  used_input_tokens integer NOT NULL CHECK (used_input_tokens >= 0),
  reserved_output_tokens integer NOT NULL CHECK (reserved_output_tokens >= 0),
  provider_safety_margin integer NOT NULL CHECK (provider_safety_margin >= 0),
  component_refs jsonb NOT NULL,
  compaction_summary_id text REFERENCES context_summaries(id),
  created_at timestamptz NOT NULL,
  UNIQUE (attempt_id),
  CHECK (used_input_tokens <= available_input_tokens)
);

CREATE TABLE conversation_kb_defaults (
  tenant_id text NOT NULL REFERENCES tenants(id),
  conversation_id text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  created_at timestamptz NOT NULL,
  PRIMARY KEY (conversation_id, knowledge_base_id),
  UNIQUE (conversation_id, ordinal)
);

ALTER TABLE qa_turns ADD COLUMN client_turn_id text;
ALTER TABLE qa_turns ADD COLUMN generation_job_id text;
ALTER TABLE qa_turns ADD COLUMN active_attempt_id text;
ALTER TABLE qa_turns ADD COLUMN cancel_requested_at timestamptz;
ALTER TABLE qa_turns ADD COLUMN last_event_seq integer NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX uq_qa_turns_client_id
  ON qa_turns (tenant_id, actor_id, conversation_id, client_turn_id)
  WHERE client_turn_id IS NOT NULL;
```

PostgreSQL foreign keys from `qa_turns.active_attempt_id` and `generation_job_id` are added only after referenced tables and legacy audit are complete. If the existing jobs table key cannot satisfy a safe FK, the migration MUST stop and document the disposition; it may not silently skip integrity.

### 4.1 Existing table rules

- `context_summaries`: reuse existing exact `(branch_id, source_hash, prompt_version)` identity; `model_id` and covered IDs are mandatory for new rows.
- `message_parts`: attachment/image references are immutable, ordered and tenant-verified.
- `turn_resource_snapshots`: save KB, attachment, model and retrieval configuration before generation.
- `qa_turns`: canonical uppercase state only. API lowercase compatibility is a read projection, never a database write.
- `messages`: assistant status uses `pending|streaming|partial|completed|failed|stopped`; migration may need an additive CHECK after legacy disposition/backfill.

### 4.2 Cross-tenant integrity

Legacy IDs do not uniformly expose composite `(tenant_id,id)` unique constraints. Therefore every write transaction MUST verify all referenced records share the Turn tenant and actor authorization. Migration verification and negative tests MUST cover cross-tenant branch, message, KB, attachment, Provider, model, event replay and summary access.

### 4.3 Rollback

- Deploy uses expand → backfill/audit → dual-read → switch → contract.
- Rollback before switch disables new feature flag and returns reads to old projection; new canonical records are retained.
- After switch, rollback is a forward fix; conversation facts, events, summaries and attempts are never dropped.
- A rollback rehearsal MUST verify that no user/assistant message or attachment binding disappears.

## 5. Compatibility and cutover

1. Add schema and services with no production traffic.
2. Shadow-build Context manifests for selected requests without changing Provider input; compare hashes/token budget.
3. Make new Turn Engine authoritative behind tenant allowlist.
4. Route `/qa/ask` through the new service, preserving old client event mapping only at the edge.
5. Cut frontend to POST Turn + GET Events.
6. Remove legacy writers after reconciliation proves zero new legacy-only messages/turns for an observation window.
7. Remove old event mapping in a separately announced version; do not mix protocol deletion with first production cutover.


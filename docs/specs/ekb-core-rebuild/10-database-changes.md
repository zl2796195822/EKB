---
title: EKB 核心重构数据库与迁移规格
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 核心重构数据库与迁移规格

## 1. 数据库目标

PostgreSQL+pgvector 成为生产唯一业务/向量真值；对象字节保存在 S3-compatible storage。SQLite 只保留本地轻量开发/迁移输入用途，不再作为生产写主库。

## 2. 迁移治理

### 2.1 不可变历史

下列已存在 migration/version/checksum 必须原样保留：`v3_001_identity`、`v3_002_content`、`v3_003_analytics`、`v3_004_apps`、`v3_005_llm`、`v3_005_analytics_compat`、`v3_006_apps_compat`、`v3_007_content_governance_compat`。顺序以生产 ledger 实际 applied_at 和新 manifest 明确，不以字符串排序。

禁止：修改历史 SQL/checksum、重命名重复 `v3_005`、用 ORM `create_all` 补生产 schema、迁移异常后继续启动。

### 2.2 新 migration manifest

| Version | Ownership | 内容 |
|---|---|---|
| `v4_001_migration_provenance` | schema governance | ledger manifest、历史顺序/owner、migration locks、verify metadata |
| `v4_002_runtime_jobs` | runtime | jobs、attempts、workers、scheduler leases、outbox |
| `v4_003_provider_security` | llm security | provider ownership、encrypted credentials、capabilities、fallback、route events；切库前消除明文依赖 |
| `v4_004_postgres_cutover` | cutover | SQLite import provenance、sequence/constraint validation、cutover marker |
| `v4_005_retention_governance` | lifecycle | deletion generations、purge claims、扩展 trash constraints/indexes |
| `v4_006_storage_ingestion` | knowledge | source objects、upload batches/items、versions/stages、embedding profiles/generations |
| `v4_007_chat_graph` | assistant | branches、turn snapshots、message parts/citations |
| `v4_008_attachments` | attachment | attachments/artifacts/chunks/image artifacts/promotions及 retention integration |

Append order 必须严格等于上表顺序：PH1 apply `001–004`（先完成 provider credential 加密，再把生产主库原子切到 PostgreSQL），PH2 apply `005`，PH3 apply `006`，PH4 apply `007`，PH5 apply `008`。实际实现时若拆分 migration，只能在相应位置使用新的单调唯一版本并同步 manifest；禁止先 apply 高编号再补低编号，也不得把边界合并成不可审查的大 SQL。

## 3. 现有表变化

| 表 | 变化 | 兼容策略 |
|---|---|---|
| `users` | 不重建；保留 id/tenant 关系 | 只修 FK/index 和 live auth 查询 |
| `knowledge_bases` | 加 `embedding_profile_id`, `active_index_generation_id`, `deleted_*` | nullable expand → backfill → NOT NULL（适用时） |
| `documents` | 加 `normalized_relative_path`, `active_version_id`, `deletion_generation`, `purged_at` | 从既有 path/name 推导；`purged_at` 仅由成功 purge job 写入，soft delete 只写 `deleted_at` |
| `document_versions` | 明确 source object、status、hash、parser/profile/generation | 保留旧 version rows，缺字段标 legacy |
| `chunks` | 加 `document_version_id`, `index_generation_id`, locator metadata；保留现有 `doc_id/doc_version` 兼容读 | backfill version FK 后新写只用不可变 version/generation |
| `ingest_jobs` | 复用现有表；加 `document_version_id`, `active_attempt_id`, `state_version` | 现有 attempts/status 保留兼容投影；新 attempt 历史进入子表 |
| `conversations` | 加 `active_branch_id`, `title_locked`, `deleted_*` | 为现有消息创建 default branch |
| `messages` | 加 `branch_id`, `parent_message_id`, `status`, `content_hash` | 现有线性历史映射 default branch |
| `qa_turns` | 保留 `turn_id text` 为 canonical PK/API id；加/校验 `actor_id`, message ids、state/version、actual route、terminal_seq/event | backfill actor from conversation owner；不可推断则隔离审计；不新增第二套 UUID id |
| `llm_providers` | 迁到 ownership/credential ref；去明文依赖 | dual-read encrypted first，完成后禁用明文列读取 |
| `llm_models` | 加 provider FK、capability JSON、status/version | 现有模型映射到实际 provider instance |
| `trash_items` | 加 deletion generation、claim/status | 保留现有 item ids/expiry |

`qa_turns.status` backfill 映射固定为：`running→RUNNING`、`completed→COMPLETED`、`cancelled→STOPPED`、`timeout|error→FAILED`；原 `finish_reason` 保留。旧字符串时间必须以严格 UTC parser 转换为 `timestamptz`，解析失败阻止切库并输出 row disposition。

## 4. 核心 PostgreSQL DDL 合同

以下是约束级合同；实现 migration 必须等价，并为 SQLite 导入提供显式转换，不要求 SQLite 支持 pgvector。

ID 策略：现有 EKB 主键/外键为 opaque string，v4 在 PostgreSQL 中继续使用 `text` 保存原 ID；新 ID 由应用生成 UUID 字符串但不改列类型。导入前对长度、字符集、唯一性和引用完整性审计；非法/重复/孤儿 ID 阻止迁移并输出 disposition，禁止在导入时静默重写。未来改为 PostgreSQL `uuid` 必须另立 migration 和 old→new mapping，不属于本轮。

```sql
CREATE TABLE source_objects (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  object_key text NOT NULL,
  sha256 char(64) NOT NULL,
  byte_size bigint NOT NULL CHECK (byte_size >= 0),
  detected_mime text NOT NULL,
  ref_count bigint NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, object_key),
  UNIQUE (tenant_id, sha256, byte_size)
);

CREATE TABLE upload_batches (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),
  created_by text NOT NULL REFERENCES users(id),
  mode text NOT NULL CHECK (mode IN ('FILE','MULTI_FILE','DIRECTORY')),
  status text NOT NULL,
  client_request_id text NOT NULL,
  item_count integer NOT NULL CHECK (item_count BETWEEN 0 AND 1000),
  total_bytes bigint NOT NULL CHECK (total_bytes BETWEEN 0 AND 5368709120),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, created_by, client_request_id)
);

CREATE TABLE upload_items (
  id text PRIMARY KEY,
  batch_id text NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
  client_item_id text NOT NULL,
  display_path text NOT NULL,
  normalized_relative_path text NOT NULL,
  byte_size bigint NOT NULL CHECK (byte_size BETWEEN 0 AND 104857600),
  expected_sha256 char(64),
  source_object_id text REFERENCES source_objects(id),
  status text NOT NULL,
  uploaded_bytes bigint NOT NULL DEFAULT 0 CHECK (uploaded_bytes >= 0),
  error_code text,
  error_detail jsonb,
  UNIQUE (batch_id, client_item_id)
);

CREATE TABLE upload_sessions (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  upload_item_id text NOT NULL REFERENCES upload_items(id) ON DELETE CASCADE,
  method text NOT NULL CHECK (method IN ('SINGLE','MULTIPART','API_STREAM')),
  provider_upload_id text,
  state text NOT NULL CHECK (state IN ('ACTIVE','COMPLETING','COMPLETED','ABORTED','EXPIRED','FAILED')),
  part_size bigint,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (upload_item_id)
);

CREATE TABLE upload_parts (
  session_id text NOT NULL REFERENCES upload_sessions(id) ON DELETE CASCADE,
  part_no integer NOT NULL CHECK (part_no > 0),
  etag text NOT NULL,
  byte_size bigint NOT NULL CHECK (byte_size > 0),
  checksum text,
  confirmed_at timestamptz NOT NULL,
  PRIMARY KEY (session_id, part_no)
);

CREATE INDEX ix_upload_sessions_expiry
ON upload_sessions (expires_at)
WHERE state IN ('ACTIVE','COMPLETING');

-- scheduler 对 expires_at<=now() 的 ACTIVE/COMPLETING session 创建幂等 abort job；
-- 成功 abort 后 session=EXPIRED、item=FAILED/UPLOAD_SESSION_EXPIRED，已确认 parts 保留审计元数据。

ALTER TABLE documents
  ADD COLUMN normalized_relative_path text,
  ADD COLUMN active_version_id text REFERENCES document_versions(id);

CREATE UNIQUE INDEX uq_documents_live_path
ON documents (kb_id, normalized_relative_path)
WHERE purged_at IS NULL;

CREATE TABLE embedding_profiles (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  llm_provider_id text NOT NULL REFERENCES llm_providers(id),
  model_id text NOT NULL REFERENCES llm_models(id),
  dimensions integer NOT NULL CHECK (dimensions > 0),
  tokenizer text NOT NULL,
  chunker_id text NOT NULL,
  chunker_version text NOT NULL,
  config jsonb NOT NULL,
  fingerprint char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, fingerprint)
);

ALTER TABLE document_versions
  ADD COLUMN source_object_id text REFERENCES source_objects(id),
  ADD COLUMN ingest_status text NOT NULL DEFAULT 'LEGACY' CHECK (ingest_status IN ('LEGACY','UPLOADED','PROCESSING','SUCCEEDED','FAILED','CANCELLED')),
  ADD COLUMN parser_id text,
  ADD COLUMN parser_version text,
  ADD COLUMN embedding_profile_id text REFERENCES embedding_profiles(id),
  ADD COLUMN index_generation_id text,
  ADD COLUMN activated_at timestamptz;

ALTER TABLE chunks
  ADD COLUMN document_version_id text REFERENCES document_versions(id),
  ADD COLUMN index_generation_id text,
  ADD COLUMN locator jsonb NOT NULL DEFAULT '{}'::jsonb;

-- 以下唯一索引只能在 legacy duplicate verifier 通过后创建；
-- 物理列 doc_id/version/checksum 分别对应领域列 document_id/version_no/sha256。
CREATE UNIQUE INDEX uq_document_versions_doc_version
ON document_versions (doc_id, version);

CREATE UNIQUE INDEX uq_document_versions_doc_checksum
ON document_versions (doc_id, checksum);

CREATE UNIQUE INDEX uq_chunks_version_ordinal
ON chunks (document_version_id, chunk_index)
WHERE document_version_id IS NOT NULL;

ALTER TABLE ingest_jobs
  ADD COLUMN document_version_id text REFERENCES document_versions(id),
  ADD COLUMN active_attempt_id text,
  ADD COLUMN state_version integer NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX uq_ingest_job_version
ON ingest_jobs (tenant_id, document_version_id)
WHERE document_version_id IS NOT NULL;

CREATE TABLE ingest_job_attempts (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  ingest_job_id text NOT NULL REFERENCES ingest_jobs(id) ON DELETE CASCADE,
  attempt_no integer NOT NULL CHECK (attempt_no > 0),
  state text NOT NULL CHECK (state IN (
    'WAITING','VALIDATING','CONVERTING','PARSING','CHUNKING',
    'EMBEDDING','INDEXING','SUCCEEDED','FAILED','CANCELLED'
  )),
  current_stage text,
  progress_current bigint NOT NULL DEFAULT 0,
  progress_total bigint,
  lease_owner text,
  lease_expires_at timestamptz,
  heartbeat_at timestamptz,
  error_code text,
  sanitized_error jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ingest_job_id, attempt_no)
);

ALTER TABLE ingest_jobs
  ADD CONSTRAINT fk_ingest_active_attempt
  FOREIGN KEY (active_attempt_id) REFERENCES ingest_job_attempts(id);

CREATE UNIQUE INDEX uq_ingest_one_active_attempt
ON ingest_job_attempts (ingest_job_id)
WHERE state IN ('WAITING','VALIDATING','CONVERTING','PARSING','CHUNKING','EMBEDDING','INDEXING');

CREATE TABLE ingest_stages (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  attempt_id text NOT NULL REFERENCES ingest_job_attempts(id) ON DELETE CASCADE,
  stage text NOT NULL,
  status text NOT NULL,
  input_hash char(64) NOT NULL,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at timestamptz,
  ended_at timestamptz,
  UNIQUE (attempt_id, stage, input_hash)
);

CREATE TABLE index_generations (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),
  embedding_profile_id text NOT NULL REFERENCES embedding_profiles(id),
  generation_no integer NOT NULL,
  state text NOT NULL CHECK (state IN ('BUILDING','READY','ACTIVE','FAILED','RETIRED')),
  vector_count bigint NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz,
  UNIQUE (knowledge_base_id, embedding_profile_id, generation_no)
);

CREATE UNIQUE INDEX uq_kb_one_active_generation
ON index_generations (knowledge_base_id)
WHERE state = 'ACTIVE';

ALTER TABLE knowledge_bases
  ADD COLUMN embedding_profile_id text REFERENCES embedding_profiles(id),
  ADD COLUMN active_index_generation_id text REFERENCES index_generations(id);

ALTER TABLE document_versions
  ADD CONSTRAINT fk_document_version_generation
  FOREIGN KEY (index_generation_id) REFERENCES index_generations(id);

ALTER TABLE chunks
  ADD CONSTRAINT fk_chunks_index_generation
  FOREIGN KEY (index_generation_id) REFERENCES index_generations(id);
```

`normalized_relative_path`、`chunks.document_version_id` 等 legacy backfill 列先 nullable expand；只有 verifier 证明无未处置 legacy row 后才在 contract migration 设 NOT NULL。创建 version/chunk 唯一索引前必须审计重复 `(doc_id, version)`、`(doc_id, checksum)` 和 `(document_version_id, chunk_index)`；重复 row 保留 canonical 引用并进入显式 disposition，禁止静默删除或改号。无法推导的 row 同样进入 disposition，不能以空字符串通过唯一约束。

Legacy document version 初始 `ingest_status=LEGACY`，source/parser/profile/generation 可空且不能冒充已索引；PH3 reconcile 以原文件可用性决定重建或保持 legacy-only。新 version 必须具备 source object、parser、profile、READY generation 才能在同一事务把旧 generation `ACTIVE→RETIRED`、新 generation `READY→ACTIVE` 并更新 KB/profile/generation 与 document active version 指针；partial unique index 和行锁阻止两个 ACTIVE generation。

Vector 列按 profile 维度生成兼容表/分区；若 pgvector schema 不能在同一列容纳多维索引，则使用 `embedding_<dimension>` 受控表族或每 profile generation 表，不在运行时拼接未验证 SQL。

```sql
ALTER TABLE conversations
  ADD COLUMN active_branch_id text,
  ADD COLUMN title_locked boolean NOT NULL DEFAULT false;

ALTER TABLE messages
  ADD COLUMN branch_id text,
  ADD COLUMN parent_message_id text REFERENCES messages(id),
  ADD COLUMN status text NOT NULL DEFAULT 'completed',
  ADD COLUMN content_hash char(64);

ALTER TABLE qa_turns
  ADD COLUMN user_message_id text REFERENCES messages(id),
  ADD COLUMN requested_provider_id text REFERENCES llm_providers(id),
  ADD COLUMN requested_model_id text REFERENCES llm_models(id),
  ADD COLUMN actual_provider_id text REFERENCES llm_providers(id),
  ADD COLUMN actual_model_id text REFERENCES llm_models(id),
  ADD COLUMN state_version integer NOT NULL DEFAULT 0,
  ADD COLUMN terminal_seq integer,
  ADD COLUMN terminal_event text,
  ADD COLUMN updated_at timestamptz;

CREATE TABLE conversation_branches (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  conversation_id text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  parent_branch_id text REFERENCES conversation_branches(id),
  fork_message_id text,
  created_by text NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE conversations
  ADD CONSTRAINT fk_conversations_active_branch
  FOREIGN KEY (active_branch_id) REFERENCES conversation_branches(id);

ALTER TABLE messages
  ADD CONSTRAINT fk_messages_branch FOREIGN KEY (branch_id) REFERENCES conversation_branches(id);

CREATE TABLE message_parts (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  part_type text NOT NULL CHECK (part_type IN ('TEXT','IMAGE_REF','ATTACHMENT_REF','CITATION_MARKER')),
  ordinal integer NOT NULL,
  text_content text,
  json_content jsonb,
  resource_id text,
  UNIQUE (message_id, ordinal)
);

CREATE TABLE context_summaries (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  conversation_id text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  branch_id text NOT NULL REFERENCES conversation_branches(id) ON DELETE CASCADE,
  first_message_id text NOT NULL REFERENCES messages(id),
  last_message_id text NOT NULL REFERENCES messages(id),
  source_hash char(64) NOT NULL,
  model_id text NOT NULL REFERENCES llm_models(id),
  prompt_version text NOT NULL,
  before_tokens integer NOT NULL,
  after_tokens integer NOT NULL,
  summary_text text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (branch_id, source_hash, prompt_version)
);

CREATE TABLE turn_resource_snapshots (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  turn_id text NOT NULL REFERENCES qa_turns(turn_id) ON DELETE CASCADE,
  resource_type text NOT NULL,
  resource_id text NOT NULL,
  resource_version_id text,
  parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
  ordinal integer NOT NULL,
  UNIQUE (turn_id, resource_type, resource_id, ordinal)
);

CREATE TABLE message_citations (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  document_id text,
  document_version_id text,
  chunk_id text,
  attachment_id text,
  locator jsonb NOT NULL,
  display_snapshot jsonb NOT NULL,
  rank integer NOT NULL,
  CHECK ((document_version_id IS NOT NULL) <> (attachment_id IS NOT NULL))
);

CREATE TABLE attachments (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  owner_user_id text NOT NULL REFERENCES users(id),
  conversation_id text REFERENCES conversations(id),
  source_object_id text NOT NULL REFERENCES source_objects(id),
  client_request_id text NOT NULL,
  status text NOT NULL,
  detected_mime text NOT NULL,
  byte_size bigint NOT NULL,
  expires_at timestamptz,
  deleted_at timestamptz,
  deleted_by text REFERENCES users(id),
  deletion_batch_id text REFERENCES deletion_batches(id),
  deletion_reason text,
  deletion_generation integer NOT NULL DEFAULT 0,
  purged_at timestamptz,
  UNIQUE (tenant_id, owner_user_id, client_request_id)
);

CREATE TABLE attachment_artifacts (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  attachment_id text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  parser_version text NOT NULL,
  text_object_id text REFERENCES source_objects(id),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  token_estimate integer,
  status text NOT NULL,
  UNIQUE (attachment_id, parser_version)
);

CREATE TABLE attachment_chunks (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  attachment_id text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  artifact_id text NOT NULL REFERENCES attachment_artifacts(id) ON DELETE CASCADE,
  ordinal integer NOT NULL,
  text_content text NOT NULL,
  metadata jsonb NOT NULL,
  embedding_profile_id text REFERENCES embedding_profiles(id),
  UNIQUE (attachment_id, artifact_id, ordinal)
);

CREATE TABLE message_attachments (
  tenant_id text NOT NULL REFERENCES tenants(id),
  message_id text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  attachment_id text NOT NULL REFERENCES attachments(id),
  ordinal integer NOT NULL,
  usage_mode text NOT NULL CHECK (usage_mode IN ('INLINE','RETRIEVAL','VISION','OCR_FALLBACK')),
  PRIMARY KEY (message_id, attachment_id),
  UNIQUE (message_id, ordinal)
);

CREATE TABLE image_artifacts (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  attachment_id text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
  derived_object_id text REFERENCES source_objects(id),
  width integer NOT NULL CHECK (width > 0),
  height integer NOT NULL CHECK (height > 0),
  method text NOT NULL CHECK (method IN ('NATIVE_VISION','OCR','OCR_CAPTION')),
  ocr_text_object_id text REFERENCES source_objects(id),
  caption text,
  confidence numeric,
  provider_id text REFERENCES llm_providers(id),
  model_id text REFERENCES llm_models(id)
);

CREATE TABLE attachment_promotions (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  attachment_id text NOT NULL REFERENCES attachments(id),
  target_knowledge_base_id text NOT NULL REFERENCES knowledge_bases(id),
  normalized_relative_path text NOT NULL,
  client_request_id text NOT NULL,
  document_version_id text REFERENCES document_versions(id),
  ingest_job_id text REFERENCES ingest_jobs(id),
  status text NOT NULL,
  UNIQUE (tenant_id, attachment_id, target_knowledge_base_id, client_request_id)
);

ALTER TABLE message_citations
  ADD CONSTRAINT fk_citation_document FOREIGN KEY (document_id) REFERENCES documents(id),
  ADD CONSTRAINT fk_citation_version FOREIGN KEY (document_version_id) REFERENCES document_versions(id),
  ADD CONSTRAINT fk_citation_chunk FOREIGN KEY (chunk_id) REFERENCES chunks(id),
  ADD CONSTRAINT fk_citation_attachment FOREIGN KEY (attachment_id) REFERENCES attachments(id);
```

上述多租户 polymorphic/跨表引用无法全部由单列 FK 证明同 tenant；migration 应为可行表建立 composite UNIQUE/FK，其他情况由共享 service verifier 在写入事务中校验 tenant/owner/resource version，并以跨租户 citation/snapshot/promotion 负测作为 migration verify 硬门禁。

Chat backfill 顺序固定为：每个 conversation 创建一个 default branch → 按 `(created_at,id)` 把现有 messages 归入该 branch 并计算 content hash → 设置 conversation active branch → 从 `messages.turn_id` 和相邻 user/assistant 对补 qa turn message ids → 映射旧 turn status/时间 → 校验 actor/tenant。无法唯一配对的 turn 不猜测 user message，记录 legacy disposition 并禁止 Regenerate，直到人工/脚本修复；所有可用 message 完成后将 `messages.branch_id` 设 NOT NULL。

```sql
CREATE TABLE background_jobs (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  job_type text NOT NULL,
  idempotency_key text NOT NULL,
  state text NOT NULL CHECK (state IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED','CANCEL_REQUESTED','CANCELLED','DEAD')),
  priority integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL,
  payload jsonb NOT NULL,
  available_at timestamptz NOT NULL DEFAULT now(),
  lease_owner text,
  lease_expires_at timestamptz,
  heartbeat_at timestamptz,
  error_code text,
  sanitized_error jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, job_type, idempotency_key)
);

CREATE INDEX ix_jobs_claim
ON background_jobs (state, available_at, priority DESC)
WHERE state IN ('QUEUED','RETRY_WAIT');

CREATE TABLE background_job_attempts (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  job_id text NOT NULL REFERENCES background_jobs(id) ON DELETE CASCADE,
  attempt_no integer NOT NULL,
  worker_id text,
  state text NOT NULL CHECK (state IN ('RUNNING','SUCCEEDED','FAILED','CANCELLED','LEASE_EXPIRED')),
  started_at timestamptz NOT NULL,
  ended_at timestamptz,
  sanitized_error jsonb,
  UNIQUE (job_id, attempt_no)
);

CREATE TABLE worker_heartbeats (
  worker_id text PRIMARY KEY,
  worker_type text NOT NULL,
  queues jsonb NOT NULL,
  version text NOT NULL,
  heartbeat_at timestamptz NOT NULL,
  started_at timestamptz NOT NULL
);

CREATE TABLE scheduler_leases (
  schedule_name text PRIMARY KEY,
  owner_id text NOT NULL,
  lease_expires_at timestamptz NOT NULL,
  fencing_token bigint NOT NULL
);

CREATE TABLE scheduler_runs (
  id text PRIMARY KEY,
  schedule_name text NOT NULL,
  scope_type text NOT NULL CHECK (scope_type IN ('PLATFORM','TENANT')),
  tenant_id text REFERENCES tenants(id),
  started_at timestamptz NOT NULL,
  ended_at timestamptz,
  status text NOT NULL,
  counters jsonb NOT NULL,
  sanitized_error jsonb,
  CHECK ((scope_type = 'PLATFORM' AND tenant_id IS NULL) OR
         (scope_type = 'TENANT' AND tenant_id IS NOT NULL))
);

CREATE TABLE job_outbox (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  aggregate_type text NOT NULL,
  aggregate_id text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, event_type, aggregate_type, aggregate_id)
);
```

Platform-wide scheduler leadership uses `scheduler_leases`; it must fan out tenant-scoped `background_jobs` rather than creating `tenant_id=NULL` jobs。Job claim 使用 `FOR UPDATE SKIP LOCKED` + state/available_at，attempt 创建与 job lease/state CAS 在同一事务；terminal attempts 保留至审计保留期。

Retention governance 由 `v4_005_retention_governance` 所有：

```sql
CREATE TABLE deletion_batches (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  root_resource_type text NOT NULL,
  root_resource_id text NOT NULL,
  deletion_generation integer NOT NULL,
  deleted_by text NOT NULL REFERENCES users(id),
  reason text,
  deleted_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  UNIQUE (tenant_id, root_resource_type, root_resource_id, deletion_generation),
  CHECK (expires_at = deleted_at + interval '30 days')
);

ALTER TABLE trash_items
  ADD COLUMN deletion_generation integer NOT NULL DEFAULT 1,
  ADD COLUMN deletion_batch_id text REFERENCES deletion_batches(id),
  ADD COLUMN purge_state text NOT NULL DEFAULT 'ELIGIBLE' CHECK (purge_state IN ('ELIGIBLE','CLAIMED','PURGING_DB','PURGING_OBJECTS','PURGING_INDEX','SUCCEEDED','RETRY_WAIT','DEAD')),
  ADD COLUMN claim_token text,
  ADD COLUMN claim_fencing_token bigint NOT NULL DEFAULT 0,
  ADD COLUMN claim_expires_at timestamptz,
  ADD COLUMN purge_job_id text REFERENCES background_jobs(id);

DROP INDEX ux_trash_items_resource;
CREATE UNIQUE INDEX ux_trash_items_resource_generation
ON trash_items (tenant_id, resource_type, resource_id, deletion_generation);
CREATE UNIQUE INDEX ux_trash_claim_token
ON trash_items (claim_token) WHERE claim_token IS NOT NULL;
CREATE INDEX ix_trash_expiry_claim
ON trash_items (expires_at, purge_state)
WHERE restored_at IS NULL AND purged_at IS NULL;

ALTER TABLE knowledge_bases
  ADD COLUMN expires_at timestamptz,
  ADD COLUMN deleted_by text REFERENCES users(id),
  ADD COLUMN deletion_batch_id text REFERENCES deletion_batches(id),
  ADD COLUMN deletion_reason text,
  ADD COLUMN deletion_generation integer NOT NULL DEFAULT 0,
  ADD COLUMN purged_at timestamptz;

ALTER TABLE documents
  ADD COLUMN deleted_at timestamptz,
  ADD COLUMN expires_at timestamptz,
  ADD COLUMN deleted_by text REFERENCES users(id),
  ADD COLUMN deletion_batch_id text REFERENCES deletion_batches(id),
  ADD COLUMN deletion_reason text,
  ADD COLUMN deletion_generation integer NOT NULL DEFAULT 0,
  ADD COLUMN purged_at timestamptz;

ALTER TABLE document_versions
  ADD COLUMN deleted_at timestamptz,
  ADD COLUMN expires_at timestamptz,
  ADD COLUMN deleted_by text REFERENCES users(id),
  ADD COLUMN deletion_batch_id text REFERENCES deletion_batches(id),
  ADD COLUMN deletion_reason text,
  ADD COLUMN deletion_generation integer NOT NULL DEFAULT 0,
  ADD COLUMN purged_at timestamptz;

ALTER TABLE conversations
  ADD COLUMN expires_at timestamptz,
  ADD COLUMN deleted_by text REFERENCES users(id),
  ADD COLUMN deletion_batch_id text REFERENCES deletion_batches(id),
  ADD COLUMN deletion_reason text,
  ADD COLUMN deletion_generation integer NOT NULL DEFAULT 0,
  ADD COLUMN purged_at timestamptz;
```

已有字符串时间在 PH1 PostgreSQL 导入时转换为 `timestamptz`。删除事务创建 deletion batch 并同步写资源和 trash item；恢复事务清空资源的 deleted/expiry/batch/reason，写 restored_at；claim CAS 仅允许 `ELIGIBLE→CLAIMED` 且 `expires_at<=now()`。文档 soft delete 继续占用 `(kb_id, normalized_relative_path)`，因此所有列表、摄取、检索和 RAG 必须过滤 `documents.deleted_at IS NULL AND purged_at IS NULL`，相同路径新建返回 `PATH_RESERVED_BY_TRASH`。

`v4_008_attachments` 创建附件表时直接包含同等 deleted/expiry/batch/reason/generation/purged 字段，并补 `deletion_batch_id` FK；Conversation 的 Message 作为 aggregate child 随会话 purge，不作为独立 trash item。

Provider security 的约束级增量表：

```sql
CREATE TABLE provider_credentials (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  owner_user_id text REFERENCES users(id),
  ownership_scope text NOT NULL CHECK (ownership_scope IN ('PERSONAL','TEAM')),
  ownership_key text NOT NULL,
  ciphertext text NOT NULL,
  key_version text NOT NULL,
  secret_last4 text,
  status text NOT NULL CHECK (status IN ('ACTIVE','ROTATING','REVOKED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  rotated_at timestamptz,
  CONSTRAINT ck_provider_credential_owner CHECK (
    (ownership_scope = 'PERSONAL' AND owner_user_id IS NOT NULL AND ownership_key = 'USER:' || owner_user_id) OR
    (ownership_scope = 'TEAM' AND owner_user_id IS NULL AND ownership_key = 'TEAM')
  ),
  UNIQUE (id, tenant_id, ownership_key)
);

ALTER TABLE llm_providers
  ALTER COLUMN user_id DROP NOT NULL,
  ADD COLUMN ownership_scope text NOT NULL DEFAULT 'PERSONAL',
  ADD COLUMN ownership_key text,
  ADD COLUMN adapter_type text,
  ADD COLUMN credential_id text,
  ADD COLUMN egress_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN config_version integer NOT NULL DEFAULT 1,
  ADD COLUMN disabled_at timestamptz;

UPDATE llm_providers
SET ownership_key = CASE
  WHEN ownership_scope = 'PERSONAL' THEN 'USER:' || user_id
  ELSE 'TEAM'
END;

ALTER TABLE llm_providers
  ALTER COLUMN ownership_key SET NOT NULL,
  ADD CONSTRAINT ck_llm_provider_owner CHECK (
    (ownership_scope = 'PERSONAL' AND user_id IS NOT NULL AND ownership_key = 'USER:' || user_id) OR
    (ownership_scope = 'TEAM' AND user_id IS NULL AND ownership_key = 'TEAM')
  ),
  ADD CONSTRAINT fk_llm_provider_credential_owner
    FOREIGN KEY (credential_id, tenant_id, ownership_key)
    REFERENCES provider_credentials (id, tenant_id, ownership_key);

ALTER TABLE llm_models
  ADD COLUMN health_status text NOT NULL DEFAULT 'UNKNOWN',
  ADD COLUMN capability_source text NOT NULL DEFAULT 'BUILT_IN',
  ADD COLUMN capabilities_verified_at timestamptz,
  ADD COLUMN config_version integer NOT NULL DEFAULT 1,
  ADD COLUMN disabled_at timestamptz;

CREATE TABLE model_fallback_policies (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  owner_user_id text REFERENCES users(id),
  scope text NOT NULL CHECK (scope IN ('PERSONAL','TEAM')),
  ordered_model_ids jsonb NOT NULL,
  allowed_error_classes jsonb NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  version integer NOT NULL DEFAULT 1
);

CREATE TABLE model_route_events (
  id text PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES tenants(id),
  turn_id text NOT NULL REFERENCES qa_turns(turn_id),
  requested_model_id text NOT NULL REFERENCES llm_models(id),
  actual_model_id text REFERENCES llm_models(id),
  actual_provider_id text REFERENCES llm_providers(id),
  fallback_reason text,
  input_tokens integer,
  output_tokens integer,
  latency_ms integer,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

`llm_providers` 沿用既有物理所有者列 `user_id`；`provider_credentials` 使用 `owner_user_id`。两表的 `ownership_key` 将 scope/owner 规范化为 `USER:<id>` 或 `TEAM`，复合 FK 强制 credential 与 provider 同 tenant、同 scope、同 owner。迁移先 expand 并回填 provider 与 credential，再运行跨租户/跨 owner verifier 和负向约束测试，最后才把 `credential_id` 收紧为 `NOT NULL`；PERSONAL 必须有 owner，TEAM 必须无 owner。Credential ciphertext 不进入 audit/route event。

Turn terminal 竞争必须使用单条 CAS；外部 API 的 `turn_id` 就是 `qa_turns.turn_id`：

```sql
UPDATE qa_turns
SET status = :terminal_status,
    terminal_seq = :terminal_seq,
    terminal_event = :terminal_event,
    updated_at = now()
WHERE turn_id = :turn_id
  AND tenant_id = :tenant_id
  AND actor_id = :actor_id
  AND status NOT IN ('COMPLETED','STOPPED','FAILED')
RETURNING turn_id, status, terminal_seq, terminal_event;
```

返回 0 行时读取同一 tenant+actor scoped row 并返回实际终态；调用方不得再次写消息 terminal status 或发第二个 SSE terminal event。

## 5. Provider 密钥迁移

新增 `provider_credentials`，只保存 ciphertext、key_version、last4、status。迁移流程：

1. 部署支持 encrypted-first dual-read 的代码，生产缺 master key 时 provider 功能 fail closed。
2. 现有 provider 先回填 `ownership_scope=PERSONAL`、`ownership_key='USER:' || user_id`；受控 job 读取旧 key、立即加密写入同 tenant/owner/key 的 credential row 并校验解密，日志只记录 provider id。
3. 回填 `credential_id`，运行 tenant/scope/owner 对账、跨租户与跨 owner 负向约束测试，再验证真实模型调用。
4. Provider 改为只读 credential ref；verifier 确认无空引用后将 `credential_id` 设为 `NOT NULL`，禁止旧列读写并在后续 contract migration 将旧值置空/删除列。
5. 回滚仅回到仍支持 encrypted credential 的上一版本，不把密钥解密写回旧列。

## 6. SQLite → PostgreSQL 数据迁移

### 6.1 流程

1. 生产备份 SQLite、对象/本地文件、配置和 PostgreSQL。
2. 在隔离环境导入 schema，按 manifest 应用所有 migration。
3. 以稳定主键和显式类型映射导入；禁止 ORM create_all。
4. 对所有 legacy text ID 做长度/唯一/FK 审计；保持原值导入。任何非法、重复或孤儿进入阻断报告，不自动生成替代 ID。
5. backfill default branches、normalized paths、legacy source object markers、provider refs；`qa_turns.turn_id` 原值保持为 API/FK canonical key。
6. 校验每表行数、FK、唯一约束、hash、时间、租户分布和抽样业务旅程。
7. PH1 生产进入短写冻结；增量导入/重放；再次校验。
8. PH1 原子切 DSN，保持旧 SQLite 只读快照；PH2 以后禁止继续向 SQLite 写入或为新功能维护双写。

### 6.2 必须相等/解释的计数

tenants、users、memberships、knowledge bases、documents、versions、chunks、conversations、messages、providers、models、trash、audit。任何差异必须有逐行 migration disposition；不能只比较总数。

## 7. Rollback

- Expand 阶段：旧代码忽略新表，可直接回旧镜像。
- Cutover 前：删除测试 PostgreSQL 并从备份重演，不改生产 SQLite。
- Cutover 后观察窗：停止新写，导出 PostgreSQL 增量到兼容回放包；只有回放验证通过才允许 DSN 回 SQLite。
- 已产生对象/附件/分支等旧 schema 无法表达的数据时，不做破坏性 down migration；采用旧 UI/API 关闭新功能但继续由新 DB 服务，或前滚修复。
- 审计和 migration ledger 永不因 rollback 删除。

## 8. Migration Gate

独立 migration job 必须成功完成 `apply + verify` 后 API/worker 才启动。Verify 检查 manifest append order/checksum、extensions、tables/columns/indexes/FK/check、legacy ID/time/status audit/backfill counts、一个 active ingest attempt、multipart session/part/expiry、tenant-scoped job idempotency、deletion batch/generation/claim/path reservation、default branch/message/turn backfill、`qa_turns.turn_id` FK/terminal CAS、attachment 全表、citation/snapshot tenant verifier、Provider ownership/encrypted credential/无旧明文依赖、pgvector dimensions 和 orphan rows。任何失败非零退出并阻止切换。

## 9. PH1 foundation local implementation note (2026-08-12)

第一真实业务切片已按本文件的 additive migration 约束落地到本地工作树：

- `v4_001_migration_provenance` 独立于历史 `schema_migrations`，记录 v3 历史事实、v4 owner/checksum/applied_at/verify_status/manifest position/provenance，并为 provenance 与 audit 建立不可变保护。未知、重复、缺失、顺序漂移或 checksum 漂移均阻断 apply/verify。
- `v4_002_runtime_jobs` 只追加 jobs、attempts、worker heartbeats、scheduler leases/runs 和 outbox 表；tenant/job_type/idempotency_key 唯一性、状态约束、租约与 outbox 约束由数据库持有。`services/jobs.py` 的 enqueue/claim/heartbeat/complete/fail/publish 使用 DB transaction/CAS，不以内存队列作为真值。
- v4 runner 仅按 `v4_001 → v4_002` 追加，支持 apply/verify、重复 apply no-op、expect versions 和 rollback dry-run；rollback 保留 v4 ledger/audit，不执行破坏性 down migration。

本记录的验证边界是 SQLite 本地测试与一次 disposable 本地 PostgreSQL+pgvector apply/verify/repeat；未对生产 PostgreSQL、生产 DSN 或生产迁移执行任何写操作。v4 runner 要求已导入的 legacy schema，禁止隐式调用 ORM `create_all`；仅测试 fixture 显式 bootstrap，生产路径在缺少 imported schema 时 fail closed。PG 验证覆盖 JSONB 绑定、租户/attempt 唯一约束、租约/CAS、双 worker single-claim 和 provenance 负向不可变保护。PostgreSQL 切库、v4_003–v4_008、备份恢复和部署仍按后续 Phase 门禁处理。

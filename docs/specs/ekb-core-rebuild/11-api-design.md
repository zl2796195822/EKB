---
title: EKB 核心重构 API 设计
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 核心重构 API 设计

## 1. 通用合同

- Base：`/api/v1`；现有 `/kb`、`/qa/ask`、`/conversations`、`/llm/*` 在兼容期保留。
- 认证：Bearer/API key 经 live membership/role/ACL；所有响应带 `X-Request-Id`。
- 新列表：`{"items":[],"next_cursor":null,"page_size":50}`；cursor 绑定 tenant、actor、filter hash。
- 写命令支持 `Idempotency-Key`；重复请求返回原资源/operation。
- 时间为 UTC RFC3339；ID 为 opaque string，新建资源通常使用 UUID-string，legacy ID 原样保留；只有 `client_request_id/client_turn_id` 等新幂等字段强制 UUID 格式。
- 长任务返回 `202` + operation/job id；不得固定返回假 success。

### 错误 Envelope

```json
{
  "error": {
    "code": "EMBEDDING_RATE_LIMITED",
    "message": "向量生成暂时不可用，可稍后重试",
    "retryable": true,
    "stage": "EMBEDDING",
    "request_id": "opaque",
    "details": {"job_id": "opaque"}
  }
}
```

`details` 经过 allowlist，不包含密钥、正文、内部路径或 Provider 原始 payload。

## 2. Upload/Knowledge API

| Method | Path | 作用 |
|---|---|---|
| POST | `/knowledge-bases/{kb_id}/upload-batches` | 预检并创建批次 |
| GET | `/upload-batches/{batch_id}` | 批次/文件/阶段 projection |
| POST | `/upload-items/{item_id}/complete` | 校验对象并创建版本/job |
| POST | `/upload-items/{item_id}/abort` | 终止未完成上传 |
| POST | `/ingest-jobs/{job_id}/retry` | 新 attempt，复用原件 |
| POST | `/ingest-jobs/{job_id}/cancel` | 请求取消安全阶段 |
| GET | `/knowledge-bases/{kb_id}/documents` | cursor/filter/path 列表 |
| GET | `/documents/{document_id}/versions` | 版本和状态 |
| POST | `/knowledge-bases/{kb_id}/reindex` | 新 profile/generation 重建 |
| GET | `/operations/{operation_id}` | 通用长任务状态 |

创建批次请求：

```json
{
  "mode": "DIRECTORY",
  "client_request_id": "uuid-from-client",
  "items": [
    {
      "client_item_id": "1",
      "relative_path": "产品/手册/guide.pdf",
      "byte_size": 245760,
      "browser_mime": "application/pdf",
      "sha256": "optional-64-hex"
    }
  ]
}
```

响应逐项返回 `accepted`、规范路径、错误或 `upload_session`。Upload session 可包含预签名 single/multipart parts；服务端不返回内部 object key。

`GET batch` 的 item：

```json
{
  "id": "opaque",
  "relative_path": "产品/手册/guide.pdf",
  "status": "EMBEDDING",
  "progress": {"unit":"chunks","current":80,"total":120},
  "version_id": "opaque",
  "job_id": "opaque",
  "error": null
}
```

## 3. Conversation/Turn API

| Method | Path | 作用 |
|---|---|---|
| POST | `/conversations` | 新建会话和默认配置 |
| GET | `/conversations` | owner scoped 列表 |
| GET | `/conversations/{id}` | 会话、默认 KB、active branch |
| PATCH | `/conversations/{id}` | title/title lock/archive/default KB |
| DELETE | `/conversations/{id}` | soft delete |
| GET | `/conversations/{id}/messages` | active/指定 branch 历史 |
| POST | `/conversations/{id}/turns` | 创建 Turn 并返回 SSE location |
| GET | `/turns/{turn_id}` | owner scoped 状态/最终消息 |
| GET | `/turns/{turn_id}/events` | SSE；首期不承诺 replay |
| POST | `/turns/{turn_id}/cancel` | 幂等 Stop |
| POST | `/turns/{turn_id}/retry` | 失败 Turn 新 attempt |
| POST | `/messages/{message_id}/regenerate` | 新回答 branch |
| PATCH | `/conversations/{id}/active-branch` | 切换活动分支 |

创建 Turn：

```json
{
  "client_turn_id": "uuid",
  "content": [{"type":"text","text":"总结附件并结合知识库回答"}],
  "model_id": "opaque",
  "answer_mode": "knowledge_enhanced",
  "knowledge_base_ids": ["opaque"],
  "attachment_ids": ["opaque"],
  "reserved_output_tokens": 2048
}
```

返回 `201`：conversation、turn、user_message、assistant_message、event_url。旧 `/qa/ask` 在兼容 adapter 中转换为新 Turn；`attachment_doc_ids` 不再合并到 `kb_ids`，废弃字段返回 deprecation header，最终版本化移除。

SSE wire 合同以 [05-ai-chat.md](./05-ai-chat.md) 为唯一来源：HTTP `event:` 保存 event name，`id:` 为 `<turn_id>:<seq>`，`data:` 是包含 turn/request/seq/timestamp/payload 的 JSON；heartbeat 是不递增 seq 的 comment。Cancel/query 必须使用 `qa_turns.turn_id + tenant_id + actor_id` 条件，找不到与无权使用相同外部语义。`AC-CR-024` 必须逐字验证 id/event/data/comment 和 terminal 竞争。

## 4. Attachment API

| Method | Path | 作用 |
|---|---|---|
| POST | `/attachments` | 创建上传会话 |
| POST | `/attachments/{id}/complete` | 校验对象并开始处理 |
| GET | `/attachments/{id}` | owner/status/artifacts projection |
| DELETE | `/attachments/{id}` | soft delete |
| POST | `/attachments/{id}/restore` | 保留期内恢复 |
| POST | `/attachments/{id}/retry` | 处理失败重试 |
| POST | `/attachments/{id}/promotions` | 提升到 KB |

创建请求包含 filename、byte_size、browser_mime、sha256、conversation_id；服务端返回限制和 upload session。`GET` 可返回 processing method、token estimate、pages/sheets、Vision/OCR compatibility，但不返回解析全文。

Promotion 请求：`target_knowledge_base_id`、`relative_path`；返回 `202`、promotion/document version/ingest job ids。

## 5. Provider/Model API

兼容现有 `/llm/providers` 和 `/llm/models` 路径，统一以下语义：

| Method | Path | 作用 |
|---|---|---|
| GET/POST | `/llm/providers` | 可见 Provider / 创建 |
| PATCH/DELETE | `/llm/providers/{id}` | 更新/禁用（历史引用存在时不物理删） |
| POST | `/llm/providers/{id}/credentials:rotate` | 轮换密钥，不回显 |
| POST | `/llm/providers/{id}/health-check` | 真实轻量检查 |
| GET/POST | `/llm/providers/{id}/models` | 列表/手工模型 |
| POST | `/llm/providers/{id}/models:sync` | 真实供应商同步 operation |
| PATCH/DELETE | `/llm/models/{id}` | enable/capability override/disable |
| GET | `/llm/available-models` | Assistant selector 唯一 projection |
| GET/PUT | `/llm/fallback-policy` | owner/tenant 显式链 |

Provider create 的 credential 只在请求 body 传入；响应包含 credential status/last4，绝不返回完整值。Base URL 必须通过 scheme、host/IP 和 tenant policy 校验。

创建请求的 ownership 只能是 `PERSONAL|TEAM`；客户端不能提交 owner user/tenant id。服务端对 PERSONAL 使用当前 actor，对 TEAM 使用当前 tenant。权限矩阵：

| 操作 | Personal Provider | Team Provider | 不可访问 ID |
|---|---|---|---|
| list/get | owner | tenant member 仅看可用投影；admin 看配置 | 统一 404 |
| create/update/credential/health | owner | tenant owner/admin + `llm:provider:manage` | 统一 404，DENIED audit |
| model enable/disable/sync | owner | tenant owner/admin | 统一 404 |
| use in Turn | owner 且 egress policy 允许 | tenant policy/role 允许 | `MODEL_UNAVAILABLE`，不泄露 provider |
| historical route read | conversation owner 仅看 display/model/status | conversation owner 仅看 snapshot | credential/base URL 永不返回 |

每个写操作记录 actor、ownership、provider/model id、before/after status 和 request id；不记录 credential。禁用后历史 Turn route snapshot 仍可读，但不能发起新请求。

`available-models` 返回 provider display/name/logo key、model capability、context/limits、ownership、health 和 fallback eligibility；不返回 credential 或内部 URL。

## 6. RAG/Citation API

RAG 作为 Turn 参数，不提供绕过会话 ACL 的独立生成端点。可保留 `/search` 用于授权的知识搜索，但其结果必须包含 version locator，且不能代表回答引用已经持久化。

| Method | Path | 作用 |
|---|---|---|
| GET | `/messages/{id}/citations` | 生成时引用快照 |
| GET | `/citations/{id}/source` | 重新授权后打开版本定位 |
| POST | `/knowledge-bases/{id}/retrieval-preview` | 管理员调试检索，不调用 LLM |

Citation source 失权/已清理时返回不可访问状态，不泄露原文；display snapshot 可保留标题/路径的合规副本。

## 7. Trash/Jobs API

| Method | Path | 作用 |
|---|---|---|
| GET | `/trash` | 类型/时间/cursor 列表，server remaining seconds |
| POST | `/trash/{id}/restore` | 恢复或冲突 |
| DELETE | `/trash/{id}` | 创建 purge job |
| DELETE | `/trash` | 批量创建 purge jobs |
| GET | `/admin/jobs` | tenant scoped job 列表 |
| GET | `/admin/jobs/{id}` | attempts/stages/heartbeat/error |
| POST | `/admin/jobs/{id}/retry` | retry dead/failed |
| POST | `/admin/jobs/{id}/cancel` | request cancel |
| GET | `/admin/workers` | heartbeat/queue 能力 |
| GET | `/admin/scheduler/runs` | cleanup/reconcile outcomes |

永久删除返回 `202` operation，不等待对象/向量物理清理。

Trash list item 的最小 shape：

```json
{
  "id": "opaque-trash-id",
  "resource_type": "DOCUMENT",
  "resource_id": "opaque",
  "deletion_generation": 2,
  "deletion_batch_id": "opaque",
  "parent": {"type":"KNOWLEDGE_BASE","id":"opaque","deleted":false},
  "display_snapshot": {"title":"guide.pdf","relative_path":"产品/guide.pdf"},
  "deleted_at": "UTC timestamp",
  "expires_at": "UTC timestamp",
  "remaining_seconds": 12345,
  "purge_state": "ELIGIBLE"
}
```

Restore 请求包含 `client_request_id`、`expected_deletion_generation`、`restore_parents`（默认 false）和可选 `new_relative_path`。成功返回恢复的 root/descendant ids；父级删除返回 `409 PARENT_DELETED` + 可恢复父链 IDs，已被 purge claim 返回 `409 PURGE_IN_PROGRESS`。软删文档在保留期继续占用路径，新建/上传相同路径返回 `409 PATH_RESERVED_BY_TRASH`；服务端内部 claim/fencing token 永不返回客户端。

永久删除请求通过 `Idempotency-Key` + `expected_deletion_generation` 绑定；返回 operation/job id。重复请求返回同一 operation。资源类型不能由 path id 猜测，必须使用 trash item 的 server-side type/generation。

## 8. Web Search 移除合同

- 新 Turn schema 不含 `web_search`、tool 或 search provider 字段。
- 兼容端点收到旧 search option 返回 `422 FEATURE_REMOVED`，不静默忽略。
- capability endpoint 明确 `web_search=false` 且含 removal reason，过渡 UI 随后删除控件。
- 搜索 KB 的 `/search` 与 Internet Web Search 是不同能力，保留前者。

## 9. 并发、幂等与限流

- 上传 complete、Turn create、cancel、retry、regenerate、promotion、purge 均有 idempotency key/资源唯一约束。
- 对上传、Provider health/sync、Turn 创建按 tenant/user 限流；返回 `Retry-After`。
- 版本切换、active branch、purge claim 和 job lease 使用 row version/CAS，冲突返回 409。
- 批量操作返回逐项结果，不能因部分失败返回整体假成功。

## 10. 兼容与弃用

现有端点先由 adapter 映射新 service；响应新增字段保持 optional。弃用通过 header、文档和 telemetry 观察至少一个发布窗口。只有兼容测试证明没有活跃调用后，才在版本化 API 中移除旧字段/路径。

| Legacy path/field | Adapter target | 保留语义 | Deprecation/telemetry | 删除前置 |
|---|---|---|---|---|
| `POST /qa/ask` | create Turn + SSE v2 | 现有 id/event/data envelope 和必要顶层字段 | `Deprecation/Sunset/Link` + call counter | 新 Turn API 覆盖全部活跃客户端 |
| `attachment_doc_ids` | explicit `attachment_ids` | 不再合并到 KB；兼容期仅对可证明的 legacy document attachment 转换，否则 422 | field counter/error counter | 使用率为零且迁移 UI 已发布 |
| `/kb/{id}/docs*` | Knowledge service | 旧 list array/状态码字段保持 | path counter | 新 path 稳定且兼容 suite 通过 |
| `/llm/providers*`、`/llm/models*` | canonical registry | 路径继续保留；内部去 mock/明文 | mock-source metric 必须为零 | 不计划删除，仅演进 optional 字段 |
| `/trash*` | retention service | 旧 list/restore/delete adapter；新 purge 返回 operation | legacy synchronous expectation metric | 客户端接受 async purge |

旧 `/qa/ask` 的 `event:` 名称映射到新领域 event 时必须有固定表和 contract fixture；不能在同一版本把 heartbeat comment 改成数据事件，也不能宣称 `Last-Event-ID` replay。

| Legacy `/qa/ask` event | 新 Turn event/内部状态 | 兼容要求 |
|---|---|---|
| `request` | `turn.accepted` | 旧端点继续发 `request`，保留 conversation/message/stream_version 顶层提升 |
| `retrieval_started` | `turn.stage(stage=retrieving)` | 旧 payload 移除 `web_search` 后保留授权 KB 数和 phase |
| `retrieval_completed` | `turn.stage(stage=retrieval_completed)` | 命中数来自授权 KB/附件，不含 Web |
| `generation_started` | `turn.stage(stage=streaming)` | 首 delta 前最多一次 |
| `content_delta`（v1 `token`） | `message.delta` | 旧名称/字段继续；新端点使用 typed delta |
| `citations`（v1 `citation`） | `citation.upsert` | 旧 aggregate/逐项形状由 fixture 锁定；来源仅 KB/附件 |
| `compaction_performed` | `context.compacted` | app-v2 必须建模；旧名称继续 |
| `error` + `done(error)` | `turn.failed` | 新端点只发一个 terminal；旧端点在兼容窗保留双事件 wire，但 DB 只写一个 FAILED |
| `done(cancelled)` | `turn.stopped` | 旧 finish_reason 保留；partial message 持久 |
| 其他 `done` | `turn.completed` | 旧 last_seq 语义保持 |
| `web_search_*` | 无 | 请求搜索返回 `FEATURE_REMOVED`，不产生事件 |

# EKB API 接口契约

> **配套文档**：[技术架构与详细设计](./技术架构与详细设计_v1.0_2026-08-07.md) · [数据模型与数据库设计](./数据模型与数据库设计_v1.0_2026-08-07.md) · [安全与合规设计](./安全与合规设计_v1.0_2026-08-07.md)  
> **文档版本**：v1.0（待评审）  
> **编写日期**：2026-08-07  
> **用途**：定义 M1/M2 API 的路径、鉴权、请求响应、错误码、SSE 事件和幂等规则。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| Base Path | `/api/v1` |
| 内容类型 | JSON 默认为 `application/json`; 上传使用 `multipart/form-data`; 问答流使用 `text/event-stream` |
| 认证 | `Authorization: Bearer <access_token>` |
| 租户 | 服务端从 Token 与成员关系解析；客户端可传 `X-EKB-Tenant-Id` 表示当前工作租户，但不能作为唯一信任来源 |
| Trace | 所有响应包含 `X-Request-Id`; 请求可传 `X-Request-Id`，服务端可覆盖不合法值 |
| 幂等 | 写操作可传 `Idempotency-Key`; 同一主体、租户、路径和 key 在有效窗口内只执行一次 |
| 时间 | ISO 8601 UTC，例如 `2026-08-07T08:00:00Z` |
| 分页 | `page_size` 默认 20，最大 100；列表返回 `next_cursor` |

## 2. 错误响应

```json
{
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "无权执行该操作",
    "request_id": "req_01HX...",
    "details": {}
  }
}
```

| HTTP | code | 说明 | 安全要求 |
|---:|---|---|---|
| 400 | `VALIDATION_ERROR` | 请求字段不合法 | 可返回字段级错误，不返回内部栈 |
| 401 | `UNAUTHENTICATED` | 未登录或 Token 无效 | 不泄露账号是否存在 |
| 403 | `PERMISSION_DENIED` | 无权访问 | 不泄露目标资源是否存在 |
| 404 | `NOT_FOUND` | 当前授权范围内不存在 | 与越权响应保持相近信息量 |
| 409 | `CONFLICT` | 状态冲突、版本冲突、幂等冲突 | 返回可重试建议 |
| 413 | `PAYLOAD_TOO_LARGE` | 文件过大 | 返回当前租户限制 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 格式不支持 | 返回 M0 冻结的格式枚举 |
| 429 | `RATE_LIMITED` | 限流或配额超限 | 返回 `retry_after_seconds` |
| 500 | `INTERNAL_ERROR` | 未预期错误 | 只返回 request_id |
| 502 | `UPSTREAM_ERROR` | RAG/模型上游失败 | 不返回上游敏感错误 |
| 504 | `UPSTREAM_TIMEOUT` | RAG/模型超时 | 记录降级和耗时 |

## 3. 认证与主体

### 3.1 登录

`POST /api/v1/auth/login`

```json
{
  "tenant_hint": "tenant-slug",
  "email": "user@example.com",
  "password": "string",
  "mfa_code": "string"
}
```

`200 OK`

```json
{
  "access_token": "jwt",
  "expires_in": 900,
  "refresh_token": "opaque",
  "token_type": "Bearer",
  "user": {
    "id": "uuid",
    "name": "张三",
    "email": "user@example.com"
  },
  "tenants": [
    {
      "id": "uuid",
      "name": "内部知识库",
      "role": "MEMBER"
    }
  ]
}
```

### 3.2 刷新与退出

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/auth/refresh` | 使用 refresh token 换取新的 access token |
| `POST` | `/api/v1/auth/logout` | 撤销当前 refresh token |
| `GET` | `/api/v1/me` | 返回用户、租户、能力集合和策略版本 |

## 4. 知识库

### 4.1 创建知识库

`POST /api/v1/kb`

```json
{
  "name": "运维 SOP",
  "description": "内部运维流程和故障处理手册",
  "visibility": "PRIVATE",
  "tags": ["ops", "sop"]
}
```

`201 Created`

```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "name": "运维 SOP",
  "visibility": "PRIVATE",
  "role": "OWNER",
  "document_count": 0,
  "created_at": "2026-08-07T08:00:00Z",
  "updated_at": "2026-08-07T08:00:00Z"
}
```

### 4.2 列表与详情

| 方法 | 路径 | 查询 | 权限 |
|---|---|---|---|
| `GET` | `/api/v1/kb` | `q, visibility, tag, cursor, page_size` | 返回当前主体可见知识库 |
| `GET` | `/api/v1/kb/{kb_id}` | 无 | `VIEWER` 及以上 |
| `PATCH` | `/api/v1/kb/{kb_id}` | JSON body | `ADMIN` 及以上 |
| `DELETE` | `/api/v1/kb/{kb_id}` | 无 | `OWNER` |

删除默认为软删除；若知识库仍有关联文档或审计记录，不允许物理删除。

## 5. 文档与入库任务

### 5.1 上传文档

`POST /api/v1/kb/{kb_id}/docs`

Headers: `Idempotency-Key: <stable-key>`  
Body: `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | file | 是 | M0 冻结的支持格式 |
| `title` | string | 否 | 默认取文件名 |
| `source_type` | enum | 是 | `UPLOAD|URL|SYNC` |
| `source_uri` | string | 否 | URL 或源系统标识，不保存敏感凭据 |
| `tags` | array | 否 | 文档标签 |

`202 Accepted`

```json
{
  "doc_id": "uuid",
  "job_id": "uuid",
  "status": "PROCESSING",
  "trace_id": "req_01HX..."
}
```

### 5.2 文档状态

`GET /api/v1/kb/{kb_id}/docs/{doc_id}`

```json
{
  "id": "uuid",
  "kb_id": "uuid",
  "title": "数据库故障 SOP.pdf",
  "status": "READY",
  "version": 3,
  "mime_type": "application/pdf",
  "checksum": "sha256:...",
  "chunk_count": 128,
  "failure_reason": null,
  "created_at": "2026-08-07T08:00:00Z",
  "updated_at": "2026-08-07T08:05:00Z"
}
```

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/kb/{kb_id}/docs` | 文档列表，支持状态、标签、来源筛选 |
| `POST` | `/api/v1/kb/{kb_id}/docs/{doc_id}/retry` | 重试失败入库任务 |
| `DELETE` | `/api/v1/kb/{kb_id}/docs/{doc_id}` | 软删除文档并下线索引 |

## 6. 检索

`POST /api/v1/search`

```json
{
  "query": "数据库连接池耗尽怎么处理？",
  "kb_ids": ["uuid"],
  "filters": {
    "tags": ["database"],
    "source_type": ["UPLOAD"],
    "updated_after": "2026-01-01T00:00:00Z"
  },
  "top_k": 10
}
```

`200 OK`

```json
{
  "results": [
    {
      "chunk_id": "uuid",
      "doc_id": "uuid",
      "kb_id": "uuid",
      "title": "数据库故障 SOP",
      "section_path": ["故障处理", "连接池耗尽"],
      "snippet": "授权范围内的命中片段，高亮由客户端处理或服务端返回 offsets",
      "score": 0.82,
      "updated_at": "2026-08-07T08:00:00Z"
    }
  ],
  "trace_id": "req_01HX..."
}
```

服务端必须忽略或拒绝用户无权访问的 `kb_ids`，不能在结果或错误中暴露被过滤资源。

## 7. 问答 SSE

`POST /api/v1/qa/ask`

Headers: `Accept: text/event-stream`

```json
{
  "conversation_id": "uuid",
  "question": "数据库连接池耗尽应该先看哪些指标？",
  "kb_ids": ["uuid"],
  "filters": {
    "tags": ["database"]
  },
  "options": {
    "stream": true,
    "max_citations": 5
  }
}
```

### 7.1 SSE 事件

```text
event: request
data: {"request_id":"req_01HX...","message_id":"uuid"}

event: retrieval
data: {"status":"running","authorized_kb_count":2}

event: token
data: {"text":"先检查连接池使用率"}

event: citation
data: {"citation_id":"c1","doc_id":"uuid","chunk_id":"uuid","title":"数据库故障 SOP","section_path":["故障处理"],"version":3}

event: done
data: {"message_id":"uuid","finish_reason":"stop","confidence":"medium"}
```

### 7.2 错误与取消事件

```text
event: error
data: {"code":"UPSTREAM_TIMEOUT","message":"模型响应超时，已停止生成","request_id":"req_01HX..."}

event: done
data: {"finish_reason":"cancelled"}
```

| 事件 | 必须字段 | 说明 |
|---|---|---|
| `request` | `request_id,message_id` | 客户端建立本轮请求标识 |
| `retrieval` | `status` | 检索状态，不返回未授权细节 |
| `token` | `text` | 增量文本 |
| `citation` | `citation_id,doc_id,chunk_id,title,version` | 系统侧引用 |
| `error` | `code,message,request_id` | 可恢复错误 |
| `done` | `finish_reason` | `stop|refusal|timeout|cancelled|error` |

## 8. 会话与反馈

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/conversations` | 当前主体可见会话列表 |
| `GET` | `/api/v1/conversations/{conversation_id}/messages` | 会话消息和引用 |
| `PATCH` | `/api/v1/conversations/{conversation_id}` | 修改标题、归档 |
| `DELETE` | `/api/v1/conversations/{conversation_id}` | 软删除或归档，按留存策略执行 |
| `POST` | `/api/v1/qa/messages/{message_id}/feedback` | 点赞/点踩和原因 |

反馈请求：

```json
{
  "rating": "DOWN",
  "reason": "引用不支持结论",
  "comment": "第二条引用是过期版本"
}
```

## 9. 管理接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/v1/admin/tenants` | 平台管理员 | 租户列表 |
| `POST` | `/api/v1/admin/tenants` | 平台管理员 | 创建租户 |
| `GET` | `/api/v1/admin/audit` | 审计权限 | 审计查询 |
| `POST` | `/api/v1/admin/audit/export` | 审计权限 + 二次确认 | 导出审计 |
| `GET` | `/api/v1/admin/models` | 平台/租户管理员 | 模型策略 |
| `PATCH` | `/api/v1/admin/models/{model_config_id}` | 平台/租户管理员 | 修改模型路由和出域策略 |
| `GET` | `/api/v1/admin/metrics` | 管理员 | 运营指标 |

模型配置只返回 `secret_ref`，不得返回真实密钥。

## 10. API 评审门禁

- [ ] 每个写接口都有权限、幂等、审计和错误码。
- [ ] 每个列表接口都使用授权范围和游标分页。
- [ ] 问答 SSE 覆盖成功、拒答、取消、超时和上游错误。
- [ ] 错误响应不泄露跨租户资源是否存在。
- [ ] OpenAPI 生成物与本文契约一致；M1 后变更必须走版本化。

---

_本契约在 M1 开发中作为后端、前端、测试和审计的共同边界。若实现需要变更路径或字段，先更新本文和追踪矩阵，再开发。_

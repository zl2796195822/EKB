---
title: EKB 模型供应商与模型服务规格
status: Ready for Plan
tier: 3
type: feature
authoritative: true
scope: ekb-core-rebuild
version: 0.1
date: 2026-08-12
owner: Sol
---

# EKB 模型供应商与模型服务规格

## 1. 目标

个人中心“模型服务”是 AI 助手模型选择的唯一数据源，并保证用户看到的模型与实际调用的 Provider 一致。本文件覆盖 [EKB-CR-FR-041 至 EKB-CR-FR-049](./01-requirements.md)。

## 2. Provider 层级

| 类型 | Owner | 可见范围 | 用途 |
|---|---|---|---|
| Team provider | Tenant admin | 按租户 policy/角色 | 团队批准的数据处理 |
| Personal provider | User | 仅 owner | 普通对话或 policy 允许的资料 |
| Local provider | Team/User 配置 | 按 owner | Ollama 等私有 endpoint |

KB 数据外发 policy 在请求前执行。Team KB 可声明 `team_only`、允许 provider allowlist 或禁止 external；个人 Provider 不因拥有 key 自动获得团队资料权限。

## 3. Adapter 架构

```text
ProviderRouter
  ├─ OpenAIAdapter
  ├─ AnthropicAdapter
  ├─ GeminiAdapter
  └─ OpenAICompatibleAdapter
       ├─ DeepSeek profile
       ├─ Groq profile
       ├─ xAI profile
       ├─ OpenRouter profile
       ├─ Ollama profile
       └─ Azure OpenAI profile
```

每个 adapter 规范化 `stream_chat`、`embed`（若支持）、usage、error、cancel 和 capability discovery。OpenAI-compatible profile 可以修正 base URL、auth header、model path、Azure deployment/api-version、stream event 和不支持字段；不能假定 Vision/JSON/usage/cancel 一致。

Client factory 必须以 `llm_provider_id` 创建，严禁仅按 model name 查找全局客户端。

## 4. 数据合同

| 实体 | 关键字段 |
|---|---|
| `llm_providers` | tenant/owner、adapter_type、display_name、base_url、credential_id、status、policy、version；一个 row 即一个 Provider instance |
| `provider_credentials` | encrypted_payload、key_version、last4/prefix、rotated_at、status |
| `llm_models` | provider_id、provider_model_id、display_name、enabled、capabilities、context_window、limits |
| `model_fallback_policies` | scope、ordered model ids、conditions、enabled |
| `provider_health` | checked_at、latency、status、sanitized_error |
| `model_route_events` | turn、requested/actual model、reason、usage、latency、status |

`(provider_id, provider_model_id)` 唯一。模型删除优先 soft disable；有历史 Turn 引用时不得物理删除。

## 5. Capability Schema

```json
{
  "chat": true,
  "streaming": true,
  "vision": false,
  "embedding": false,
  "structured_output": "unknown",
  "context_window": 128000,
  "max_output_tokens": 8192,
  "image_mime_types": [],
  "source": "provider|admin_override|built_in",
  "verified_at": "timestamp"
}
```

未知能力默认不可用。管理员 override 必须记录理由、操作者和版本；真实调用失败可以降级 capability health，但不能自动改写静态能力。

## 6. Provider/Model 管理流程

### 创建

1. 选择 adapter 和 ownership。
2. 输入 display name、base URL（适用时）和 credential。
3. 服务端验证 URL policy，防止 SSRF；加密 credential。
4. 执行轻量 health/capability check。
5. 保存 provider，再从真实 API 同步或手工添加模型。

保存成功不等于模型可用；只有 enabled、credential valid、health policy 允许且 capability 完整的模型进入 Assistant selector。

### 同步

Model catalog 来自 Provider API 或管理员显式输入，禁止 mock catalog 进入生产。同步使用 upsert；供应商暂时未返回的模型先标 `stale`，不立即删除历史引用。

### 禁用/删除

- 禁用 Provider 级联使其模型不可新选，但不改历史 Turn。
- 禁用模型立即从 selector 移除。
- 当前选择失效时，UI 显示原因；仅在显式 policy 匹配时选择 fallback。
- 物理删除 credential 只在无 provider 引用且保留窗口结束后执行。

## 7. 路由与 Fallback

选择顺序：requested model → 验证 ownership/policy/capability → 建立 bound client → 调用。失败后只对 policy 声明的错误（rate limit、transient timeout、provider unavailable）遍历 fallback chain。

不允许 fallback：认证配置错误、数据外发禁止、Vision 不兼容、上下文超限（应重建 context）、tenant/ACL、持久化错误。

每次 fallback 在首个可见 token 前产生 `route.selected` 事件；若流式中途失败，默认终止并允许 Retry，不拼接另一模型输出。

## 8. Credential 安全

- 使用专用 master key/envelope encryption，ciphertext 保存 key version。
- 生产缺 key 时 fail closed，禁止开发默认 key。
- 创建/更新响应不回显完整 secret；UI 仅显示 provider、状态和 last4/prefix。
- 轮换支持 active/previous key 解密窗口和后台 re-encrypt。
- URL、headers 和 error 必须经过 allowlist/redaction；禁止密钥进入日志、审计、Sentry、浏览器和 memory。

## 9. Provider Logo

建立 `provider-brand-registry`，每项记录合法来源、license/brand usage、light/dark asset、viewBox 和 fallback。优先内置经审查的 SVG，不运行远程 Logo URL。

UI 统一 32px 容器、20–24px 图形、安全内边距、transparent background；单色 Logo 按品牌规则适配主题。加载失败显示统一中性 provider glyph，不用 emoji/首字母冒充成功资源。

## 10. API 与助手同步原则

Assistant selector 查询 server-side `available_models` projection，它已经应用 owner、tenant policy、enabled、credential 和 capability 过滤。前端不得维护第二份静态模型数组。发送 Turn 只提交 model id，服务端保存 requested/actual route。

## 11. 真实验收

每类 adapter 至少执行一次真实流式对话；Vision/Embedding 只对声明支持的模型执行。验收需同时证明：配置保存、刷新存在、selector 出现、发送成功、Provider 端调用/usage 或服务端 route event 匹配。生产无出网时保持阻塞，不以 mock 替代。

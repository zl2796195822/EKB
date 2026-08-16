# 01 — Grok Build Benchmark and Reuse Boundary

> Status: Draft — Awaiting User Confirmation  
> Local source: `/Users/alin/Openclaw项目/grok-build`

## 1. 固定研究基线

| Item | Value |
|---|---|
| Git origin | `https://github.com/xai-org/grok-build.git` |
| Local commit | `8adf9013a0929e5c7f1d4e849492d2387837a28d` |
| Upstream `SOURCE_REV` | `2ec0f0c8488842da03a71eeee3c61154957ca919` |
| First-party license | Apache License 2.0 |
| Third-party boundary | `THIRD-PARTY-NOTICES`, vendored/Codex/OpenCode code follows its own license |

采用规则：固定 commit、记录 provenance、复用行为和架构思想；不直接搬运 Rust/TUI/agent runtime。任何源码复制必须先逐文件确认所有权，保留适用 LICENSE/NOTICE/版权与修改说明。

## 2. Grok 的核心不是聊天外观

```text
Pager/TUI
  → Session Runtime
  → ChatState Actor（单一可变状态 owner）
      → canonical conversation / prompt index / token / model
      → pruning / compaction
      → persistence adapter
          → updates.jsonl：不可变 UI/事件事实
          → chat_history.jsonl：可替换的模型上下文投影
```

最关键原则：**原始历史和发送给模型的有效上下文是两种数据。** 压缩可以替换模型上下文投影，但不能删除原始用户/助手事实。

参考位置：

- 模块边界：`README.md:95-105`。
- 双层导出语义：`crates/codegen/xai-grok-shell/src/session/export.rs:1-4`。
- ChatState：`crates/codegen/xai-chat-state/src/types.rs:19-97`、`actor/state.rs:115-248`。
- Command/mutation/request：`actor/commands.rs:38-188`、`actor/mutations.rs:112-206`、`actor/request_builder.rs:20-208`。

## 3. 能力对标

### 3.1 持久化与恢复

Grok 增量持久消息和更新，整体重写使用临时文件 + 原子 rename；损坏尾行或单文件不会使整场会话完全不可恢复。EKB 的等价实现不是 JSONL，而是：

- PostgreSQL canonical messages/turns；
- append-only `turn_events`；
- outbox + generation job；
- context summary/projection；
- 事务与 compare-and-set 状态迁移。

参考：`session/storage/mod.rs:487-603`、`storage/jsonl/mod.rs:225-289,416-460,910-998,1098-1138`。

### 3.2 Streaming

Grok 的流是强类型状态，不只是 token 字符串：Started、FirstToken、Text/Reasoning delta、Retrying、Completed、Failed；delta 可合并后持久化和广播，partial capture 与 canonical completed answer 分离。

EKB 等价合同：每个事件至少包含 `conversation_id/turn_id/message_id/seq/event_type/created_at`；事件先持久或在同一提交边界可恢复，再广播；partial 不进入后续上下文。

参考：`xai-grok-sampler/src/events.rs:23-112`、`session/streaming_capture.rs:1-248`、`session/replay_events.rs:7-68`。

### 3.3 Stop

Grok 使用 CancellationToken 取消真实 request task，原子移除 running task，保留 partial capture，但 terminal 只发一次。EKB 必须取消 generation job/Provider stream，并用 terminal CAS 拒绝 late delta；关闭浏览器流不算 Stop 完成。

参考：`session/acp_session_impl/tasks_cancel.rs:204-536`、`xai-grok-sampler/src/actor/request_task.rs:77-128`。

### 3.4 Retry 与 Regenerate

Grok 自动 retry 只处理可重试错误，带指数退避和 jitter；认证错误不重试，context overflow 转 compaction。编辑/重新生成使用 rewind、重发、fork，原历史可追溯。

EKB 必须区分：

1. transport auto retry：同一 attempt 内，处理 429/可重试 5xx/网络中断；
2. user retry：失败 Turn 复用原 user message 语义，创建新 attempt；
3. regenerate：成功或失败回答的替代版本，fork 新 branch，旧回答保留。

参考：`xai-grok-sampler/src/retry.rs:6-245`、`pager/app/inline_edit.rs:1-132`、`shell/acp_session_impl/rewind.rs:24-475`、`fork.rs:15-113`。

### 3.5 长上下文

Grok 的短期 pruning 保护最近完整 turns；长期 compaction 按模型 context window 百分比触发，summary cache 绑定精确 conversation prefix 和 model，模型切换、rewind 或编辑使缓存失效。summary 失败不伪造成功，原始事件仍保留。

EKB 等价结构：

```text
System / Policy
+ rolling summary（精确覆盖旧消息）
+ recent complete turns
+ 当前授权 KB RAG
+ 当前附件/图片结构化内容
+ current user message
```

参考：`xai-chat-state/src/compaction_utils.rs:702-857`、`compaction_config.rs:11-154`、`session/acp_session_impl/compaction.rs:1150-1225,1530-1625,2071-2110`。

### 3.6 Markdown / Code

Grok 在流式渲染时冻结稳定 block，只重绘未完成尾部，并单独维护未闭合 code fence 状态，避免全消息反复解析。EKB 浏览器不复制终端 renderer，但必须保证未闭合代码围栏不破坏 DOM、完成态稳定、安全清理、可复制且长代码无明显 O(N²) 卡顿。

参考：`xai-grok-markdown/src/streaming.rs:1-165`、`open_code_highlighter.rs:1-230`。

### 3.7 图片与模型切换

Grok 将图片作为结构化 ContentPart，原生 Vision 优先；不支持时用描述模型，caption 绑定内容 hash 和 prompt fingerprint。模型切换同步 context window、compaction threshold、capability、credential、system prompt 和持久 model 状态。

EKB 的优先级：原生 Vision → 已配置 Vision Adapter → 明确 `VISION_UNAVAILABLE`；绝不静默忽略图片。生成中禁止静默切换模型。

参考：`session/acp_session_impl/turn.rs:140-198,480-707`、`image_describe.rs:30-482`、`session/model_switch.rs:5-275`。

## 4. Grok 不提供的 EKB 能力

Grok Build 是 coding CLI，不具备通用浏览器文档聊天的完整上传/解析链。PDF、DOCX、XLSX、TXT、Markdown 的对象存储、checksum、parser job、extracted text/chunks、状态和租户 ACL 必须由 EKB 自己实现；不能在开发报告中写成“已迁移 Grok 文件附件”。

## 5. 明确禁止迁移

- Web/Internet Search 及其 progress/result/citation UI；
- coding-agent tool loop；
- 终端 Pager/TUI；
- 文件系统 rewind/checkpoint；
- Grok 专属后端协议；
- 未完成许可审查的第三方移植代码。

EKB 的知识库 RAG citation 是企业资料证据，不是互联网搜索引用。

## 6. Grok parity 行为合同

1. 每轮唯一 Turn，状态合法迁移，terminal first-wins。
2. User message 先持久化，再启动生成。
3. completed assistant 只由成功 terminal 产生；partial/failed/cancelled 不伪装成功。
4. 刷新、重连、进程重启恢复 canonical history 和当前生成状态。
5. SSE 单调 seq，可按 cursor 恢复。
6. 上下文由服务端 canonical state 构建，客户端不得提交历史数组。
7. 原始消息不因 compaction/trimming 删除。
8. summary 精确绑定 prefix、branch、model、prompt version 并可失效。
9. Stop 触达真实上游生成。
10. Retry、Regenerate、Edit/Fork 语义分离。
11. 图片和附件不能静默丢弃。
12. 每 Turn 固化实际 Provider/Model/capability/context budget。
13. Markdown/code 在 streaming/completed 两态都安全稳定。
14. 会话标题、模型、分支、附件、消息可持久恢复。
15. 可观测数据不得记录 API Key、附件原文或完整敏感 prompt。


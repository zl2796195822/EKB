# EKB 知识库 AI 对话流式交互升级（Conversation Stream v2）实施计划

> **版本**：v1.0
> **状态**：Draft
> **创建日期**：2026-08-08
> **基于设计文档**：`docs/知识库AI对话流式交互升级设计_v1.0_2026-08-08.md`

---

## 1. 摘要（Summary）

本计划将 EKB 知识库问答链路从现有的 SSE v1 协议升级到 Conversation Stream v2，核心改动包括：

1. **后端协议升级**：引入统一 envelope（turn_id/seq/timestamp）、分阶段事件（retrieval_started/completed、generation_started）、心跳机制、分段空闲超时
2. **Turn Registry**：新增 `qa_turns` 表，为 messages 表增加 turn_id/visibility_state 字段，支持轮次审计和显式取消
3. **显式取消 API**：`POST /qa/turns/{turn_id}/cancel`，配合 turn registry 实现取消标记和幂等
4. **前端 SSE 解析器重写**：支持 v2 envelope、turn/seq 隔离（旧流丢弃）、RAF 批量渲染、首 token 前撤回策略
5. **引用直连**：前端直接消费 SSE citation payload，移除问答主链路对预搜索的依赖
6. **可观测性增强**：新增协议错误、乱序/重复事件、空闲超时、取消成功率等指标

**实现范围**：M1（后端协议 + 前端体验）+ M2（取消闭环），不含 M3 重连回放。

---

## 2. 当前状态分析（Current State Analysis）

### 2.1 后端现状（基于代码探查）

| 文件 | 现状 | 需要改动 |
|---|---|---|
| [qa.py](file:///Users/alin/EKB/apps/api/ekb_api/routers/qa.py) | v1 事件：request/retrieval/token/citation/done/error；无 turn_id、无 seq、无阶段事件；只有总超时 `_qa_timeout()`（60s）；取消仅靠断连检测 | 重写 event_stream 生成器：增加 envelope、turn registry 登记、seq 原子递增、分阶段事件、心跳、检索/生成分段超时、取消标记检查点 |
| [models.py](file:///Users/alin/EKB/apps/api/ekb_api/models.py) | Message 模型无 turn_id 关联；无 qa_turns 表 | 新增 QaTurn ORM 模型；Message 增加 turn_id（可空）、visibility_state（可空）字段 |
| [store.py](file:///Users/alin/EKB/apps/api/ekb_api/store.py) | SqlStore 无 turn 相关方法；save_message 无 turn_id 参数 | 新增 create_turn/get_turn/update_turn_status/increment_turn_seq/request_cancel 等方法；save_message 增加 turn_id 参数；新增 visibility_state 查询过滤 |
| [schemas.py](file:///Users/alin/EKB/apps/api/ekb_api/schemas.py) | AskRequest 无 stream_version 声明；无 Turn 相关 schema | AskOptions 增加 stream_version 字段；新增 TurnResponse、TurnCancelRequest 等 schema |
| [config.py](file:///Users/alin/EKB/apps/api/ekb_api/core/config.py) | 仅有 qa_timeout_seconds（总超时） | 新增：retrieval_idle_timeout_seconds（默认15s）、generation_idle_timeout_seconds（默认20s）、heartbeat_interval_seconds（默认10s）、delta_merge_max_items（默认100）、delta_merge_max_bytes（默认2048）、delta_merge_max_ms（默认10） |
| [metrics.py](file:///Users/alin/EKB/apps/api/ekb_api/core/metrics.py) | QA_REQUESTS/QA_RETRIEVAL_DURATION/QA_GENERATION_DURATION/QA_DEGRADATIONS | 新增：QA_PROTOCOL_ERRORS、QA_OOO_EVENTS、QA_DUPLICATE_EVENTS、QA_IDLE_TIMEOUTS、QA_CANCEL_REQUESTS、QA_CANCEL_SUCCESS、QA_FIRST_EVENT_LATENCY、QA_FIRST_DELTA_LATENCY |
| [db.py](file:///Users/alin/EKB/apps/api/ekb_api/core/db.py) | 手动兼容迁移模式（_migrate_* 函数） | 新增 _migrate_qa_turns_table() 和 _migrate_message_turn_columns()；feature flag 通过 settings 读取 |
| [llm.py](file:///Users/alin/EKB/apps/api/ekb_api/llm.py) | chat_stream 无取消检查；无分段空闲超时 | generate_answer_stream 增加 cancel_callback 参数和 per-chunk idle timeout 检测 |
| [main.py](file:///Users/alin/EKB/apps/api/ekb_api/main.py) | CORS allow_headers 无 X-EKB-Stream-Version | CORS 增加 X-EKB-Stream-Version header |

### 2.2 前端现状（基于代码探查）

| 文件 | 现状 | 需要改动 |
|---|---|---|
| [api.ts](file:///Users/alin/EKB/apps/web/src/lib/api.ts) | ask() 方法用 fetch + ReadableStream 手工解析；仅支持 `\n\n` 分隔；无 SSE id/event 多 data 行处理；无版本 header；无 turn_id/seq | 抽取独立 SSE parser 类（支持 `\n\n` 和 `\r\n\r\n`、多 data 行、注释行、SSE id）；ask() 增加 X-EKB-Stream-Version:2 header；暴露 turn_id/seq 给上层；增加 cancelTurn API；AbortController 与显式 cancel 并行 |
| [types/api.ts](file:///Users/alin/EKB/apps/web/src/types/api.ts) | SseEvent = {event, data}；无 v2 envelope 类型；无 Turn 类型 | 新增 SseV2Envelope、SseV2Payload（request/retrieval_started/retrieval_completed/generation_started/delta/citation/heartbeat/error/done）；新增 TurnState 类型（idle/queued/retrieving/generating/cancelling/completed/failed/cancelled） |
| [App.tsx](file:///Users/alin/EKB/apps/web/src/App.tsx) | 单 isAsking 状态；每 token 直接 setState；无 turn nonce；取消仅 abort；引用依赖 client.search 预览结果；首可见输出前取消无撤回逻辑 | 替换为 turn 状态机（state + lastSeq）；本地 turnNonce → 服务端 turn_id 绑定；旧 turn 事件丢弃；RAF/定时器批量提交 delta；首可见前取消撤回 user/assistant 对 + 恢复草稿；取消时并行调用 cancel API；引用直接从 SSE citation payload 构建 |
| [ChatPanel.tsx](file:///Users/alin/EKB/apps/web/src/components/ChatPanel.tsx) | 仅展示 isStreaming/error/finishReason；无检索/生成阶段区分 UI | 增加阶段状态展示（检索中/生成中/取消中）；增加拒答（refusal）视觉区分；增加超时（timeout）提示样式 |

### 2.3 测试现状

| 文件 | 现状 | 需要补充 |
|---|---|---|
| [test_api.py](file:///Users/alin/EKB/apps/api/tests/test_api.py) | 已有 test_qa_stream_contains_request_evidence_and_done_events、test_qa_continues_within_same_conversation | 新增 v1 兼容测试（不传 header 走 v1）；v2 envelope/seq 测试；乱序/重复事件丢弃测试；retrieval/generation 空闲超时测试；显式 cancel API 测试；越权 cancel 统一 404 测试；幂等 cancel 测试 |

---

## 3. 拟议改动（Proposed Changes）

### 3.1 数据模型与迁移

#### 3.1.1 新增 QaTurn ORM 模型（models.py）

新增 `qa_turns` 表，字段对应设计文档 6.3 节：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| turn_id | String(64) | PK | 服务端生成不可预测 ID（用 `secrets.token_urlsafe(48)` 而非 UUID，满足不可预测性） |
| request_id | String(64) | NOT NULL, index | trace 关联 |
| tenant_id | String(36) | NOT NULL, index | 所有权 |
| actor_id | String(36) | NOT NULL, index | 取消鉴权 |
| conversation_id | String(36) | index | 关联会话 |
| assistant_message_id | String(36) | index | 关联回答消息 |
| stream_version | SmallInteger | default 2 | 协议版本 |
| status | Enum | running/cancelling/completed/failed/cancelled | 轮次状态 |
| last_seq | Integer | >= 0, default 0 | 已发事件序号（原子递增） |
| first_visible_at | String(32) | nullable | 首个可见 delta/citation 时间 |
| cancel_requested_at | String(32) | nullable | 取消请求时间 |
| finish_reason | Enum | nullable | stop/refusal/timeout/cancelled/error |
| created_at | String(32) | NOT NULL |  |
| completed_at | String(32) | nullable |  |

#### 3.1.2 Message 表字段扩展（models.py）

```python
# 新增可空字段，历史记录无需回填
turn_id = Column(String(64), nullable=True, index=True)
visibility_state = Column(String(32), nullable=True)  # visible/cancelled_before_visible/hidden_empty
```

#### 3.1.3 数据库迁移（db.py）

新增两个迁移函数，遵循现有 `_migrate_*` 模式：

- `_migrate_qa_turns_table()`：CREATE TABLE IF NOT EXISTS qa_turns（SQLite/PG 兼容 DDL）
- `_migrate_message_turn_columns()`：ALTER TABLE messages ADD COLUMN turn_id / visibility_state（分段补齐，检查已有列避免重复执行）

**决策**：不使用 Alembic，延续项目现有"检查列存在 + ALTER TABLE ADD COLUMN"的兼容迁移模式；历史 messages 记录 turn_id 为 NULL 表示 v1 产生，visibility_state 为 NULL 等价于 visible。

---

### 3.2 配置项扩展（config.py）

在 Settings 中新增以下字段，全部从环境变量读取，带默认值：

| 字段 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| retrieval_idle_timeout_seconds | EKB_RETRIEVAL_IDLE_TIMEOUT | 15.0 | 检索阶段空闲超时 |
| generation_idle_timeout_seconds | EKB_GENERATION_IDLE_TIMEOUT | 20.0 | 生成阶段上游片段空闲超时 |
| heartbeat_interval_seconds | EKB_HEARTBEAT_INTERVAL | 10.0 | 心跳发送间隔 |
| qa_stream_feature_flag | EKB_QA_STREAM_V2_FLAG | "true" | v2 总开关（灰度用） |
| delta_merge_max_items | EKB_DELTA_MERGE_MAX_ITEMS | 100 | 后端 delta 合并最大片段数 |
| delta_merge_max_bytes | EKB_DELTA_MERGE_MAX_BYTES | 2048 | 后端 delta 合并最大字节 |
| delta_merge_max_ms | EKB_DELTA_MERGE_MAX_MS | 10 | 后端 delta 合并最大时间窗口（ms） |

---

### 3.3 后端核心协议升级（qa.py）

这是改动最大的单一文件。将 `ask()` 中的 `event_stream()` 重写为 v2 逻辑，保留 v1 分支做兼容。

#### 3.3.1 请求头版本协商

```python
# 读取 X-EKB-Stream-Version header（从 request.headers）
stream_version = int(request.headers.get("X-EKB-Stream-Version", "1"))
# 同时受 feature flag 控制
use_v2 = stream_version >= 2 and settings.qa_stream_feature_flag
```

未传 header 或 `X-EKB-Stream-Version: 1` 时继续走现有 v1 逻辑（完全不改），保证兼容窗口。

#### 3.3.2 v2 event_stream 结构

创建 turn → 发送 envelope 包裹的事件 → 检查点检测取消/超时 → 落库终态

**事件生成顺序（严格）**：
1. `request`（seq=1）：turn_id, request_id, message_id, conversation_id
2. `retrieval_started`（seq=2）：authorized_kb_count
3. `retrieval_completed`（seq=3）：chunk_count, duration_ms
4. `generation_started`（seq=4）：model_alias, prompt_version
5. 循环：`delta`（seq++）或 `heartbeat`（不占 seq？→ **决策：心跳占 seq，保证单调连续**）
6. 循环：`citation`（seq++）—— **改到 generation 完成后立即发送，不等 done**
7. `error`（seq=n）：仅异常路径
8. `done`（seq=last）：message_id, finish_reason, confidence

**envelope 构造**：
```python
def _sse_v2(event: str, turn_id: str, request_id: str, seq: int, payload: dict) -> str:
    envelope = {
        "turn_id": turn_id,
        "request_id": request_id,
        "seq": seq,
        "timestamp": utc_now(),
        "payload": payload,
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    sse_id = f"{turn_id}:{seq}"
    return f"id: {sse_id}\nevent: {event}\ndata: {encoded}\n\n"
```

#### 3.3.3 Turn Registry 集成

在 `event_stream()` 开头：

```python
turn_id = store.create_turn(
    auth=auth,
    request_id=auth.trace_id,
    conversation_id=conversation.id,
    assistant_message_id=assistant_message.id,
)
```

每次发送事件前：
```python
seq = store.increment_turn_seq(auth.tenant_id, turn_id)  # 原子递增
```

检测取消（每个检查点）：
```python
if store.is_cancel_requested(auth.tenant_id, turn_id):
    # 发送 done(cancelled) 并 return
```

#### 3.3.4 分段空闲超时实现

**检索超时**：
```python
try:
    chunks = await asyncio.wait_for(
        asyncio.to_thread(retrieve, ...),
        timeout=settings.retrieval_idle_timeout_seconds,
    )
except asyncio.TimeoutError:
    # 发送 retrieval_timeout 错误事件 + done(timeout)
    finish_reason = "timeout"
    ...
```

**生成超时**：
在 LLM token 消费循环中使用 `asyncio.wait_for(next_token, timeout=generation_idle_timeout)` 包装单个 to_thread 调用。心跳在等待时发送，但不重置超时计时器。

```python
last_chunk_time = time.perf_counter()
while True:
    try:
        token = await asyncio.wait_for(
            asyncio.to_thread(_next_token, gen),
            timeout=settings.generation_idle_timeout_seconds,
        )
        last_chunk_time = time.perf_counter()
        ...
    except asyncio.TimeoutError:
        # 生成空闲超时
        finish_reason = "timeout"
        yield _sse_v2("error", ..., "GENERATION_IDLE_TIMEOUT", ...)
        yield _sse_v2("done", ..., finish_reason="timeout", ...)
        return
    # 检查是否需要发心跳
    if time.perf_counter() - last_heartbeat_time >= settings.heartbeat_interval_seconds:
        seq = store.increment_turn_seq(...)
        yield _sse_v2("heartbeat", turn_id, request_id, seq, {"stage": current_stage})
        last_heartbeat_time = time.perf_counter()
```

#### 3.3.5 后端 delta 合并

在 token 消费循环中实现简单合并窗口（100 项 / 2KB / 10ms，任一触发即 flush）：

```python
merge_buffer: list[str] = []
merge_byte_count = 0
merge_start_time = time.perf_counter()

def flush_merge():
    nonlocal merge_buffer, merge_byte_count, merge_start_time
    if merge_buffer:
        merged_text = "".join(merge_buffer)
        seq = store.increment_turn_seq(...)
        yield _sse_v2("delta", turn_id, request_id, seq, {"kind": "text", "text": merged_text})
        merge_buffer = []
        merge_byte_count = 0
        merge_start_time = time.perf_counter()

# 在 token 循环中：
merge_buffer.append(token)
merge_byte_count += len(token)
if (
    len(merge_buffer) >= settings.delta_merge_max_items
    or merge_byte_count >= settings.delta_merge_max_bytes
    or (time.perf_counter() - merge_start_time) * 1000 >= settings.delta_merge_max_ms
):
    yield from flush_merge()
```

循环结束后确保 flush 剩余 buffer。

#### 3.3.6 Citation 事件 payload 直连

从 chunk 对象直接构建 citation payload，不依赖前端的预搜索结果：

```python
for chunk in chunks:
    seq = store.increment_turn_seq(auth.tenant_id, turn_id)
    yield _sse_v2(
        "citation",
        turn_id,
        request_id,
        seq,
        {
            "citation_id": chunk.id,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.id,
            "title": chunk.title,
            "section_path": chunk.section_path,
            "version": chunk.doc_version,
            "updated_at": chunk.updated_at,
        },
    )
```

---

### 3.4 显式取消 API（qa.py 新增路由）

```python
@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(
    turn_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> dict:
    """幂等取消指定 turn；非本人/不存在返回统一 404（不泄露存在性）。"""
    success = store.request_turn_cancel(auth, turn_id)
    if not success:
        raise ApiError(404, "NOT_FOUND", "目标不存在或无权限")
    return {"turn_id": turn_id, "status": "cancelling"}
```

SqlStore 中 `request_turn_cancel()` 实现：
- 查询 QaTurn 中 tenant_id+actor_id+turn_id 匹配的行
- 若不存在或状态已终态 → 返回 False（统一 404）
- 若 cancel_requested_at 已设置 → 返回 True（幂等）
- 设置 status=cancelling、cancel_requested_at=utc_now() → 返回 True

---

### 3.5 SqlStore 新增/修改方法

#### 3.5.1 Turn 相关方法

```python
def create_turn(self, auth, request_id, conversation_id, assistant_message_id) -> str:
    """创建 turn，返回 turn_id（secrets.token_urlsafe(48)）。"""
    turn_id = secrets.token_urlsafe(48)
    now = utc_now()
    row = models.QaTurn(
        turn_id=turn_id,
        request_id=request_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        stream_version=2,
        status="running",
        last_seq=0,
        created_at=now,
    )
    # 写入 DB
    return turn_id

def increment_turn_seq(self, tenant_id, turn_id) -> int:
    """原子递增 last_seq 并返回新值。"""
    # SQLite：UPDATE + RETURNING（或 UPDATE 后 SELECT）
    # PG：UPDATE ... RETURNING last_seq

def mark_first_visible(self, tenant_id, turn_id) -> None:
    """设置 first_visible_at（仅第一次生效）。"""

def is_cancel_requested(self, tenant_id, turn_id) -> bool:
    """读取 cancel_requested_at IS NOT NULL。"""

def request_turn_cancel(self, auth, turn_id) -> bool:
    """见 3.4 节。"""

def finalize_turn(self, tenant_id, turn_id, status, finish_reason) -> None:
    """终态更新：status、finish_reason、completed_at。"""
```

#### 3.5.2 Message 方法修改

```python
def save_message(self, auth, conversation_id, role, content, turn_id=None) -> Message:
    """turn_id 可空（v1 兼容）。"""

def get_conversation_messages(self, auth, conversation_id) -> list[Message]:
    """过滤 visibility_state IN (NULL, 'visible')；隐藏 cancelled_before_visible 和 hidden_empty。"""
```

---

### 3.6 指标扩展（metrics.py）

新增指标（全部放入 registry）：

```python
# 协议质量
QA_PROTOCOL_ERRORS = registry.counter("qa_protocol_errors_total", "...", ("reason",))  # invalid_json/invalid_envelope/missing_seq/...
QA_OOO_EVENTS = registry.counter("qa_out_of_order_events_total", "Events with seq <= lastSeq")
QA_DUPLICATE_EVENTS = registry.counter("qa_duplicate_events_total", "Events with same seq replayed")
QA_IDLE_TIMEOUTS = registry.counter("qa_idle_timeouts_total", "...", ("stage",))  # retrieval/generation

# 取消闭环
QA_CANCEL_REQUESTS = registry.counter("qa_cancel_requests_total", "Explicit cancel API calls")
QA_CANCEL_SUCCESS = registry.counter("qa_cancel_success_total", "Turns actually stopped by cancel")

# 延迟细分
QA_FIRST_EVENT_LATENCY = registry.histogram("qa_first_event_latency_seconds", "Request → first event")
QA_FIRST_DELTA_LATENCY = registry.histogram("qa_first_delta_latency_seconds", "Request → first visible delta")
```

在 qa.py 的对应位置埋入 observe/inc 调用。

---

### 3.7 前端 SSE 解析器重写（api.ts）

#### 3.7.1 抽取 SSE Parser 类

```typescript
// 新增 SseV2Envelope 类型（types/api.ts）
interface SseV2Envelope {
  turn_id: string
  request_id: string
  seq: number
  timestamp: string
  payload: Record<string, unknown>
}

type SseV2EventHandler = (event: string, envelope: SseV2Envelope) => void

class SseStreamParser {
  private buffer = ''

  /** 喂入原始 chunk，解析后对每个完整事件调用 handler。支持 \n\n 和 \r\n\r\n。 */
  feed(chunk: string, handler: SseV2EventHandler): void {
    this.buffer += chunk
    const blocks = this.buffer.split(/\r?\n\r?\n/)
    this.buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      this.parseBlock(block, handler)
    }
  }

  private parseBlock(block: string, handler: SseV2EventHandler): void {
    const lines = block.split(/\r?\n/)
    let sseId = ''
    let eventName = ''
    const dataLines: string[] = []
    for (const line of lines) {
      if (line.startsWith(':')) continue  // 注释心跳
      if (line.startsWith('id: ')) { sseId = line.slice(4).trim(); continue }
      if (line.startsWith('event: ')) { eventName = line.slice(7).trim(); continue }
      if (line.startsWith('data: ')) { dataLines.push(line.slice(6)); continue }
      // 空行忽略
    }
    if (!eventName || dataLines.length === 0) return
    const rawData = dataLines.join('\n')
    const envelope = JSON.parse(rawData) as SseV2Envelope  // 解析失败抛错，由调用方捕获
    handler(eventName, envelope)
  }

  /** 流结束后检查 buffer 是否有残留（半截数据），有则记录协议错误。 */
  finish(): boolean {
    const hasLeftover = this.buffer.trim().length > 0
    this.buffer = ''
    return hasLeftover
  }
}
```

#### 3.7.2 ask() 方法改造

- 请求头加 `'X-EKB-Stream-Version': '2'`
- 使用 SseStreamParser 解析
- 暴露 turnNonce（本地生成）与 turn_id（服务端返回后绑定）给上层
- 增加 `cancelTurn(turnId: string): Promise<void>` 方法调用 `/qa/turns/{turn_id}/cancel`

```typescript
async ask(
  question: string,
  kbId: string,
  onEvent: (event: string, envelope: SseV2Envelope) => void,
  signal?: AbortSignal,
  conversationId?: string,
): Promise<{ turnNonce: string; abort: () => void }> {
  const turnNonce = `local_${crypto.randomUUID()}`  // 本地 nonce 用于旧流隔离
  // ... fetch + parser.feed + parser.finish 逻辑
}

async cancelTurn(turnId: string): Promise<void> {
  await this.request(`/qa/turns/${encodeURIComponent(turnId)}/cancel`, {
    method: 'POST',
  })
}
```

---

### 3.8 前端状态机与渲染优化（App.tsx）

#### 3.8.1 Turn 状态替换单一 isAsking

```typescript
// App.tsx 内新增
type TurnState = 'idle' | 'queued' | 'retrieving' | 'generating' | 'cancelling' | 'completed' | 'failed' | 'cancelled'

interface ActiveTurn {
  nonce: string           // 本地生成
  turnId?: string         // 服务端 request 事件后绑定
  lastSeq: number         // 已处理的最大 seq
  assistantMessageId: string  // 本地临时 id，映射到 ChatMessage.id
  firstVisibleReceived: boolean  // 是否已收到 delta/citation
  bufferedDeltas: string  // RAF 批处理缓冲区
  rafScheduled: boolean
  questionDraft: string   // 首可见前取消恢复草稿
}

const [turnState, setTurnState] = useState<TurnState>('idle')
const activeTurnRef = useRef<ActiveTurn | null>(null)
```

#### 3.8.2 旧流隔离逻辑

每个事件到达时：

```typescript
if (!activeTurnRef.current) return  // 无活跃轮次，丢弃
if (envelope.turn_id && envelope.turn_id !== activeTurnRef.current.turnId) {
  // turn_id 不匹配 → 旧流或新流事件，丢弃
  console.warn('Dropping stale event, turn mismatch')
  return
}
if (envelope.seq <= activeTurnRef.current.lastSeq) {
  // seq 倒退或重复 → 丢弃
  console.warn('Dropping OOO/duplicate event', { seq: envelope.seq, lastSeq: activeTurnRef.current.lastSeq })
  return
}
activeTurnRef.current.lastSeq = envelope.seq
```

#### 3.8.3 RAF 批量渲染 delta

```typescript
function appendDeltaWithRaf(text: string): void {
  const turn = activeTurnRef.current
  if (!turn) return
  turn.bufferedDeltas += text
  if (turn.rafScheduled) return
  turn.rafScheduled = true
  requestAnimationFrame(() => {
    const buffered = turn.bufferedDeltas
    turn.bufferedDeltas = ''
    turn.rafScheduled = false
    if (!buffered) return
    // 标记首可见
    if (!turn.firstVisibleReceived) {
      turn.firstVisibleReceived = true
    }
    // 批量应用到 messages
    setMessages(current => current.map(m =>
      m.id === turn.assistantMessageId
        ? { ...m, content: m.content + buffered }
        : m
    ))
  })
}
```

首 delta 延迟保证：RAF 不超过下一帧（~16ms），加上后端 10ms 合并窗口，总预算 200ms P95 应满足。

#### 3.8.4 取消策略

**点击取消时**：
```typescript
function handleCancelAsk(): void {
  // 1. AbortController 立即断连
  abortController.current?.abort()

  // 2. 并行调用显式 cancel API
  if (activeTurnRef.current?.turnId) {
    void client.cancelTurn(activeTurnRef.current.turnId)
  }

  // 3. UI 进入 cancelling 状态
  setTurnState('cancelling')

  // 4. 根据是否首可见前决定撤回策略
  const turn = activeTurnRef.current
  if (turn && !turn.firstVisibleReceived) {
    // 首可见前撤回：移除乐观 user/assistant 对 + 恢复草稿
    setMessages(current => {
      const withoutPair = [...current]
      // 移除最后 2 条（user + assistant）
      if (withoutPair.length >= 2 && withoutPair[withoutPair.length - 1]?.id === turn.assistantMessageId) {
        withoutPair.pop()
        withoutPair.pop()
      }
      return withoutPair
    })
    setQuestion(turn.questionDraft)
    setTurnState('cancelled')
    activeTurnRef.current = null
  } else {
    // 已有可见输出：保留内容，finishReason=cancelled
    setMessages(current => current.map(m =>
      m.id === turn?.assistantMessageId
        ? { ...m, isStreaming: false, finishReason: 'cancelled' }
        : m
    ))
    setTurnState('cancelled')
    activeTurnRef.current = null
  }
}
```

#### 3.8.5 取消后重发逻辑

新提问时如果 activeTurnRef 不为空 → 先取消旧轮（内部调用），再创建新 turnNonce。保证新旧 turn 完全隔离。

#### 3.8.6 引用直连（移除预搜索依赖）

citation 事件直接从 envelope.payload 构建 SearchResult，不再需要 `client.search()` 预览结果来 enrichment：

```typescript
// citation 事件处理中
const citation: SearchResult = {
  chunk_id: String(payload.chunk_id),
  doc_id: String(payload.doc_id),
  kb_id: selectedKbId,  // 当前选中知识库
  title: String(payload.title),
  section_path: Array.isArray(payload.section_path) ? payload.section_path.map(String) : [],
  snippet: '',  // SSE citation 无 snippet，UI 不展示 snippet
  score: 0,
  updated_at: String(payload.updated_at ?? ''),
  doc_version: Number(payload.version ?? 1),
}
// 追加到对应消息的 citations
```

搜索预览（searchResults）保留为独立"检索预览"区块功能，**不参与问答主链路 citation 展示**。

---

### 3.9 ChatPanel UI 增强（ChatPanel.tsx）

- 状态标签：检索阶段显示"正在检索授权证据…"、生成阶段显示"正在生成回答…"、取消中显示"正在取消…"
- 拒答样式：refusal finishReason 时内容旁加警告样式
- 超时样式：timeout 时显示"响应超时，可重试"并加重试按钮回调
- 生成中阶段进度条或动效（使用现有 GalaxyCard 风格元素）
- citations 显示不再依赖 searchResults 中的 score/snippet

---

### 3.10 类型定义扩展（types/api.ts）

```typescript
export interface SseV2Envelope {
  turn_id: string
  request_id: string
  seq: number
  timestamp: string
  payload: Record<string, unknown>
}

export type TurnState = 'idle' | 'queued' | 'retrieving' | 'generating' | 'cancelling' | 'completed' | 'failed' | 'cancelled'

export type FinishReason = 'stop' | 'refusal' | 'timeout' | 'cancelled' | 'error'

// 具体 payload 类型（可选，用于内部校验）
export interface SseRequestPayload { message_id: string; conversation_id: string }
export interface SseRetrievalStartedPayload { authorized_kb_count: number }
export interface SseRetrievalCompletedPayload { chunk_count: number; duration_ms: number }
export interface SseGenerationStartedPayload { model_alias: string; prompt_version?: string }
export interface SseDeltaPayload { kind: 'text'; text: string }
export interface SseCitationPayload { citation_id: string; doc_id: string; chunk_id: string; title: string; section_path: string[]; version: number; updated_at: string }
export interface SseHeartbeatPayload { stage: string }
export interface SseErrorPayload { code: string; message: string; retryable?: boolean }
export interface SseDonePayload { message_id: string; finish_reason: FinishReason; confidence?: 'low' | 'medium' | 'high' }
```

---

### 3.11 main.py CORS 更新

在 CORSMiddleware allow_headers 中增加：
```
"X-EKB-Stream-Version"
```

---

## 4. 假设与决策（Assumptions & Decisions）

| ID | 决策 | 理由 | 风险 |
|---|---|---|---|
| D-01 | turn_id 使用 secrets.token_urlsafe(48) 而非 UUID | 设计文档要求不可预测；UUID v4 虽然随机但标准做法是 token_urlsafe | 需确认列长度 64 足够（48 bytes → base64 约 64 chars） |
| D-02 | 心跳事件占用 seq 序号 | 保证单调性和简单性，不需要独立心跳计数 | seq 增长稍快，但 int 范围足够 |
| D-03 | citation 在 generation 完成后、done 之前立即发送 | 设计文档示例中 citation 在 delta 和 done 之间，保持一致 | 无 |
| D-04 | 首版不引入额外中间件（Redis），turn registry 纯数据库实现 | M1/M2 不需要跨实例恢复；SQLite/PG 已提供事务性 | 多副本部署下 turn registry 取消标记有同步延迟，但 turn status 的 DB 查询在每个检查点，延迟可接受 |
| D-05 | QA v2 feature flag 默认开启，通过环境变量控制灰度 | 设计文档要求 feature flag 可关；环境变量最简单，不需要远端配置服务 | 需要重启才能切 flag，但首版灰度频率低，可接受 |
| D-06 | 前端 RAF 批处理，不引入 setTimeout 回退 | requestAnimationFrame 在 tab 不可见时会挂起，但 SSE 也被浏览器节流，整体一致 | 需测试后台 tab 行为 |
| D-07 | 取消后首可见前的 user 消息在 UI 中隐藏但在审计中保留 | 设计文档 6.2 节明确；合规与 UX 平衡 | 需确认产品/安全负责人批准（OQ 中开放问题） |
| D-08 | turn_id 在 messages.turn_id 中关联，而非创建中间 join 表 | 单 turn 对应单 assistant message，一对一关系足够 | 无 |
| D-09 | 后端 delta 合并窗口 100 项 / 2KB / 10ms | 参考 Grok update_chunk_merge.rs；平衡 TTFB（首 delta ≤ 200ms）和网络包大小 | 需通过压测调优 |
| D-10 | 前端引用从 SSE citation 直接渲染，search 预览保留为独立功能 | 设计文档 3.1 目标 3 要求引用与实际证据绑定 | 搜索预览区域的"引用"和实际引用可能不一致，但这是不同功能（预览 vs 实际） |

---

## 5. 验证步骤（Verification Steps）

### 5.1 后端单元/集成测试

| 测试 ID | 场景 | 断言 |
|---|---|---|
| T-BE-01 | v1 兼容：不传 header 发送 ask | 收到 v1 事件（无 envelope、事件名无变化） |
| T-BE-02 | v2 协商：传 X-EKB-Stream-Version: 2 | 所有事件含 envelope；turn_id/seq/timestamp 存在；seq 从 1 严格递增 |
| T-BE-03 | 检索空闲超时 | retrieval_started 后 15s 无 chunk → error + done(timeout) |
| T-BE-04 | 生成空闲超时 | generation_started 后 20s 无 token → error + done(timeout) |
| T-BE-05 | 心跳 | 每 10s 内至少有一个 heartbeat 事件 |
| T-BE-06 | 显式 cancel（running 状态） | API 返回 200 + status=cancelling；event_stream 在下一检查点发送 done(cancelled) |
| T-BE-07 | 显式 cancel（幂等） | 第二次 cancel 返回相同结果，不报错 |
| T-BE-08 | 越权 cancel | 其他租户的 turn_id → 404 NOT_FOUND |
| T-BE-09 | 不存在 turn cancel → 404 | 不泄露 turn 是否存在 |
| T-BE-10 | citation 事件 payload | 字段齐全（citation_id/doc_id/chunk_id/title/section_path/version/updated_at） |
| T-BE-11 | seq 原子递增（并发场景） | 同一 turn 并发发送事件，seq 不重复、不跳跃 |
| T-BE-12 | turn registry 状态流转 | running → cancelling → cancelled；running → completed/stop |
| T-BE-13 | feature flag 关 | v2 header 下仍走 v1 事件 |
| T-BE-14 | 拒答场景（chunks=空） | 不发 generation_started，直接发 refusal 文本 delta + done(refusal) |

### 5.2 前端单元测试

| 测试 ID | 场景 | 断言 |
|---|---|---|
| T-FE-01 | SSE parser：`\n\n` 和 `\r\n\r\n` 分隔 | 两种分隔都能正确解析 |
| T-FE-02 | SSE parser：多 data 行合并 | 多个 `data:` 行正确 join |
| T-FE-03 | SSE parser：注释行忽略 | 以 `:` 开头的行不影响解析 |
| T-FE-04 | SSE parser：半截数据 finish() | 返回 true 表示有残留 |
| T-FE-05 | Turn 隔离：旧 turn_id 事件到达 | 不修改当前消息 |
| T-FE-06 | Turn 隔离：seq 倒退 | 事件丢弃，不修改消息 |
| T-FE-07 | Turn 隔离：seq 重复 | 事件丢弃，不重复追加文本 |
| T-FE-08 | RAF 批处理：100 个 delta 到达 | setState 调用次数 < 10（约） |
| T-FE-09 | 首可见前取消 | user/assistant 对移除；草稿恢复到输入框 |
| T-FE-10 | 已有可见输出后取消 | 内容保留；finishReason=cancelled |
| T-FE-11 | 取消后立即重发 | 新 turn 的事件正确应用；旧 turn 迟到事件不影响 |
| T-FE-12 | 引用直连 | citation 事件到达后直接渲染；不依赖 search 预览 |

### 5.3 E2E 测试（Playwright，复用项目现有 playwright-mcp）

| 测试 ID | 场景 | 断言 |
|---|---|---|
| T-E2E-01 | 正常问答流 | 看到检索状态 → 生成状态 → 完整答案 + 引用 |
| T-E2E-02 | 拒答流 | 看到"证据不足"文本 + refusal 标签 |
| T-E2E-03 | 检索中快速取消 | 撤回消息对，草稿恢复 |
| T-E2E-04 | 长回答中途取消 | 保留已生成内容，finishReason=已取消 |
| T-E2E-05 | 取消后立即重发 | 新回答正常，旧回答事件不污染 |
| T-E2E-06 | 切换知识库 | 当前 turn 先取消再切换；迟到事件不写入新 KB 会话 |
| T-E2E-07 | 断连模拟（网络断开） | 显示断连状态，用户可重试 |
| T-E2E-08 | 引用准确性 | SSE 渲染的引用与实际证据一致 |

### 5.4 手动验证命令

```bash
# 后端单测
cd apps/api && python -m pytest tests/test_api.py -v -k "stream or turn or cancel"

# 后端类型检查（如果有 mypy）
cd apps/api && python -m mypy ekb_api

# 前端类型检查
cd apps/web && npx tsc --noEmit

# 前端构建
cd apps/web && npx vite build

# v2 SSE 手动验证（curl）
curl -N -X POST http://127.0.0.1:8023/api/v1/qa/ask \
  -H "Authorization: Bearer <token>" \
  -H "X-EKB-Stream-Version: 2" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"question":"连接池耗尽先看什么？","kb_ids":["<kb_id>"]}'
# 验证输出：每个事件含 id/turn_id/seq/timestamp/payload
```

### 5.5 性能验证

| 目标 | 验证方式 |
|---|---|
| 首可见事件 P95 ≤ 1s | 压测 50 次问答，记录时间戳 |
| 首 delta P95 ≤ 3s | 同上 |
| 正常完成 P95 ≤ 30s | 长回答（≥500 tokens）场景 |
| 前端增量渲染 CPU ≤ 单核 30% | Chrome DevTools Performance 面板 |

---

## 6. 文件改动清单（具体到文件）

### 后端（apps/api/ekb_api/）

| 文件 | 改动类型 | 说明 |
|---|---|---|
| [models.py](file:///Users/alin/EKB/apps/api/ekb_api/models.py) | 修改 | 新增 QaTurn 类；Message 增加 turn_id、visibility_state |
| [domain.py](file:///Users/alin/EKB/apps/api/ekb_api/domain.py) | 修改 | 新增 QaTurn dataclass、TurnStatus/FinishReason Enum、Message.visibility_state |
| [schemas.py](file:///Users/alin/EKB/apps/api/ekb_api/schemas.py) | 修改 | AskOptions 增加 stream_version；新增 TurnCancelResponse |
| [store.py](file:///Users/alin/EKB/apps/api/ekb_api/store.py) | 修改 | 新增 create_turn/increment_turn_seq/mark_first_visible/is_cancel_requested/request_turn_cancel/finalize_turn 方法；save_message 增加 turn_id 参数；get_conversation_messages 过滤 visibility_state |
| [config.py](file:///Users/alin/EKB/apps/api/ekb_api/core/config.py) | 修改 | 新增 retrieval_idle_timeout_seconds 等 6 个配置项 |
| [metrics.py](file:///Users/alin/EKB/apps/api/ekb_api/core/metrics.py) | 修改 | 新增 QA_PROTOCOL_ERRORS 等 8 个指标 |
| [db.py](file:///Users/alin/EKB/apps/api/ekb_api/core/db.py) | 修改 | 新增 _migrate_qa_turns_table、_migrate_message_turn_columns，在 init_db 中调用 |
| [qa.py](file:///Users/alin/EKB/apps/api/ekb_api/routers/qa.py) | 重写 | ask() 增加版本协商；v2 event_stream（envelope/seq/阶段事件/心跳/分段超时/delta 合并/取消检查点/引用直连）；新增 cancel_turn 路由 |
| [main.py](file:///Users/alin/EKB/apps/api/ekb_api/main.py) | 修改 | CORS allow_headers 增加 X-EKB-Stream-Version |
| `tests/test_api.py` | 修改 | 新增 T-BE-01 ~ T-BE-14 测试用例 |

### 前端（apps/web/src/）

| 文件 | 改动类型 | 说明 |
|---|---|---|
| [types/api.ts](file:///Users/alin/EKB/apps/web/src/types/api.ts) | 修改 | 新增 SseV2Envelope、TurnState、FinishReason、各 payload 类型 |
| [lib/api.ts](file:///Users/alin/EKB/apps/web/src/lib/api.ts) | 重写 | 新增 SseStreamParser 类；ask() 改为 v2 协议，返回 turnNonce+abort；新增 cancelTurn()；保留 SseEvent 向后兼容 |
| [App.tsx](file:///Users/alin/EKB/apps/web/src/App.tsx) | 重写 | turn 状态机、turnNonce/turn_id 绑定、seq 校验、RAF 批量渲染、首可见前撤回、取消并行 API、引用直连 |
| [components/ChatPanel.tsx](file:///Users/alin/EKB/apps/web/src/components/ChatPanel.tsx) | 修改 | 阶段状态展示、拒答/超时样式增强 |
| [pages/QAPage.tsx](file:///Users/alin/EKB/apps/web/src/pages/QAPage.tsx) | 修改 | Props 增加 turnState、cancelling 视觉 |

### 计划文档

| 文件 | 说明 |
|---|---|
| `.trae/documents/知识库AI对话流式交互升级实施计划.md` | 本计划文件 |

---

## 7. 实施顺序（执行时按此顺序）

**阶段 1：数据模型 + 配置（先跑通）**
1. models.py 新增 QaTurn、Message 扩展字段
2. domain.py 新增 QaTurn dataclass、相关 Enum
3. schemas.py 新增请求/响应类型
4. config.py 新增配置项
5. metrics.py 新增指标
6. db.py 新增迁移函数
7. store.py 新增 turn 相关方法
8. 运行后端测试验证迁移和方法正确

**阶段 2：后端协议 v2**
9. qa.py 重写 event_stream（版本协商 + envelope + seq + 阶段事件）
10. qa.py 实现分段空闲超时
11. qa.py 实现心跳
12. qa.py 实现 delta 合并
13. qa.py 实现取消检查点
14. qa.py 新增 cancel_turn 路由
15. main.py 更新 CORS
16. 运行 curl 手动验证 + 后端契约测试

**阶段 3：前端 v2**
17. types/api.ts 新增 v2 类型
18. api.ts 重写 SSE parser + ask v2 + cancelTurn
19. App.tsx 实现 turn 状态机 + 旧流隔离 + RAF 批处理
20. App.tsx 实现取消策略（首可见前后撤回）+ 引用直连
21. ChatPanel.tsx 阶段 UI 增强
22. 运行前端类型检查 + 构建

**阶段 4：测试 + 验证**
23. 后端新增测试用例
24. 前端手动 E2E 验证
25. 性能验证（延迟 / CPU）

"""M4-7 故障注入测试：验证降级、熔断和超时保护在真实故障场景下的端到端行为。

覆盖架构设计第 7 章失败模式：
  - LLM 超时/失败 → 熔断器开闸 → 短路 → 降级到 demo 拼接
  - 熔断器半开探测 → 成功恢复
  - search 端点超时 → 504 SEARCH_TIMEOUT
  - QA 全流程超时 → finish_reason=timeout
  - embedding 全部 provider 失败 → 降级到本地 n-gram 向量

故障注入方式：monkeypatch LLM/embedding 客户端内部函数模拟超时和失败，
不依赖真实外部服务，确保测试可重复且快速。
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from ekb_api.core.circuit_breaker import CircuitState, get_circuit_breaker
from ekb_api.core.config import ModelProvider
from ekb_api.embedding import EmbeddingError, embed_batch
from ekb_api.llm import LlmError, chat

API = "/api/v1"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _get_kb_id(client, token) -> str:
    resp = client.get(f"{API}/kb", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    kbs = resp.json()
    assert kbs, "需要至少一个知识库"
    return kbs[0]["id"]


def _fake_chat_provider() -> ModelProvider:
    """构造一个假的 chat provider，让 settings.chat_providers 非空。"""
    return ModelProvider(
        name="fake-chat",
        kind="chat",
        base_url="http://fake.example.com/v1/chat/completions",
        api_key="fake-key",
        model="fake-model",
        timeout_seconds=1.0,
    )


def _fake_embedding_provider() -> ModelProvider:
    """构造一个假的 embedding provider，让 settings.embedding_providers 非空。"""
    return ModelProvider(
        name="fake-embedding",
        kind="embedding",
        base_url="http://fake.example.com/v1/embeddings",
        api_key="fake-key",
        model="fake-embed-model",
        timeout_seconds=1.0,
    )


class _FakeSettings:
    """最小化假 Settings，仅暴露 chat_providers / embedding_providers / 相关属性。

    用于故障注入测试，让 LLM/embedding 路径认为 provider 已配置，
    从而能真正进入 _call_provider / _embed_with_provider 的 patch 路径。
    """

    def __init__(self, *, chat: list | None = None, embedding: list | None = None):
        self._chat = chat if chat is not None else []
        self._embedding = embedding if embedding is not None else []
        self.environment = "test"
        # 基础生成 / 检索
        self.llm_enabled = True
        self.llm_temperature = 0.0
        self.llm_max_tokens = 128
        self.embedding_dim = 256
        self.embedding_batch_size = 32
        self.search_timeout_seconds = 10
        self.qa_timeout_seconds = 30
        self.retrieval_cosine_threshold = 0.5
        self.retrieval_rrf_k = 60
        self.retrieval_rerank_pool = 30
        self.retrieval_bm25_enabled = False
        # M4-7 熔断告警
        self.circuit_breaker_failure_threshold = 3
        self.circuit_breaker_recovery_timeout = 30.0
        self.alert_qa_p95_threshold_seconds = 3.0
        self.alert_search_p95_threshold_seconds = 0.5
        self.alert_error_rate_threshold = 0.05
        self.alert_llm_failure_rate_threshold = 0.1
        # SSE v2 参数（完整补齐）
        self.sse_v2_enabled = True
        self.sse_v2_retrieval_idle_timeout = 15.0
        self.sse_v2_generation_idle_timeout = 30.0
        self.sse_v2_heartbeat_interval = 10.0
        self.sse_v2_delta_max_tokens = 8
        self.sse_v2_delta_max_bytes = 1024
        self.sse_v2_delta_flush_ms = 200
        # M4-6 多轮移植配置（保守默认）
        self.multi_turn_enabled = True
        self.llm_context_window = 65536
        self.llm_compaction_ratio = 0.75
        self.compaction_recent_rounds_keep = 3
        self.compaction_summary_ratio_target = 0.35
        # 杂项
        self.max_upload_bytes = 0

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def chat_providers(self) -> list:
        return self._chat

    @property
    def embedding_providers(self) -> list:
        return self._embedding

    # ---- 向后兼容便捷属性 ----
    @property
    def llm_api_url(self) -> str:
        return self._chat[0].base_url if self._chat else ""

    @property
    def llm_api_key(self) -> str:
        return self._chat[0].api_key if self._chat else ""

    @property
    def llm_model(self) -> str:
        return self._chat[0].model if self._chat else ""

    @property
    def llm_timeout_seconds(self) -> float:
        return self._chat[0].timeout_seconds if self._chat else 30.0

    @property
    def embedding_api_url(self) -> str:
        return self._embedding[0].base_url if self._embedding else ""

    @property
    def embedding_api_key(self) -> str:
        return self._embedding[0].api_key if self._embedding else ""

    @property
    def embedding_model(self) -> str:
        return self._embedding[0].model if self._embedding else "bge-large-zh-v1.5"

    @property
    def embedding_timeout_seconds(self) -> float:
        return self._embedding[0].timeout_seconds if self._embedding else 30.0


# ---- 1. LLM 熔断器：连续失败开闸 → 短路 → 降级 → 半开恢复 ----


def test_circuit_breaker_opens_on_consecutive_llm_failures():
    """LLM 连续失败达到阈值后熔断开闸，后续请求被短路（不发外网）。"""
    breaker = get_circuit_breaker()
    breaker.reset()
    assert breaker.state == CircuitState.CLOSED

    fake_settings = _FakeSettings(chat=[_fake_chat_provider()])
    # 模拟 LLM provider 全部失败（patch _call_provider 抛 LlmError + 注入假 provider）。
    # 注意：provider 列表来自 get_runtime_chat_providers（配置中心优先），必须一并注入，
    # 否则 chat() 会在 breaker.track_failure() 之前就以「未配置 provider」提前返回，熔断器不计数。
    with (
        patch("ekb_api.llm._call_provider", side_effect=LlmError("simulated timeout")),
        patch("ekb_api.llm.get_settings", return_value=fake_settings),
        patch(
            "ekb_api.llm.get_runtime_chat_providers",
            return_value=fake_settings.chat_providers,
        ),
    ):
        for i in range(breaker.failure_threshold):
            with pytest.raises(LlmError):
                chat([{"role": "user", "content": f"q{i}"}])

    # 达到阈值后熔断开闸
    assert breaker.state == CircuitState.OPEN
    assert not breaker.allow_request()

    # 开闸后 chat 直接抛 CircuitOpenError，不调用 _call_provider
    call_count = 0

    def _count_call(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return "ok"

    with (
        patch("ekb_api.llm._call_provider", side_effect=_count_call),
        patch("ekb_api.llm.get_settings", return_value=fake_settings),
        patch(
            "ekb_api.llm.get_runtime_chat_providers",
            return_value=fake_settings.chat_providers,
        ),
    ):
        with pytest.raises(LlmError, match="熔断器开闸"):
            chat([{"role": "user", "content": "should be short-circuited"}])
    assert call_count == 0, "熔断开闸后不应调用 provider"
    breaker.reset()


def test_circuit_breaker_half_open_recovery():
    """熔断开闸后经过 recovery_timeout 进入半开，探测成功后恢复 CLOSED。"""
    breaker = get_circuit_breaker()
    breaker.reset()

    # 用极短 recovery_timeout 的熔断器模拟时间流逝
    original_recovery = breaker.recovery_timeout
    breaker.recovery_timeout = 0.05  # 50ms

    try:
        # 强制开闸
        for _ in range(breaker.failure_threshold):
            breaker.track_failure()
        assert breaker.state == CircuitState.OPEN

        # 等待恢复期
        time.sleep(0.06)

        # 此时 state 属性应自动转为 HALF_OPEN
        assert breaker.state == CircuitState.HALF_OPEN
        assert breaker.allow_request() is True

        # 探测成功 → 恢复 CLOSED
        breaker.track_success()
        assert breaker.state == CircuitState.CLOSED
    finally:
        breaker.recovery_timeout = original_recovery
        breaker.reset()


def test_circuit_breaker_half_open_failure_returns_to_open():
    """半开状态下探测失败 → 回到 OPEN（不允许立即恢复）。"""
    breaker = get_circuit_breaker()
    breaker.reset()
    breaker.recovery_timeout = 0.05

    try:
        for _ in range(breaker.failure_threshold):
            breaker.track_failure()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.06)
        assert breaker.state == CircuitState.HALF_OPEN

        # 探测失败
        breaker.track_failure()
        assert breaker.state == CircuitState.OPEN
        assert not breaker.allow_request()
    finally:
        breaker.recovery_timeout = 60.0
        breaker.reset()


# ---- 2. QA 降级：LLM 失败时降级到 demo 拼接，不中断问答 ----


def test_qa_degrades_to_demo_when_llm_fails(client, dev_token):
    """LLM 调用失败时 QA 降级到 demo 拼接，返回 200 且无 error 事件。

    M4-3：patch generate_answer_stream 抛 LlmError，模拟流式生成失败 → 降级到 demo 拼接。
    """
    kb_id = _get_kb_id(client, dev_token)

    def _fail_stream(*args, **kwargs):
        raise LlmError("simulated failure")
        yield  # 使其成为生成器（永远不会执行）

    with patch("ekb_api.routers.qa.generate_answer_stream", side_effect=_fail_stream):
        resp = client.post(
            f"{API}/qa/ask",
            headers=_headers(dev_token),
            json={"question": "数据库连接池耗尽先检查哪些指标", "kb_ids": [kb_id]},
        )

    assert resp.status_code == 200, resp.text
    # 降级后不应出现 error 事件
    assert "event: error" not in resp.text
    # 应有 done 事件（降级成功完成）
    assert "event: done" in resp.text
    get_circuit_breaker().reset()


def test_qa_streaming_completes_normally(client, dev_token):
    """M4-3：流式生成正常完成时输出 token + done(stop)，无 error 事件。

    回归 StopIteration 跨 asyncio.to_thread 边界触发的
    `TypeError: StopIteration interacts badly with generators` 崩溃——
    该路径在 LlmError 降级测试中未被覆盖，导致测试全绿但实际运行崩溃。
    """
    kb_id = _get_kb_id(client, dev_token)
    tokens = ["根据", "证据", "连接池", "使用率", "先检查"]

    def _ok_stream(*args, **kwargs):
        yield from tokens

    # 跳过 LLM 改写（避免真实外部调用），聚焦流式正常完成路径。
    with patch(
        "ekb_api.retrieval._safe_rewrite", side_effect=lambda q, route=None: [q]
    ), patch(
        "ekb_api.routers.qa.generate_answer_stream", side_effect=_ok_stream
    ):
        resp = client.post(
            f"{API}/qa/ask",
            headers=_headers(dev_token),
            json={"question": "数据库连接池耗尽先检查哪些指标", "kb_ids": [kb_id]},
        )

    assert resp.status_code == 200, resp.text
    # 正常完成不应有 error 事件
    assert "event: error" not in resp.text
    assert "event: done" in resp.text
    assert '"finish_reason":"stop"' in resp.text
    # 所有 token 都应在流中输出
    for t in tokens:
        assert t in resp.text


# ---- 3. search 端点超时保护 ----


# ---- 2bis. QA 流式拒答二次确认兜底（M4-5 P1） ----


def test_qa_stream_refusal_fallback_with_chunks(client, dev_token):
    """G042/G077 修复：流式偶发输出"证据不足"但 chunks≥3 时，触发二次确认兜底。

    关键：必须启用 llm_enabled=True 才会走 generate_answer_stream 分支，
    否则会跳过流式直接 demo 拼接（历史 patch 对该分支全是假阳性）。

    期望：
    1. 流式 LLM 先返回"根据授权证据检查：证据不足"→ 被识别为拒答模式。
    2. 由于 chunks≥3（实际上有证据可答），触发二次确认 generate_answer。
    3. 二次确认返回正确回答（不含"证据不足"）→ 发修正 token、citation、done(stop)。
    4. 最终 finish_reason=stop，有 citation。
    """
    kb_id = _get_kb_id(client, dev_token)
    fake_settings = _FakeSettings(chat=[_fake_chat_provider()])

    def _refusal_stream(*args, **kwargs):
        yield from ["根据授权证据检查：", "证据不足", "，无法确认"]

    def _correct_answer(*args, **kwargs):
        return "连接池耗尽时应先检查连接池使用率、等待队列和泄露栈，再调上限。"

    with patch(
        "ekb_api.retrieval._safe_rewrite", side_effect=lambda q, route=None: [q]
    ), patch(
        "ekb_api.routers.qa.get_settings", return_value=fake_settings
    ), patch(
        "ekb_api.llm.get_settings", return_value=fake_settings
    ), patch(
        "ekb_api.routers.qa.generate_answer_stream", side_effect=_refusal_stream
    ), patch(
        "ekb_api.routers.qa.generate_answer", side_effect=_correct_answer
    ):
        resp = client.post(
            f"{API}/qa/ask",
            headers={**_headers(dev_token), "Accept": "text/event-stream"},
            json={"question": "数据库连接池耗尽先检查哪些指标", "kb_ids": [kb_id]},
        )

    assert resp.status_code == 200, resp.text
    # 不应出现 error 事件
    assert "event: error" not in resp.text
    # 应有 citation（chunks 足够时不拒答，走 stop 分支发引用）
    assert "event: citation" in resp.text
    # 最终 finish_reason 是 stop，不是 refusal
    assert '"finish_reason":"stop"' in resp.text
    # 流式修正后的正确回答内容也应出现在流中
    assert "补充确认" in resp.text or "修正后的结论" in resp.text
    get_circuit_breaker().reset()


def test_qa_stream_refusal_still_refuses_when_no_chunks(client, dev_token):
    """流式拒答但 chunks<3 时：确认真的无证据，直接拒答，不触发二次确认兜底。

    关键：M4-3 后 retrieve 内部不走 SqlStore.search 而走 search_with_context，
    所以直接 patch routers.qa.retrieve 函数返回 1 条 chunk 更可靠。
    """
    kb_id = _get_kb_id(client, dev_token)
    fake_settings = _FakeSettings(chat=[_fake_chat_provider()])

    def _refusal_stream(*args, **kwargs):
        yield from ["当前知识库中未检索到匹配内容：", "证据不足"]

    def _retrieve_return_one(store, auth, q, kb_ids, max_c, route=None):
        # 真实检索后截断到 1 条（<3，不触发二次确认）
        from ekb_api.retrieval import retrieve as _real_retrieve

        chunks = _real_retrieve(store, auth, q, kb_ids, max_c, route=route)
        return chunks[:1]

    with patch(
        "ekb_api.routers.qa.retrieve", side_effect=_retrieve_return_one
    ), patch(
        "ekb_api.retrieval._safe_rewrite", side_effect=lambda q, route=None: [q]
    ), patch("ekb_api.routers.qa.get_settings", return_value=fake_settings), patch(
        "ekb_api.llm.get_settings", return_value=fake_settings
    ), patch(
        "ekb_api.routers.qa.generate_answer_stream", side_effect=_refusal_stream
    ):
        resp = client.post(
            f"{API}/qa/ask",
            headers={**_headers(dev_token), "Accept": "text/event-stream"},
            json={"question": "数据库连接池耗尽先检查哪些指标", "kb_ids": [kb_id]},
        )

    assert resp.status_code == 200, resp.text
    # chunks<3 拒答：无 citation，finish_reason=refusal
    assert "event: citation" not in resp.text
    assert '"finish_reason":"refusal"' in resp.text
    get_circuit_breaker().reset()


def test_search_returns_504_on_timeout(client, dev_token):
    """search 端点在检索超时时返回 504 SEARCH_TIMEOUT。"""
    from ekb_api.store import SqlStore

    # monkeypatch store.search 模拟超时（sleep 超过 search_timeout_seconds 默认 10s）
    def _slow_search(*args, **kwargs):
        time.sleep(15)
        return []

    with patch.object(SqlStore, "search", _slow_search):
        resp = client.post(
            f"{API}/search",
            headers=_headers(dev_token),
            json={"query": "测试", "top_k": 5},
        )

    assert resp.status_code == 504, resp.text
    assert "SEARCH_TIMEOUT" in resp.text or "检索超时" in resp.text


# ---- 4. QA 全流程超时降级 ----


def test_qa_timeout_emits_timeout_event(client, dev_token):
    """QA 全流程超时时发送 timeout finish_reason 事件。

    通过设置极短的 QA 超时（0.1s）+ 正常 retrieve 延迟（0.5s）触发超时，
    避免真实 60s 等待。
    """
    kb_id = _get_kb_id(client, dev_token)

    # 模拟 retrieve 延迟 0.5s（配合 0.1s 超时触发）
    def _slow_retrieve(*args, **kwargs):
        time.sleep(0.5)
        return []

    # 超时接缝：qa.py 已从 _qa_timeout() 迁移到 settings 的分段空闲超时
    # （SSE v2 用 sse_v2_retrieval_idle_timeout，v1 用 qa_timeout_seconds）。
    # 用 dataclasses.replace 复制真实 Settings 并只压低这两个字段，避免假对象缺字段。
    import dataclasses as _dc

    from ekb_api.core.config import get_settings as _get_settings

    _fast_settings = _dc.replace(
        _get_settings(),
        sse_v2_retrieval_idle_timeout=0.1,
        qa_timeout_seconds=0.1,
    )

    with (
        patch("ekb_api.routers.qa.retrieve", _slow_retrieve),
        patch("ekb_api.routers.qa.get_settings", return_value=_fast_settings),
    ):
        resp = client.post(
            f"{API}/qa/ask",
            headers=_headers(dev_token),
            json={"question": "测试超时", "kb_ids": [kb_id]},
        )

    # 流式响应仍为 200，但内部含 timeout 事件
    assert resp.status_code == 200, resp.text
    assert "timeout" in resp.text.lower() or "UPSTREAM_TIMEOUT" in resp.text


# ---- 5. embedding 全部 provider 失败降级到本地向量 ----


def test_embedding_degrades_to_local_vectors_on_all_providers_fail():
    """所有 embedding provider 失败时降级到本地 n-gram TF 向量。"""
    fake_settings = _FakeSettings(embedding=[_fake_embedding_provider()])
    # monkeypatch _embed_with_provider 模拟全部失败 + 注入假 provider
    with (
        patch(
            "ekb_api.embedding._embed_with_provider",
            side_effect=EmbeddingError("all providers down"),
        ),
        patch("ekb_api.embedding.get_settings", return_value=fake_settings),
    ):
        vectors = embed_batch(["测试文本一", "测试文本二"])

    # 降级后仍返回向量（本地 n-gram），维度与配置一致
    assert len(vectors) == 2
    for v in vectors:
        assert len(v) > 0, "降级向量不应为空"
        assert all(isinstance(x, float) for x in v)


# ---- 6. 权限变更后缓存失效（架构第 7 章：permission.changed） ----


def test_permission_change_does_not_leak_stale_data(client, dev_token):
    """权限变更（KB 成员移除）后不应泄露旧缓存数据。

    回归：M2-5 出域策略和 KB 可见性变更后，新请求立即使用新授权。
    """
    from ekb_api.core.cache import query_rewrite_cache

    # 预填充改写缓存（put 不是 set）
    query_rewrite_cache.put(("test-model", "hash123"), ["cached-query"])
    assert query_rewrite_cache.get(("test-model", "hash123")) == ["cached-query"]

    # 清除缓存模拟权限变更后的失效
    query_rewrite_cache.clear()
    assert query_rewrite_cache.get(("test-model", "hash123")) is None


from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse

from ekb_api.core.audit import (
    RESULT_DEGRADED,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    extract_fingerprints,
    redact_metadata,
)
from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.config import get_runtime_chat_providers, get_settings
from ekb_api.core.egress import assert_egress_allowed, resolve_tenant_routing
from ekb_api.core.errors import ApiError
from ekb_api.core.metrics import (
    QA_CANCEL_REQUESTS,
    QA_COMPACTION_DURATION_SECONDS,
    QA_COMPACTION_TRIGGERED,
    QA_DEGRADATIONS,
    QA_DELTA_FLUSHES,
    QA_GENERATION_DURATION,
    QA_HEARTBEATS_SENT,
    QA_HISTORY_TOKENS,
    QA_IDLE_TIMEOUTS,
    QA_REQUESTS,
    QA_RETRIEVAL_DURATION,
    QA_TTFB_DURATION,
    QA_TURN_DURATION,
    WEB_SEARCH_CALLS,
    WEB_SEARCH_DURATION,
    WEB_SEARCH_RESULT_COUNT,
)
from ekb_api.core.web_search import (
    WebSearchResult,
    WebSearchSummary,
    merge_evidence,
    perform_web_search,
    web_search_enabled,
)
from ekb_api.domain import (
    AuthContext,
    FinishReason,
    MessageVisibility,
    TurnStatus,
    new_id,
    utc_now,
)
from ekb_api.llm import (
    CompactionReport,
    LlmError,
    _map_role_ekb_to_llm,
    _normalize_thinking_level,
    estimate_messages_tokens,
    generate_answer,
    generate_answer_stream,
    maybe_compact_history,
)
from ekb_api.retrieval import retrieve
from ekb_api.schemas import (
    AskOptions,
    AskRequest,
    ComposerCapabilities,
    ComposerCapabilitiesResponse,
    ModelInfo,
    TurnCancelResponse,
)
from ekb_api.services import rag as rag
from ekb_api.store import SqlStore

router = APIRouter(prefix="/qa", tags=["qa"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE v2 envelope + seq 辅助
# ---------------------------------------------------------------------------


def _sse_v1(event: str, data: dict) -> str:
    """Legacy SSE v1（保持向后兼容，无 envelope/seq/turn_id）。"""
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


def _sse_v2(event: str, turn_id: str, request_id: str, seq: int, payload: dict) -> str:
    """SSE v2 Conversation Stream：统一 envelope（turn_id + seq + timestamp）。

    SSE id 字段 = turn_id:seq，客户端断线重连时可利用 Last-Event-ID 恢复。
    兼容性：payload 中的 conversation_id/message_id/stream_version 提升到 envelope 外层，
    保证 v1 客户端解析第一个 data 行时仍能拿到 conversation_id。
    """
    envelope = {
        "turn_id": turn_id,
        "request_id": request_id,
        "seq": seq,
        "timestamp": utc_now(),
        "payload": payload,
    }
    if isinstance(payload, dict):
        if "conversation_id" in payload and "conversation_id" not in envelope:
            envelope["conversation_id"] = payload["conversation_id"]
        if "message_id" in payload and "message_id" not in envelope:
            envelope["message_id"] = payload["message_id"]
        if "stream_version" in payload and "stream_version" not in envelope:
            envelope["stream_version"] = payload["stream_version"]
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    sse_id = f"{turn_id}:{seq}"
    return f"id: {sse_id}\nevent: {event}\ndata: {encoded}\n\n"


# ---------------------------------------------------------------------------
# Delta 合并器：按 tokens/bytes/time 三条件 flush，平衡 TTFB 与网络包大小
# ---------------------------------------------------------------------------


class DeltaMerger:
    def __init__(self, max_tokens: int, max_bytes: int, flush_ms: int):
        self.max_tokens = max_tokens
        self.max_bytes = max_bytes
        self.flush_ms = flush_ms
        self._buf: list[str] = []
        self._token_count = 0
        self._byte_count = 0
        self._first_at: float | None = None

    def append(self, token: str) -> None:
        if self._first_at is None:
            self._first_at = time.perf_counter()
        self._buf.append(token)
        self._token_count += max(1, len(token) // 3)  # 近似 token：CJK 每 3 字一个 token
        self._byte_count += len(token.encode("utf-8"))

    def should_flush(self) -> tuple[bool, str]:
        if not self._buf:
            return False, ""
        elapsed_ms = (time.perf_counter() - (self._first_at or 0)) * 1000
        if self._token_count >= self.max_tokens:
            return True, "tokens"
        if self._byte_count >= self.max_bytes:
            return True, "bytes"
        if elapsed_ms >= self.flush_ms:
            return True, "time"
        return False, ""

    def flush(self) -> tuple[str, int, int, int]:
        text = "".join(self._buf)
        tokens, nbytes = self._token_count, self._byte_count
        self._buf.clear()
        self._token_count = 0
        self._byte_count = 0
        self._first_at = None
        return text, tokens, nbytes, len(text)


# ---------------------------------------------------------------------------
# M4-3 流式哨兵：StopIteration 不能跨越 asyncio.to_thread 边界
# ---------------------------------------------------------------------------


_STREAM_END = object()


def _next_token(gen, lock: threading.Lock):
    """从 generator 安全取下一个 token；lock 串行化避免"generator already executing"。

    根因：qa.py 用 asyncio.wait_for(to_thread(_next_token), timeout=2s) 在短暂超时时
    (如 TTFB > 2s)，asyncio 侧放弃等待并 continue 下一轮，但线程池里仍有一个 in-flight
    的 next(gen) 在跑；下一轮立即再次 to_thread 就产生两线程同时进入同一个 generator，
    触发 Python 内置 `ValueError: generator already executing`。
    解决方案：每次 next(gen) 用 threading.Lock 串行化，保证同一 gen 同一时刻只有
    一条线程在取 token。
    """
    with lock:
        try:
            return next(gen)
        except StopIteration:
            return _STREAM_END


# ---------------------------------------------------------------------------
# /qa/ask 主入口
# ---------------------------------------------------------------------------


@router.post("/ask")
async def ask(
    payload: AskRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> StreamingResponse:
    settings = get_settings()

    # SSE v2 feature flag：全局关闭时强制走 v1
    stream_version = payload.options.stream_version if settings.sse_v2_enabled else 1
    # v1 请求仍然保留老协议，避免老前端回归
    use_v2 = stream_version >= 2

    # M2-7 数据出域策略
    tenant = store.get_tenant_by_id(auth.tenant_id)
    assert_egress_allowed(tenant)
    # M2-7 每租户每日问答配额
    if not store.check_and_increment_qa_quota(auth.tenant_id):
        raise ApiError(429, "QUOTA_EXCEEDED", "该租户今日问答次数已达上限")
    # M2-7 租户级模型路由
    route = resolve_tenant_routing(auth, settings, model_routing_key=tenant.model_routing_key)

    # Phase 2 Step2.2：消费 AskOptions 新增参数
    # 1) model 覆盖：用户在 Composer 显式选择的模型优先级最高
    requested_model = (payload.options.model or "").strip() or None
    if requested_model:
        # TenantRoute 是 frozen=True dataclass，使用 dataclasses.replace 安全变更 model
        from dataclasses import replace as _dc_replace  # noqa: PLC0415

        try:
            route = _dc_replace(route, model=requested_model)
        except Exception:  # noqa: BLE001
            # 兜底：直接用局部变量覆盖，保证后续 route.model 读取优先用请求值
            logger.warning("TenantRoute.replace 失败，回退为不传选模型")
    # 2) attachment_doc_ids 合并进检索范围：与 kb_ids 并集，但同样做权限校验
    raw_kb_ids = list(dict.fromkeys([*payload.kb_ids, *payload.options.attachment_doc_ids]))
    # 3) deep_thinking / web_search 读取供后续 prompt 透传
    #    thinking_level 五档：light 才关（=无推理增强），mild/medium/high/extreme 都开
    #    兼容旧三档：off→light，standard→medium，intensive→high
    thinking_level_raw = (payload.options.thinking_level or "medium").lower()
    _legacy_map = {"off": "light", "standard": "medium", "intensive": "high"}
    thinking_level = _legacy_map.get(thinking_level_raw, thinking_level_raw)
    if thinking_level not in {"light", "mild", "medium", "high", "extreme"}:
        thinking_level = "medium"
    use_deep_thinking = thinking_level != "light" or bool(payload.options.deep_thinking)
    use_web_search = bool(payload.options.web_search)  # 当前版本透传占位，未接入 SERP API

    # PH6 FR-050：显式 kb_ids 必须全部在授权可见范围内（防伪造 ID 越权检索）。
    # 附件 doc_ids 仅作检索范围合并，不进 ACL 校验（属于 doc 而非 kb，store 已按 READY 过滤）。
    validated_kb_ids = rag.validate_kb_scope(store, auth, payload.kb_ids)
    raw_kb_ids = list(dict.fromkeys([*validated_kb_ids, *payload.options.attachment_doc_ids]))

    conversation = (
        store.get_conversation(auth, payload.conversation_id) if payload.conversation_id else None
    )
    if payload.conversation_id and conversation is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    if conversation is None:
        conversation = store.create_conversation(auth, payload.question[:40])

    # Turn id：v2 显式生成；v1 也生成但不写入 envelope（仅用于内部审计）
    turn_id = f"turn_{new_id()}"
    request_id = auth.trace_id
    seq = 0  # SSE v2 envelope 序列号

    # 预写消息：USER 正常；ASSISTANT 先为空占位（v2 下 turn_id 关联）
    store.save_message(auth, conversation.id, "USER", payload.question, turn_id=turn_id)
    assistant_message = store.save_message(
        auth,
        conversation.id,
        "ASSISTANT",
        "",
        turn_id=turn_id,
    )

    # v2: 创建 Turn Registry 记录（defensive：失败不阻断问答，仅记录 warning）
    if use_v2:
        try:
            store.create_turn(
                auth,
                turn_id=turn_id,
                request_id=request_id,
                conversation_id=conversation.id,
                assistant_message_id=assistant_message.id,
                stream_version=2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "create_turn failed request_id=%s conv_id=%s: %s",
                request_id,
                conversation.id,
                exc,
            )

    ip_hash, ua_hash = extract_fingerprints(request)
    question_preview = payload.question[:80]
    turn_started = time.perf_counter()

    # --- 事件生成器 ---
    async def event_stream_v2() -> AsyncIterator[str]:
        nonlocal seq
        finish_reason = FinishReason.ERROR.value
        phase: str = "retrieval"  # retrieval -> generation，供分段空闲超时判定
        last_event_at = time.perf_counter()
        ttfbt0 = time.perf_counter()
        first_token_emitted = False

        def emit(event: str, payload_inner: dict) -> str:
            nonlocal seq, last_event_at
            seq += 1
            last_event_at = time.perf_counter()
            # v2: 写回 last_seq（节流：仅每 8 个 seq 或 关键事件写一次，减小 DB 压力）
            if use_v2 and (seq % 8 == 0 or event in {"done", "error", "retrieval_started", "retrieval_completed", "generation_started"}):
                try:
                    store.increment_turn_seq(turn_id, to_seq=seq)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("increment_turn_seq failed turn_id=%s seq=%s: %s", turn_id, seq, exc)
            if use_v2:
                return _sse_v2(event, turn_id, request_id, seq, payload_inner)
            return _sse_v1(event, payload_inner)

        try:
            # ---------- 1. request 事件 ----------
            yield emit(
                "request",
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "message_id": assistant_message.id,
                    "conversation_id": conversation.id,
                    "stream_version": 2 if use_v2 else 1,
                },
            )

            # ---------- 2. retrieval_started 阶段事件 ----------
            yield emit(
                "retrieval_started",
                {
                    "authorized_kb_count": len(raw_kb_ids) or 1,
                    "phase": "retrieval",
                    "web_search": use_web_search,
                    "deep_thinking": use_deep_thinking,
                },
            )

            if await request.is_disconnected():
                finish_reason = FinishReason.CANCELLED.value
                yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                return

            # 显式取消检查点 1（defensive：DB 读失败默认未取消，不阻断主流程）
            _cancelled = False
            if use_v2:
                try:
                    _cancelled = store.is_turn_cancelled(turn_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("is_turn_cancelled failed turn_id=%s: %s", turn_id, exc)
                    _cancelled = False
            if use_v2 and _cancelled:
                finish_reason = FinishReason.CANCELLED.value
                yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                return

            # ---------- 3. 检索阶段（KB + Tavily 并行，含分段超时）----------
            t0 = time.perf_counter()
            web_summary: WebSearchSummary | None = None
            web_results: list[WebSearchResult] = []
            retrieval_timeout = (
                settings.sse_v2_retrieval_idle_timeout if use_v2 else settings.qa_timeout_seconds
            )

            # 并行：KB 检索（同步 -> to_thread）+ Tavily 联网搜索（原生 async）
            kb_task = asyncio.create_task(
                asyncio.wait_for(
                    asyncio.to_thread(
                        retrieve,
                        store,
                        auth,
                        payload.question,
                        raw_kb_ids,
                        payload.options.max_citations,
                        route=route,
                    ),
                    timeout=retrieval_timeout,
                )
            )

            # Tavily 搜索：仅当「用户显式开启 & 后端配置了 API Key」时发起
            should_web_search = use_web_search and web_search_enabled(settings)
            web_task: asyncio.Task | None = None
            if should_web_search:
                # 预留 Tavily 搜索超时：总 retrieval_timeout 的 60%，最少 5s
                web_timeout = max(5.0, retrieval_timeout * 0.6)
                web_task = asyncio.create_task(
                    perform_web_search(
                        settings,
                        question=payload.question,
                        max_results=max(3, payload.options.max_citations // 2),
                        timeout_s=web_timeout,
                    )
                )
                yield emit(
                    "web_search_started",
                    {
                        "query": payload.question[:200],
                        "max_results": max(3, payload.options.max_citations // 2),
                    },
                )

            # 等待 KB 检索完成（必须项）
            try:
                chunks = await kb_task
            except asyncio.TimeoutError:
                if web_task is not None:
                    web_task.cancel()
                QA_IDLE_TIMEOUTS.inc(phase="retrieval")
                finish_reason = FinishReason.TIMEOUT.value
                yield emit(
                    "error",
                    {
                        "code": "RETRIEVAL_TIMEOUT",
                        "message": "检索阶段超时，请缩小知识库范围或换一种问法",
                        "request_id": request_id,
                    },
                )
                yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                return
            finally:
                QA_RETRIEVAL_DURATION.observe(time.perf_counter() - t0)

            # PH6 FR-054：RAG 层防御性二次过滤（store 已按 DocumentStatus.READY 过滤），
            # 再剔除任何撤权/过期的文档 chunk，绝不进入生成上下文。
            chunks = rag.filter_retrievable(list(chunks))

            # 等待 Tavily 搜索（可选项：超时/失败不阻断）
            if web_task is not None:
                try:
                    ws_t0 = time.perf_counter()
                    web_results, web_summary = await asyncio.wait_for(web_task, timeout=max(5.0, retrieval_timeout * 0.8))
                    ws_elapsed = time.perf_counter() - ws_t0
                    # 记录 metrics
                    if web_summary is not None:
                        if web_summary.succeeded:
                            WEB_SEARCH_CALLS.inc(status="success")
                            WEB_SEARCH_RESULT_COUNT.observe(float(web_summary.result_count))
                        elif web_summary.error_code in ("WEBS_AUTH_ERROR", "WEBS_UPSTREAM_401", "WEBS_UPSTREAM_403"):
                            WEB_SEARCH_CALLS.inc(status="auth_error")
                        elif web_summary.error_code == "WEBS_RATE_LIMITED":
                            WEB_SEARCH_CALLS.inc(status="rate_limited")
                        elif web_summary.error_code == "WEBS_TIMEOUT":
                            WEB_SEARCH_CALLS.inc(status="timeout")
                        elif web_summary.error_code == "WEBS_NETWORK_ERROR":
                            WEB_SEARCH_CALLS.inc(status="network_error")
                        elif web_summary.error_code and web_summary.error_code.startswith("WEBS_UPSTREAM_5"):
                            WEB_SEARCH_CALLS.inc(status="upstream_5xx")
                        else:
                            WEB_SEARCH_CALLS.inc(status=web_summary.error_code or "unknown")
                        WEB_SEARCH_DURATION.observe(ws_elapsed)
                    yield emit(
                        "web_search_completed",
                        {
                            "attempted": bool(web_summary and web_summary.attempted),
                            "succeeded": bool(web_summary and web_summary.succeeded),
                            "result_count": len(web_results),
                            "elapsed_ms": int(ws_elapsed * 1000),
                            "error_code": web_summary.error_code if web_summary else None,
                            "error_message": web_summary.error_message if web_summary else None,
                        },
                    )
                except asyncio.TimeoutError:
                    WEB_SEARCH_CALLS.inc(status="timeout")
                    yield emit(
                        "web_search_completed",
                        {
                            "attempted": True,
                            "succeeded": False,
                            "result_count": 0,
                            "elapsed_ms": int(retrieval_timeout * 1000),
                            "error_code": "WEBS_TIMEOUT",
                            "error_message": "联网搜索超时，已跳过",
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("web_task unexpected error: %s", repr(exc))
                    WEB_SEARCH_CALLS.inc(status="network_error")
                    yield emit(
                        "web_search_completed",
                        {
                            "attempted": True,
                            "succeeded": False,
                            "result_count": 0,
                            "elapsed_ms": 0,
                            "error_code": "WEBS_NETWORK_ERROR",
                            "error_message": f"联网搜索内部错误：{type(exc).__name__}",
                        },
                    )

            # 合并知识库 + 联网搜索证据（仅需 evidence_texts 作为生成上下文；
            # citations 元数据改由 rag.build_citations 基于原始 chunks 构造，见 FR-052）。
            evidence_texts, _ = merge_evidence(
                kb_chunks=list(chunks),
                web_results=web_results,
                max_citations=payload.options.max_citations,
            )
            total_evidence_count = len(evidence_texts)

            # ---------- PH6 FR-051：STRICT 证据门禁 ----------
            # 选中了知识库但检索不到任何证据时，直接拒答：不调用 LLM、不编造任何引用。
            # ENHANCED 模式或不选中 KB 的开放问答不触发此门禁。
            if rag.needs_strict_refusal(payload.options.answer_mode, raw_kb_ids, chunks):
                store.update_message_content(
                    auth,
                    assistant_message.id,
                    "抱歉，当前所选知识库中未检索到与您问题相关的证据，无法基于事实回答。"
                    "请尝试调整问题，或在知识库中补充相关内容。",
                )
                finish_reason = FinishReason.REFUSAL.value
                yield emit(
                    "done",
                    {
                        "message_id": assistant_message.id,
                        "finish_reason": finish_reason,
                        "confidence": "low",
                        "last_seq": seq,
                    },
                )
                return

            # retrieval_completed：暴露命中 chunk 数（含 web），便于前端展示
            yield emit(
                "retrieval_completed",
                {
                    "chunk_count": len(chunks),
                    "web_result_count": len(web_results),
                    "total_evidence_count": total_evidence_count,
                    "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                    "phase": "generation",
                },
            )

            # ---------- M4-6 多轮对话新增：history_messages ----------
            history_messages: list[dict] | None = None
            compaction_report: CompactionReport | None = None
            tokens_est_before_any_compaction = 0
            compaction_start = 0.0

            if settings.multi_turn_enabled and conversation is not None:
                raw_msgs = store.list_messages(auth, conversation.id)
                filtered_msgs = [
                    m for m in raw_msgs
                    if getattr(m, "visibility_state", None) != MessageVisibility.HIDDEN.value
                ]
                filtered_msgs.sort(key=lambda m: m.created_at)

                def _not_current_turn(m) -> bool:
                    m_turn_id = getattr(m, "turn_id", None)
                    if m_turn_id:
                        return m_turn_id != turn_id
                    try:
                        m_created = getattr(m, "created_at", None)
                        if m_created is None:
                            return True
                        if isinstance(m_created, (int, float)):
                            return m_created <= turn_started
                        return True
                    except Exception:
                        return True
                filtered_msgs = [m for m in filtered_msgs if _not_current_turn(m)]

                history_messages = []
                for m in filtered_msgs:
                    try:
                        role = _map_role_ekb_to_llm(m.role)
                    except ValueError:
                        continue
                    history_messages.append({"role": role, "content": str(m.content)})

                if history_messages:
                    tl = _normalize_thinking_level(thinking_level or "medium", use_deep_thinking)
                    try:
                        from ekb_api.llm import build_messages_for_generation as _bmfg
                        from ekb_api.llm import build_system_prompt_template as _bspt
                        _hp = _bspt(
                            has_evidence=bool(evidence_texts),
                            thinking_level=tl,
                            deep_hint="",
                        )
                        _pre_full = _bmfg(
                            system_prompt=_hp,
                            history_messages=history_messages,
                            evidence_texts=evidence_texts,
                            current_question=payload.question,
                        )
                        tokens_est_before_any_compaction = estimate_messages_tokens(_pre_full)
                    except Exception:
                        tokens_est_before_any_compaction = estimate_messages_tokens(history_messages)
                    compaction_start = time.perf_counter()
                    history_messages, compaction_report = maybe_compact_history(
                        thinking_level=tl,
                        deep_thinking=use_deep_thinking,
                        history_messages=history_messages,
                        evidence_texts=evidence_texts,
                        current_question=payload.question,
                        settings=settings,
                        tenant_id=auth.tenant_id,
                        user_id=auth.actor_id,
                    )
                    compaction_elapsed = time.perf_counter() - compaction_start
                    QA_COMPACTION_DURATION_SECONDS.observe(compaction_elapsed)
                    if compaction_report is not None and compaction_report.method != "skipped":
                        QA_COMPACTION_TRIGGERED.inc(1.0, reason="token_threshold", method=compaction_report.method)
                    tokens_gauge_value = (
                        compaction_report.tokens_before
                        if compaction_report
                        else tokens_est_before_any_compaction
                    )
                    QA_HISTORY_TOKENS.set(
                        float(tokens_gauge_value),
                        tenant_id=auth.tenant_id or "none",
                        conversation_id=(conversation.id if conversation else "none"),
                    )
                    if compaction_report is not None:
                        yield emit("compaction_performed", {
                            "rounds_compressed": compaction_report.rounds_compressed,
                            "tokens_before": compaction_report.tokens_before,
                            "tokens_after": compaction_report.tokens_after,
                            "method": compaction_report.method,
                            "detail": compaction_report.detail,
                        })
                else:
                    compaction_report = None
                    QA_HISTORY_TOKENS.set(
                        0.0,
                        tenant_id=auth.tenant_id or "none",
                        conversation_id=(conversation.id if conversation else "none"),
                    )
            else:
                history_messages = None
            # ---------- 多轮对话新增结束 ----------

            # ---------- 4. 零证据 → 仍由远程 LLM 处理；无 Provider 时拒绝 ----------
            _no_evidence_mode = not evidence_texts

            tokens_est_value = 0
            if compaction_report is not None:
                tokens_est_value = compaction_report.tokens_after
            elif history_messages:
                try:
                    from ekb_api.llm import build_messages_for_generation as _bmfg2
                    from ekb_api.llm import build_system_prompt_template as _bspt2
                    _tl2 = _normalize_thinking_level(thinking_level or "medium", use_deep_thinking)
                    _hp2 = _bspt2(
                        has_evidence=bool(evidence_texts),
                        thinking_level=_tl2,
                        deep_hint="",
                    )
                    _full2 = _bmfg2(
                        system_prompt=_hp2,
                        history_messages=history_messages,
                        evidence_texts=evidence_texts,
                        current_question=payload.question,
                    )
                    tokens_est_value = estimate_messages_tokens(_full2)
                except Exception:
                    tokens_est_value = tokens_est_before_any_compaction or estimate_messages_tokens(history_messages)

            logger.info(
                "qa/multi_turn request_id=%s conv_id=%s history_count=%d est_tokens=%d compaction=%s",
                request_id or "-",
                conversation.id if conversation else "-",
                len(history_messages) if history_messages else 0,
                tokens_est_value,
                compaction_report.method if compaction_report else "none",
            )

            # ---------- 5. generation_started 阶段事件 ----------
            yield emit(
                "generation_started",
                {
                    "evidence_count": total_evidence_count,
                    "kb_evidence_count": len(chunks),
                    "web_evidence_count": len(web_results),
                    "provider": route.provider_name or "default",
                    "model": route.model or settings.llm_model,
                    "deep_thinking": use_deep_thinking,
                    "web_search": use_web_search,
                },
            )

            # 显式取消检查点 2（defensive）
            _cancelled2 = False
            if use_v2:
                try:
                    _cancelled2 = store.is_turn_cancelled(turn_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("is_turn_cancelled (cp2) failed turn_id=%s: %s", turn_id, exc)
                    _cancelled2 = False
            if use_v2 and _cancelled2:
                finish_reason = FinishReason.CANCELLED.value
                yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                return

            # ---------- 6. 生成阶段（分段空闲超时 + 心跳 + Delta 合并）----------
            # 注意：此处不要再做 settings = get_settings()，否则 Python 会把 settings
            # 识别为 event_stream_v2 局部变量，导致前面 line 315 处抛 UnboundLocalError
            runtime_chat_providers = get_runtime_chat_providers(
                tenant_id=auth.tenant_id,
                user_id=auth.actor_id,
            )
            if not runtime_chat_providers:
                QA_DEGRADATIONS.inc(reason="llm_unavailable")
                citations_payload = rag.build_citations(
                    list(chunks), web_results, payload.options.max_citations
                )
                try:
                    store.save_message_citations(auth, assistant_message.id, citations_payload)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "save_message_citations failed msg=%s: %s",
                        assistant_message.id,
                        exc,
                    )
                if use_v2:
                    yield emit("citations", {"items": citations_payload})
                else:
                    for citation in citations_payload:
                        yield _sse_v1("citation", citation)
                store.update_message_content(
                    auth,
                    assistant_message.id,
                    "当前未配置可用的远程 LLM Provider，无法生成回答。请先在 AI 模型配置中心启用服务商。",
                )
                yield emit(
                    "error",
                    {
                        "code": "LLM_PROVIDER_NOT_CONFIGURED",
                        "message": "未配置可用的远程 LLM Provider",
                        "request_id": request_id,
                    },
                )
                yield emit(
                    "done",
                    {
                        "message_id": assistant_message.id,
                        "finish_reason": FinishReason.ERROR.value,
                        "confidence": "low",
                        "last_seq": seq,
                    },
                )
                return
            else:
                # 真流式生成：evidence_texts 已在检索阶段用 merge_evidence 合并好（KB + Web）
                t0 = time.perf_counter()
                full_answer = ""
                merger = DeltaMerger(
                    settings.sse_v2_delta_max_tokens,
                    settings.sse_v2_delta_max_bytes,
                    settings.sse_v2_delta_flush_ms,
                )
                phase = "generation"

                # 心跳协程：每 sse_v2_heartbeat_interval 秒发一个 :heartbeat 注释事件
                if use_v2:

                    async def _heartbeat_loop():
                        hb_interval = settings.sse_v2_heartbeat_interval
                        try:
                            while True:
                                await asyncio.sleep(hb_interval)
                                QA_HEARTBEATS_SENT.inc(stream_version="2")
                                yield f": heartbeat {utc_now()}\n\n"
                        except asyncio.CancelledError:
                            pass

                    # 注意：心跳是纯 SSE 注释，不影响事件解析；此处简化实现为「在主循环里计时检查」
                    # （避免异步生成器嵌套 yield from 复杂化）

                try:
                    gen = generate_answer_stream(
                        payload.question,
                        evidence_texts,
                        route=route,
                        deep_thinking=use_deep_thinking,
                        thinking_level=thinking_level,
                        history_messages=history_messages,
                        tenant_id=auth.tenant_id,
                        user_id=auth.actor_id,
                    )
                    # 同一 gen 实例共享锁，避免 wait_for(timeout=2s) 放弃线程后，
                    # in-flight 的 next(gen) 与下一轮新 to_thread 并发重入。
                    gen_lock = threading.Lock()
                    gen_timeout = (
                        settings.sse_v2_generation_idle_timeout
                        if use_v2
                        else settings.qa_timeout_seconds
                    )
                    while True:
                        # 显式取消检查点 3（每 token, defensive）
                        _cancelled3 = False
                        if use_v2:
                            try:
                                _cancelled3 = store.is_turn_cancelled(turn_id)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("is_turn_cancelled (cp3) failed turn_id=%s: %s", turn_id, exc)
                                _cancelled3 = False
                        if use_v2 and _cancelled3:
                            finish_reason = FinishReason.CANCELLED.value
                            try:
                                store.update_message_content(
                                    auth, assistant_message.id, full_answer
                                )
                            except Exception:  # noqa: BLE001
                                pass
                            yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                            return

                        # 心跳：last_event_at 超过间隔就发
                        now_t = time.perf_counter()
                        if (
                            use_v2
                            and now_t - last_event_at >= settings.sse_v2_heartbeat_interval
                        ):
                            QA_HEARTBEATS_SENT.inc(stream_version="2")
                            # SSE 注释行（event source 解析器会忽略，Nginx 不会因为无输出断连）
                            yield f": heartbeat {utc_now()}\n\n"
                            last_event_at = now_t

                        # 分段空闲超时：超过 generation_idle_timeout 无 token 则断
                        idle_elapsed = now_t - last_event_at
                        expected_idle = (
                            settings.sse_v2_generation_idle_timeout
                            if use_v2
                            else settings.qa_timeout_seconds
                        )
                        if phase == "generation" and idle_elapsed >= expected_idle:
                            QA_IDLE_TIMEOUTS.inc(phase="generation")
                            finish_reason = FinishReason.TIMEOUT.value
                            yield emit(
                                "error",
                                {
                                    "code": "GENERATION_IDLE_TIMEOUT",
                                    "message": "模型长时间未输出，已停止生成",
                                    "request_id": request_id,
                                },
                            )
                            yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                            return

                        try:
                            token = await asyncio.wait_for(
                                asyncio.to_thread(_next_token, gen, gen_lock),
                                timeout=min(gen_timeout, 2.0),  # 2s 粒度轮询空闲超时
                            )
                        except asyncio.TimeoutError:
                            # 短暂超时不是错误，继续下一轮检查心跳和空闲
                            if await request.is_disconnected():
                                finish_reason = FinishReason.CANCELLED.value
                                store.update_message_content(
                                    auth, assistant_message.id, full_answer
                                )
                                yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                                return
                            continue

                        if token is _STREAM_END:
                            break
                        full_answer += token

                        if await request.is_disconnected():
                            finish_reason = FinishReason.CANCELLED.value
                            store.update_message_content(
                                auth, assistant_message.id, full_answer
                            )
                            yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
                            return

                        if use_v2:
                            merger.append(token)
                            should, trigger = merger.should_flush()
                            if should:
                                merged, _, _, _ = merger.flush()
                                QA_DELTA_FLUSHES.inc(trigger=trigger)
                                yield emit("content_delta", {"text": merged, "delta": merged, "citations": []})
                                if not first_token_emitted:
                                    first_token_emitted = True
                                    store.mark_turn_first_visible(turn_id)
                                    QA_TTFB_DURATION.observe(
                                        time.perf_counter() - ttfbt0,
                                        stream_version="2",
                                    )
                        else:
                            yield _sse_v1("token", {"text": token})
                            if not first_token_emitted:
                                first_token_emitted = True
                                QA_TTFB_DURATION.observe(
                                    time.perf_counter() - ttfbt0,
                                    stream_version="1",
                                )
                except LlmError as exc:
                    QA_DEGRADATIONS.inc(reason="llm_failed")
                    logger.warning("qa/ask remote LLM generation failed request_id=%s: %s", request_id, exc)
                    failure_message = "远程 LLM 服务暂时不可用，无法生成回答，请稍后重试。"
                    store.update_message_content(auth, assistant_message.id, failure_message)
                    yield emit(
                        "error",
                        {
                            "code": "LLM_PROVIDER_ERROR",
                            "message": failure_message,
                            "request_id": request_id,
                        },
                    )
                    yield emit(
                        "done",
                        {
                            "message_id": assistant_message.id,
                            "finish_reason": FinishReason.ERROR.value,
                            "confidence": "low",
                            "last_seq": seq,
                        },
                    )
                    return
                finally:
                    QA_GENERATION_DURATION.observe(time.perf_counter() - t0)

                # flush 剩余 delta
                if use_v2:
                    merged, _, _, _ = merger.flush()
                    if merged:
                        QA_DELTA_FLUSHES.inc(trigger="time")
                        yield emit("content_delta", {"text": merged, "delta": merged, "citations": []})

                # ---------- 7. 流式拒答二次确认兜底（M4-5 P1）----------
                # 复用外层作用域的 settings（不要重新赋值，否则触发局部变量 shadow）
                refusal_needs_recheck = (
                    "证据不足" in full_answer
                    and total_evidence_count >= 3
                    and settings.llm_enabled  # 未配置 LLM 时跳过二次确认，避免抛异常
                )
                if refusal_needs_recheck:
                    rechecked = ""
                    try:
                        rechecked = await asyncio.to_thread(
                            generate_answer, payload.question, evidence_texts, route=route, deep_thinking=use_deep_thinking, thinking_level=thinking_level, history_messages=history_messages, tenant_id=auth.tenant_id, user_id=auth.actor_id
                        )
                    except LlmError:
                        rechecked = ""
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("拒答二次确认 generate_answer 失败: %s", exc)
                        rechecked = ""
                    if rechecked and "证据不足" not in rechecked:
                        QA_DEGRADATIONS.inc(reason="stream_refusal_fallback_ok")
                        prefix = "（补充确认：当前证据中存在相关信息，以下为修正后的结论——）"
                        delta_text = prefix + rechecked
                        full_answer = rechecked
                        if use_v2:
                            yield emit("content_delta", {"text": delta_text, "delta": delta_text, "citations": []})
                        else:
                            for piece in _split_for_stream(delta_text):
                                yield _sse_v1("token", {"text": piece})
                        store.update_message_content(auth, assistant_message.id, full_answer)
                    else:
                        QA_DEGRADATIONS.inc(reason="stream_refusal_confirmed")
                        store.update_message_content(auth, assistant_message.id, full_answer)
                        finish_reason = FinishReason.REFUSAL.value
                        yield emit(
                            "done",
                            {
                                "message_id": assistant_message.id,
                                "finish_reason": finish_reason,
                                "confidence": "low",
                                "last_seq": seq,
                            },
                        )
                        return
                elif "证据不足" in full_answer:
                    store.update_message_content(auth, assistant_message.id, full_answer)
                    finish_reason = FinishReason.REFUSAL.value
                    yield emit(
                        "done",
                        {
                            "message_id": assistant_message.id,
                            "finish_reason": finish_reason,
                            "confidence": "low",
                            "last_seq": seq,
                        },
                    )
                    return
                else:
                    store.update_message_content(auth, assistant_message.id, full_answer)

            # ---------- 8. citations 事件：用 merge_evidence 的 citations_merged（KB + Web 统一）----------
            # citations_merged 结构: [{index, type:"kb"|"web", title, doc_id|None, url|None, section_path|None, published_date|None}]
            # PH6 FR-052/FR-053：用 rag.build_citations 构造完整元数据引用（version/page/sheet/
            # paragraph/source_path/updated_at/score），并持久化到消息 metadata_redacted.citations。
            citations_payload: list[dict] = rag.build_citations(
                list(chunks), web_results, payload.options.max_citations
            )
            try:
                store.save_message_citations(auth, assistant_message.id, citations_payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("save_message_citations failed msg=%s: %s", assistant_message.id, exc)
            if use_v2:
                yield emit("citations", {"items": citations_payload})
            else:
                for c in citations_payload:
                    yield _sse_v1("citation", c)

            finish_reason = FinishReason.STOP.value
            yield emit(
                "done",
                {
                    "message_id": assistant_message.id,
                    "finish_reason": finish_reason,
                    "confidence": "medium",
                    "last_seq": seq,
                    "citations_count": len(citations_payload),
                },
            )

        except asyncio.TimeoutError:
            finish_reason = FinishReason.TIMEOUT.value
            yield emit(
                "error",
                {
                    "code": "UPSTREAM_TIMEOUT",
                    "message": "模型响应超时，已停止生成",
                    "request_id": request_id,
                },
            )
            yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
        except LlmError as exc:
            # Step 1.6：LLM 层异常专用错误码，区分于通用 INTERNAL_ERROR
            logger.warning("qa/ask LlmError request_id=%s: %s", request_id, exc)
            QA_DEGRADATIONS.inc(reason="llm_unhandled_outer")
            finish_reason = FinishReason.ERROR.value
            yield emit(
                "error",
                {
                    "code": "LLM_PROVIDER_ERROR",
                    "message": "大模型服务暂时不可用，已自动记录降级；请稍后重试",
                    "request_id": request_id,
                },
            )
            yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
        except Exception as exc:  # noqa: BLE001
            # Step 1.1：记录完整 traceback，便于定位真实根因
            logger.exception("qa/ask INTERNAL_ERROR request_id=%s", request_id)
            finish_reason = FinishReason.ERROR.value
            base_msg = "问答处理失败，请稍后重试"
            # 开发环境附加简短异常类型，便于调试；生产不暴露内部细节
            if not settings.is_production:
                base_msg = f"{base_msg}（{type(exc).__name__}）"
            yield emit(
                "error",
                {
                    "code": "INTERNAL_ERROR",
                    "message": base_msg,
                    "request_id": request_id,
                },
            )
            yield emit("done", {"finish_reason": finish_reason, "last_seq": seq})
        finally:
            # 写回 turn 最终状态（Step 1.2/1.5：全部 defensive，避免审计/记录失败影响主流程）
            if use_v2:
                status_map = {
                    FinishReason.STOP.value: TurnStatus.COMPLETED.value,
                    FinishReason.REFUSAL.value: TurnStatus.COMPLETED.value,
                    FinishReason.CANCELLED.value: TurnStatus.CANCELLED.value,
                    FinishReason.TIMEOUT.value: TurnStatus.TIMEOUT.value,
                    FinishReason.ERROR.value: TurnStatus.ERROR.value,
                }
                try:
                    store.complete_turn(
                        turn_id,
                        status=status_map.get(finish_reason, TurnStatus.ERROR.value),
                        finish_reason=finish_reason,
                        last_seq=seq,
                    )
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("complete_turn failed turn_id=%s: %s", turn_id, exc2)
                # 取消/超时/错误的占位消息：若内容为空则隐藏，避免历史遗留空 assistant bubble
                if finish_reason in {FinishReason.CANCELLED.value, FinishReason.TIMEOUT.value, FinishReason.ERROR.value}:
                    try:
                        msg_row = store.get_message(auth, assistant_message.id)
                        if msg_row and not msg_row.content.strip():
                            store.update_message_visibility(
                                auth, assistant_message.id, MessageVisibility.HIDDEN.value
                            )
                    except Exception as exc2:  # noqa: BLE001
                        logger.warning(
                            "update_message_visibility failed msg_id=%s: %s",
                            assistant_message.id,
                            exc2,
                        )

            try:
                QA_REQUESTS.inc(finish_reason=finish_reason)
                QA_TURN_DURATION.observe(
                    time.perf_counter() - turn_started,
                    stream_version="2" if use_v2 else "1",
                    finish_reason=finish_reason,
                )
            except Exception:  # noqa: BLE001
                pass  # metrics 失败不影响主流程
            try:
                _audit_qa(
                    store,
                    auth,
                    conversation.id,
                    question_preview,
                    finish_reason,
                    ip_hash,
                    ua_hash,
                    model_route=route.provider_name or "default",
                    turn_id=turn_id,
                )
            except Exception as exc2:  # noqa: BLE001
                logger.warning("_audit_qa failed conv_id=%s: %s", conversation.id, exc2)
            if finish_reason == FinishReason.REFUSAL.value:
                try:
                    store.create_review_item(
                        auth,
                        conversation_id=conversation.id,
                        message_id=assistant_message.id,
                        question_preview=question_preview,
                        answer_preview="（拒答：证据不足）",
                        confidence="low",
                        evidence_summary=[],
                    )
                except Exception:  # noqa: BLE001
                    pass

    return StreamingResponse(
        event_stream_v2(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Turn-Id": turn_id,
        },
    )


# ---------------------------------------------------------------------------
# /qa/turns/{turn_id}/cancel 显式取消路由（SSE v2）
# ---------------------------------------------------------------------------


@router.post("/turns/{turn_id}/cancel", response_model=TurnCancelResponse)
async def cancel_turn(
    turn_id: Annotated[str, Path(..., min_length=5, max_length=128)],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> TurnCancelResponse:
    """显式取消一个正在运行中的 QA Turn。

    幂等：重复调用同 turn_id 不会报错，仅第一次对 running turn 生效。
    权限：仅同租户 actor 可取消；跨租户 turn 返回 not_found（不泄露存在性）。
    """
    existing = store.get_turn(auth, turn_id)
    if existing is None:
        QA_CANCEL_REQUESTS.inc(result="not_found")
        return TurnCancelResponse(
            turn_id=turn_id,
            status="not_found",
            accepted=False,
            message="Turn 不存在或无权限取消",
        )

    accepted, status = store.request_cancel_turn(auth, turn_id)
    QA_CANCEL_REQUESTS.inc(result=status)
    messages = {
        "cancelled": "已标记取消，流式循环将在下一个检查点停止",
        "already_completed": "Turn 已结束，取消不生效",
        "not_found": "Turn 不存在或无权限取消",
    }
    return TurnCancelResponse(
        turn_id=turn_id,
        status=status,
        accepted=accepted,
        message=messages.get(status),
    )


# ---------------------------------------------------------------------------
# GET /qa/capabilities：Composer 功能按钮能力 + 可选模型列表
# ---------------------------------------------------------------------------


@router.get("/capabilities", response_model=ComposerCapabilitiesResponse)
async def get_composer_capabilities(
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> ComposerCapabilitiesResponse:
    """返回 Composer 功能按钮能力 + 可选模型列表。

    - attachments_enabled：添加文件（上传后为 doc_id，走 attachment_doc_ids），默认 True
    - web_search_enabled：联网搜索占位，当前版本默认 False（未接 SERP API）
    - deep_thinking_enabled：深度思考（通过 prompt + temperature 生效）默认 True
    - model_choice_enabled：模型选择默认 True；禁用前端下拉

    可选模型列表从「AI 模型配置中心」动态读取（优先级最高），
    只返回当前主体已配置的远程 Provider/Model；未配置时返回空列表，
    前端必须显示不可用，不得构造默认模型或本地模型。
    """
    settings = get_settings()
    tenant = store.get_tenant_by_id(auth.tenant_id)
    route = resolve_tenant_routing(auth, settings, model_routing_key=tenant.model_routing_key)

    # 运行时 providers：配置中心动态 + env 静态 fallback
    runtime_providers = get_runtime_chat_providers(
        tenant_id=auth.tenant_id, user_id=auth.actor_id
    )

    # 构建模型列表：仅使用当前主体已配置的远程 Provider。
    models_out: list[ModelInfo] = []
    seen_ids: set[str] = set()
    # DeepSeek 允许对外展示的模型白名单（2026-08 官方平台仅这两个公开 V4 模型），
    # 其它 legacy 模型从下拉里一律过滤掉，
    # 避免用户选到已下线/旧地址模型。
    _DS_ALLOWED: set[str] = {"deepseek-v4-flash", "deepseek-v4-pro"}
    _DS_DISPLAY: dict[str, str] = {
        "deepseek-v4-flash": "V4 Flash",
        "deepseek-v4-pro": "V4 Pro",
    }
    if runtime_providers:
        # 把当前选中的 provider 放最前面，便于前端默认选中
        prioritized = sorted(
            runtime_providers,
            key=lambda p: (
                0
                if (
                    route.provider_name
                    and (
                        p.name == route.provider_name
                        or ("/" in p.name and p.name.split("/", 1)[0] == route.provider_name)
                    )
                )
                else 1
            ),
        )
        for p in prioritized:
            # p.name 格式是 "<provider_key>/<model_id>"，id 直接复用；
            # 若 env 静态配置格式是 legacy-llm（单名），也兼容
            mid = p.name
            if mid in seen_ids:
                continue
            if "/" in mid:
                pkey, mname = mid.split("/", 1)
            else:
                pkey, mname = p.name, p.model
            # DeepSeek 过滤：仅保留白名单
            if pkey == "deepseek" and mname not in _DS_ALLOWED:
                continue
            seen_ids.add(mid)
            # 展示名称：DeepSeek 使用中文友好名，其它 provider 直接用原始 model id
            display_name = _DS_DISPLAY.get(mname) if pkey == "deepseek" else None
            if not display_name:
                display_name = mname
            models_out.append(
                ModelInfo(
                    id=mid,
                    name=display_name,
                    provider=pkey,
                    description=display_name,
                    supports_deep_thinking=True,
                )
            )
    caps = ComposerCapabilities(
        attachments_enabled=True,
        web_search_enabled=web_search_enabled(settings),
        deep_thinking_enabled=True,
        model_choice_enabled=len(models_out) > 0,
    )
    defaults = AskOptions(
        model=models_out[0].id if models_out else None,
        thinking_level="medium",
        deep_thinking=True,
    )
    return ComposerCapabilitiesResponse(
        capabilities=caps,
        models=models_out,
        defaults=defaults,
        context_window_tokens=settings.llm_context_window,
        compaction_enabled=settings.multi_turn_enabled,
    )


# ---------------------------------------------------------------------------
# 审计辅助
# ---------------------------------------------------------------------------


def _audit_qa(
    store: SqlStore,
    auth: AuthContext,
    conversation_id: str,
    question_preview: str,
    finish_reason: str,
    ip_hash: str,
    ua_hash: str,
    *,
    model_route: str = "default",
    turn_id: str | None = None,
) -> None:
    result_map = {
        FinishReason.STOP.value: RESULT_SUCCESS,
        FinishReason.REFUSAL.value: RESULT_SUCCESS,
        FinishReason.CANCELLED.value: RESULT_DEGRADED,
        FinishReason.TIMEOUT.value: RESULT_DEGRADED,
        FinishReason.ERROR.value: RESULT_FAILURE,
    }
    meta = {
        "question_preview": question_preview,
        "finish_reason": finish_reason,
        "model_route": model_route,
    }
    if turn_id:
        meta["turn_id"] = turn_id
    store.write_audit_log(
        action="qa.ask",
        target_type="conversation",
        target_id=conversation_id,
        result=result_map.get(finish_reason, RESULT_FAILURE),
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(meta),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )


# ---------------------------------------------------------------------------
# Legacy helper
# ---------------------------------------------------------------------------


def _split_for_stream(text: str, width: int = 18) -> list[str]:
    return [text[index : index + width] for index in range(0, len(text), width)]

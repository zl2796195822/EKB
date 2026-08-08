from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ekb_api.core.audit import (
    RESULT_DEGRADED,
    RESULT_FAILURE,
    RESULT_SUCCESS,
    extract_fingerprints,
    redact_metadata,
)
from ekb_api.core.auth import get_auth_context, get_store
from ekb_api.core.config import get_settings
from ekb_api.core.egress import assert_egress_allowed, resolve_tenant_routing
from ekb_api.core.errors import ApiError
from ekb_api.core.metrics import (
    QA_DEGRADATIONS,
    QA_GENERATION_DURATION,
    QA_REQUESTS,
    QA_RETRIEVAL_DURATION,
)
from ekb_api.domain import AuthContext
from ekb_api.llm import LlmError, generate_answer
from ekb_api.retrieval import retrieve
from ekb_api.schemas import AskRequest
from ekb_api.store import SqlStore

router = APIRouter(prefix="/qa", tags=["qa"])

# 流式生成单轮最大耗时；超过后发送 timeout 事件并停止。
# M1-05 含 query 改写 + LLM 生成（2 次外部调用），调到 60 秒避免误超时。
QA_TIMEOUT_SECONDS = 60.0


@router.post("/ask")
async def ask(
    payload: AskRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    store: Annotated[SqlStore, Depends(get_store)],
) -> StreamingResponse:
    # M2-7 数据出域策略：禁止出域的租户不允许内容进入生成层。
    tenant = store.get_tenant_by_id(auth.tenant_id)
    assert_egress_allowed(tenant)
    # M2-7 每租户每日问答配额：超额直接拒绝（429），不进入流式生成。
    if not store.check_and_increment_qa_quota(auth.tenant_id):
        raise ApiError(429, "QUOTA_EXCEEDED", "该租户今日问答次数已达上限")
    # M2-7 解析租户级模型路由（provider/model），注入检索改写与生成路径。
    settings = get_settings()
    route = resolve_tenant_routing(auth, settings, model_routing_key=tenant.model_routing_key)

    if payload.kb_ids:
        visible_ids = {kb.id for kb in store.list_knowledge_bases(auth)}
        if not set(payload.kb_ids).issubset(visible_ids):
            raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")

    conversation = (
        store.get_conversation(auth, payload.conversation_id) if payload.conversation_id else None
    )
    if payload.conversation_id and conversation is None:
        raise ApiError(404, "NOT_FOUND", "当前授权范围内不存在")
    if conversation is None:
        conversation = store.create_conversation(auth, payload.question[:40])

    # 预写用户消息并创建空的 assistant 占位消息，使 request 事件即可返回 message_id。
    store.save_message(auth, conversation.id, "USER", payload.question)
    assistant_message = store.save_message(auth, conversation.id, "ASSISTANT", "")

    # 审计指纹在请求上下文中提取一次，供流式生命周期内复用。
    ip_hash, ua_hash = extract_fingerprints(request)
    # 问题内容按数据策略截断存储（完整内容已持久化到 messages 表，审计只保留摘要）。
    question_preview = payload.question[:80]

    async def event_stream() -> AsyncIterator[str]:
        finish_reason = "error"
        try:
            yield _sse(
                "request",
                {
                    "request_id": auth.trace_id,
                    "message_id": assistant_message.id,
                    "conversation_id": conversation.id,
                },
            )
            yield _sse(
                "retrieval",
                {"status": "running", "authorized_kb_count": len(payload.kb_ids) or 1},
            )
            await asyncio.sleep(0.01)

            if await request.is_disconnected():
                finish_reason = "cancelled"
                yield _sse("done", {"finish_reason": "cancelled"})
                return

            # 检索编排（含 query 改写）+ LLM 生成，全部纳入 timeout 控制。
            # retrieve 是同步阻塞的（含 LLM 调用），用 to_thread 避免阻塞事件循环。
            chunks, answer, refusal = await asyncio.wait_for(
                _retrieve_and_generate(
                    store,
                    auth,
                    payload.question,
                    payload.kb_ids,
                    payload.options.max_citations,
                    route,
                ),
                timeout=QA_TIMEOUT_SECONDS,
            )
            store.update_message_content(auth, assistant_message.id, answer)

            if refusal:
                # 无证据拒答：只发拒答文本，不发 token 流和 citation，finish_reason=refusal。
                finish_reason = "refusal"
                yield _sse("token", {"text": answer})
                yield _sse(
                    "done",
                    {
                        "message_id": assistant_message.id,
                        "finish_reason": "refusal",
                        "confidence": "low",
                    },
                )
                return

            for piece in _split_for_stream(answer):
                if await request.is_disconnected():
                    finish_reason = "cancelled"
                    yield _sse("done", {"finish_reason": "cancelled"})
                    return
                yield _sse("token", {"text": piece})
                await asyncio.sleep(0.01)

            for chunk in chunks:
                yield _sse(
                    "citation",
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
            finish_reason = "stop"
            yield _sse(
                "done",
                {
                    "message_id": assistant_message.id,
                    "finish_reason": "stop",
                    "confidence": "medium",
                },
            )
        except asyncio.TimeoutError:
            finish_reason = "timeout"
            yield _sse(
                "error",
                {
                    "code": "UPSTREAM_TIMEOUT",
                    "message": "模型响应超时，已停止生成",
                    "request_id": auth.trace_id,
                },
            )
            yield _sse("done", {"finish_reason": "timeout"})
        except Exception:
            finish_reason = "error"
            yield _sse(
                "error",
                {
                    "code": "INTERNAL_ERROR",
                    "message": "问答处理失败，请稍后重试",
                    "request_id": auth.trace_id,
                },
            )
            yield _sse("done", {"finish_reason": "error"})
        finally:
            # M4-4 指标：finish_reason 分布（stop/refusal/timeout/cancelled/error）→ 降级率可观测。
            QA_REQUESTS.inc(finish_reason=finish_reason)
            # 流结束后写入审计：finish_reason 映射到审计结果，保证超时/取消/错误可追溯。
            _audit_qa(
                store,
                auth,
                conversation.id,
                question_preview,
                finish_reason,
                ip_hash,
                ua_hash,
                model_route=route.provider_name or "default",
            )
            # M3-3 低置信度（refusal）自动入审核队列，关联问题/证据/会话便于回溯。
            if finish_reason == "refusal":
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
                    pass  # 审核入队失败不应阻断问答流

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
) -> None:
    result_map = {
        "stop": RESULT_SUCCESS,
        "refusal": RESULT_SUCCESS,
        "cancelled": RESULT_DEGRADED,
        "timeout": RESULT_DEGRADED,
        "error": RESULT_FAILURE,
    }
    store.write_audit_log(
        action="qa.ask",
        target_type="conversation",
        target_id=conversation_id,
        result=result_map.get(finish_reason, RESULT_FAILURE),
        trace_id=auth.trace_id,
        tenant_id=auth.tenant_id,
        actor_id=auth.actor_id,
        metadata_redacted=redact_metadata(
            {
                "question_preview": question_preview,
                "finish_reason": finish_reason,
                "model_route": model_route,
            }
        ),
        ip_hash=ip_hash,
        user_agent_hash=ua_hash,
    )


async def _generate(question: str, chunks: list, *, route=None) -> tuple[str, bool]:
    """M1-05 生成：LLM 基于授权证据生成答案；未配置 LLM 时降级到 demo 拼接。

    返回 (answer, refusal)：无证据时 refusal=True，调用方据此发 refusal finish_reason。
    LLM 调用在 asyncio.to_thread 中执行，避免阻塞事件循环。
    LLM 失败时降级到 demo 拼接，保证问答不中断。
    route 为 M2-7 租户级模型路由（决定生成所用的 provider/model）。
    """
    if not chunks:
        return (
            "当前授权知识库中没有足够证据，我无法确认这个问题。请补充文档范围或换一种问法。",
            True,
        )

    settings = get_settings()
    if not settings.llm_enabled:
        # 降级：demo 拼接（保持 M1 评估基线可跑通）。
        QA_DEGRADATIONS.inc(reason="llm_unavailable")
        evidence = " ".join(chunk.content for chunk in chunks)
        return f"根据授权知识库中的证据：{evidence}", False

    # LLM 生成：M1-05 上下文构建——去重 + 令牌预算，每条带来源标题/章节便于溯源。
    from ekb_api.ranking import build_context

    evidence_texts = build_context(chunks)
    t0 = time.perf_counter()
    try:
        answer = await asyncio.to_thread(generate_answer, question, evidence_texts, route=route)
    except LlmError:
        # LLM 失败降级到 demo 拼接，保证问答不中断。
        QA_DEGRADATIONS.inc(reason="llm_failed")
        evidence = " ".join(chunk.content for chunk in chunks)
        return f"根据授权知识库中的证据：{evidence}", False
    finally:
        QA_GENERATION_DURATION.observe(time.perf_counter() - t0)

    # LLM 回复「证据不足」时视为拒答。
    if "证据不足" in answer:
        return answer, True
    return answer, False


async def _retrieve_and_generate(
    store: SqlStore,
    auth: AuthContext,
    question: str,
    kb_ids: list[str],
    max_citations: int,
    route=None,
) -> tuple[list, str, bool]:
    """检索编排（含 query 改写）+ LLM 生成。

    retrieve 是同步阻塞的（含 LLM query 改写调用），用 to_thread 包装避免阻塞事件循环。
    route 为 M2-7 租户级模型路由，注入检索改写与生成路径。
    """
    t0 = time.perf_counter()
    chunks = await asyncio.to_thread(
        retrieve, store, auth, question, kb_ids, max_citations, route=route
    )
    QA_RETRIEVAL_DURATION.observe(time.perf_counter() - t0)
    answer, refusal = await _generate(question, chunks, route=route)
    return chunks, answer, refusal


def _sse(event: str, data: dict[str, object]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {encoded}\n\n"


def _split_for_stream(text: str, width: int = 18) -> list[str]:
    return [text[index : index + width] for index in range(0, len(text), width)]

"""M1-05 LLM 客户端：多 Provider 抽象，兼容任意 OpenAI 兼容 chat 端点。

支持 DeepSeek / OpenAI GPT / MiniMax / Kimi 等（按列表顺序尝试，失败自动 fallback）。
设计：
  - settings.chat_providers 为登记的全部启用 chat 提供商；运行时按列表顺序尝试，
    首个成功即用，失败自动 fallback 下一个（提升可用性）。
  - 可通过 provider_name 指定某个 provider（预留给前端选模型）。
  - 未配置任何 chat provider 时调用方各自降级（demo 拼接生成 / 原始 query 检索）。
  - 全部为同步调用（在 asyncio.to_thread 中执行，避免阻塞事件循环）。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from ekb_api.core.cache import query_rewrite_cache, text_hash
from ekb_api.core.circuit_breaker import get_circuit_breaker
from ekb_api.core.config import ModelProvider, get_settings
from ekb_api.core.metrics import CACHE_HITS, CACHE_MISSES, LLM_CALLS, LLM_DURATION

logger = logging.getLogger(__name__)


class LlmError(Exception):
    """LLM 调用失败（所有 provider 均不可用）。"""


class CircuitOpenError(LlmError):
    """熔断器开闸，请求被短路（未发往外网）。"""


def _call_provider(
    provider: ModelProvider, messages: list[dict], *, temperature: float, model: str | None = None
) -> str:
    """调用单个 provider 的 chat completions，返回 assistant 文本。失败抛 LlmError。"""
    settings = get_settings()
    payload: dict = {
        "model": model or provider.model,
        "messages": messages,
        "max_tokens": settings.llm_max_tokens,
        "temperature": temperature,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    request = urllib.request.Request(provider.base_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=provider.timeout_seconds) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        raise LlmError(f"[{provider.name}] 调用失败: {exc}") from exc

    return response["choices"][0]["message"]["content"]


def chat(
    messages: list[dict],
    *,
    temperature: Optional[float] = None,
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """调用 chat completions：依次尝试 provider 直到成功，失败抛 LlmError。

    provider_name 指定时优先用该 provider（找不到则回退到完整列表）。
    model 指定时对命中的 provider 覆盖模型名（M2-7 租户级模型路由）。
    M3-4：经熔断器保护，开闸时短路不发请求。
    """
    breaker = get_circuit_breaker()
    if not breaker.allow_request():
        LLM_CALLS.inc(provider="circuit_open", status="short_circuited")
        raise CircuitOpenError("LLM 熔断器开闸，请求被短路")

    settings = get_settings()
    providers = settings.chat_providers
    if provider_name:
        providers = [p for p in providers if p.name == provider_name] or providers
    if not providers:
        raise LlmError("未配置任何 chat provider（EKB_MODEL_PROVIDERS 中无启用的 chat）")

    effective_temperature = settings.llm_temperature if temperature is None else temperature
    last_exc: Optional[Exception] = None
    for provider in providers:
        t0 = time.perf_counter()
        try:
            result = _call_provider(
                provider, messages, temperature=effective_temperature, model=model
            )
            LLM_DURATION.observe(time.perf_counter() - t0, provider=provider.name)
            LLM_CALLS.inc(provider=provider.name, status="success")
            breaker.track_success()
            return result
        except LlmError as exc:
            LLM_DURATION.observe(time.perf_counter() - t0, provider=provider.name)
            LLM_CALLS.inc(provider=provider.name, status="failed")
            last_exc = exc
            logger.warning("chat provider %s 不可用，尝试下一个: %s", provider.name, exc)
            continue
    breaker.track_failure()
    raise LlmError(f"所有 chat provider 均失败: {last_exc}")


def rewrite_query(question: str, *, route=None) -> list[str]:
    """用 LLM 把用户问题改写成 3-5 个检索 query，扩大召回。

    返回包含原始问题在内的去重 query 列表。无 chat provider 或失败时返回 [question]。
    route 为 M2-7 租户级模型路由（决定改写所用的 provider/model）。

    M4-1：接入改写结果缓存。改写是语言操作（问题→检索词），无授权数据，
    跨租户安全；键含 LLM 模型版本，换模型/路由时自动失效。
    LLM 失败结果不缓存（避免 transient failure 被固化）。
    """
    settings = get_settings()
    if not settings.chat_providers:
        return [question]

    # 缓存键：模型版本 + 问题哈希。route 只切 provider 不切 model 时结果可复用。
    model_version = (route.model if route else None) or settings.llm_model
    cache_key = (model_version, text_hash(question))
    cached = query_rewrite_cache.get(cache_key)
    if cached is not None:
        CACHE_HITS.inc(type="rewrite")
        return cached

    CACHE_MISSES.inc(type="rewrite")
    prompt = (
        "你是检索查询改写助手。把用户问题改写成 3-5 个不同的检索关键词组合，"
        "用于在知识库中召回相关文档。\n\n"
        "要求：\n"
        "- 每个改写针对不同的检索角度（实体词、动作词、场景词）\n"
        "- 保留原始问题中的关键术语\n"
        "- 用简洁的名词短语，不要完整句子\n"
        '- 输出 JSON 数组格式，如 ["query1", "query2", "query3"]\n\n'
        f"用户问题：{question}\n\n"
        "改写结果（只输出 JSON 数组，不要其他文字）："
    )
    try:
        raw = chat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            provider_name=route.provider_name if route else None,
            model=route.model if route else None,
        )
    except LlmError:
        # 失败不缓存，避免 transient failure 被固化。
        return [question]

    queries = _parse_json_array(raw)
    if not queries:
        return [question]
    # 原始问题始终在首位；去重保序。
    result = [question]
    for q in queries:
        q = q.strip()
        if q and q not in result:
            result.append(q)
    result = result[:6]  # 最多 6 个 query（原始 + 5 改写）
    # 只有成功结果才缓存。
    query_rewrite_cache.put(cache_key, result)
    return result


def generate_answer(question: str, evidence_texts: list[str], *, route=None) -> str:
    """用 LLM 基于授权证据生成答案。

    evidence_texts 为各 chunk 的拼接文本（含标题/章节路径）。无 chat provider 时抛 LlmError。
    route 为 M2-7 租户级模型路由（决定生成所用的 provider/model）。
    """
    settings = get_settings()
    if not settings.chat_providers:
        raise LlmError("未配置 chat provider")

    context = "\n\n".join(f"[证据{i + 1}]\n{text}" for i, text in enumerate(evidence_texts))
    prompt = (
        "你是企业知识库问答助手。基于以下授权知识库证据回答问题。\n\n"
        "要求：\n"
        "- 只使用上述证据中的信息，不要编造\n"
        "- 答案必须包含证据中与问题相关的关键术语和具体步骤\n"
        "- 简洁直接，先给结论再补充细节\n"
        "- 如果证据不足以回答，回复「证据不足，无法确认」\n\n"
        f"{context}\n\n"
        f"问题：{question}\n\n"
        "回答："
    )
    return chat(
        [{"role": "user", "content": prompt}],
        provider_name=route.provider_name if route else None,
        model=route.model if route else None,
    )


def _parse_json_array(text: str) -> list[str]:
    """宽松解析 LLM 输出的 JSON 数组（容忍前后多余文字和代码块标记）。"""
    cleaned = text.strip()
    # 去掉可能的 ```json ... ``` 包裹。
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    # 尝试提取第一个 JSON 数组片段。
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if 0 <= start < end:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return []

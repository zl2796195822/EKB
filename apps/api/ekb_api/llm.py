"""M1-05 LLM 客户端：多 Provider 抽象，兼容任意 OpenAI 兼容 chat 端点。

支持 DeepSeek / OpenAI GPT / MiniMax / Kimi 等（按列表顺序尝试，失败自动 fallback）。
设计：
  - settings.chat_providers 为登记的全部启用 chat 提供商；运行时按列表顺序尝试，
    首个成功即用，失败自动 fallback 下一个（提升可用性）。
  - 可通过 provider_name 指定某个 provider（预留给前端选模型）。
  - 未配置任何 chat provider 时显式抛出 LlmError；调用方不得拼接证据冒充回答。
  - 全部为同步调用（在 asyncio.to_thread 中执行，避免阻塞事件循环）。
"""

from __future__ import annotations

import base64
import json
import logging
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from ekb_api.core.cache import query_rewrite_cache, text_hash
from ekb_api.core.circuit_breaker import get_circuit_breaker
from ekb_api.core.config import (
    ModelProvider,
    get_runtime_chat_providers,
    get_settings,
)
from ekb_api.core.metrics import CACHE_HITS, CACHE_MISSES, LLM_CALLS, LLM_DURATION

logger = logging.getLogger(__name__)

ProviderObserver = Callable[[ModelProvider, bool], None]


def _notify_provider_observer(
    observer: ProviderObserver | None,
    provider: ModelProvider,
    fallback_occurred: bool,
) -> None:
    """Report a confirmed provider without making observation part of the stream."""

    if observer is None:
        return
    try:
        observer(provider, fallback_occurred)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "LLM provider observer failed provider=%s error_type=%s",
            provider.name,
            type(exc).__name__,
        )


def _is_cjk_char(cp: int) -> bool:
    if 0x4E00 <= cp <= 0x9FFF:
        return True
    if 0x3400 <= cp <= 0x4DBF:
        return True
    if 0x20000 <= cp <= 0x2A6DF:
        return True
    if 0x2A700 <= cp <= 0x2B73F:
        return True
    if 0x2B740 <= cp <= 0x2B81F:
        return True
    if 0x2B820 <= cp <= 0x2CEAF:
        return True
    if 0x2CEB0 <= cp <= 0x2EBEF:
        return True
    if 0x3040 <= cp <= 0x309F:
        return True
    if 0x30A0 <= cp <= 0x30FF:
        return True
    if 0xAC00 <= cp <= 0xD7AF:
        return True
    if 0x3000 <= cp <= 0x303F:
        return True
    if 0xFF00 <= cp <= 0xFFEF:
        return True
    return False


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 2
    acc = 0.0
    for ch in text:
        cp = ord(ch)
        if _is_cjk_char(cp):
            acc += 1.0 / 3.0
        else:
            acc += 1.0 / 4.0
    import math

    return max(2, math.ceil(max(0.0, acc)) + 2)


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    valid_roles = {"system", "user", "assistant", "tool", "function"}
    for msg in messages:
        role = msg.get("role", "")
        if role in valid_roles:
            total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
    return total


def build_system_prompt_template(
    *,
    has_evidence: bool,
    thinking_level: str,
    deep_hint: str,
    tenant_name: str = "当前企业",
) -> str:
    """构造三层结构的 System Prompt（借鉴 Grok prompt/ 分层设计）。

    返回 str（注意：返回的是 role=system 消息的 content 文本本身，不带 messages 包裹）。
    """
    role_layer = (
        f"你是 EKB 企业知识库 AI 助手，服务于 {tenant_name} 内部用户。\n"
        "回答原则：\n"
        "- 使用简洁、专业的中文，避免套话；先说结论再补充细节。\n"
        "- 引用数字 / 时间 / 人名时尽量给出出处（若有证据对应）。\n"
        "- 符合企业合规要求，不讨论违规、违法、政治敏感内容；不泄露第三方商业秘密。\n"
        "- 当用户要求执行代码 / 命令 / 对外 HTTP 请求时，仅回答 EKB 范围内的知识，明确拒绝越权操作。"
    )

    if has_evidence:
        evidence_layer = (
            "本回答必须严格基于下面 Context 中的企业知识库证据信息，不要编造。\n"
            "如果提供的证据不足以回答问题，明确回复「证据不足，无法确认」并说明还需要什么补充信息。"
        )
    else:
        evidence_layer = (
            "重要提示：用户的问题超出了当前已授权的企业知识库范围，没有可用证据；\n"
            "因此你可以基于通用知识直接回答，但回答开头必须先用一句话声明：\n"
            "「以下回答未参考企业知识库，可能与企业内部规定不一致，仅作通用参考」。\n"
            "不要让用户去补充文档；若问题本身缺条件，礼貌提问补充。"
        )

    parts = [role_layer, evidence_layer]
    if deep_hint:
        parts.append(deep_hint.strip())

    return "\n\n".join(parts)


def _map_role_ekb_to_llm(ekb_role: str) -> str:
    """内部辅助：把 EKB store 里的 role 值映射为 LLM 原生 role。

    EKB: "USER" / "ASSISTANT" / "SYSTEM"
    LLM:  "user" / "assistant" / "system"
    如果传的已经是小写 user/assistant/system（qa.py 手动构造时），原样返回。
    其它非法值（大写非这三个）：raise ValueError(f"Unknown message role: {ekb_role!r}")。
    """
    if ekb_role in {"user", "assistant", "system"}:
        return ekb_role
    mapping = {
        "USER": "user",
        "ASSISTANT": "assistant",
        "SYSTEM": "system",
    }
    if ekb_role in mapping:
        return mapping[ekb_role]
    raise ValueError(f"Unknown message role: {ekb_role!r}")


def build_messages_for_generation(
    *,
    system_prompt: str,
    history_messages: list[dict],
    evidence_texts: list[str],
    current_question: str,
    image_data_urls: list[str] | None = None,
) -> list[dict]:
    """把 system_prompt + 历史消息 + 证据上下文 + 当前问题 组装成 LLM messages 数组。

    输出顺序（严格）：
    [0]  {role: "system", content: system_prompt}
    [1..M] 逐条展开 history_messages（已经按时间升序，role 经 _map_role_ekb_to_llm 转换）；
           每条 history_messages 的输入格式为 {role, content} 即可；其它字段忽略。
    [M+1] 如果 evidence_texts 非空，插入一条"证据上下文"消息；
    [最后一条] {role: "user", content: current_question}
    """
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    for msg in history_messages:
        role = _map_role_ekb_to_llm(msg["role"])
        messages.append({"role": role, "content": msg["content"]})

    if evidence_texts:
        evidence_content = "以下为当前问题对应的授权知识库证据：\n" + "\n\n".join(
            f"[证据{i+1}]\n{text}" for i, text in enumerate(evidence_texts)
        )
        messages.append({"role": "system", "name": "context", "content": evidence_content})

    if image_data_urls:
        content: list[dict] = [{"type": "text", "text": current_question}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_data_urls)
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": current_question})
    return messages


def _image_data_urls(image_attachments: list[dict] | None) -> list[str]:
    urls: list[str] = []
    for item in image_attachments or []:
        raw = item.get("bytes") if isinstance(item, dict) else None
        mime = str(item.get("mime") or "application/octet-stream") if isinstance(item, dict) else "application/octet-stream"
        if isinstance(raw, bytes) and raw:
            urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}")
    return urls


def _contains_image_content(messages: list[dict]) -> bool:
    return any(
        isinstance(message.get("content"), list)
        and any(part.get("type") == "image_url" for part in message["content"] if isinstance(part, dict))
        for message in messages
    )


class LlmError(Exception):
    """LLM 调用失败（所有 provider 均不可用）。"""


class CircuitOpenError(LlmError):
    """熔断器开闸，请求被短路（未发往外网）。"""


def _model_for_provider(provider: ModelProvider, requested: str | None) -> str:
    """Translate the UI provider/model id to the upstream model id."""
    if not requested or requested == provider.name:
        return provider.model
    prefix = f"{provider.name.split('/', 1)[0]}/"
    if requested.startswith(prefix):
        return requested[len(prefix) :]
    if requested.endswith(f"/{provider.model}"):
        return provider.model
    return requested


def _resolve_reasoning_effort(thinking_level: str) -> str:
    """五档 thinking_level → DeepSeek V4 原生 reasoning_effort。

    light / mild      → low      （快速、省钱）
    medium（默认）    → medium   （平衡质量与速度）
    high              → high     （强推理）
    extreme           → max      （最强推理，消耗更多额度）
    对非 V4 provider 来说，忽略 reasoning_effort 也兼容（不认识就忽略，不会报错）。
    """
    if thinking_level in {"light", "mild"}:
        return "low"
    if thinking_level == "medium":
        return "medium"
    if thinking_level == "high":
        return "high"
    if thinking_level == "extreme":
        return "max"
    return "medium"


def _resolve_temperature(thinking_level: str, base_t: Optional[float]) -> float:
    """五档 thinking_level → 采样温度。"""
    if thinking_level == "light":
        return 0.2 if base_t is None else max(0.1, min(base_t - 0.1, 0.4))
    if thinking_level == "mild":
        return 0.35 if base_t is None else max(0.15, min(base_t, 0.55))
    if thinking_level == "medium":
        return 0.5 if base_t is None else max(0.25, min(base_t + 0.1, 0.7))
    if thinking_level == "high":
        return 0.65 if base_t is None else max(0.35, min(base_t + 0.25, 0.85))
    if thinking_level == "extreme":
        return 0.85 if base_t is None else max(0.5, min(base_t + 0.4, 0.98))
    return 0.5


def _normalize_thinking_level(thinking_level: Optional[str], deep_thinking: bool) -> str:
    """把任意旧/非法输入归一为五档之一；并做 deep_thinking 兼容。

    - 旧前端值（off/standard/intensive）自动映射：off→light，standard→medium，intensive→high。
    - deep_thinking=False 强制降为 light（即关闭推理增强）。
    """
    legacy_map = {
        "off": "light",
        "standard": "medium",
        "intensive": "high",
    }
    raw = (thinking_level or "medium").lower()
    tl = legacy_map.get(raw, raw)
    if tl not in {"light", "mild", "medium", "high", "extreme"}:
        tl = "medium"
    if not deep_thinking and tl != "light":
        tl = "light"
    if deep_thinking and tl == "light":
        # 兼容旧版传参：明确开了深度思考但选了 light → 升为 mild
        tl = "mild"
    return tl


def _deep_hint_for(thinking_level: str) -> str:
    """按档位返回推理要求的 prompt 片段（越高端越严格），light 返回空串。"""
    if thinking_level == "light":
        return ""
    if thinking_level == "mild":
        return (
            "\n\n思考增强（Mild）：\n"
            "- 回答前快速梳理问题关键词，避免跑题\n"
            "- 有多个要点时分点列出，结构清晰\n"
        )
    if thinking_level == "medium":
        return (
            "\n\n思考增强（Medium，推荐档）：\n"
            "- 先分步骤分析问题与依据的对应关系\n"
            "- 对多项依据交叉验证，有矛盾时标明最可信版本\n"
            "- 结论前先给 1-2 条要点，保证可追溯\n"
        )
    if thinking_level == "high":
        return (
            "\n\n思考增强（High）：\n"
            "- 对每个关键断言，先列出可能的多种解释，再逐一排除\n"
            "- 引用数字/百分比/时间节点时给出取值区间或置信度\n"
            "- 答案结论前必须先列出推理要点（至少 3 条）\n"
            "- 输出采用「分点 + 关键依据 + 最终结论」三段式\n"
        )
    # extreme
    return (
        "\n\n思考增强（Extreme，最高档）：\n"
        "- 先以「反证法」验证结论，再给出正面答案\n"
        "- 对每个关键断言，先列出 ≥3 种解释，再逐一排除并说明排除理由\n"
        "- 引用数字/百分比/时间节点：给出下界 / 上界 / 中值区间 + 置信度 + 来源说明\n"
        "- 结论前必须先列出推理要点（至少 5 条），并标记哪条是核心依据\n"
        "- 回答必须采用「推理链要点 + 关键依据表 + 最终结论」三段式\n"
        "- 最终结论末尾说明：「本回答使用了极致推理模式（Extreme），耗时/额度更高」\n"
    )


def _call_provider(
    provider: ModelProvider,
    messages: list[dict],
    *,
    temperature: float,
    model: str | None = None,
    reasoning_effort: Optional[str] = None,
) -> str:
    """调用单个 provider 的 chat completions，返回 assistant 文本。失败抛 LlmError。

    reasoning_effort：DeepSeek V4 原生支持 low/medium/high/max；非 V4 会忽略该字段。
    """
    settings = get_settings()
    payload: dict = {
        "model": model or provider.model,
        "messages": messages,
        "max_tokens": settings.llm_max_tokens,
        "temperature": temperature,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
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
    reasoning_effort: Optional[str] = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """调用 chat completions：依次尝试 provider 直到成功，失败抛 LlmError。

    provider_name 指定时优先用该 provider（找不到则回退到完整列表）。
    model 指定时对命中的 provider 覆盖模型名（M2-7 租户级模型路由）。
    reasoning_effort：透传（DeepSeek V4 原生支持 low/medium/high/max）。
    """
    breaker = get_circuit_breaker()
    if not breaker.allow_request():
        LLM_CALLS.inc(provider="circuit_open", status="short_circuited")
        raise CircuitOpenError("LLM 熔断器开闸，请求被短路")

    providers = get_runtime_chat_providers(tenant_id=tenant_id, user_id=user_id)
    has_image_content = _contains_image_content(messages)
    if has_image_content:
        providers = [provider for provider in providers if provider.supports_vision]
    if provider_name:
        preferred = [p for p in providers if p.name == provider_name]
        if not preferred:
            preferred = [
                p for p in providers
                if "/" in p.name and p.name.split("/", 1)[0] == provider_name
            ]
        providers = preferred or providers
    if has_image_content and model:
        providers = [
            provider
            for provider in providers
            if provider.model == model
            or provider.name == model
            or provider.name.endswith(f"/{model}")
        ]
    if not providers:
        if has_image_content:
            raise LlmError("请求选择的远程 LLM 模型不支持 Vision")
        raise LlmError("未配置任何 chat provider（请在「AI 模型配置中心」启用一个服务商）")

    settings = get_settings()
    effective_temperature = settings.llm_temperature if temperature is None else temperature
    last_exc: Optional[Exception] = None
    for provider in providers:
        t0 = time.perf_counter()
        try:
            result = _call_provider(
                provider, messages,
                temperature=effective_temperature,
                model=_model_for_provider(provider, model),
                reasoning_effort=reasoning_effort,
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


def rewrite_query(
    question: str,
    *,
    route=None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> list[str]:
    """用 LLM 把用户问题改写成 3-5 个检索 query，扩大召回。

    返回包含原始问题在内的去重 query 列表。无 chat provider 或失败时仅保留原问题，
    不使用本地模型或伪造改写结果。
    route 为 M2-7 租户级模型路由（决定改写所用的 provider/model）。
    tenant_id/user_id：从「AI 模型配置中心」读取用户启用的 provider。

    M4-1：接入改写结果缓存。改写是语言操作（问题→检索词），无授权数据，
    跨租户安全；键含 LLM 模型版本，换模型/路由时自动失效。
    LLM 失败结果不缓存（避免 transient failure 被固化）。
    """
    providers = get_runtime_chat_providers(tenant_id=tenant_id, user_id=user_id)
    if not providers:
        return [question]

    settings = get_settings()
    # 缓存键：模型版本 + 问题哈希。route 只切 provider 不切 model 时结果可复用。
    model_version = (route.model if route else None) or settings.llm_model or (
        providers[0].model if providers else ""
    )
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
            tenant_id=tenant_id,
            user_id=user_id,
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


def generate_answer(
    question: str,
    evidence_texts: list[str],
    *,
    route=None,
    deep_thinking: bool = True,
    thinking_level: str = "medium",  # light / mild / medium / high / extreme
    history_messages: list[dict] | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """基于证据 + 可选历史生成答案。
    当 history_messages=None（旧调用方 / 灰度关闭）时：走原来的单条 user prompt 分支（一字不动保留）。
    当 history_messages 非空：走多轮分支，system_prompt + history + [evidence context] + question。
    """
    providers = get_runtime_chat_providers(tenant_id=tenant_id, user_id=user_id)
    if not providers:
        raise LlmError("未配置 chat provider（请在「AI 模型配置中心」启用一个服务商）")

    tl = _normalize_thinking_level(thinking_level, deep_thinking)
    reasoning_effort = _resolve_reasoning_effort(tl)
    deep_hint = _deep_hint_for(tl)
    evidence_texts = list(evidence_texts or [])
    effective_deep_thinking = tl != "light"
    settings = get_settings()
    temperature = _resolve_temperature(tl, settings.llm_temperature)

    if history_messages is None:
        context = "\n\n".join(f"[证据{i + 1}]\n{text}" for i, text in enumerate(evidence_texts))

        if evidence_texts:
            thinking_nl = "- 答案必须包含证据中与问题相关的关键术语和具体步骤\n" if effective_deep_thinking else ""
            prompt = (
                "你是企业知识库问答助手。基于以下授权知识库证据回答问题。\n\n"
                "要求：\n"
                "- 只使用上述证据中的信息，不要编造\n"
                f"{thinking_nl}"
                "- 简洁直接，先给结论再补充细节\n"
                "- 如果证据不足以回答，回复「证据不足，无法确认」\n"
                f"{deep_hint}\n"
                f"{context}\n\n"
                f"问题：{question}\n\n"
                "回答："
            )
        else:
            prompt = (
                "你是企业 AI 助手。\n\n"
                "重要提示：用户的问题超出了当前已授权的企业知识库范围，没有可用证据；\n"
                "因此你应当基于你的通用知识直接给出回答，不要强迫用户去补充文档，\n"
                "但必须在回答开头先用一句话说明「以下回答未参考企业知识库，可能与企业内部规定不一致，仅作通用参考」。\n"
                "要求：\n"
                "- 若问题有明确答案，直接给出结论 + 要点；涉及数值预测/未来趋势时给出主流机构（IMF/世界银行等）常见区间并注明年份和来源\n"
                "- 若问题本身信息不足，用一句话说明原因并礼貌提问补充条件；不要只说「请换一种问法」\n"
                f"{deep_hint}\n"
                f"问题：{question}\n\n"
                "回答："
            )
        return chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            provider_name=route.provider_name if route else None,
            model=route.model if route else None,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    else:
        has_evidence = bool(evidence_texts)
        system_prompt = build_system_prompt_template(
            has_evidence=has_evidence,
            thinking_level=tl,
            deep_hint=deep_hint,
        )
        messages = build_messages_for_generation(
            system_prompt=system_prompt,
            history_messages=history_messages,
            evidence_texts=evidence_texts,
            current_question=question,
        )
        return chat(
            messages,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            provider_name=route.provider_name if route else None,
            model=route.model if route else None,
            tenant_id=tenant_id,
            user_id=user_id,
        )


# ---- M4-3 LLM 流式生成 ----


def _call_provider_stream(
    provider: ModelProvider,
    messages: list[dict],
    *,
    temperature: float,
    model: str | None = None,
    reasoning_effort: Optional[str] = None,
):
    """调用单个 provider 的 streaming chat completions，yield content delta。

    使用 OpenAI 兼容的 SSE 流式协议（stream=true）。
    reasoning_effort：DeepSeek V4 原生支持 low/medium/high/max；非 V4 会忽略该字段。
    """
    settings = get_settings()
    payload: dict = {
        "model": model or provider.model,
        "messages": messages,
        "max_tokens": settings.llm_max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    request = urllib.request.Request(provider.base_url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(request, timeout=provider.timeout_seconds)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LlmError(f"[{provider.name}] 流式连接失败: {exc}") from exc

    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
            if delta:
                yield delta
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise LlmError(f"[{provider.name}] 流式解析失败: {exc}") from exc
    finally:
        resp.close()


def chat_stream(
    messages: list[dict],
    *,
    temperature: Optional[float] = None,
    provider_name: Optional[str] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    observer: ProviderObserver | None = None,
):
    """流式 chat completions：yield content delta 字符串。

    与 chat() 相同的熔断器保护和多 provider fallback 逻辑。
    reasoning_effort：透传（DeepSeek V4 原生支持 low/medium/high/max）。
    """
    breaker = get_circuit_breaker()
    if not breaker.allow_request():
        LLM_CALLS.inc(provider="circuit_open", status="short_circuited")
        raise CircuitOpenError("LLM 熔断器开闸，请求被短路")

    providers = get_runtime_chat_providers(tenant_id=tenant_id, user_id=user_id)
    has_image_content = _contains_image_content(messages)
    if has_image_content:
        providers = [provider for provider in providers if provider.supports_vision]
    requested_provider_matched = True
    if provider_name:
        preferred = [p for p in providers if p.name == provider_name]
        if not preferred:
            preferred = [
                p for p in providers
                if "/" in p.name and p.name.split("/", 1)[0] == provider_name
            ]
        requested_provider_matched = bool(preferred)
        providers = preferred or providers
    if has_image_content and model:
        providers = [
            provider
            for provider in providers
            if provider.model == model
            or provider.name == model
            or provider.name.endswith(f"/{model}")
        ]
    if not providers:
        if has_image_content:
            raise LlmError("请求选择的远程 LLM 模型不支持 Vision")
        raise LlmError("未配置任何 chat provider（请在「AI 模型配置中心」启用一个服务商）")

    settings = get_settings()
    effective_temperature = settings.llm_temperature if temperature is None else temperature
    last_exc: Optional[Exception] = None
    for provider_index, provider in enumerate(providers):
        t0 = time.perf_counter()
        try:
            gen = _call_provider_stream(
                provider, messages,
                temperature=effective_temperature,
                model=_model_for_provider(provider, model),
                reasoning_effort=reasoning_effort,
            )
            # peek 第一个 token 确认 provider 可用
            try:
                first = next(gen)
            except StopIteration:
                _notify_provider_observer(
                    observer,
                    provider,
                    provider_index > 0 or not requested_provider_matched,
                )
                breaker.track_success()
                LLM_CALLS.inc(provider=provider.name, status="success")
                LLM_DURATION.observe(time.perf_counter() - t0, provider=provider.name)
                return

            breaker.track_success()
            LLM_CALLS.inc(provider=provider.name, status="success")
            LLM_DURATION.observe(time.perf_counter() - t0, provider=provider.name)

            _notify_provider_observer(
                observer,
                provider,
                provider_index > 0 or not requested_provider_matched,
            )

            yield first
            yield from gen
            return
        except LlmError as exc:
            LLM_CALLS.inc(provider=provider.name, status="failed")
            last_exc = exc
            logger.warning("chat_stream provider %s 不可用: %s", provider.name, exc)
            continue

    breaker.track_failure()
    raise LlmError(f"所有 chat provider 均失败: {last_exc}")


def generate_answer_stream(
    question: str,
    evidence_texts: list[str],
    *,
    route=None,
    deep_thinking: bool = True,
    thinking_level: str = "medium",  # light / mild / medium / high / extreme
    history_messages: list[dict] | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    image_attachments: list[dict] | None = None,
    observer: ProviderObserver | None = None,
):
    """流式生成答案：yield content delta。

    thinking_level 五档真实生效：
      - 温度 sampling（light 最低，extreme 最高）
      - prompt 推理要求严格度（light 空，extreme 要推理链 5 点 + 反证 + 区间）
      - 透传 reasoning_effort 到 DeepSeek V4 原生（low/medium/high/max）
    """
    providers = get_runtime_chat_providers(tenant_id=tenant_id, user_id=user_id)
    if not providers:
        raise LlmError("未配置 chat provider（请在「AI 模型配置中心」启用一个服务商）")

    tl = _normalize_thinking_level(thinking_level, deep_thinking)
    reasoning_effort = _resolve_reasoning_effort(tl)
    deep_hint = _deep_hint_for(tl)
    evidence_texts = list(evidence_texts or [])
    effective_deep_thinking = tl != "light"
    image_data_urls = _image_data_urls(image_attachments)
    settings = get_settings()
    temperature = _resolve_temperature(tl, settings.llm_temperature)

    if history_messages is None:
        context = "\n\n".join(f"[证据{i + 1}]\n{text}" for i, text in enumerate(evidence_texts))

        if evidence_texts:
            thinking_nl = "- 答案必须包含证据中与问题相关的关键术语和具体步骤\n" if effective_deep_thinking else ""
            prompt = (
                "你是企业知识库问答助手。基于以下授权知识库证据回答问题。\n\n"
                "要求：\n"
                "- 只使用上述证据中的信息，不要编造\n"
                f"{thinking_nl}"
                "- 简洁直接，先给结论再补充细节\n"
                "- 如果证据不足以回答，回复「证据不足，无法确认」\n"
                f"{deep_hint}\n"
                f"{context}\n\n"
                f"问题：{question}\n\n"
                "回答："
            )
        else:
            prompt = (
                "你是企业 AI 助手。\n\n"
                "重要提示：用户的问题超出了当前已授权的企业知识库范围，没有可用证据；\n"
                "因此你应当基于你的通用知识直接给出回答，不要强迫用户去补充文档，\n"
                "但必须在回答开头先用一句话说明「以下回答未参考企业知识库，可能与企业内部规定不一致，仅作通用参考」。\n"
                "要求：\n"
                "- 若问题有明确答案，直接给出结论 + 要点；涉及数值预测/未来趋势时给出主流机构（IMF/世界银行等）常见区间并注明年份和来源\n"
                "- 若问题本身信息不足，用一句话说明原因并礼貌提问补充条件；不要只说「请换一种问法」\n"
                f"{deep_hint}\n"
                f"问题：{question}\n\n"
                "回答："
            )
        user_content: str | list[dict] = prompt
        if image_data_urls:
            user_content = [{"type": "text", "text": prompt}]
            user_content.extend(
                {"type": "image_url", "image_url": {"url": url}} for url in image_data_urls
            )
        yield from chat_stream(
            [{"role": "user", "content": user_content}],
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            provider_name=route.provider_name if route else None,
            model=route.model if route else None,
            tenant_id=tenant_id,
            user_id=user_id,
            observer=observer,
        )
    else:
        has_evidence = bool(evidence_texts)
        system_prompt = build_system_prompt_template(
            has_evidence=has_evidence,
            thinking_level=tl,
            deep_hint=deep_hint,
        )
        messages = build_messages_for_generation(
            system_prompt=system_prompt,
            history_messages=history_messages,
            evidence_texts=evidence_texts,
            current_question=question,
            image_data_urls=image_data_urls,
        )
        yield from chat_stream(
            messages,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            provider_name=route.provider_name if route else None,
            model=route.model if route else None,
            tenant_id=tenant_id,
            user_id=user_id,
            observer=observer,
        )


@dataclass
class CompactionReport:
    rounds_compressed: int
    tokens_before: int
    tokens_after: int
    method: str
    detail: str | None = None


def _format_older_items_for_summary(older_items: list[dict]) -> str:
    parts: list[str] = []
    for msg in older_items:
        role = msg.get("role", "")
        role_lc = role.lower()
        if role_lc == "user":
            prefix = "U"
        elif role_lc == "assistant":
            prefix = "A"
        elif role_lc == "system":
            prefix = "S"
        else:
            prefix = "?"
        content = msg.get("content", "")
        parts.append(f"{prefix}: {content}")
    return "\n".join(parts)


def maybe_compact_history(
    *,
    thinking_level: str,
    deep_thinking: bool,
    history_messages: list[dict],
    evidence_texts: list[str],
    current_question: str,
    settings,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> tuple[list[dict], CompactionReport | None]:
    tl = _normalize_thinking_level(thinking_level, deep_thinking)
    deep_hint = _deep_hint_for(tl)
    has_evidence = bool(evidence_texts)
    system_prompt = build_system_prompt_template(
        has_evidence=has_evidence,
        thinking_level=tl,
        deep_hint=deep_hint,
    )
    full_messages = build_messages_for_generation(
        system_prompt=system_prompt,
        history_messages=history_messages,
        evidence_texts=evidence_texts,
        current_question=current_question,
    )
    total_tokens = estimate_messages_tokens(full_messages)
    threshold = int(settings.llm_context_window * settings.llm_compaction_ratio)

    if total_tokens <= threshold:
        return (history_messages, None)

    n = len(history_messages)
    n_keep_pairs = settings.compaction_recent_rounds_keep
    n_keep_msgs = n_keep_pairs * 2

    if n_keep_msgs >= n:
        older_items: list[dict] = []
        recent_pairs = list(history_messages)
    else:
        older_items = list(history_messages[: n - n_keep_msgs])
        recent_pairs = list(history_messages[n - n_keep_msgs :])

    if not older_items:
        return (
            history_messages,
            CompactionReport(
                rounds_compressed=0,
                tokens_before=total_tokens,
                tokens_after=total_tokens,
                method="skipped",
                detail="older_items 为空，不压缩，等 provider 自己兜底",
            ),
        )

    older_tokens_est = estimate_messages_tokens(older_items)

    older_items_text = _format_older_items_for_summary(older_items)
    compression_prompt = (
        "下面是一段较早的对话历史，请用不超过 200 字的中文摘要概括关键信息，保留："
        "所有被要求\"记住 X=Y\"的赋值、所有名词定义、用户提到的偏好或约束条件，不要编造。\n\n"
        "对话历史：\n"
        f"{older_items_text}\n\n"
        "请直接输出摘要文本："
    )

    try:
        summary_text = chat(
            [{"role": "user", "content": compression_prompt}],
            temperature=0.1,
            reasoning_effort=None,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except LlmError:
        summary_text = None

    llm_summary_adopted = False
    if summary_text is not None:
        summary_single_msg = [{"role": "system", "content": "对话历史摘要：" + summary_text}]
        summary_tokens = estimate_messages_tokens(summary_single_msg)
        ratio_target = settings.compaction_summary_ratio_target
        if summary_tokens < older_tokens_est * ratio_target:
            new_history = [summary_single_msg[0]] + recent_pairs
            llm_summary_adopted = True
            method = "llm_summary"
            rounds_compressed = max(1, len(older_items) // 2)
            detail = f"LLM 摘要压缩成功：older_tokens={older_tokens_est}, summary_tokens={summary_tokens}"

    if not llm_summary_adopted:
        method = "hard_truncate"
        older_remaining = list(older_items)
        original_older_len = len(older_items)
        for _ in range(3):
            if not older_remaining:
                break
            history_candidate = older_remaining + recent_pairs
            cand_system_prompt = build_system_prompt_template(
                has_evidence=has_evidence,
                thinking_level=tl,
                deep_hint=deep_hint,
            )
            cand_full = build_messages_for_generation(
                system_prompt=cand_system_prompt,
                history_messages=history_candidate,
                evidence_texts=evidence_texts,
                current_question=current_question,
            )
            cand_total = estimate_messages_tokens(cand_full)
            if cand_total <= threshold:
                break
            drop_count = max(1, math.ceil(len(older_remaining) / 3))
            older_remaining = older_remaining[drop_count:]

        new_history = older_remaining + recent_pairs
        rounds_compressed = max(
            1, (original_older_len - len(older_remaining)) // 2
        )
        detail = (
            f"hard_truncate：older_items 从 {original_older_len} 条截到 {len(older_remaining)} 条"
        )

    new_system_prompt = build_system_prompt_template(
        has_evidence=has_evidence,
        thinking_level=tl,
        deep_hint=deep_hint,
    )
    new_full_messages = build_messages_for_generation(
        system_prompt=new_system_prompt,
        history_messages=new_history,
        evidence_texts=evidence_texts,
        current_question=current_question,
    )
    tokens_after = estimate_messages_tokens(new_full_messages)

    return (
        new_history,
        CompactionReport(
            rounds_compressed=rounds_compressed,
            tokens_before=total_tokens,
            tokens_after=tokens_after,
            method=method,
            detail=detail,
        ),
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

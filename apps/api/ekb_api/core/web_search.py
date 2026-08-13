"""Tavily 联网搜索客户端（异步 httpx，纯标准库之外只依赖 httpx/ekb_api 已有依赖）。

用法：
    from ekb_api.core.web_search import web_search_enabled, perform_web_search

    if web_search_enabled(settings):
        results = await perform_web_search(
            settings,
            question="今年公司净利润是多少？",
            max_results=5,
            topic="general",  # or "news"
            timeout_s=10.0,
        )
        # results: list[WebSearchResult]
        for r in results:
            print(r.title, r.url, r.published_date)
            print(r.as_evidence(index=1))

设计原则（面向稳定性而不是完备性）：
1) **零硬失败**：只要启用了 web_search，Tavily 抛任何异常（网络/鉴权/限流/解析）都不会阻断主 QA，
   只记录 warning 日志 + 返回空列表。调用方可以在 retrieval_completed 里看 web search 的命中数。
2) **通过 settings 读取**：Tavily 的 API key / base_url 都走 `EKB_TAVILY_API_KEY` / `EKB_TAVILY_BASE_URL`，
   这样 Docker 部署只需要把 key 写入 .env，不需要 `pip install tavily-python`。
3) **请求大小可控**：显式 `max_results`（默认 5，上限 10），topic 可选 `general / news`。
4) **与现有检索证据无缝合并**：`WebSearchResult.as_evidence(index=i)` 直接吐出和 [证据1]、[证据2]
   等知识库证据同格式的段落，便于 QA 主流程合并为 evidence_texts 后喂给 LLM。
5) **引用信息携带**：每条 result 有标题/URL/发布日期/来源。前端 citations 渲染里可以区分 kb 和 web。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ekb_api.core.config import Settings


TAVILY_BASE_URL_DEFAULT = "https://api.tavily.com"
MAX_RESULTS_CAP = 10  # 硬上限：避免 API quota 被大 query 快速耗尽


# ===================================================================
# 领域对象
# ===================================================================


@dataclass
class WebSearchResult:
    """一条 Tavily 搜索命中。"""

    title: str
    url: str
    snippet: str
    published_date: str | None = None
    score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # 兼容 RetrieverChunk 的最小属性子集，qa.py 合并 citation 时可以复用
    @property
    def content(self) -> str:
        return self.snippet

    @property
    def source(self) -> str:
        return "web"

    def as_evidence(self, index: int, *, section_path: str | None = None) -> str:
        """格式化为 LLM prompt 中的 [证据N] 块，和知识库证据格式一致。"""
        meta_bits = [self.title.strip() or "（无标题）"]
        if self.published_date:
            meta_bits.append(f"发布日期：{self.published_date}")
        meta_bits.append(f"来源：{self.url}")
        if section_path:
            meta_bits.append(f"章节：{section_path}")
        header = " | ".join(meta_bits)
        body = self.snippet.strip() or "（无正文摘要）"
        return f"[证据{index} — 联网搜索]\n{header}\n{body}"


@dataclass
class WebSearchSummary:
    """一次 perform_web_search 的调用侧元数据（用于 SSE 事件/审计日志）。

    - `enabled`: 调用方是否显式启用 & 后端具备 TAVILY_API_KEY
    - `attempted`: 是否真的发起了 Tavily 请求（否则 quota disabled / feature off）
    - `succeeded`: 请求 200 且解析出合法 results（可能 0 条）
    - `result_count`: 实际命中条数
    - `elapsed_ms`: 总耗时（含网络+JSON 解析）
    - `error_code`: 如果 attempted 但失败，记录错误码（WEBS_* 命名空间）
    - `error_message`: 对应错误的简短可展示说明
    """

    enabled: bool
    attempted: bool
    succeeded: bool
    result_count: int
    elapsed_ms: int = 0
    error_code: str | None = None
    error_message: str | None = None


# ===================================================================
# Feature flag 与配置读取
# ===================================================================


def get_tavily_settings(settings: Settings) -> tuple[str, str]:
    """返回 (api_key, base_url)；未配置时返回 ("", default_base)。

    约定：
    - EKB_TAVILY_API_KEY: 非空即启用
    - EKB_TAVILY_BASE_URL: 可选，默认 https://api.tavily.com
    """
    import os  # noqa: PLC0415

    api_key = (os.getenv("EKB_TAVILY_API_KEY") or "").strip()
    base_url = (os.getenv("EKB_TAVILY_BASE_URL") or "").strip() or TAVILY_BASE_URL_DEFAULT
    return api_key, base_url


def web_search_enabled(settings: Settings) -> bool:
    """返回「后端是否具备联网搜索能力」——用于 /qa/capabilities。

    决策条件：
    1) EKB_TAVILY_API_KEY 非空；
    2) 允许通过显式 EKB_WEB_SEARCH_ENABLED=false 强制关闭（灰度/演练）。
    """
    import os  # noqa: PLC0415

    force_off = (os.getenv("EKB_WEB_SEARCH_ENABLED") or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }
    if force_off:
        return False
    api_key, _ = get_tavily_settings(settings)
    return bool(api_key)


# ===================================================================
# 搜索实现（异步）
# ===================================================================


async def perform_web_search(
    settings: Settings,
    *,
    question: str,
    max_results: int = 5,
    topic: str = "general",
    timeout_s: float | None = None,
    include_raw_content: bool = False,
    extra_query_params: dict[str, Any] | None = None,
) -> tuple[list[WebSearchResult], WebSearchSummary]:
    """调用 Tavily /search，返回 (results, summary)。

    - 不抛异常：任何异常都包在 summary.error_code 里，返回空列表。
    - max_results 超过上限会被截断到 MAX_RESULTS_CAP。
    - topic 非法时回退到 general。
    - 返回顺序：按 Tavily 原始顺序（通常已按相关性排序），不做二次精排。
    """
    import time  # noqa: PLC0415

    from ekb_api.core.logging import get_logger  # noqa: PLC0415

    log = get_logger("ekb.web_search")
    t0 = time.perf_counter()

    enabled = web_search_enabled(settings)
    summary = WebSearchSummary(
        enabled=enabled,
        attempted=False,
        succeeded=False,
        result_count=0,
    )
    q = (question or "").strip()
    if not enabled or not q:
        return [], summary

    # 标准化参数
    if max_results <= 0:
        max_results = 5
    if max_results > MAX_RESULTS_CAP:
        max_results = MAX_RESULTS_CAP
    if topic not in ("general", "news"):
        topic = "general"

    api_key, base_url = get_tavily_settings(settings)
    if not api_key:
        return [], summary

    if timeout_s is None:
        timeout_s = float(getattr(settings, "search_timeout_seconds", 10.0) or 10.0)

    summary.attempted = True
    endpoint = base_url.rstrip("/") + "/search"
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": q,
        "max_results": int(max_results),
        "topic": topic,
        "include_answer": False,
        "include_raw_content": bool(include_raw_content),
        "search_depth": "basic",
    }
    if extra_query_params and isinstance(extra_query_params, dict):
        payload.update({k: v for k, v in extra_query_params.items() if k not in payload})

    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        summary.error_code = "WEBS_CLIENT_MISSING"
        summary.error_message = "部署缺少 httpx 依赖，请重建镜像（pyproject dev 依赖里已含 httpx）"
        log.warning("web_search aborted: httpx unavailable")
        return [], summary

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            follow_redirects=True,
        ) as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={
                    "user-agent": "ekb-web-search/1.0 (+https://trae.ai)",
                    "accept": "application/json",
                },
            )
            if resp.status_code >= 500:
                summary.error_code = "WEBS_UPSTREAM_5XX"
                summary.error_message = f"Tavily HTTP {resp.status_code}"
                log.warning(
                    "web_search upstream 5xx: url=%s status=%s body_preview=%s",
                    endpoint,
                    resp.status_code,
                    _preview(resp.text, 200),
                )
                return [], summary
            if resp.status_code == 429:
                summary.error_code = "WEBS_RATE_LIMITED"
                summary.error_message = "Tavily 限流（429），请稍后重试"
                log.warning("web_search rate limited (429)")
                return [], summary
            if resp.status_code == 401 or resp.status_code == 403:
                summary.error_code = "WEBS_AUTH_ERROR"
                summary.error_message = "Tavily API Key 无效或无权限"
                log.warning(
                    "web_search auth fail: status=%s body=%s",
                    resp.status_code,
                    _preview(resp.text, 200),
                )
                return [], summary
            if resp.status_code >= 400:
                summary.error_code = f"WEBS_UPSTREAM_{resp.status_code}"
                summary.error_message = f"Tavily HTTP {resp.status_code}"
                log.warning(
                    "web_search upstream %s: %s", resp.status_code, _preview(resp.text, 200)
                )
                return [], summary

            data = resp.json()
    except asyncio.TimeoutError:
        summary.error_code = "WEBS_TIMEOUT"
        summary.error_message = "联网搜索超时，已跳过"
        log.warning("web_search timeout after %ss", timeout_s)
        return [], summary
    except Exception as exc:  # noqa: BLE001
        summary.error_code = "WEBS_NETWORK_ERROR"
        summary.error_message = f"联网搜索失败：{_exc_summary(exc)}"
        log.warning("web_search network error: %s", repr(exc))
        return [], summary

    # 解析
    results_raw = data.get("results") if isinstance(data, dict) else None
    results: list[WebSearchResult] = []
    if isinstance(results_raw, list):
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            title = _strip_html(str(item.get("title") or ""))
            content = str(item.get("content") or item.get("snippet") or "").strip()
            content = _strip_html(content)
            if not title and not content:
                continue
            try:
                score = float(item.get("score")) if item.get("score") is not None else None
            except (TypeError, ValueError):
                score = None
            pub = item.get("published_date")
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=content,
                    published_date=str(pub).strip() if pub else None,
                    score=score,
                    raw=item,
                )
            )

    summary.succeeded = True
    summary.result_count = len(results)
    summary.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    log.info(
        "web_search done attempted=1 succeeded=1 count=%s elapsed_ms=%s query_preview=%s",
        summary.result_count,
        summary.elapsed_ms,
        _preview(q, 48),
    )
    return results, summary


# ===================================================================
# 工具函数
# ===================================================================


def merge_evidence(
    *,
    kb_chunks: list[Any],
    web_results: list[WebSearchResult],
    max_citations: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """把知识库 chunks 和联网搜索结果合并成：
    (evidence_texts: list[str], citations: list[dict])

    顺序约定：先知识库，后联网搜索；总数超过 max_citations 时优先保留知识库，截断 web_results。
    这样「证据不足」的情况下不会因为只取了 web 证据反而漏了 kb 里最相关的几条。

    citation 结构和 llm.py 里现有输出兼容（前端 AssistantComposer 按同样字段渲染）：
        { "index": int, "type": "kb"|"web", "title": str,
          "doc_id"|None, "url": str|None, "section_path": str|None,
          "published_date": str|None }
    """
    evidence_texts: list[str] = []
    citations: list[dict[str, Any]] = []
    budget = max(max_citations, 1)

    idx = 0
    # 1) 知识库（RetrieverChunk 预期具有 content / doc_title / doc_id / section_path 属性；
    #    兼容任何带 content 的 duck-typed 对象）
    for chunk in kb_chunks:
        if idx >= budget:
            break
        title = str(getattr(chunk, "doc_title", None) or getattr(chunk, "title", None) or "知识库文档").strip()
        doc_id = getattr(chunk, "doc_id", None)
        section_path = getattr(chunk, "section_path", None)
        section_hint = f"章节：{section_path}" if section_path else ""
        header_bits = [title]
        if section_hint:
            header_bits.append(section_hint)
        header = " | ".join(header_bits)
        body = str(getattr(chunk, "content", "") or "").strip()
        if not body:
            continue
        idx += 1
        evidence_texts.append(f"[证据{idx} — 知识库]\n{header}\n{body}")
        citations.append(
            {
                "index": idx,
                "type": "kb",
                "title": title,
                "doc_id": doc_id,
                "url": None,
                "section_path": section_path,
                "published_date": None,
            }
        )

    # 2) 联网搜索：把剩余预算留给 web
    web_budget = max(budget - idx, 0)
    for wr in web_results:
        if idx >= budget or web_budget <= 0:
            break
        web_budget -= 1
        idx += 1
        evidence_texts.append(
            wr.as_evidence(
                index=idx,
                section_path=wr.published_date,
            )
        )
        citations.append(
            {
                "index": idx,
                "type": "web",
                "title": wr.title or wr.url,
                "doc_id": None,
                "url": wr.url,
                "section_path": None,
                "published_date": wr.published_date,
            }
        )

    return evidence_texts, citations


# ===================================================================
# 内部 helpers
# ===================================================================


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    """简单去 HTML 标签并折叠空白，避免 Tavily 片段里带格式时污染 prompt。"""
    if not text:
        return ""
    t = _HTML_TAG_RE.sub(" ", text)
    t = _WHITESPACE_RE.sub(" ", t).strip()
    return t


def _preview(text: str | None, n: int) -> str:
    if text is None:
        return ""
    s = str(text).replace("\r", " ").replace("\n", " ")
    return s[:n] if len(s) <= n else s[: n - 1] + "…"


def _exc_summary(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc).strip() or repr(exc)
    return f"{name}: {msg[:160]}"

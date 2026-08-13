from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ekb_api.core.config import _split_csv, get_settings
from ekb_api.core.db import get_pgvector_health, init_db
from ekb_api.core.errors import install_error_handlers
from ekb_api.core.logging import configure_logging, get_logger
from ekb_api.core.metrics import HTTP_DURATION, HTTP_REQUESTS, registry
from ekb_api.core.tracing import configure_tracing, instrument_fastapi
from ekb_api.routers import (
    admin,
    analytics,
    apps,
    attachments,
    auth,
    chat_graph,
    content_governance,
    conversations,
    feedback,
    identity_v3,
    jobs,
    kb,
    kb_upload,
    knowledge_v3,
    llm,
    me,
    qa,
    search,
    trash,
)

settings = get_settings()


_LOCAL_SCHEDULER_DEFAULT_INTERVAL_SECONDS = 60
_LOCAL_SCHEDULER_MIN_INTERVAL_SECONDS = 1
_LOCAL_SCHEDULER_MAX_INTERVAL_SECONDS = 300


def _local_scheduler_interval_seconds(raw_value: str | None) -> int:
    if not isinstance(raw_value, str):
        return _LOCAL_SCHEDULER_DEFAULT_INTERVAL_SECONDS

    normalized_value = raw_value.strip()
    if not normalized_value.isascii() or not normalized_value.isdecimal():
        return _LOCAL_SCHEDULER_DEFAULT_INTERVAL_SECONDS

    try:
        interval_seconds = int(normalized_value)
    except ValueError:
        return _LOCAL_SCHEDULER_DEFAULT_INTERVAL_SECONDS

    if not (
        _LOCAL_SCHEDULER_MIN_INTERVAL_SECONDS
        <= interval_seconds
        <= _LOCAL_SCHEDULER_MAX_INTERVAL_SECONDS
    ):
        return _LOCAL_SCHEDULER_DEFAULT_INTERVAL_SECONDS
    return interval_seconds


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    scheduler_task: asyncio.Task | None = None
    if settings.environment == "development" and os.getenv(
        "EKB_LOCAL_SCHEDULER", "true"
    ).lower() in {"1", "true", "yes", "on"}:
        scheduler_interval_seconds = _local_scheduler_interval_seconds(
            os.getenv("EKB_LOCAL_SCHEDULER_INTERVAL")
        )

        async def _run_local_scheduler() -> None:
            from ekb_api.core.db import get_engine
            from ekb_api.services.ingest_worker import run_ingest_tick
            from ekb_api.services.scheduler import run_retention_tick

            while True:
                try:
                    await asyncio.to_thread(run_retention_tick)
                    await asyncio.to_thread(run_ingest_tick, get_engine())
                except Exception as exc:  # noqa: BLE001 - keep API alive; next tick retries
                    _log.warning("local.scheduler.tick_failed", error_type=type(exc).__name__)
                await asyncio.sleep(scheduler_interval_seconds)

        scheduler_task = asyncio.create_task(_run_local_scheduler())
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


configure_logging(settings.environment)
_log = get_logger("ekb.main")
init_db()
_log.info("ekb.startup", environment=settings.environment, api_port=settings.api_port)
configure_tracing(service_name="ekb-api", environment=settings.environment)
# 默认安全白名单：本机 + settings 显式配置 + EKB_ALLOWED_HOSTS env CSV
# 若配置 "*" 或部署在边缘（nginx 已做 Host 过滤）时，允许所有 Host
_base_hosts = ["127.0.0.1", "localhost", settings.api_host]
_extra = _split_csv(
    getattr(settings, "allowed_hosts_env", "") or os.getenv("EKB_ALLOWED_HOSTS", "")
)
if _extra:
    _base_hosts.extend(h for h in _extra if h and h not in _base_hosts)
allowed_hosts = _base_hosts
# 兼容旧逻辑：若用户明确开启宽松模式或列表里已有 "*" → 单元素 ["*"]
if "*" in allowed_hosts:
    allowed_hosts = ["*"]
if settings.environment == "test":
    if allowed_hosts != ["*"]:
        allowed_hosts.append("testserver")

app = FastAPI(
    title="EKB API",
    version="0.1.0",
    lifespan=_lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=allowed_hosts,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
    expose_headers=[
        "X-Request-Id",
        "X-Turn-Id",  # SSE v2: 返回 turn_id 给前端作为 cancel 路由参数
    ],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    if response.status_code >= 500:
        _log.error(
            "http.error",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            request_id=request.state.request_id,
        )
    response.headers["X-Request-Id"] = request.state.request_id
    # 指标采集：路径归一化（hex id → :id）避免高基数 label。
    path = _normalize_path(request.url.path)
    HTTP_REQUESTS.inc(method=request.method, path=path, status=str(response.status_code))
    HTTP_DURATION.observe(elapsed, method=request.method, path=path)
    from ekb_api.core.alerting import maybe_check_alerts  # noqa: PLC0415

    maybe_check_alerts()
    return response


def _normalize_path(path: str) -> str:
    """把路径中的 hex id 段（≥12 字符）归一化为 :id，控制 label 基数。"""
    segments = []
    for seg in path.split("/"):
        if len(seg) >= 12 and re.fullmatch(r"[0-9a-f]+", seg):
            segments.append(":id")
        else:
            segments.append(seg)
    return "/".join(segments)


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, object]:
    return {"status": "ok", "pgvector": get_pgvector_health()}


@app.get("/metrics", tags=["system"], include_in_schema=False)
def metrics() -> PlainTextResponse:
    """Prometheus exposition 端点（M4-4 可观测性）。"""
    return PlainTextResponse(registry.collect(), media_type="text/plain; version=0.0.4")


app.include_router(auth.router, prefix="/api/v1")
app.include_router(me.router, prefix="/api/v1")
app.include_router(identity_v3.router, prefix="/api/v1")
app.include_router(kb.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(qa.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(trash.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(apps.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(kb_upload.router, prefix="/api/v1")
app.include_router(content_governance.router, prefix="/api/v1")
app.include_router(knowledge_v3.router, prefix="/api/v1")
app.include_router(llm.router, prefix="/api/v1")
app.include_router(chat_graph.router, prefix="/api/v1")
app.include_router(attachments.router, prefix="/api/v1")

install_error_handlers(app)
instrument_fastapi(app)

from __future__ import annotations

import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ekb_api.core.config import get_settings
from ekb_api.core.db import init_db
from ekb_api.core.errors import install_error_handlers
from ekb_api.core.metrics import HTTP_DURATION, HTTP_REQUESTS, registry
from ekb_api.routers import admin, auth, conversations, feedback, kb, me, qa, search

settings = get_settings()
init_db()
allowed_hosts = ["127.0.0.1", "localhost", settings.api_host]
if settings.environment == "test":
    allowed_hosts.append("testserver")

app = FastAPI(
    title="EKB API",
    version="0.1.0",
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
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    response.headers["X-Request-Id"] = request.state.request_id
    # 指标采集：路径归一化（hex id → :id）避免高基数 label。
    path = _normalize_path(request.url.path)
    HTTP_REQUESTS.inc(method=request.method, path=path, status=str(response.status_code))
    HTTP_DURATION.observe(elapsed, method=request.method, path=path)
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
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics", tags=["system"], include_in_schema=False)
def metrics() -> PlainTextResponse:
    """Prometheus exposition 端点（M4-4 可观测性）。"""
    return PlainTextResponse(registry.collect(), media_type="text/plain; version=0.0.4")


app.include_router(auth.router, prefix="/api/v1")
app.include_router(me.router, prefix="/api/v1")
app.include_router(kb.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(qa.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")

install_error_handlers(app)

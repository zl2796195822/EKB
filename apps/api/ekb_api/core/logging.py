"""M4-4 结构化日志：基于 structlog 的 JSON 渲染器。

开发环境（EKB_ENV != production）使用彩色 ConsoleRenderer 方便阅读；
生产环境使用 JSONRenderer，每行一条结构化 JSON 日志，便于 Elasticsearch/Loki 采集。

使用方式：
    from ekb_api.core.logging import get_logger
    log = get_logger(__name__)
    log.info("qa.ask.start", question_len=len(question), kb_count=len(kb_ids))
"""
from __future__ import annotations

import logging
import sys

import structlog

_configured = False


def configure_logging(environment: str = "development") -> None:
    """在应用启动时调用一次，配置 structlog 全局处理链。"""
    global _configured
    if _configured:
        return
    _configured = True

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if environment == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    # 静默 uvicorn 自带 handler，避免重复输出。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvi_logger = logging.getLogger(name)
        uvi_logger.handlers.clear()
        uvi_logger.propagate = True


def get_logger(name: str = "ekb") -> structlog.stdlib.BoundLogger:
    """获取绑定了 name 的 structlog logger。"""
    return structlog.get_logger(name)

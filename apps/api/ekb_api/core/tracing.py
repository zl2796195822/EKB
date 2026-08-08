"""M4-4 分布式 trace：OpenTelemetry SDK。

默认使用 OTLP gRPC exporter（通过 OTEL_EXPORTER_OTLP_ENDPOINT 配置）；
未配置端点时静默降级为 NoOp exporter，不阻断服务启动。

关键 span：
  - HTTP 请求（由 FastAPI instrumentor 自动注入）
  - qa.ask（问答完整流程）
  - qa.retrieval（检索阶段）
  - qa.generation（生成阶段）
  - llm.call（每次 LLM 调用）
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_tracer_provider: TracerProvider | None = None


def configure_tracing(service_name: str = "ekb-api", environment: str = "development") -> None:
    """在应用启动时调用一次，配置 TracerProvider。"""
    global _tracer_provider
    if _tracer_provider is not None:
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.1.0",
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,  # noqa: PLC0415
            )

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            # opentelemetry-exporter-otlp-proto-grpc 未安装，降级到控制台输出。
            if environment != "production":
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif environment != "production":
        # 开发环境：不输出 trace（避免噪声），仅注册 provider 供业务打 span。
        pass

    trace.set_tracer_provider(provider)
    _tracer_provider = provider


def get_tracer(name: str = "ekb") -> trace.Tracer:
    """获取指定组件的 Tracer。"""
    return trace.get_tracer(name)


def instrument_fastapi(app) -> None:  # type: ignore[type-arg]
    """为 FastAPI app 注入自动 HTTP span。"""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001
        pass  # instrument 失败不阻断服务

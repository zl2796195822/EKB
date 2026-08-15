"""Vision capability detection for image attachments (PH5).

Per ``spec/02-conversation-engine-spec.md`` §9, Vision selection order is:

1. **Native Vision** — selected model supports vision → image passed as
   structured Provider content, ``usage_mode = VISION``,
   ``image_artifacts.method = 'NATIVE_VISION'``.
2. **Remote Vision Adapter** — configured remote OCR/caption service →
   ``usage_mode = OCR_FALLBACK``, ``image_artifacts.method = 'OCR'`` or
   ``'OCR_CAPTION'`` (see ``ocr.py``).
3. **Explicit failure** — neither available → ``VisionUnavailable`` with
   error code ``VISION_UNAVAILABLE``.  Never silently drops an image.

The decision is made by ``VisionCapability`` which consults the runtime chat
providers for a vision flag. We expose a protocol + a default implementation
that reads ``get_runtime_chat_providers`` so the behaviour is real when a
vision model is configured and safe (False) otherwise. Tests inject a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

#: Error code surfaced to the API/frontend when no Vision path is available.
VISION_UNAVAILABLE = "VISION_UNAVAILABLE"


class VisionUnavailable(RuntimeError):
    """Neither native Vision nor a remote adapter is configured.

    Raised per spec 02 §9: if an image cannot be processed, the user must be
    explicitly told — silent removal is prohibited.
    """

    _DEFAULT_MSG = "无可用 Vision 路径：模型不支持图片且未配置远程适配器"

    def __init__(self, message: str = _DEFAULT_MSG) -> None:
        super().__init__(message)
        self.error_code = VISION_UNAVAILABLE


class VisionCapability(Protocol):
    def supports_vision(self) -> bool: ...


def _provider_supports_vision(provider: object) -> bool:
    flag = getattr(provider, "supports_vision", None)
    if callable(flag):
        try:
            return bool(flag())
        except Exception:  # noqa: BLE001
            return False
    return bool(flag)


class RuntimeVisionCapability:
    """Reads the runtime chat providers for a vision-capable model."""

    def __init__(self, providers: Optional[list[object]] = None) -> None:
        self._providers = providers

    def supports_vision(self) -> bool:
        providers = self._providers
        if providers is None:
            try:
                from ekb_api.core.config import get_runtime_chat_providers

                providers = get_runtime_chat_providers()
            except Exception:  # noqa: BLE001
                providers = []
        return any(_provider_supports_vision(p) for p in (providers or []))


@dataclass(frozen=True)
class VisionStrategy:
    """Resolved Vision strategy for an image attachment."""

    stage: str  # "native_vision" | "remote_adapter" | "unavailable"
    usage_mode: str  # "VISION" | "OCR_FALLBACK"
    method: str  # "NATIVE_VISION" | "OCR" | "OCR_CAPTION"
    reason: str = ""


def resolve_vision_strategy(
    capability: VisionCapability,
    ocr_provider: Optional[object] = None,
) -> VisionStrategy:
    """Three-stage cascade: native Vision → remote adapter → explicit failure.

    Returns a :class:`VisionStrategy` describing which path to take, or raises
    :class:`VisionUnavailable` when neither native Vision nor a remote adapter
    is available.
    """
    # Stage 1: Native Vision
    if capability.supports_vision():
        return VisionStrategy(
            stage="native_vision",
            usage_mode="VISION",
            method="NATIVE_VISION",
        )
    # Stage 2: Remote Vision Adapter (OCR/caption)
    if ocr_provider is not None:
        return VisionStrategy(
            stage="remote_adapter",
            usage_mode="OCR_FALLBACK",
            method="OCR",
            reason="model lacks native vision; remote adapter configured",
        )
    # Stage 3: Explicit failure
    raise VisionUnavailable()


def decide_image_mode(capability: VisionCapability) -> tuple[str, str]:
    """Return ``(usage_mode, image_artifacts.method)`` for an image attachment.

    Deprecated: prefer :func:`resolve_vision_strategy` for the full
    three-stage cascade.  This function is retained for backward
    compatibility with existing callers that handle OCR fallback separately.

    ``VISION`` + ``NATIVE_VISION`` when a vision model is available, otherwise
    ``OCR_FALLBACK`` (the OCR step fills in ``method`` via ``ocr.py``).
    """
    if capability.supports_vision():
        return "VISION", "NATIVE_VISION"
    return "OCR_FALLBACK", "OCR"

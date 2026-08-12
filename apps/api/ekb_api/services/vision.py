"""Vision capability detection for image attachments (PH5).

Per ``spec/07-file-and-image.md`` an image attachment is handled differently
depending on whether the tenant's chat model supports vision:

* vision-capable  -> attach the (compressed) image directly, ``usage_mode =
  VISION``, ``image_artifacts.method = 'NATIVE_VISION'``.
* not vision       -> OCR fallback, ``usage_mode = OCR_FALLBACK``,
  ``image_artifacts.method = 'OCR'`` (see ``ocr.py``).

The decision is made by ``VisionCapability`` which consults the runtime chat
providers for a vision flag. We expose a protocol + a default implementation
that reads ``get_runtime_chat_providers`` so the behaviour is real when a
vision model is configured and safe (False) otherwise. Tests inject a fake.
"""

from __future__ import annotations

from typing import Optional, Protocol


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


def decide_image_mode(capability: VisionCapability) -> tuple[str, str]:
    """Return ``(usage_mode, image_artifacts.method)`` for an image attachment.

    ``VISION`` + ``NATIVE_VISION`` when a vision model is available, otherwise
    ``OCR_FALLBACK`` (the OCR step fills in ``method`` via ``ocr.py``).
    """
    if capability.supports_vision():
        return "VISION", "NATIVE_VISION"
    return "OCR_FALLBACK", "OCR"

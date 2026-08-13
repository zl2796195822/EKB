"""OCR fallback for image attachments (PH5).

When a model is *not* vision-capable (per ``spec/07-file-and-image.md``) an image
attachment must still be usable: run a configured remote OCR/caption adapter and
disclose the fallback via ``usage_mode = OCR_FALLBACK``. When no remote OCR
provider is configured, processing fails closed; it never writes local metadata
or placeholder text as if it were extracted content.

The module defines an ``OcrProvider`` protocol so the configured remote adapter
can be injected and tested without bundling a local model or OCR engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


class OcrUnavailable(RuntimeError):
    """No approved remote OCR/caption capability is configured."""


class OcrProvider(Protocol):
    """Extract text from raw image bytes."""

    def ocr(self, raw: bytes, *, mime: str) -> str: ...


@dataclass
class OcrResult:
    text: str
    method: str  # "OCR" for a real provider, or "OCR_CAPTION" for captioning
    confidence: float
    degraded: bool


def run_ocr(provider: Optional[OcrProvider], raw: bytes, *, mime: str) -> OcrResult:
    """Run only the supplied remote OCR/caption provider.

    A missing or failing provider is a real processing failure. This prevents a
    local metadata string from being sent to an LLM as if it described pixels.
    """
    if provider is None:
        raise OcrUnavailable("未配置远程 OCR/caption provider")
    try:
        text = provider.ocr(raw, mime=mime)
        if not text.strip():
            raise OcrUnavailable("远程 OCR/caption provider 返回空内容")
        return OcrResult(
            text=text,
            method="OCR",
            confidence=0.85,
            degraded=False,
        )
    except OcrUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OcrUnavailable("远程 OCR/caption provider 调用失败") from exc

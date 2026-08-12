"""OCR fallback for image attachments (PH5).

When a model is *not* vision-capable (per ``spec/07-file-and-image.md``) an image
attachment must still be usable: run OCR to extract text and disclose the
OCR fallback via ``usage_mode = OCR_FALLBACK`` and ``image_artifacts.method =
'OCR'``. When no OCR provider is configured we degrade to a deterministic local
extraction (EXIF/format metadata + placeholder text) so the attachment pipeline
never hard-fails; the degraded result is clearly flagged.

The module defines an ``OcrProvider`` protocol so tests can inject a fake. A
``LocalOcrProvider`` is the default when no provider is wired (no external
dependency, always safe).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Protocol

from ekb_api.domain import utc_now

# Confidence used when OCR runs through a real provider vs. local degraded.
_LOCAL_CONFIDENCE = 0.0


class OcrProvider(Protocol):
    """Extract text from raw image bytes."""

    def ocr(self, raw: bytes, *, mime: str) -> str: ...


@dataclass
class OcrResult:
    text: str
    method: str  # "OCR" for real provider, "OCR_CAPTION" / "LOCAL_DEGRADED"
    confidence: float
    degraded: bool


class LocalOcrProvider:
    """Dependency-free fallback: never raises, returns a deterministic stub.

    Real OCR (Tesseract / cloud vision OCR) is pluggable via the ``OcrProvider``
    protocol; in environments without an OCR engine we still need the attachment
    to reach READY so the chat flow is not blocked.
    """

    def ocr(self, raw: bytes, *, mime: str) -> str:
        # Best-effort: surface basic signal that is safe to compute locally.
        width, height = _probe_dimensions(raw)
        dims = f"{width}x{height}" if width and height else "unknown"
        return (
            f"[OCR 不可用：本地降级] 图片类型={mime} 尺寸={dims} "
            f"字节数={len(raw)} 提取时间={utc_now()}"
        )


def _probe_dimensions(raw: bytes) -> tuple[Optional[int], Optional[int]]:
    """Read PNG/JPEG dimensions without external libs; None if unknown."""
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
            width = struct.unpack(">I", raw[16:20])[0]
            height = struct.unpack(">I", raw[20:24])[0]
            return width, height
        if raw[:2] == b"\xff\xd8" and len(raw) >= 4:
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    height = struct.unpack(">H", raw[i + 5 : i + 7])[0]
                    width = struct.unpack(">H", raw[i + 7 : i + 9])[0]
                    return width, height
                seg_len = struct.unpack(">H", raw[i + 2 : i + 4])[0]
                i += 2 + seg_len
    except Exception:  # noqa: BLE001
        return None, None
    return None, None


def run_ocr(provider: Optional[OcrProvider], raw: bytes, *, mime: str) -> OcrResult:
    """Run OCR via the supplied provider, degrading to ``LocalOcrProvider``.

    The function never raises: OCR is best-effort for chat context, a failure
    must not block the attachment from becoming READY.
    """
    effective = provider if provider is not None else LocalOcrProvider()
    try:
        text = effective.ocr(raw, mime=mime)
        real = provider is not None
        return OcrResult(
            text=text,
            method="OCR" if real else "OCR_CAPTION",
            confidence=0.85 if real else _LOCAL_CONFIDENCE,
            degraded=not real,
        )
    except Exception as exc:  # noqa: BLE001
        return OcrResult(
            text=f"[OCR 失败：{exc}] 字节数={len(raw)}",
            method="OCR_CAPTION",
            confidence=_LOCAL_CONFIDENCE,
            degraded=True,
        )

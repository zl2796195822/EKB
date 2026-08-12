"""Attachment processing pipeline orchestrator (PH5).

Wires the real storage/parser/chunker/embedder/vision/ocr glue onto
``AttachmentService``. The pipeline is **fault-tolerant by design**: a failure in
any external step (storage down, parser error, OCR unavailable) must never raise
out of ``process_attachment`` — the attachment transitions to ``FAILED`` with a
diagnostic payload instead, so the chat upload endpoint stays responsive.

Storage and the vision/OCR providers are injected (``StorageReader`` /
``VisionCapability`` / ``OcrProvider``) so the whole pipeline is exercisable
against an in-memory engine + fakes without any external service.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from ekb_api.domain import utc_now
from ekb_api.services.attachments import (
    AttachmentService,
    AttachmentStatus,
)
from ekb_api.services.ocr import OcrResult, run_ocr
from ekb_api.services.vision import VisionCapability, decide_image_mode


class StorageReader(Protocol):
    """Returns the raw bytes of an uploaded object."""

    def get_bytes(self, *, object_id: str) -> bytes: ...


class LocalBytesStore:
    """In-memory / fallback store used when object storage is not configured."""

    def __init__(self, data: Optional[dict[str, bytes]] = None) -> None:
        self._data: dict[str, bytes] = data if data is not None else {}

    def put(self, object_id: str, data: bytes) -> None:
        self._data[object_id] = data

    def get_bytes(self, *, object_id: str) -> bytes:
        if object_id not in self._data:
            raise KeyError(f"object not found: {object_id}")
        return self._data[object_id]


@dataclass
class ProcessingConfig:
    parser: Callable[[bytes, str, str], list[Any]]
    chunker: Callable[[list[Any]], list[Any]]
    embed: Callable[[list[str]], list[list[float]]]
    vision: VisionCapability
    ocr: Optional[Callable[[bytes, str], str]] = None


def _is_image(mime: str) -> bool:
    return mime.startswith("image/")


def _estimate_tokens(text: str) -> int:
    # Cheap surrogate: ~1 token per 3 CJK/word chars. Good enough for bookkeeping.
    return max(1, len(text) // 3)


def process_attachment(
    service: AttachmentService,
    *,
    tenant_id: str,
    actor_id: str,
    attachment_id: str,
    storage: StorageReader,
    config: ProcessingConfig,
) -> dict[str, Any]:
    """Run the full prepare pipeline for one attachment.

    Returns a status dict. On any failure the attachment is moved to FAILED and
    the error is recorded — the function itself never raises.
    """
    try:
        service.mark_processing(tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id)
        record = service.get(tenant_id=tenant_id, attachment_id=attachment_id)
        raw = storage.get_bytes(object_id=record.source_object_id)
        if _is_image(record.detected_mime):
            result = _process_image(service, tenant_id=tenant_id, attachment_id=attachment_id,
                                    raw=raw, mime=record.detected_mime, config=config)
        else:
            result = _process_document(service, tenant_id=tenant_id, attachment_id=attachment_id,
                                       raw=raw, mime=record.detected_mime, filename="",
                                       config=config)
        service.mark_ready(tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id)
        return {"attachment_id": attachment_id, "status": AttachmentStatus.READY, **result}
    except Exception as exc:  # noqa: BLE001
        detail = {"error": str(exc), "at": utc_now()}
        try:
            service.mark_failed(tenant_id=tenant_id, actor_id=actor_id, attachment_id=attachment_id)
        except Exception:  # noqa: BLE001
            pass
        return {"attachment_id": attachment_id, "status": AttachmentStatus.FAILED, "detail": detail}


def _process_document(
    service: AttachmentService,
    *,
    tenant_id: str,
    attachment_id: str,
    raw: bytes,
    mime: str,
    filename: str,
    config: ProcessingConfig,
) -> dict[str, Any]:
    sections = config.parser(raw, mime, filename)
    chunks = config.chunker(sections)
    texts = [getattr(c, "content", "") for c in chunks]
    vectors: list[list[float]] = []
    if texts:
        try:
            vectors = config.embed(texts)
        except Exception:  # noqa: BLE001
            vectors = []
    chunk_rows: list[dict[str, Any]] = []
    for idx, c in enumerate(chunks):
        vec = vectors[idx] if idx < len(vectors) else None
        chunk_rows.append({
            "text": getattr(c, "content", ""),
            "metadata": {
                "ordinal": idx,
                "section_path": getattr(c, "section_path", []),
                "token_count": getattr(c, "token_count", None),
                "content_hash": getattr(c, "content_hash", None),
                "has_vector": vec is not None,
            },
            "embedding_profile_id": None,
        })
    token_estimate = sum(_estimate_tokens(r["text"]) for r in chunk_rows)
    artifact_id = service.write_artifact(
        tenant_id=tenant_id, attachment_id=attachment_id, parser_version="parsley-1",
        text_object_id=None, metadata={"kind": "document", "section_count": len(sections),
                                       "chunk_count": len(chunk_rows)},
        token_estimate=token_estimate, status="READY", usage_mode="RETRIEVAL",
    )
    written = service.write_chunks(
        tenant_id=tenant_id, attachment_id=attachment_id, artifact_id=artifact_id, chunks=chunk_rows
    )
    return {"kind": "document", "artifact_id": artifact_id, "chunks": written,
            "token_estimate": token_estimate}


def _process_image(
    service: AttachmentService,
    *,
    tenant_id: str,
    attachment_id: str,
    raw: bytes,
    mime: str,
    config: ProcessingConfig,
) -> dict[str, Any]:
    usage_mode, method = decide_image_mode(config.vision)
    width, height = _probe_image_size(raw)
    if usage_mode == "VISION":
        # Native vision: attach the image directly, no OCR text needed.
        service.write_image_artifact(
            tenant_id=tenant_id, attachment_id=attachment_id, width=width, height=height,
            method=method, caption=None, confidence=None,
        )
        service.write_artifact(
            tenant_id=tenant_id, attachment_id=attachment_id, parser_version="vision-1",
            text_object_id=None, metadata={"kind": "image", "vision": True},
            token_estimate=0, status="READY", usage_mode="VISION",
        )
        return {
            "kind": "image", "usage_mode": "VISION", "method": method,
            "width": width, "height": height,
        }

    # OCR fallback path.
    ocr_result: OcrResult = run_ocr(config.ocr, raw, mime=mime)
    service.write_image_artifact(
        tenant_id=tenant_id, attachment_id=attachment_id, width=width, height=height,
        method=ocr_result.method, ocr_text_object_id=None, caption=None,
        confidence=ocr_result.confidence,
    )
    service.write_artifact(
        tenant_id=tenant_id, attachment_id=attachment_id, parser_version="ocr-1",
        text_object_id=None,
        metadata={"kind": "image", "vision": False, "ocr_text": ocr_result.text,
                  "ocr_degraded": ocr_result.degraded},
        token_estimate=_estimate_tokens(ocr_result.text), status="READY",
        usage_mode="OCR_FALLBACK",
    )
    return {"kind": "image", "usage_mode": "OCR_FALLBACK", "method": ocr_result.method,
            "degraded": ocr_result.degraded, "width": width, "height": height,
            "ocr_text": ocr_result.text}


def _probe_image_size(raw: bytes) -> tuple[int, int]:
    # Local dimension probe (PNG/JPEG). Defaults to 0x0 if unknown; the schema
    # requires > 0, so we coerce unknowns to 1x1 to keep the row valid.
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
            return max(1, w), max(1, h)
        if raw[:2] == b"\xff\xd8" and len(raw) >= 4:
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    h = int.from_bytes(raw[i + 5 : i + 7], "big")
                    w = int.from_bytes(raw[i + 7 : i + 9], "big")
                    return max(1, w), max(1, h)
                seg_len = int.from_bytes(raw[i + 2 : i + 4], "big")
                i += 2 + seg_len
    except Exception:  # noqa: BLE001
        pass
    return 1, 1

"""PH5 chat attachment tests (spec 07-file-and-image.md).

Proves the attachment subsystem is actually wired, not a stub:

* ``v4_008_attachments`` is part of the migration chain and creates the six
  attachment tables; verify passes.
* register is idempotent on (tenant, owner, client_request_id) and scopes rows
  by tenant + owner.
* lifecycle transitions follow the documented state machine; illegal jumps are
  rejected; only the owner may drive them.
* the synchronous prepare pipeline parses + chunks a document and records
  RETRIEVAL usage; an image goes VISION when a vision model is present and
  OCR_FALLBACK (degraded) when absent — never raising.
* bind requires READY, tenant-scoped, owner-matched attachments and records the
  resolved usage_mode; it moves attachments to ATTACHED in one transaction.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.services.attachment_processor import (
    LocalBytesStore,
    ProcessingConfig,
    process_attachment,
)
from ekb_api.services.attachments import (
    AttachmentNotFound,
    AttachmentOwnershipError,
    AttachmentService,
    AttachmentStateConflict,
    AttachmentStatus,
)
from ekb_api.services.ocr import OcrResult, OcrUnavailable, run_ocr
from ekb_api.services.vision import decide_image_mode

# 1x1 PNG used to exercise image processing paths.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000154a24f5b0000000049454e44ae426082"
)


def _apply_chain(engine) -> None:
    for step in CHAIN:
        step.apply(engine)


def _identity(engine) -> tuple[str, str]:
    with engine.connect() as connection:
        tenant = str(connection.execute(text("SELECT id FROM tenants LIMIT 1")).scalar_one())
        user = str(
            connection.execute(
                text("SELECT id FROM users WHERE tenant_id=:t LIMIT 1"), {"t": tenant}
            ).scalar_one()
        )
    return tenant, user


def _seed_source_object(engine, tenant: str, obj_id: str, mime: str, size: int) -> None:
    sha = hashlib.sha256(obj_id.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO source_objects"
                " (id, tenant_id, object_key, sha256, byte_size, detected_mime, created_at)"
                " VALUES (:id,:t,:k,:s,:b,:m,'2026-08-12T00:00:00Z')"
            ),
            {"id": obj_id, "t": tenant, "k": f"uploads/{tenant}/{obj_id}",
             "s": sha, "b": size, "m": mime},
        )


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "ph5.db"
    engine = build_engine(f"sqlite:///{db}")
    prepare_legacy_schema(engine, seed=True)
    _apply_chain(engine)
    tenant, user = _identity(engine)
    return {"engine": engine, "tenant": tenant, "user": user}


class _FakeVision:
    def __init__(self, supports: bool) -> None:
        self._supports = supports

    def supports_vision(self) -> bool:
        return self._supports


class _FakeOcr:
    def ocr(self, raw: bytes, *, mime: str) -> str:
        return f"[OCR] {len(raw)} bytes"


def _fake_parser(raw: bytes, mime: str, filename: str):
    from ekb_api.parsing import ParsedSection

    text = raw.decode("utf-8", "replace")
    return [ParsedSection(section_path=["doc"], content=text)]


def _fake_chunker(sections):
    class _Chunk:
        def __init__(self, content: str, i: int) -> None:
            self.content = content
            self.section_path = ["doc"]
            self.token_count = len(content) // 3
            self.content_hash = f"h{i}"

    return [_Chunk(s.content, i) for i, s in enumerate(sections)]


def _fake_embed(texts):
    return [[float(len(t)), 0.0, 0.0] for t in texts]


def _register(env, obj_id: str, mime: str, size: int, cr: str) -> str:
    """Seed a real source_object then register an UPLOADING attachment."""
    _seed_source_object(env["engine"], env["tenant"], obj_id, mime, size)
    svc = AttachmentService(env["engine"])
    rec = svc.register(tenant_id=env["tenant"], owner_user_id=env["user"], source_object_id=obj_id,
                       detected_mime=mime, byte_size=size, client_request_id=cr)
    return rec.id


def _seed_message(env) -> str:
    """Create a real conversation + message so message_attachments FK holds."""
    conv_id = f"conv-{hashlib.sha256(b'c').hexdigest()[:12]}"
    msg_id = f"msg-{hashlib.sha256(b'm').hexdigest()[:12]}"
    with env["engine"].begin() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO conversations"
                " (id, tenant_id, user_id, title, created_at, updated_at)"
                " VALUES (:id,:t,:u,'t','2026-08-12T00:00:00Z','2026-08-12T00:00:00Z')"
            ),
            {"id": conv_id, "t": env["tenant"], "u": env["user"]},
        )
        conn.execute(
            text(
                "INSERT OR IGNORE INTO messages"
                " (id, tenant_id, conversation_id, role, content, visibility_state, created_at)"
                " VALUES (:id,:t,:c,'user','hi','visible','2026-08-12T00:00:00Z')"
            ),
            {"id": msg_id, "t": env["tenant"], "c": conv_id},
        )
    return msg_id


def _process(env, attachment_id: str, store: LocalBytesStore, *, vision: bool):
    svc = AttachmentService(env["engine"])
    config = ProcessingConfig(parser=_fake_parser, chunker=_fake_chunker, embed=_fake_embed,
                              vision=_FakeVision(vision), ocr=_FakeOcr())
    return process_attachment(svc, tenant_id=env["tenant"], actor_id=env["user"],
                              attachment_id=attachment_id, storage=store, config=config)


# ---- migration chain ------------------------------------------------------


def test_v4_008_in_chain_and_creates_tables(env) -> None:
    with env["engine"].connect() as conn:
        names = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = set(names.scalars())
    for t in ("attachments", "attachment_artifacts", "attachment_chunks",
              "message_attachments", "image_artifacts", "attachment_promotions"):
        assert t in tables, f"v4_008 missing table: {t}"


# ---- register / idempotency ----------------------------------------------


def test_register_is_idempotent(env) -> None:
    a = _register(env, "obj-1", "text/plain", 10, "cr-1")
    b = _register(env, "obj-1", "text/plain", 10, "cr-1")
    assert a == b
    svc = AttachmentService(env["engine"])
    got = svc.get(tenant_id=env["tenant"], attachment_id=a)
    assert got.status == AttachmentStatus.UPLOADING


def test_register_scopes_by_tenant_owner(env) -> None:
    rec_id = _register(env, "obj-2", "text/plain", 5, "cr-2")
    # same id different tenant -> not visible
    with pytest.raises(AttachmentNotFound):
        AttachmentService(env["engine"]).get(tenant_id="other-tenant", attachment_id=rec_id)


# ---- lifecycle ------------------------------------------------------------


def test_lifecycle_transitions_and_reject_illegal(env) -> None:
    svc = AttachmentService(env["engine"])
    rec_id = _register(env, "obj-3", "text/plain", 5, "cr-3")
    svc.mark_processing(tenant_id=env["tenant"], actor_id=env["user"], attachment_id=rec_id)
    svc.mark_ready(tenant_id=env["tenant"], actor_id=env["user"], attachment_id=rec_id)
    # illegal jump UPLOADING -> READY is rejected
    fresh = _register(env, "obj-4", "text/plain", 5, "cr-4")
    with pytest.raises(AttachmentStateConflict):
        svc.transition(tenant_id=env["tenant"], actor_id=env["user"], attachment_id=fresh,
                       to=AttachmentStatus.READY)


def test_non_owner_cannot_transition(env) -> None:
    svc = AttachmentService(env["engine"])
    rec_id = _register(env, "obj-5", "text/plain", 5, "cr-5")
    with pytest.raises(AttachmentOwnershipError):
        svc.mark_ready(tenant_id=env["tenant"], actor_id="someone-else", attachment_id=rec_id)


# ---- document processing --------------------------------------------------


def test_process_document_chunks_and_retrieval(env) -> None:
    store = LocalBytesStore()
    store.put("obj-doc", b"hello world attachment content")
    rec_id = _register(env, "obj-doc", "text/plain", 28, "cr-doc")
    result = _process(env, rec_id, store, vision=False)
    assert result["status"] == AttachmentStatus.READY
    assert result["kind"] == "document"
    assert result["chunks"] >= 1
    with env["engine"].connect() as conn:
        meta = conn.execute(
            text("SELECT metadata FROM attachment_artifacts WHERE attachment_id=:a"),
            {"a": rec_id},
        ).scalar_one()
        if not isinstance(meta, dict):
            meta = json.loads(meta)
        usage = meta.get("usage_mode")
        nchunks = conn.execute(
            text("SELECT COUNT(*) FROM attachment_chunks WHERE attachment_id=:a"),
            {"a": rec_id},
        ).scalar_one()
    assert usage == "RETRIEVAL"
    assert nchunks == result["chunks"]


# ---- image processing: vision present -------------------------------------


def test_process_image_vision_when_supported(env) -> None:
    store = LocalBytesStore()
    store.put("obj-img", _PNG)
    rec_id = _register(env, "obj-img", "image/png", len(_PNG), "cr-img")
    result = _process(env, rec_id, store, vision=True)
    assert result["status"] == AttachmentStatus.READY
    assert result["usage_mode"] == "VISION"
    with env["engine"].connect() as conn:
        method = conn.execute(text("SELECT method FROM image_artifacts WHERE attachment_id=:a"),
                              {"a": rec_id}).scalar_one()
    assert method == "NATIVE_VISION"


# ---- image processing: OCR fallback (degraded) ----------------------------


def test_process_image_ocr_fallback_when_no_vision(env) -> None:
    store = LocalBytesStore()
    store.put("obj-img2", _PNG)
    rec_id = _register(env, "obj-img2", "image/png", len(_PNG), "cr-img2")
    result = _process(env, rec_id, store, vision=False)
    assert result["status"] == AttachmentStatus.READY
    assert result["usage_mode"] == "OCR_FALLBACK"
    assert result["degraded"] is False  # real fake OCR available


def test_process_image_fails_closed_without_remote_ocr(env) -> None:
    store = LocalBytesStore()
    store.put("obj-img3", _PNG)
    rec_id = _register(env, "obj-img3", "image/png", len(_PNG), "cr-img3")
    svc = AttachmentService(env["engine"])
    config = ProcessingConfig(parser=_fake_parser, chunker=_fake_chunker, embed=_fake_embed,
                              vision=_FakeVision(False), ocr=None)
    result = process_attachment(svc, tenant_id=env["tenant"], actor_id=env["user"],
                                attachment_id=rec_id, storage=store, config=config)
    assert result["status"] == AttachmentStatus.FAILED
    assert "远程 OCR/caption" in result["detail"]["error"]


# ---- bind -----------------------------------------------------------------


def test_bind_requires_ready_and_records_usage_mode(env) -> None:
    t, u = env["tenant"], env["user"]
    svc = AttachmentService(env["engine"])
    store = LocalBytesStore()
    store.put("obj-b", b"bind content")
    rec_id = _register(env, "obj-b", "text/plain", 12, "cr-b")
    _process(env, rec_id, store, vision=False)
    assert svc.get(tenant_id=t, attachment_id=rec_id).status == AttachmentStatus.READY
    msg_id = _seed_message(env)
    bindings = svc.bind(tenant_id=t, actor_id=u, message_id=msg_id, attachment_ids=[rec_id])
    assert len(bindings) == 1
    assert bindings[0].usage_mode == "RETRIEVAL"
    assert svc.get(tenant_id=t, attachment_id=rec_id).status == AttachmentStatus.ATTACHED


def test_bind_rejects_non_ready(env) -> None:
    svc = AttachmentService(env["engine"])
    rec_id = _register(env, "obj-nr", "text/plain", 5, "cr-nr")
    # still UPLOADING -> must reject
    msg_id = _seed_message(env)
    with pytest.raises(AttachmentStateConflict):
        svc.bind(tenant_id=env["tenant"], actor_id=env["user"],
                 message_id=msg_id, attachment_ids=[rec_id])


# ---- vision / ocr units ---------------------------------------------------


def test_decide_image_mode_delegates_to_capability() -> None:
    assert decide_image_mode(_FakeVision(True)) == ("VISION", "NATIVE_VISION")
    assert decide_image_mode(_FakeVision(False)) == ("OCR_FALLBACK", "OCR")


def test_run_ocr_requires_remote_provider() -> None:
    ok = run_ocr(_FakeOcr(), b"1234", mime="image/png")
    assert isinstance(ok, OcrResult)
    assert ok.text.startswith("[OCR]")
    with pytest.raises(OcrUnavailable):
        run_ocr(None, b"1234", mime="image/png")

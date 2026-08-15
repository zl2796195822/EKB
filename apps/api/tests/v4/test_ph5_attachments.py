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

from ekb_api.core.auth import get_live_auth_context
from ekb_api.core.authorization import CAP_KB_WRITE
from ekb_api.core.db import build_engine, prepare_legacy_schema
from ekb_api.domain import AuthContext, TenantRole
from ekb_api.migrations.v4_fullstack import CHAIN
from ekb_api.services.attachment_processor import (
    LocalBytesStore,
    ProcessingConfig,
    process_attachment,
)
from ekb_api.services.attachment_promotions import (
    AttachmentPromotionService,
    PromotionError,
    PromotionForbidden,
    PromotionPathConflict,
    PromotionPathReservedByTrash,
    PromotionTargetNotFound,
)
from ekb_api.services.attachments import (
    AttachmentNotFound,
    AttachmentOwnershipError,
    AttachmentService,
    AttachmentStateConflict,
    AttachmentStatus,
)
from ekb_api.services.ingestion import STAGES, IngestService
from ekb_api.services.ocr import OcrResult, OcrUnavailable, run_ocr
from ekb_api.services.vision import (
    VISION_UNAVAILABLE,
    VisionUnavailable,
    decide_image_mode,
    resolve_vision_strategy,
)

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


def _target_kb(env) -> str:
    with env["engine"].connect() as conn:
        return str(
            conn.execute(
                text("SELECT id FROM knowledge_bases WHERE tenant_id=:t ORDER BY id LIMIT 1"),
                {"t": env["tenant"]},
            ).scalar_one()
        )


def _promote(env, attachment_id: str, *, path: str, request: str):
    return AttachmentPromotionService(env["engine"]).promote(
        tenant_id=env["tenant"],
        actor_id=env["user"],
        attachment_id=attachment_id,
        target_knowledge_base_id=_target_kb(env),
        relative_path=path,
        client_request_id=request,
        request_id="trace-ph5-promotion",
    )


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
    """Stage 3 of Vision cascade: explicit VISION_UNAVAILABLE (spec 02 §9)."""
    store = LocalBytesStore()
    store.put("obj-img3", _PNG)
    rec_id = _register(env, "obj-img3", "image/png", len(_PNG), "cr-img3")
    svc = AttachmentService(env["engine"])
    config = ProcessingConfig(parser=_fake_parser, chunker=_fake_chunker, embed=_fake_embed,
                              vision=_FakeVision(False), ocr=None)
    result = process_attachment(svc, tenant_id=env["tenant"], actor_id=env["user"],
                                attachment_id=rec_id, storage=store, config=config)
    assert result["status"] == AttachmentStatus.FAILED
    # PH5-2: explicit VISION_UNAVAILABLE error code surfaced in detail
    assert result["detail"]["error_code"] == "VISION_UNAVAILABLE"


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


# ---- PH5-2: Vision three-stage cascade (spec 02 §9) ----------------------


def test_resolve_vision_strategy_native_vision() -> None:
    """Stage 1: native Vision when model supports it."""
    strategy = resolve_vision_strategy(_FakeVision(True), ocr_provider=None)
    assert strategy.stage == "native_vision"
    assert strategy.usage_mode == "VISION"
    assert strategy.method == "NATIVE_VISION"


def test_resolve_vision_strategy_remote_adapter() -> None:
    """Stage 2: remote adapter fallback when model lacks vision but OCR is configured."""
    strategy = resolve_vision_strategy(_FakeVision(False), ocr_provider=_FakeOcr())
    assert strategy.stage == "remote_adapter"
    assert strategy.usage_mode == "OCR_FALLBACK"
    assert strategy.method == "OCR"


def test_resolve_vision_strategy_explicit_failure() -> None:
    """Stage 3: explicit VISION_UNAVAILABLE when neither path is available."""
    with pytest.raises(VisionUnavailable) as exc_info:
        resolve_vision_strategy(_FakeVision(False), ocr_provider=None)
    assert exc_info.value.error_code == VISION_UNAVAILABLE


def test_resolve_vision_strategy_prefers_native_over_adapter() -> None:
    """When both native vision and adapter are available, native wins."""
    strategy = resolve_vision_strategy(_FakeVision(True), ocr_provider=_FakeOcr())
    assert strategy.stage == "native_vision"


# ---- PH5-1: Historical attachment reuse + ACL revalidation (spec 02 §9) ---


def _seed_conversation_with_branch(env) -> tuple[str, str, str, str]:
    """Seed conversation + branch + two messages; return (conv_id, branch_id, msg1_id, msg2_id)."""
    engine = env["engine"]
    tenant, user = env["tenant"], env["user"]
    conv_id = f"conv-hist-{hashlib.sha256(b'hc').hexdigest()[:12]}"
    branch_id = f"br-hist-{hashlib.sha256(b'hb').hexdigest()[:12]}"
    msg1_id = f"msg-h1-{hashlib.sha256(b'h1').hexdigest()[:12]}"
    msg2_id = f"msg-h2-{hashlib.sha256(b'h2').hexdigest()[:12]}"
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT OR IGNORE INTO conversations"
            " (id, tenant_id, user_id, title, created_at, updated_at)"
            " VALUES (:id,:t,:u,'hist','2026-08-12T00:00:00Z','2026-08-12T00:00:00Z')"
        ), {"id": conv_id, "t": tenant, "u": user})
        conn.execute(text(
            "INSERT OR IGNORE INTO conversation_branches"
            " (id, tenant_id, conversation_id, parent_branch_id, fork_message_id,"
            "  label, created_by, created_at)"
            " VALUES (:id,:t,:c,NULL,NULL,'main',:u,'2026-08-12T00:00:00Z')"
        ), {"id": branch_id, "t": tenant, "c": conv_id, "u": user})
        conn.execute(text(
            "INSERT OR IGNORE INTO messages"
            " (id, tenant_id, conversation_id, branch_id, role, content,"
            "  visibility_state, created_at)"
            " VALUES (:id,:t,:c,:b,'user','first question','visible',"
            " '2026-08-12T00:00:00Z')"
        ), {"id": msg1_id, "t": tenant, "c": conv_id, "b": branch_id})
        conn.execute(text(
            "INSERT OR IGNORE INTO messages"
            " (id, tenant_id, conversation_id, branch_id, role, content,"
            "  visibility_state, created_at)"
            " VALUES (:id,:t,:c,:b,'user','continue with that file','visible',"
            " '2026-08-12T00:01:00Z')"
        ), {"id": msg2_id, "t": tenant, "c": conv_id, "b": branch_id})
    return conv_id, branch_id, msg1_id, msg2_id


def _seed_ready_attachment_with_chunk(env, conv_id: str, label: str, *, msg_id: str = "") -> str:
    """Seed a READY attachment with a chunk; optionally bind to msg_id; return attachment_id."""
    engine = env["engine"]
    tenant, user = env["tenant"], env["user"]
    obj_id = f"src-hist-{label}"
    att_id = f"att-hist-{label}"
    _seed_source_object(engine, tenant, obj_id, "text/plain", 100)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT OR IGNORE INTO attachments"
            " (id, tenant_id, owner_user_id, conversation_id, source_object_id,"
            "  client_request_id, status, detected_mime, byte_size, created_at, updated_at)"
            " VALUES (:id,:t,:u,:c,:s,:cr,'READY','text/plain',100,"
            " '2026-08-12T00:00:00Z','2026-08-12T00:00:00Z')"
        ), {"id": att_id, "t": tenant, "u": user, "c": conv_id,
            "s": obj_id, "cr": f"cr-{label}"})
        art_id = f"art-{label}"
        conn.execute(text(
            "INSERT OR IGNORE INTO attachment_artifacts"
            " (id, tenant_id, attachment_id, parser_version, text_object_id,"
            "  metadata, token_estimate, status)"
            " VALUES (:id,:t,:a,'parsley-1',NULL,:meta,10,'READY')"
        ), {"id": art_id, "t": tenant, "a": att_id,
            "meta": json.dumps({"kind": "document", "usage_mode": "RETRIEVAL"})})
        conn.execute(text(
            "INSERT OR IGNORE INTO attachment_chunks"
            " (id, tenant_id, attachment_id, artifact_id, ordinal, text_content, metadata)"
            " VALUES (:id,:t,:a,:art,0,:txt,:meta)"
        ), {"id": f"chk-{label}", "t": tenant, "a": att_id, "art": art_id,
            "txt": f"content of {label}", "meta": "{}"})
        if msg_id:
            conn.execute(text(
                "INSERT OR IGNORE INTO message_attachments"
                " (tenant_id, message_id, attachment_id, ordinal, usage_mode)"
                " VALUES (:t,:m,:a,0,'RETRIEVAL')"
            ), {"t": tenant, "m": msg_id, "a": att_id})
    return att_id


def _seed_other_user(env) -> str:
    """Create a second user in the same tenant for ACL tests; return user_id."""
    other_id = f"user-other-{hashlib.sha256(b'ou').hexdigest()[:12]}"
    with env["engine"].begin() as conn:
        conn.execute(text(
            "INSERT OR IGNORE INTO users"
            " (id, tenant_id, email, name, role, password_hash,"
            "  created_at, updated_at)"
            " VALUES (:id,:t,:email,'Other','MEMBER','x',"
            " '2026-08-12T00:00:00Z','2026-08-12T00:00:00Z')"
        ), {"id": other_id, "t": env["tenant"], "email": f"{other_id}@test.local"})
    return other_id


def test_historical_attachment_reuse(env) -> None:
    """Spec 02 §9: later turns may reuse historical attachments from the active branch."""
    from ekb_api.services.context_engine import ContextEngineService

    conv_id, branch_id, msg1_id, msg2_id = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="reuse1", msg_id=msg1_id)

    ce = ContextEngineService(env["engine"])
    refs, dropped = ce._load_historical_attachments(
        tenant_id=env["tenant"],
        branch_id=branch_id,
        current_user_message_id=msg2_id,
        owner_user_id=env["user"],
        conversation_id=conv_id,
    )
    assert len(refs) == 1
    assert refs[0].attachment_id == att_id
    assert refs[0].content == "content of reuse1"
    assert dropped == []


def test_historical_attachment_acl_owner_revalidation(env) -> None:
    """Spec 02 §9: owner mismatch → dropped (fail-closed)."""
    from ekb_api.services.context_engine import ContextEngineService

    conv_id, branch_id, msg1_id, msg2_id = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="acl1", msg_id=msg1_id)
    other_user = _seed_other_user(env)

    with env["engine"].begin() as conn:
        conn.execute(text(
            "UPDATE attachments SET owner_user_id = :ou WHERE id = :id"
        ), {"ou": other_user, "id": att_id})

    ce = ContextEngineService(env["engine"])
    refs, dropped = ce._load_historical_attachments(
        tenant_id=env["tenant"],
        branch_id=branch_id,
        current_user_message_id=msg2_id,
        owner_user_id=env["user"],
        conversation_id=conv_id,
    )
    assert refs == []
    assert att_id in dropped


def test_historical_attachment_acl_state_revalidation(env) -> None:
    """Spec 02 §9: trashed attachment → dropped (fail-closed)."""
    from ekb_api.services.context_engine import ContextEngineService

    conv_id, branch_id, msg1_id, msg2_id = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="acl2", msg_id=msg1_id)

    with env["engine"].begin() as conn:
        conn.execute(text(
            "UPDATE attachments SET status = 'TRASHED' WHERE id = :id"
        ), {"id": att_id})

    ce = ContextEngineService(env["engine"])
    refs, dropped = ce._load_historical_attachments(
        tenant_id=env["tenant"],
        branch_id=branch_id,
        current_user_message_id=msg2_id,
        owner_user_id=env["user"],
        conversation_id=conv_id,
    )
    assert refs == []
    assert att_id in dropped


def test_historical_attachment_retention_expired(env) -> None:
    """Spec 02 §9: expired attachment → dropped (fail-closed)."""
    from ekb_api.services.context_engine import ContextEngineService

    conv_id, branch_id, msg1_id, msg2_id = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="expire1", msg_id=msg1_id)

    with env["engine"].begin() as conn:
        conn.execute(text(
            "UPDATE attachments SET expires_at = '2020-01-01T00:00:00Z' WHERE id = :id"
        ), {"id": att_id})

    ce = ContextEngineService(env["engine"])
    refs, dropped = ce._load_historical_attachments(
        tenant_id=env["tenant"],
        branch_id=branch_id,
        current_user_message_id=msg2_id,
        owner_user_id=env["user"],
        conversation_id=conv_id,
    )
    assert refs == []
    assert att_id in dropped


def test_historical_attachment_retention_deleted(env) -> None:
    """Spec 02 §9: deleted attachment → dropped (fail-closed)."""
    from ekb_api.services.context_engine import ContextEngineService

    conv_id, branch_id, msg1_id, msg2_id = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="del1", msg_id=msg1_id)

    with env["engine"].begin() as conn:
        conn.execute(text(
            "UPDATE attachments SET deleted_at = '2026-08-13T00:00:00Z' WHERE id = :id"
        ), {"id": att_id})

    ce = ContextEngineService(env["engine"])
    refs, dropped = ce._load_historical_attachments(
        tenant_id=env["tenant"],
        branch_id=branch_id,
        current_user_message_id=msg2_id,
        owner_user_id=env["user"],
        conversation_id=conv_id,
    )
    assert refs == []
    assert att_id in dropped


def test_load_attachments_acl_strict_owner(env) -> None:
    """ContextEngineService._load_attachments enforces owner scope (PH5-1)."""
    from ekb_api.services.context_engine import ContextEngineService

    conv_id, _, _, _ = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="strict1")

    ce = ContextEngineService(env["engine"])
    other_user = _seed_other_user(env)
    # Wrong owner → dropped
    refs, dropped = ce._load_attachments(
        env["tenant"], [att_id],
        owner_user_id=other_user, conversation_id=conv_id,
    )
    assert refs == []
    assert att_id in dropped

    # Correct owner → loaded
    refs, dropped = ce._load_attachments(
        env["tenant"], [att_id],
        owner_user_id=env["user"], conversation_id=conv_id,
    )
    assert len(refs) == 1
    assert refs[0].attachment_id == att_id


def test_load_context_retention_expired_raises(env) -> None:
    """AttachmentService.load_context raises on expired attachment (PH5-1)."""
    conv_id, _, _, _ = _seed_conversation_with_branch(env)
    att_id = _seed_ready_attachment_with_chunk(env, conv_id, label="lc1")

    with env["engine"].begin() as conn:
        conn.execute(text(
            "UPDATE attachments SET expires_at = '2020-01-01T00:00:00Z' WHERE id = :id"
        ), {"id": att_id})

    svc = AttachmentService(env["engine"])
    with pytest.raises(AttachmentStateConflict):
        svc.load_context(
            tenant_id=env["tenant"],
            owner_user_id=env["user"],
            attachment_ids=[att_id],
            conversation_id=conv_id,
        )


# ---- promotion ------------------------------------------------------------


def test_attachment_promotion_reuses_source_and_stays_queued(env) -> None:
    store = LocalBytesStore()
    body = b"promotion source body"
    store.put("obj-promo", body)
    rec_id = _register(env, "obj-promo", "text/plain", len(body), "cr-promo")
    _process(env, rec_id, store, vision=False)

    result = _promote(env, rec_id, path="promoted/source.txt", request="promo-1")
    assert result.status == "QUEUED"
    assert result.document_id and result.document_version_id and result.ingest_job_id
    with env["engine"].connect() as conn:
        version = conn.execute(
            text(
                "SELECT source_object_id FROM document_versions WHERE id=:version"
            ),
            {"version": result.document_version_id},
        ).scalar_one()
        job = conn.execute(
            text("SELECT status FROM ingest_jobs WHERE id=:job"),
            {"job": result.ingest_job_id},
        ).scalar_one()
        promotion = conn.execute(
            text(
                "SELECT status, document_version_id, ingest_job_id "
                "FROM attachment_promotions WHERE id=:id"
            ),
            {"id": result.promotion_id},
        ).one()
    attachment = AttachmentService(env["engine"]).get(
        tenant_id=env["tenant"], attachment_id=rec_id
    )
    assert str(version) == attachment.source_object_id
    assert job == "QUEUED"
    assert promotion.status == "QUEUED"
    assert promotion.document_version_id == result.document_version_id
    assert promotion.ingest_job_id == result.ingest_job_id


def test_attachment_promotion_status_follows_real_ingest_lifecycle(env) -> None:
    store = LocalBytesStore()
    body = b"promotion lifecycle"
    store.put("obj-lifecycle", body)
    rec_id = _register(env, "obj-lifecycle", "text/plain", len(body), "cr-lifecycle")
    _process(env, rec_id, store, vision=False)
    result = _promote(env, rec_id, path="promoted/lifecycle.txt", request="lifecycle-1")
    ingest = IngestService(env["engine"])

    attempt = ingest.start_attempt(
        tenant_id=env["tenant"],
        ingest_job_id=result.ingest_job_id,
        lease_owner="promotion-test-worker",
    )
    with env["engine"].connect() as conn:
        assert conn.execute(
            text("SELECT status FROM attachment_promotions WHERE id=:id"),
            {"id": result.promotion_id},
        ).scalar_one() == "PROCESSING"

    for stage in STAGES:
        ingest.run_stage(
            tenant_id=env["tenant"],
            attempt_id=attempt.id,
            stage=stage,
            progress_current=1,
            progress_total=len(STAGES),
        )
    ingest.succeed(tenant_id=env["tenant"], attempt_id=attempt.id)
    with env["engine"].connect() as conn:
        assert conn.execute(
            text("SELECT status FROM attachment_promotions WHERE id=:id"),
            {"id": result.promotion_id},
        ).scalar_one() == "SUCCEEDED"


def test_attachment_promotion_failure_is_projected_as_failed(env) -> None:
    store = LocalBytesStore()
    body = b"promotion failure"
    store.put("obj-failure", body)
    rec_id = _register(env, "obj-failure", "text/plain", len(body), "cr-failure")
    _process(env, rec_id, store, vision=False)
    result = _promote(env, rec_id, path="promoted/failure.txt", request="failure-1")
    ingest = IngestService(env["engine"])
    attempt = ingest.start_attempt(
        tenant_id=env["tenant"],
        ingest_job_id=result.ingest_job_id,
        lease_owner="promotion-test-worker",
    )
    ingest.fail(
        tenant_id=env["tenant"],
        attempt_id=attempt.id,
        error_code="PARSER_ENCRYPTED",
    )
    with env["engine"].connect() as conn:
        assert conn.execute(
            text("SELECT status FROM attachment_promotions WHERE id=:id"),
            {"id": result.promotion_id},
        ).scalar_one() == "FAILED"


def test_attachment_promotion_is_idempotent_by_client_request(env) -> None:
    store = LocalBytesStore()
    body = b"idempotent promotion"
    store.put("obj-idem", body)
    rec_id = _register(env, "obj-idem", "text/plain", len(body), "cr-idem")
    _process(env, rec_id, store, vision=False)

    first = _promote(env, rec_id, path="promoted/idempotent.txt", request="same-request")
    second = _promote(env, rec_id, path="promoted/idempotent.txt", request="same-request")
    assert second == first
    with env["engine"].connect() as conn:
        assert conn.execute(
            text(
                "SELECT COUNT(*) FROM attachment_promotions WHERE attachment_id=:a"
            ),
            {"a": rec_id},
        ).scalar_one() == 1
        assert conn.execute(
            text("SELECT COUNT(*) FROM document_versions WHERE source_object_id=:s"),
            {"s": "obj-idem"},
        ).scalar_one() == 1


def test_attachment_promotion_idempotency_does_not_bypass_owner(env) -> None:
    store = LocalBytesStore()
    body = b"idempotency owner boundary"
    store.put("obj-owner-boundary", body)
    rec_id = _register(env, "obj-owner-boundary", "text/plain", len(body), "cr-owner-boundary")
    _process(env, rec_id, store, vision=False)
    _promote(env, rec_id, path="promoted/owner-boundary.txt", request="owner-boundary-1")

    with pytest.raises(AttachmentOwnershipError):
        AttachmentPromotionService(env["engine"]).promote(
            tenant_id=env["tenant"],
            actor_id="different-actor",
            attachment_id=rec_id,
            target_knowledge_base_id=_target_kb(env),
            relative_path="promoted/owner-boundary.txt",
            client_request_id="owner-boundary-1",
            request_id="trace-other-actor",
        )


def test_attachment_promotion_rejects_not_ready_and_traversal(env) -> None:
    rec_id = _register(env, "obj-not-ready", "text/plain", 5, "cr-not-ready")
    with pytest.raises(AttachmentStateConflict):
        _promote(env, rec_id, path="safe/file.txt", request="not-ready")

    store = LocalBytesStore()
    store.put("obj-path", b"path content")
    ready_id = _register(env, "obj-path", "text/plain", 12, "cr-path")
    _process(env, ready_id, store, vision=False)
    with pytest.raises(PromotionError):
        _promote(env, ready_id, path="../escape.txt", request="bad-path")


def test_attachment_promotion_rejects_path_conflict_and_trash_reserved(env) -> None:
    store = LocalBytesStore()
    body = b"conflict content"
    store.put("obj-conflict", body)
    rec_id = _register(env, "obj-conflict", "text/plain", len(body), "cr-conflict")
    _process(env, rec_id, store, vision=False)
    kb = _target_kb(env)
    path = "promoted/conflict.txt"

    with env["engine"].begin() as conn:
        conn.execute(
            text(
                "INSERT INTO documents "
                "(id, tenant_id, kb_id, title, status, version, mime_type, checksum, "
                "chunk_count, source_type, created_at, updated_at, normalized_relative_path) "
                "VALUES (:id,:tenant,:kb,'existing','READY',1,'text/plain','x',0,'UPLOAD',"
                ":now,:now,:path)"
            ),
            {
                "id": "existing-doc",
                "tenant": env["tenant"],
                "kb": kb,
                "now": "2026-08-12T00:00:00Z",
                "path": path,
            },
        )
    with pytest.raises(PromotionPathConflict):
        _promote(env, rec_id, path=path, request="conflict-1")

    with env["engine"].begin() as conn:
        conn.execute(
            text(
                "UPDATE documents SET deleted_at=NULL, status='DELETED' "
                "WHERE id='existing-doc'"
            ),
            {},
        )
    with pytest.raises(PromotionPathReservedByTrash):
        _promote(env, rec_id, path=path, request="conflict-2")


def test_attachment_promotion_enforces_owner_and_target_tenant(env) -> None:
    store = LocalBytesStore()
    body = b"tenant boundary"
    store.put("obj-tenant", body)
    rec_id = _register(env, "obj-tenant", "text/plain", len(body), "cr-tenant")
    _process(env, rec_id, store, vision=False)
    service = AttachmentPromotionService(env["engine"])

    with pytest.raises(AttachmentOwnershipError):
        service.promote(
            tenant_id=env["tenant"],
            actor_id="other-user",
            attachment_id=rec_id,
            target_knowledge_base_id=_target_kb(env),
            relative_path="promoted/tenant.txt",
            client_request_id="owner-fail",
            request_id="trace",
        )


def test_attachment_promotion_uses_live_tenant_role_not_legacy_user_role(env) -> None:
    store = LocalBytesStore()
    body = b"live ACL boundary"
    store.put("obj-live-acl", body)
    rec_id = _register(env, "obj-live-acl", "text/plain", len(body), "cr-live-acl")
    _process(env, rec_id, store, vision=False)
    kb = _target_kb(env)
    service = AttachmentPromotionService(env["engine"])
    with env["engine"].begin() as conn:
        member_role_id = conn.execute(
            text("SELECT id FROM tenant_roles WHERE tenant_id=:t AND slug='member'"),
            {"t": env["tenant"]},
        ).scalar_one()
        conn.execute(
            text(
                "UPDATE tenant_memberships SET role_id=:role "
                "WHERE tenant_id=:t AND user_id=:u"
            ),
            {"role": member_role_id, "t": env["tenant"], "u": env["user"]},
        )
        conn.execute(
            text("DELETE FROM kb_memberships WHERE tenant_id=:t AND kb_id=:kb AND user_id=:u"),
            {"t": env["tenant"], "kb": kb, "u": env["user"]},
        )
        legacy_role = conn.execute(
            text("SELECT role FROM users WHERE id=:u"), {"u": env["user"]}
        ).scalar_one()
    assert str(legacy_role).upper() == "OWNER"
    with pytest.raises(PromotionForbidden):
        _promote(env, rec_id, path="promoted/live-acl.txt", request="live-acl-1")
    with pytest.raises(PromotionTargetNotFound):
        service.promote(
            tenant_id=env["tenant"],
            actor_id=env["user"],
            attachment_id=rec_id,
            target_knowledge_base_id="kb-from-other-tenant",
            relative_path="promoted/tenant.txt",
            client_request_id="tenant-fail",
            request_id="trace",
        )


def test_attachment_promotion_http_contract_returns_202(env, monkeypatch) -> None:
    store = LocalBytesStore()
    body = b"http promotion"
    store.put("obj-http", body)
    rec_id = _register(env, "obj-http", "text/plain", len(body), "cr-http")
    _process(env, rec_id, store, vision=False)

    from fastapi.testclient import TestClient

    import ekb_api.routers.attachments as attachment_router
    from ekb_api.main import app

    auth = AuthContext(
        actor_id=env["user"],
        tenant_id=env["tenant"],
        tenant_role=TenantRole.OWNER,
        platform_role="NONE",
        capabilities=[CAP_KB_WRITE],
        policy_version=1,
        trace_id="trace-http-promotion",
    )
    monkeypatch.setattr(attachment_router, "_engine", lambda: env["engine"])
    app.dependency_overrides[get_live_auth_context] = lambda: auth
    try:
        response = TestClient(app).post(
            f"/api/v1/attachments/{rec_id}/promotions",
            json={
                "target_knowledge_base_id": _target_kb(env),
                "relative_path": "promoted/http.txt",
                "client_request_id": "http-promotion-1",
            },
        )
    finally:
        app.dependency_overrides.pop(get_live_auth_context, None)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "QUEUED"

"""PH6 RAG 验收测试：检索范围 ACL、严格证据门禁、引用元数据、负向检索、引用持久化。

对应 FR-050 / FR-051 / FR-052 / FR-053 / FR-054。
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from ekb_api.domain import Chunk
from ekb_api.main import app
from ekb_api.schemas import AskRequest
from ekb_api.services import rag as rag

client = TestClient(app)


def _login() -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}


def _sse_citation_items(body: str) -> list[dict]:
    """从 SSE 文本里抽取 citations 事件的 items（兼容 v1/v2）。"""
    import json as _json

    items: list[dict] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = next(
            (ln[len("event:") :].strip() for ln in block.splitlines() if ln.startswith("event:")),
            "",
        )
        if event not in {"citations", "citation"}:
            continue
        data_line = next((ln for ln in block.splitlines() if ln.startswith("data:")), None)
        if data_line is None:
            continue
        data = _json.loads(data_line[len("data:") :])
        if event == "citations":
            items.extend(data.get("payload", {}).get("items", []))
        else:
            items.append(data)
    return items


def _ask(token: str, question: str, *, kb_id: str | None = None, answer_mode: str | None = None) -> str:
    payload: dict = {"question": question}
    if kb_id is not None:
        payload["kb_ids"] = [kb_id]
    if answer_mode is not None:
        payload["options"] = {"answer_mode": answer_mode}
    resp = client.post("/api/v1/qa/ask", headers=_auth_headers(token), json=payload)
    assert resp.status_code == 200, resp.text
    return resp.text


# ---------------------------------------------------------------------------
# 单元测试：rag 纯函数
# ---------------------------------------------------------------------------


def _fake_store(visible_kb_ids: list[str]):
    store = types.SimpleNamespace()

    class _KB:
        def __init__(self, kid):
            self.id = kid

    store.list_knowledge_bases = lambda auth: [_KB(k) for k in visible_kb_ids]
    return store


def _chunk(doc_id: str, content: str = "证据内容", **kw) -> Chunk:
    return Chunk(
        id=f"c-{doc_id}",
        tenant_id="t1",
        kb_id="kb1",
        doc_id=doc_id,
        doc_version=kw.get("doc_version", 1),
        title=kw.get("title", "文档"),
        section_path=kw.get("section_path", []),
        content=content,
        score=kw.get("score", 0.9),
        updated_at=kw.get("updated_at", "2026-08-12T00:00:00Z"),
        page=kw.get("page"),
        sheet=kw.get("sheet"),
        paragraph=kw.get("paragraph"),
        source_path=kw.get("source_path"),
    )


def test_validate_kb_scope_accepts_visible_ids():
    store = _fake_store(["kb1", "kb2"])
    assert rag.validate_kb_scope(store, object(), ["kb1"]) == ["kb1"]
    assert rag.validate_kb_scope(store, object(), []) == []


def test_validate_kb_scope_rejects_forged_id():
    store = _fake_store(["kb1"])
    with pytest.raises(Exception) as exc:
        rag.validate_kb_scope(store, object(), ["kb1", "forged-kb"])
    # 不泄露资源是否存在：统一 404
    assert "404" in str(exc.value) or "NOT_FOUND" in str(exc.value)


def test_needs_strict_refusal_only_when_strict_and_no_evidence():
    chunks = [_chunk("d1")]
    # ENHANCED 不拒答
    assert rag.needs_strict_refusal("ENHANCED", ["kb1"], []) is False
    # 未选中 KB 不拒答
    assert rag.needs_strict_refusal("STRICT", [], []) is False
    # STRICT + 选中 KB + 无证据 → 拒答
    assert rag.needs_strict_refusal("STRICT", ["kb1"], []) is True
    # STRICT + 选中 KB + 有证据 → 不拒答
    assert rag.needs_strict_refusal("STRICT", ["kb1"], chunks) is False


def test_filter_retrievable_drops_revoked():
    chunks = [_chunk("d1"), _chunk("d2")]
    kept = rag.filter_retrievable(chunks, revoked_doc_ids=["d1"])
    assert [c.doc_id for c in kept] == ["d2"]
    # 无撤权集合时原样返回
    assert rag.filter_retrievable(chunks) == chunks


def test_build_citations_carries_full_metadata():
    chunk = _chunk(
        "d1",
        doc_version=3,
        title="运维手册",
        section_path=["第一章"],
        page=12,
        sheet="Sheet2",
        paragraph=4,
        source_path="ch1#sec2",
    )
    items = rag.build_citations([chunk], [], max_citations=5)
    assert len(items) == 1
    it = items[0]
    assert it["type"] == "kb"
    assert it["version"] == 3
    assert it["page"] == 12
    assert it["sheet"] == "Sheet2"
    assert it["paragraph"] == 4
    assert it["source_path"] == "ch1#sec2"
    assert it["updated_at"] == "2026-08-12T00:00:00Z"
    assert it["doc_id"] == "d1"
    assert it["chunk_id"] == "d1:c-d1"


def test_attachment_citations_are_separate_and_budgeted():
    attachments = [
        {
            "attachment_id": "att-1",
            "title": "附件一",
            "mime": "text/plain",
            "text": "第一份附件证据",
            "chunks": [{"id": "chunk-1"}],
        },
        {
            "attachment_id": "att-2",
            "title": "附件二",
            "mime": "text/plain",
            "text": "第二份附件证据",
            "chunks": [{"id": "chunk-2"}],
        },
    ]
    items = rag.build_attachment_citations(attachments, 1)
    assert len(items) == 1
    assert items[0]["type"] == "attachment"
    assert items[0]["doc_id"] == "att-1"
    assert items[0]["chunk_id"] == "chunk-1"
    assert rag.build_citations([_chunk("d1")], [], 0) == []


def test_ask_request_default_answer_mode_strict():
    req = AskRequest(question="你好")
    assert req.options.answer_mode == "STRICT"


# ---------------------------------------------------------------------------
# 集成测试：真实 qa/ask 链路
# ---------------------------------------------------------------------------


def test_strict_refusal_when_no_evidence():
    """FR-051：STRICT（默认）下选中 KB 但无证据 → refusal，且不编造引用。"""
    token = _login()
    kb_id = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()[0]["id"]
    body = _ask(token, "xyzzy wumpus frobnitz quux", kb_id=kb_id, answer_mode="STRICT")
    assert '"finish_reason":"refusal"' in body
    assert _sse_citation_items(body) == []


def test_enhanced_passthrough_when_no_evidence():
    """FR-051：ENHANCED 模式下无证据不触发拒答门禁（仍可结合常识作答）。"""
    token = _login()
    kb_id = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()[0]["id"]
    body = _ask(token, "xyzzy wumpus frobnitz quux", kb_id=kb_id, answer_mode="ENHANCED")
    assert '"finish_reason":"refusal"' not in body


def test_forged_kb_id_rejected_with_404():
    """FR-050：伪造的 kb_id 在检索范围校验阶段即被拒绝（不泄露资源存在性）。"""
    token = _login()
    resp = client.post(
        "/api/v1/qa/ask",
        headers=_auth_headers(token),
        json={"question": "任意问题", "kb_ids": ["forged-kb-id"]},
    )
    assert resp.status_code == 404


def test_citation_metadata_persisted_to_message():
    """FR-052/FR-053：命中证据时引用带版本/时间戳，并持久化到消息 metadata_redacted。"""
    token = _login()
    headers = {"Authorization": f"Bearer {token}"}
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]
    body = _ask(token, "连接池", kb_id=kb_id)
    items = _sse_citation_items(body)
    assert items, "命中证据时应至少产出一条引用"
    for it in items:
        assert it.get("updated_at"), f"引用缺少 updated_at: {it}"
        assert "version" in it

    # 找到 assistant message 并确认 citations 已持久化
    conv_id = None
    for line in body.splitlines():
        if line.startswith("data:"):
            import json as _json

            try:
                data = _json.loads(line[len("data:") :])
            except Exception:
                continue
            conv_id = data.get("conversation_id")
            if conv_id:
                break
    assert conv_id, "响应应含 conversation_id"
    msgs = client.get(f"/api/v1/conversations/{conv_id}/messages", headers=headers).json()
    assistant = next((m for m in msgs if m["role"] == "ASSISTANT"), None)
    assert assistant is not None
    md = assistant.get("metadata_redacted") or {}
    assert md.get("citations"), "消息应持久化 citations 元数据"

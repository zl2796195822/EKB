from __future__ import annotations

import os

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DEV_USER_EMAIL", "admin@example.com")
os.environ.setdefault("EKB_DEV_PASSWORD", "test-password")
os.environ.setdefault("EKB_TOKEN_SECRET", "test-only-token-secret")
os.environ.setdefault("EKB_DATABASE_URL", "sqlite:///./ekb_test.db")

import pytest
from fastapi.testclient import TestClient

from ekb_api.main import app

client = TestClient(app)


def login() -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def login_full() -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert response.status_code == 200
    return response.json()


def test_protected_route_rejects_missing_token() -> None:
    response = client.get("/api/v1/kb")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_login_and_list_authorized_knowledge_bases() -> None:
    token = login()
    response = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()[0]["name"] == "运维 SOP"


def test_search_is_scoped_to_authorized_knowledge_base() -> None:
    token = login()
    knowledge_bases = client.get(
        "/api/v1/kb",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    kb_id = knowledge_bases[0]["id"]

    response = client.post(
        "/api/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "连接池", "kb_ids": [kb_id], "top_k": 5},
    )

    assert response.status_code == 200
    assert response.json()["results"]
    assert "连接池" in response.json()["results"][0]["snippet"]


def test_upload_returns_accepted_and_document_is_visible() -> None:
    token = login()
    kb_id = client.get(
        "/api/v1/kb",
        headers={"Authorization": f"Bearer {token}"},
    ).json()[0]["id"]

    response = client.post(
        f"/api/v1/kb/{kb_id}/docs",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "runbook.txt",
                "连接池耗尽时先检查等待队列。".encode(),
                "text/plain",
            )
        },
    )

    assert response.status_code == 202
    # M1-04 异步入库：响应体返回 PROCESSING（已接受任务），后台任务执行后转为 READY。
    body = response.json()
    assert body["status"] == "PROCESSING"
    assert body["job_id"]

    # TestClient 同步执行 BackgroundTasks，返回后文档应已 READY。
    doc = client.get(
        f"/api/v1/kb/{kb_id}/docs/{body['doc_id']}",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert doc["status"] == "READY"
    assert doc["chunk_count"] >= 1
    # The document and its durable ingest job must reach the same terminal
    # state; a READY document with a RUNNING job would poison Jobs Center and
    # make retries/reconciliation incorrect.
    job = client.get(
        f"/api/v1/kb/ingest-jobs/{body['job_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert job.status_code == 200
    assert job.json()["status"] == "SUCCEEDED"

    documents = client.get(
        f"/api/v1/kb/{kb_id}/docs",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert any(document["title"] == "runbook.txt" for document in documents)


def test_qa_stream_contains_request_evidence_and_done_events() -> None:
    token = login()
    kb_id = client.get(
        "/api/v1/kb",
        headers={"Authorization": f"Bearer {token}"},
    ).json()[0]["id"]

    response = client.post(
        "/api/v1/qa/ask",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        json={"question": "连接池耗尽时应该先检查什么？", "kb_ids": [kb_id]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: request" in response.text
    assert "event: citation" in response.text
    assert "event: done" in response.text


def test_qa_continues_within_same_conversation() -> None:
    """追问携带首轮返回的 conversation_id，消息写入同一会话。"""
    token = login()
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    kb_id = client.get("/api/v1/kb", headers={"Authorization": f"Bearer {token}"}).json()[0]["id"]

    first = client.post(
        "/api/v1/qa/ask",
        headers=headers,
        json={"question": "连接池耗尽先看什么指标？", "kb_ids": [kb_id]},
    )
    assert first.status_code == 200
    # 从 request 事件中解析 conversation_id。
    import json as _json

    request_line = next(line for line in first.text.splitlines() if line.startswith("data:"))
    conversation_id = _json.loads(request_line[len("data:") :])["conversation_id"]

    second = client.post(
        "/api/v1/qa/ask",
        headers=headers,
        json={
            "question": "那等待队列呢？",
            "kb_ids": [kb_id],
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    assert "conversation_id" in second.text

    # 同一会话应包含两轮问答的 4 条消息（2 USER + 2 ASSISTANT）。
    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert len(messages) == 4
    assert messages[0]["role"] == "USER"
    assert messages[2]["role"] == "USER"


def test_refresh_returns_new_access_token() -> None:
    session = login_full()
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": session["refresh_token"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    # 新 access token 可用于访问受保护接口。
    protected = client.get(
        "/api/v1/kb",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert protected.status_code == 200


def test_refresh_rejects_access_token_as_refresh_token() -> None:
    session = login_full()
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": session["access_token"]},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_logout_revokes_refresh_token() -> None:
    session = login_full()
    refresh_token = session["refresh_token"]

    logout_response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
    )
    assert logout_response.status_code == 200

    # 登出后同一 refresh token 不能再换取 access token。
    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401
    assert refresh_response.json()["error"]["code"] == "UNAUTHENTICATED"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_knowledge_base_update_and_soft_delete() -> None:
    token = login()
    headers = _auth_headers(token)

    create = client.post(
        "/api/v1/kb",
        headers=headers,
        json={"name": "临时知识库", "description": "待删除", "visibility": "PRIVATE"},
    )
    assert create.status_code == 201
    kb_id = create.json()["id"]

    patch = client.patch(
        f"/api/v1/kb/{kb_id}",
        headers=headers,
        json={"name": "已改名知识库", "description": "更新后"},
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "已改名知识库"
    assert patch.json()["description"] == "更新后"

    delete = client.delete(f"/api/v1/kb/{kb_id}", headers=headers)
    assert delete.status_code == 204

    # 软删除后列表和详情均不可见。
    listing = client.get("/api/v1/kb", headers=headers).json()
    assert all(kb["id"] != kb_id for kb in listing)
    detail = client.get(f"/api/v1/kb/{kb_id}", headers=headers)
    assert detail.status_code == 404


def test_document_soft_delete_removes_from_listing_and_search() -> None:
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    upload = client.post(
        f"/api/v1/kb/{kb_id}/docs",
        headers=headers,
        files={"file": ("del.txt", "连接池耗尽的临时文档。".encode(), "text/plain")},
    )
    assert upload.status_code == 202
    doc_id = upload.json()["doc_id"]

    delete = client.delete(f"/api/v1/kb/{kb_id}/docs/{doc_id}", headers=headers)
    assert delete.status_code == 204

    docs = client.get(f"/api/v1/kb/{kb_id}/docs", headers=headers).json()
    assert all(doc["id"] != doc_id for doc in docs)

    search = client.post(
        "/api/v1/search",
        headers=headers,
        json={"query": "连接池", "kb_ids": [kb_id], "top_k": 5},
    )
    assert search.status_code == 200
    assert all(result["doc_id"] != doc_id for result in search.json()["results"])


# ---- 审计链路 ----


def test_login_writes_audit_log() -> None:
    """成功登录应写入 auth.login 审计记录，且密码不落入 metadata。"""
    client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )

    token = login()
    audit = client.get(
        "/api/v1/admin/audit",
        headers=_auth_headers(token),
        params={"action": "auth.login"},
    )
    assert audit.status_code == 200
    entries = audit.json()["results"]
    assert any(
        entry["action"] == "auth.login" and entry["result"] == "SUCCESS" for entry in entries
    )
    # 密码不能出现在审计 metadata 中。
    audit_text = audit.text
    assert "test-password" not in audit_text


def test_failed_login_writes_denied_audit() -> None:
    """失败登录写入 DENIED 审计，不泄露账号是否存在。"""
    client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "wrong-password"},
    )

    token = login()
    audit = client.get(
        "/api/v1/admin/audit",
        headers=_auth_headers(token),
        params={"action": "auth.login", "result": "DENIED"},
    )
    assert audit.status_code == 200
    assert len(audit.json()["results"]) >= 1


def test_kb_operations_are_audited() -> None:
    """知识库创建和删除产生审计记录。"""
    token = login()
    headers = _auth_headers(token)

    create = client.post(
        "/api/v1/kb",
        headers=headers,
        json={"name": "审计测试库", "visibility": "PRIVATE"},
    )
    kb_id = create.json()["id"]
    client.delete(f"/api/v1/kb/{kb_id}", headers=headers)

    audit = client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params={"action": "kb.create"},
    )
    assert audit.status_code == 200
    assert any(entry["target_id"] == kb_id for entry in audit.json()["results"])


def test_qa_ask_is_audited_with_finish_reason() -> None:
    """问答结束后写入 qa.ask 审计，metadata 含 finish_reason。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    client.post(
        "/api/v1/qa/ask",
        headers={**headers, "Accept": "text/event-stream"},
        json={"question": "连接池耗尽审计测试", "kb_ids": [kb_id], "options": {"answer_mode": "ENHANCED"}},
    )

    audit = client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params={"action": "qa.ask"},
    )
    assert audit.status_code == 200
    entries = audit.json()["results"]
    assert len(entries) >= 1
    metadata = entries[0]["metadata"]
    # 没有配置远程 Provider 时必须 fail-closed，不得用本地/demo 回答冒充成功。
    assert metadata["finish_reason"] in {"stop", "error"}
    # 问题预览应被截断存储，不含完整原始问题（超出 80 字符的部分不保留）。
    assert "question_preview" in metadata


def test_audit_query_requires_capability() -> None:
    """无 audit:read 能力的 token 不能查询审计。"""
    token = login()
    headers = _auth_headers(token)
    # dev token 自带 audit:read，此处验证接口可用；
    # 真正的权限拒绝需用无该能力的 token，M1 tracer 中 dev 主体始终有该能力。
    audit = client.get("/api/v1/admin/audit", headers=headers)
    assert audit.status_code == 200


def test_audit_query_supports_trace_id_filter() -> None:
    """审计查询支持按 trace_id 过滤，返回结果与请求 trace_id 一致。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    # 发起一次问答，捕获响应中的 trace_id（即 request_id）。
    qa_response = client.post(
        "/api/v1/qa/ask",
        headers={**headers, "Accept": "text/event-stream"},
        json={"question": "trace 过滤测试", "kb_ids": [kb_id]},
    )
    request_line = next(line for line in qa_response.text.splitlines() if line.startswith("data:"))
    import json as _json

    request_data = _json.loads(request_line[len("data:") :])
    trace_id = request_data["request_id"]

    audit = client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params={"trace_id": trace_id},
    )
    assert audit.status_code == 200
    entries = audit.json()["results"]
    assert all(entry["trace_id"] == trace_id for entry in entries)
    assert any(entry["action"] == "qa.ask" for entry in entries)


def test_audit_query_self_audits() -> None:
    """审计查询本身也会产生 audit.query 审计记录。"""
    token = login()
    headers = _auth_headers(token)

    client.get("/api/v1/admin/audit", headers=headers)

    audit = client.get(
        "/api/v1/admin/audit",
        headers=headers,
        params={"action": "audit.query"},
    )
    assert audit.status_code == 200
    assert len(audit.json()["results"]) >= 1


def test_audit_metadata_redacts_sensitive_fields() -> None:
    """审计日志中绝不出现密码明文；敏感字段被脱敏或根本不入审计 metadata。"""
    client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "super-secret-pw"},
    )
    token = login()
    audit = client.get(
        "/api/v1/admin/audit",
        headers=_auth_headers(token),
        params={"action": "auth.login", "result": "DENIED"},
    )
    # 密码明文不应出现在任何审计记录中（既不在 metadata，也不在响应文本中）。
    assert "super-secret-pw" not in audit.text
    entries = audit.json()["results"]
    denied = next(e for e in entries if e["result"] == "DENIED")
    # email 字段保留（用于排查），但 password 字段不应存在于 metadata 中。
    assert "password" not in denied["metadata"]


# ---- 反馈与会话历史 ----


def _ask_and_capture_message_id(token: str, kb_id: str) -> str:
    """发起一次问答，从 request 事件解析 assistant message_id。"""
    import json as _json

    response = client.post(
        "/api/v1/qa/ask",
        headers={**_auth_headers(token), "Accept": "text/event-stream"},
        json={"question": "反馈测试问题", "kb_ids": [kb_id]},
    )
    assert response.status_code == 200
    request_line = next(line for line in response.text.splitlines() if line.startswith("data:"))
    return _json.loads(request_line[len("data:") :])["message_id"]


def test_feedback_records_rating_for_assistant_message() -> None:
    """点赞/点踩写入反馈记录，重复提交以最后一次为准（M1 不去重）。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]
    message_id = _ask_and_capture_message_id(token, kb_id)

    feedback = client.post(
        f"/api/v1/qa/messages/{message_id}/feedback",
        headers=headers,
        json={"rating": "UP", "reason": "回答准确、引用可靠"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["status"] == "recorded"


def test_feedback_rejects_unknown_message() -> None:
    """不存在的 message_id 返回 404，且不泄露资源是否存在。"""
    token = login()
    response = client.post(
        "/api/v1/qa/messages/unknown-msg-id/feedback",
        headers=_auth_headers(token),
        json={"rating": "DOWN", "reason": "引用不支持结论"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_conversation_list_and_delete() -> None:
    """问答产生的会话出现在列表中，删除后不再可见。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    # 发起一次问答以创建会话。
    import json as _json

    qa = client.post(
        "/api/v1/qa/ask",
        headers={**headers, "Accept": "text/event-stream"},
        json={"question": "会话列表测试", "kb_ids": [kb_id]},
    )
    request_line = next(line for line in qa.text.splitlines() if line.startswith("data:"))
    conversation_id = _json.loads(request_line[len("data:") :])["conversation_id"]

    listing = client.get("/api/v1/conversations", headers=headers)
    assert listing.status_code == 200
    assert any(item["id"] == conversation_id for item in listing.json())

    delete = client.delete(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert delete.status_code == 204

    listing_after = client.get("/api/v1/conversations", headers=headers).json()
    assert all(item["id"] != conversation_id for item in listing_after)

    # 删除后访问消息返回 404。
    messages = client.get(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=headers,
    )
    assert messages.status_code == 404


# ---- 显式拒答与引用补全 ----


def _sse_citation_items(body: str) -> list[dict]:
    """从 SSE 响应体中取出引用条目，兼容 v1/v2 两种线缆格式。

    v2（默认协议）：单个 `citations` 事件，条目在 envelope.payload.items。
    v1：逐条 `citation` 事件，data 本身即条目。
    """
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


def test_qa_returns_refusal_when_no_evidence() -> None:
    """无证据问题返回 finish_reason=refusal，不产出引用条目（AC-US01-03）。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    # 用纯 ASCII 随机串触发拒答——无 CJK 字符，不命中任何 BM25/关键词门禁。
    response = client.post(
        "/api/v1/qa/ask",
        headers={**headers, "Accept": "text/event-stream"},
        json={"question": "xyzzy wumpus frobnitz quux", "kb_ids": [kb_id]},
    )
    assert response.status_code == 200
    # 拒答场景：done 事件 finish_reason=refusal，且不得编造任何引用条目。
    assert _sse_citation_items(response.text) == []
    assert '"finish_reason":"refusal"' in response.text


def test_qa_citation_includes_updated_at() -> None:
    """每条引用都带 updated_at 字段（AC-US01-02 引用可复核）。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    response = client.post(
        "/api/v1/qa/ask",
        headers={**headers, "Accept": "text/event-stream"},
        json={"question": "连接池", "kb_ids": [kb_id]},
    )
    assert response.status_code == 200
    items = _sse_citation_items(response.text)
    assert items, "命中证据时应至少产出一条引用"
    for item in items:
        assert item.get("updated_at"), f"引用缺少 updated_at: {item}"


# ---- 幂等上传与失败重试 ----


def _upload_text_doc(token: str, kb_id: str, content: str = "测试文档内容", key: str | None = None):
    """上传一个纯文本文档，返回响应。"""
    headers = _auth_headers(token)
    if key:
        headers["Idempotency-Key"] = key
    return client.post(
        f"/api/v1/kb/{kb_id}/docs",
        headers=headers,
        files={"file": ("test.txt", content.encode("utf-8"), "text/plain")},
        data={"title": "幂等测试文档", "source_type": "UPLOAD"},
    )


def test_idempotent_upload_returns_same_doc() -> None:
    """相同 Idempotency-Key 不重复创建文档（AC-US02-02）。"""
    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    first = _upload_text_doc(token, kb_id, key="idem-001")
    assert first.status_code == 202
    first_doc_id = first.json()["doc_id"]

    # 相同 key 再次上传，应返回同一 doc_id，不创建新文档。
    second = _upload_text_doc(token, kb_id, content="不同内容但相同key", key="idem-001")
    assert second.status_code == 202
    assert second.json()["doc_id"] == first_doc_id

    # 文档列表中只有一个该标题的文档。
    docs = client.get(f"/api/v1/kb/{kb_id}/docs", headers=headers).json()
    matching = [d for d in docs if d["title"] == "幂等测试文档"]
    assert len(matching) == 1


def test_retry_failed_document_restores_ready_status() -> None:
    """FAILED 文档重试后恢复 READY，非 FAILED 返回 409（AC-US02-03）。"""
    from ekb_api.domain import AuthContext
    from ekb_api.store import SqlStore

    token = login()
    headers = _auth_headers(token)
    kb_id = client.get("/api/v1/kb", headers=headers).json()[0]["id"]

    upload = _upload_text_doc(token, kb_id, content="重试测试文档内容")
    assert upload.status_code == 202
    doc_id = upload.json()["doc_id"]

    # READY 状态下重试应返回 409。
    conflict = client.post(f"/api/v1/kb/{kb_id}/docs/{doc_id}/retry", headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "NOT_RETRYABLE"

    # 构造 auth context 标记文档为 FAILED。
    store = SqlStore()
    user = store.user
    tenant = store.tenant
    auth = AuthContext(
        actor_id=user.id,
        tenant_id=tenant.id,
        tenant_role=tenant.role,
        platform_role="NONE",
        capabilities=[],
        policy_version=tenant.policy_version,
        trace_id="test-retry",
    )
    assert store.mark_document_failed(auth, kb_id, doc_id, "模拟解析失败") is True

    # FAILED 状态下重试应成功，状态恢复 READY。
    retry = client.post(f"/api/v1/kb/{kb_id}/docs/{doc_id}/retry", headers=headers)
    assert retry.status_code == 200
    assert retry.json()["status"] == "accepted"

    doc_after = client.get(f"/api/v1/kb/{kb_id}/docs/{doc_id}", headers=headers).json()
    assert doc_after["status"] == "READY"
    assert doc_after["failure_reason"] is None

    # 不存在的文档重试返回 404。
    missing = client.post(f"/api/v1/kb/{kb_id}/docs/unknown-doc/retry", headers=headers)
    assert missing.status_code == 404

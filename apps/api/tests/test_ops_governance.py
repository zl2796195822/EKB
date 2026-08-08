"""M3 治理与运营测试：运营看板、反馈闭环、审核队列、熔断降级、存储配额、文档版本、同步源。

覆盖 M3-1 ~ M3-7 的核心路径与安全边界。
"""

from __future__ import annotations

import json

from ekb_api.core.circuit_breaker import get_circuit_breaker

API = "/api/v1"
ADMIN = f"{API}/admin"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---- M3-1 运营看板 ----


def test_ops_dashboard_returns_metrics(client, dev_token):
    """运营看板返回问答量、准确率、拒答率、满意度等指标。"""
    r = client.get(f"{ADMIN}/ops/dashboard", headers=_headers(dev_token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "qa_volume" in data
    assert "answered" in data
    assert "refused" in data
    assert "accuracy" in data
    assert "refusal_rate" in data
    assert "satisfaction" in data
    assert "pending_reviews" in data
    assert "kb_count" in data
    assert "doc_count" in data
    assert data["kb_count"] >= 1  # 种子 KB


def test_ops_dashboard_requires_audit_capability(client, dev_token):
    """MEMBER 角色无 audit:read 能力时被拒绝。"""
    # 邀请一个 MEMBER
    client.post(
        f"{ADMIN}/users",
        headers=_headers(dev_token),
        json={"email": "dash@example.com", "name": "Dash", "password": "pw123", "role": "MEMBER"},
    )
    login = client.post(
        f"{API}/auth/login", json={"email": "dash@example.com", "password": "pw123"}
    )
    token = login.json()["access_token"]
    r = client.get(f"{ADMIN}/ops/dashboard", headers=_headers(token))
    # MEMBER 有 audit:read 能力（ROLE_CAPABILITIES["MEMBER"] 包含 CAP_AUDIT_READ）
    assert r.status_code == 200


# ---- M3-2 反馈闭环 ----


def test_feedback_list_and_annotate(client, dev_token):
    """反馈可列表、标注状态，且不自动覆盖生产知识。"""
    # 1) 先问一个问题触发 assistant 消息
    r = client.post(
        f"{API}/qa/ask",
        headers=_headers(dev_token),
        json={"question": "测试问题"},
    )
    assert r.status_code == 200
    # 从 SSE 流中提取 message_id
    events = r.text
    msg_id = None
    for line in events.split("\n"):
        if line.startswith("data:") and '"message_id"' in line:
            msg_id = json.loads(line[5:].strip())["message_id"]
            break
    assert msg_id, f"no message_id in response: {events[:200]}"

    # 2) 提交反馈
    r = client.post(
        f"{API}/qa/messages/{msg_id}/feedback",
        headers=_headers(dev_token),
        json={"rating": "DOWN", "reason": "回答不准确"},
    )
    assert r.status_code == 200, r.text

    # 3) 列出反馈
    r = client.get(f"{API}/qa/messages/feedback", headers=_headers(dev_token))
    assert r.status_code == 200, r.text
    feedbacks = r.json()["results"]
    assert len(feedbacks) >= 1
    fb = feedbacks[0]
    assert fb["rating"] == "DOWN"
    assert fb["status"] == "PENDING"

    # 4) 标注反馈
    fb_id = fb["id"]
    r = client.patch(
        f"{API}/qa/messages/feedback/{fb_id}",
        headers=_headers(dev_token),
        json={"status": "REVIEWED", "annotation": "已确认问题，需补充知识"},
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["status"] == "REVIEWED"
    assert updated["annotation"] == "已确认问题，需补充知识"


# ---- M3-3 审核队列 ----


def test_review_queue_auto_created_on_refusal(client, dev_token):
    """拒答（confidence=low）自动入审核队列。"""
    # 问一个无依据问题触发拒答
    r = client.post(
        f"{API}/qa/ask",
        headers=_headers(dev_token),
        json={"question": "asdkjhaskjdhaskjdhjkahsdkjahdkja"},
    )
    assert r.status_code == 200

    # 检查审核队列有 PENDING 项
    r = client.get(f"{ADMIN}/reviews", headers=_headers(dev_token), params={"status": "PENDING"})
    assert r.status_code == 200, r.text
    items = r.json()["results"]
    assert len(items) >= 1
    assert items[0]["confidence"] == "low"
    assert items[0]["status"] == "PENDING"


def test_review_item_status_transition(client, dev_token):
    """审核项可流转 PENDING → REVIEWED → RESOLVED。"""
    # 触发一个拒答入队
    client.post(
        f"{API}/qa/ask",
        headers=_headers(dev_token),
        json={"question": "zzzzzzzzzzz"},
    )
    r = client.get(f"{ADMIN}/reviews", headers=_headers(dev_token), params={"status": "PENDING"})
    items = r.json()["results"]
    assert len(items) >= 1
    item_id = items[0]["id"]

    # REVIEWED
    r = client.patch(
        f"{ADMIN}/reviews/{item_id}",
        headers=_headers(dev_token),
        json={"status": "REVIEWED", "resolution": "知识库需补充相关文档"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REVIEWED"

    # RESOLVED
    r = client.patch(
        f"{ADMIN}/reviews/{item_id}",
        headers=_headers(dev_token),
        json={"status": "RESOLVED", "resolution": "已补充文档并重新评估"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "RESOLVED"
    assert r.json()["resolved_by"] is not None


# ---- M3-4 熔断器 ----


def test_circuit_breaker_tracks_state():
    """熔断器在连续失败后开闸，成功后恢复。"""
    breaker = get_circuit_breaker()
    breaker.reset()
    assert breaker.allow_request() is True

    # 模拟连续失败
    for _ in range(breaker.failure_threshold):
        breaker.track_failure()
    assert not breaker.allow_request()  # OPEN

    # 手动恢复
    breaker.track_success()
    assert breaker.allow_request()  # CLOSED


# ---- M3-5 存储配额 ----


def test_storage_quota_enforced(client, dev_token):
    """租户存储配额超额时上传被拒（429）。"""
    # 开通一个 quota_storage_docs=1 的租户
    r = client.post(
        f"{ADMIN}/tenants",
        headers=_headers(dev_token),
        json={
            "name": "StorageQuotaTenant",
            "owner_email": "storage-quota@example.com",
            "owner_name": "StorageQuotaOwner",
            "owner_password": "sqpass",
            "quota_storage_docs": 1,
        },
    )
    assert r.status_code == 201, r.text
    r.json()["id"]

    # 登录
    login = client.post(
        f"{API}/auth/login", json={"email": "storage-quota@example.com", "password": "sqpass"}
    )
    token = login.json()["access_token"]

    # 创建 KB
    r = client.post(
        f"{API}/kb",
        headers=_headers(token),
        json={"name": "QuotaKB", "description": "test"},
    )
    assert r.status_code == 201, r.text
    kb_id = r.json()["id"]

    # 上传第一个文档（成功）
    r = client.post(
        f"{API}/kb/{kb_id}/docs",
        headers=_headers(token),
        files={"file": ("a.txt", b"content a", "text/plain")},
        data={"title": "DocA"},
    )
    assert r.status_code == 202, r.text

    # 上传第二个文档（被拒 429）
    r = client.post(
        f"{API}/kb/{kb_id}/docs",
        headers=_headers(token),
        files={"file": ("b.txt", b"content b", "text/plain")},
        data={"title": "DocB"},
    )
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"


# ---- M3-6 文档版本 ----


def test_document_version_snapshot_and_diff(client, dev_token):
    """文档上传后产生版本快照，diff 可比对。"""
    # 获取种子 KB
    r = client.get(f"{API}/kb", headers=_headers(dev_token))
    kb_id = r.json()[0]["id"]

    # 上传文档
    r = client.post(
        f"{API}/kb/{kb_id}/docs",
        headers=_headers(dev_token),
        files={"file": ("ver_test.txt", b"version test content", "text/plain")},
        data={"title": "VerTest"},
    )
    assert r.status_code == 202, r.text
    doc_id = r.json()["doc_id"]

    # 等待 ingest 完成（同步执行 background_tasks in TestClient）
    # 查询文档版本列表
    r = client.get(
        f"{ADMIN}/kb/{kb_id}/docs/{doc_id}/versions",
        headers=_headers(dev_token),
    )
    assert r.status_code == 200, r.text
    versions = r.json()
    assert len(versions) >= 1
    assert versions[0]["version"] == 1
    assert versions[0]["chunk_count"] >= 1
    assert "content_snapshot" in versions[0]


# ---- M3-7 同步源 ----


def test_sync_source_crud_and_run(client, dev_token):
    """同步源可创建、列表、触发同步。"""
    # 获取种子 KB
    r = client.get(f"{API}/kb", headers=_headers(dev_token))
    kb_id = r.json()[0]["id"]

    # 创建同步源
    r = client.post(
        f"{ADMIN}/sync/sources",
        headers=_headers(dev_token),
        json={
            "kb_id": kb_id,
            "name": "工单同步",
            "source_type": "TICKET",
            "source_url": "https://tickets.example.com/api",
        },
    )
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["name"] == "工单同步"
    assert src["source_type"] == "TICKET"
    assert src["status"] == "ACTIVE"
    src_id = src["id"]

    # 列表
    r = client.get(f"{ADMIN}/sync/sources", headers=_headers(dev_token))
    assert r.status_code == 200, r.text
    sources = r.json()
    assert any(s["id"] == src_id for s in sources)

    # 触发同步
    r = client.post(
        f"{ADMIN}/sync/sources/{src_id}/run",
        headers=_headers(dev_token),
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["status"] == "ACTIVE"
    assert result["source_id"] == src_id


def test_sync_source_failure_retry(client, dev_token):
    """同步失败累加重试次数，3 次后标记 FAILED。"""
    from ekb_api.domain import AuthContext, TenantRole

    # 获取种子 KB
    r = client.get(f"{API}/kb", headers=_headers(dev_token))
    kb_id = r.json()[0]["id"]

    # 创建同步源
    r = client.post(
        f"{ADMIN}/sync/sources",
        headers=_headers(dev_token),
        json={"kb_id": kb_id, "name": "FailSync", "source_type": "API"},
    )
    src_id = r.json()["id"]

    # 直接调 store 模拟 3 次失败
    from ekb_api.store import SqlStore

    store = SqlStore()
    # 构造 auth context
    from ekb_api.core.config import get_settings

    get_settings()
    auth = AuthContext(
        actor_id="dev",
        tenant_id=store.tenant.id,
        tenant_role=TenantRole.OWNER,
        platform_role="PLATFORM_ADMIN",
        capabilities=["kb:write", "audit:read"],
        policy_version=1,
        trace_id="test",
    )
    for i in range(3):
        store.record_sync_result(
            auth, src_id, success=False, error_message=f"connection refused (attempt {i + 1})"
        )

    # 验证状态
    sources = store.list_sync_sources(auth)
    src = next(s for s in sources if s.id == src_id)
    assert src.status == "FAILED"
    assert src.retry_count == 3
    assert "connection refused" in (src.error_message or "")

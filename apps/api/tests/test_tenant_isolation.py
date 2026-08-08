from __future__ import annotations

API = "/api/v1"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_kb(client, token, name="KB", visibility="PRIVATE"):
    resp = client.post(
        f"{API}/kb",
        headers=_headers(token),
        json={"name": name, "description": "t", "visibility": visibility},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _provision_tenant(client, token, name, email, password):
    resp = client.post(
        f"{API}/admin/tenants",
        headers=_headers(token),
        json={
            "name": name,
            "owner_email": email,
            "owner_name": name + "-owner",
            "owner_password": password,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---- M2-1 / M2-5 跨租户隔离 ----


def test_cross_tenant_kb_invisible_and_inaccessible(client, dev_token):
    kb1 = _create_kb(client, dev_token, "tenant1-kb")

    # 开通第二个租户与其 OWNER 用户。
    _provision_tenant(client, dev_token, "Tenant2", "t2@example.com", "t2pass")
    t2_token = _login_t2(client, "t2@example.com", "t2pass")

    # T2 列举不到 T1 的 KB。
    listed = client.get(f"{API}/kb", headers=_headers(t2_token)).json()
    assert all(kb["id"] != kb1 for kb in listed)

    # T2 直接访问 T1 的 KB → 404（不泄露存在）。
    assert client.get(f"{API}/kb/{kb1}", headers=_headers(t2_token)).status_code == 404
    # T2 上传文档到 T1 的 KB → 404。
    assert (
        client.post(
            f"{API}/kb/{kb1}/docs",
            headers=_headers(t2_token),
            files={"file": ("x.txt", b"hello", "text/plain")},
        ).status_code
        == 404
    )


def _login_t2(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_cross_tenant_search_isolation(client, dev_token):
    """检索按 tenant_id 过滤：T2 用户搜不到 T1 的知识库内容（M2-3 同源过滤）。"""
    kb1 = _create_kb(client, dev_token, "tenant1-kb")
    # 往 T1 的 KB 写入一个可被检索到的文档。
    client.post(
        f"{API}/kb/{kb1}/docs",
        headers=_headers(dev_token),
        files={"file": ("sop.txt", "数据库连接池耗尽时检查连接池使用率".encode(), "text/plain")},
    )
    t2_token = _login_t2(client, "t2@example.com", "t2pass")
    resp = client.post(
        f"{API}/search",
        headers=_headers(t2_token),
        json={"query": "数据库连接池耗尽", "top_k": 5},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == []


def test_cross_tenant_audit_isolation(client, dev_token):
    """T2 用户看不到 T1 的审计日志（审计按 tenant_id 过滤）。"""
    dev_tenant = client.get(f"{API}/me", headers=_headers(dev_token)).json()["tenants"][0]["id"]
    login_resp = client.post(
        f"{API}/auth/login", json={"email": "t2@example.com", "password": "t2pass"}
    )
    assert login_resp.status_code == 200
    t2_token = login_resp.json()["access_token"]
    t2_tenant = login_resp.json()["tenants"][0]["id"]

    resp = client.get(f"{API}/admin/audit", headers=_headers(t2_token))
    # T2 非平台管理员，只能看到自己租户（或平台级）的审计，绝不出现 T1 的记录。
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert all(r["tenant_id"] in (None, t2_tenant) for r in results)
    assert all(r["tenant_id"] != dev_tenant for r in results)


# ---- M2-2 RBAC：MEMBER 越权写被拒 ----


def test_member_cannot_write_kb(client, dev_token):
    # dev 在 T1 邀请一名 MEMBER。
    invite = client.post(
        f"{API}/admin/users",
        headers=_headers(dev_token),
        json={
            "email": "member1@t1.example.com",
            "name": "Member1",
            "password": "memberpass",
            "role": "MEMBER",
        },
    )
    assert invite.status_code == 201, invite.text
    member_token = _login_member(client, "member1@t1.example.com", "memberpass")

    # MEMBER 创建 KB → 403。
    assert (
        client.post(
            f"{API}/kb",
            headers=_headers(member_token),
            json={"name": "should-fail", "visibility": "PRIVATE"},
        ).status_code
        == 403
    )


def _login_member(client, email, password):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_non_manager_cannot_grant_member(client, dev_token):
    """非知识库管理员（即便有 kb:write）不能管理成员（M2-2 同源 ACL）。"""
    # dev 邀请 MEMBER，再邀请一名有 kb:write 的管理员（ADMIN 角色仍非该 KB 的管理员）。
    client.post(
        f"{API}/admin/users",
        headers=_headers(dev_token),
        json={
            "email": "admin2@t1.example.com",
            "name": "Admin2",
            "password": "adminpass",
            "role": "ADMIN",
        },
    )
    admin2_token = _login_member(client, "admin2@t1.example.com", "adminpass")
    kb = _create_kb(client, dev_token, "acl-kb")
    # admin2 不是该 KB 的成员，授予成员应 403。
    assert (
        client.post(
            f"{API}/kb/{kb}/members",
            headers=_headers(admin2_token),
            json={"email": "member1@t1.example.com", "role": "VIEWER"},
        ).status_code
        == 403
    )


# ---- M2-2/3 每知识库 ACL：成员可见、非成员不可见 ----


def test_kb_membership_grants_visibility(client, dev_token):
    kb = _create_kb(client, dev_token, "shared-kb", visibility="PRIVATE")
    # 邀请 MEMBER 并登录。
    client.post(
        f"{API}/admin/users",
        headers=_headers(dev_token),
        json={
            "email": "viewer@t1.example.com",
            "name": "Viewer",
            "password": "viewerpass",
            "role": "MEMBER",
        },
    )
    viewer_token = _login_member(client, "viewer@t1.example.com", "viewerpass")

    # 授权前：PRIVATE KB 对 viewer 不可见。
    listed_before = client.get(f"{API}/kb", headers=_headers(viewer_token)).json()
    assert all(k["id"] != kb for k in listed_before)

    # dev 授予 viewer VIEWER 成员。
    grant = client.post(
        f"{API}/kb/{kb}/members",
        headers=_headers(dev_token),
        json={"email": "viewer@t1.example.com", "role": "VIEWER"},
    )
    assert grant.status_code == 201, grant.text

    # 授权后：viewer 可见该 KB，但无 kb:write → 更新仍 403。
    listed_after = client.get(f"{API}/kb", headers=_headers(viewer_token)).json()
    assert any(k["id"] == kb for k in listed_after)
    assert (
        client.patch(
            f"{API}/kb/{kb}", headers=_headers(viewer_token), json={"name": "x"}
        ).status_code
        == 403
    )


# ---- M2-6 租户开通 ----


def test_provision_tenant_and_isolation(client, dev_token):
    before = client.get(f"{API}/admin/tenants", headers=_headers(dev_token)).json()
    t3 = _provision_tenant(client, dev_token, "Tenant3", "t3@example.com", "t3pass")
    assert t3["name"] == "Tenant3"
    after = client.get(f"{API}/admin/tenants", headers=_headers(dev_token)).json()
    assert len(after) == len(before) + 1

    # 新租户 OWNER 登录后可建 KB，且与其他租户隔离。
    t3_token = _login_t2(client, "t3@example.com", "t3pass")
    kb3 = _create_kb(client, t3_token, "t3-kb")
    t3_listed = client.get(f"{API}/kb", headers=_headers(t3_token)).json()
    assert any(k["id"] == kb3 for k in t3_listed)
    # dev（T1）看不到 T3 的 KB。
    dev_listed = client.get(f"{API}/kb", headers=_headers(dev_token)).json()
    assert all(k["id"] != kb3 for k in dev_listed)


def test_non_platform_admin_cannot_provision(client, dev_token):
    """普通租户 OWNER（非平台管理员）不能开通租户。"""
    t2_token = _login_t2(client, "t2@example.com", "t2pass")
    resp = client.post(
        f"{API}/admin/tenants",
        headers=_headers(t2_token),
        json={
            "name": "NoGo",
            "owner_email": "nogo@example.com",
            "owner_name": "nogo",
            "owner_password": "x",
        },
    )
    assert resp.status_code == 403


# ---- M2-7 出域策略 ----


def test_egress_deny_blocks_qa(client, dev_token):
    """出域策略为 deny 的租户禁止问答生成（M2-7 强制点）。"""
    t4 = _provision_tenant(client, dev_token, "Tenant4", "t4@example.com", "t4pass")
    # 通过 store 直接将该租户出域策略置为 deny（模拟高敏感租户配置）。
    from ekb_api import models
    from ekb_api.core.db import get_session_local

    with get_session_local()() as session:
        row = session.query(models.Tenant).filter_by(id=t4["id"]).first()
        row.egress_policy = "deny"
        session.commit()

    t4_token = _login_t2(client, "t4@example.com", "t4pass")
    resp = client.post(
        f"{API}/qa/ask",
        headers=_headers(t4_token),
        json={"question": "任何问题"},
    )
    # SSE 端点：拒绝以非 2xx 状态码返回（流式错误前已统一拦截）。
    assert resp.status_code == 403, resp.text

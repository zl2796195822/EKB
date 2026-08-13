"""Phase 1 /me 全链路冒烟：真实登录后逐个打通 12 个个人中心端点。

用法（后端需已在 127.0.0.1:8023 运行）：
    .venv/bin/python apps/api/scripts/smoke_v3_profile.py [base_url] [email] [password]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8023/api/v1"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "admin@example.com"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "SmokeTest#2026"

PASSED: list[str] = []
FAILED: list[str] = []


def call(method: str, path: str, token: str | None = None, body: object | None = None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  FAIL  {name} :: {detail}")


def main() -> int:
    print("== 0. 登录 ==")
    status, login = call("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})
    check("POST /auth/login", status == 200, f"status={status} body={login}")
    if status != 200:
        return 1
    token = login["access_token"]

    print("== 1. GET /me ==")
    status, me = call("GET", "/me", token)
    check("GET /me 返回 200", status == 200, f"status={status} body={me}")
    check("GET /me 带 profile 子对象", isinstance(me.get("profile"), dict), str(me)[:200])
    check("GET /me 带 capabilities", isinstance(me.get("capabilities"), list), str(me)[:200])
    check("GET /me 带 tenants", isinstance(me.get("tenants"), list), str(me)[:200])

    print("== 2. PATCH /me/profile ==")
    status, patched = call("PATCH", "/me/profile", token, {"display_name": "冒烟测试", "department": "运维中心"})
    check("PATCH /me/profile 返回 200", status == 200, f"status={status} body={patched}")
    check(
        "PATCH /me/profile 写入生效",
        patched.get("profile", {}).get("display_name") == "冒烟测试",
        str(patched)[:200],
    )
    _, me2 = call("GET", "/me", token)
    check(
        "PATCH 后 GET /me 读到新值（真落库）",
        me2.get("profile", {}).get("display_name") == "冒烟测试",
        str(me2.get("profile"))[:200],
    )
    check(
        "未提供的字段不被清空",
        me2.get("profile", {}).get("timezone") == me.get("profile", {}).get("timezone"),
        f"before={me.get('profile',{}).get('timezone')} after={me2.get('profile',{}).get('timezone')}",
    )

    print("== 3. GET/PATCH /me/preferences ==")
    status, prefs = call("GET", "/me/preferences", token)
    check("GET /me/preferences 返回 200", status == 200, f"status={status} body={prefs}")
    status, saved = call("PATCH", "/me/preferences", token, {"preferences": {"theme": "dark", "density": "compact"}})
    check("PATCH /me/preferences 返回 200", status == 200, f"status={status} body={saved}")
    _, prefs2 = call("GET", "/me/preferences", token)
    check(
        "偏好设置真落库",
        prefs2.get("preferences", {}).get("theme") == "dark",
        str(prefs2)[:200],
    )

    print("== 4. GET /me/sessions ==")
    status, sessions = call("GET", "/me/sessions", token)
    check("GET /me/sessions 返回 200", status == 200, f"status={status} body={sessions}")
    items = sessions.get("items", [])
    check("登录后 auth_sessions 有记录", len(items) >= 1, f"items={len(items)}")
    if items:
        check("会话记录含 ip_hash/ua_hash", "ip_hash" in items[0] and "ua_hash" in items[0], str(items[0])[:200])

    print("== 5. API Key 生命周期 ==")
    status, created = call("POST", "/me/api-keys", token, {"name": "冒烟密钥"})
    check("POST /me/api-keys 返回 200", status == 200, f"status={status} body={created}")
    secret = created.get("secret")
    key_id = created.get("id")
    check("创建时返回一次性 secret", bool(secret), str(created)[:200])
    check("创建时返回 prefix", bool(created.get("prefix")), str(created)[:200])

    status, keys = call("GET", "/me/api-keys", token)
    check("GET /me/api-keys 返回 200", status == 200, f"status={status}")
    listed = keys.get("items", [])
    check("列表能查到刚建的 key", any(k.get("id") == key_id for k in listed), f"items={len(listed)}")
    check("列表不回显明文 secret", all("secret" not in k for k in listed), str(listed)[:200])

    status, _ = call("DELETE", f"/me/api-keys/{key_id}", token)
    check("DELETE /me/api-keys/{id} 返回 200", status == 200, f"status={status}")
    _, keys2 = call("GET", "/me/api-keys", token)
    revoked = [k for k in keys2.get("items", []) if k.get("id") == key_id]
    check(
        "撤销后状态不再是 ACTIVE",
        not revoked or revoked[0].get("status") != "ACTIVE",
        str(revoked)[:200],
    )

    print("== 6. 通知 ==")
    status, notifs = call("GET", "/me/notifications", token)
    check("GET /me/notifications 返回 200", status == 200, f"status={status} body={notifs}")
    check("通知返回 items 数组", isinstance(notifs.get("items"), list), str(notifs)[:200])
    status, unread = call("GET", "/me/notifications?unread_only=true", token)
    check("unread_only 过滤可用", status == 200, f"status={status}")
    notif_items = notifs.get("items", [])
    if notif_items:
        nid = notif_items[0]["id"]
        status, _ = call("PATCH", f"/me/notifications/{nid}", token)
        check("PATCH /me/notifications/{id} 返回 200", status == 200, f"status={status}")
    else:
        print("  SKIP  标记已读（当前无通知数据）")

    print("== 7. 会话撤销 ==")
    if items:
        status, _ = call("DELETE", f"/me/sessions/{items[0]['id']}", token)
        check("DELETE /me/sessions/{id} 返回 200", status == 200, f"status={status}")

    print("== 8. 修改密码（改回原密码，保证可重复运行）==")
    status, _ = call("POST", "/me/password", token, {"old_password": PASSWORD, "new_password": "TempPass#2026"})
    check("POST /me/password 返回 200", status == 200, f"status={status}")
    status, relogin = call("POST", "/auth/login", body={"email": EMAIL, "password": "TempPass#2026"})
    check("新密码可登录", status == 200, f"status={status}")
    if status == 200:
        status, _ = call(
            "POST", "/me/password", relogin["access_token"], {"old_password": "TempPass#2026", "new_password": PASSWORD}
        )
        check("密码改回原值", status == 200, f"status={status}")
    status, wrong = call("POST", "/me/password", token, {"old_password": "definitely-wrong", "new_password": "x" * 12})
    check("旧密码错误被拒绝", status in (400, 401, 403, 422), f"status={status} body={wrong}")

    print("== 9. 未鉴权访问必须 401 ==")
    status, _ = call("GET", "/me")
    check("无 token 访问 /me 返回 401", status == 401, f"status={status}")

    print()
    print(f"结果：{len(PASSED)} 通过 / {len(FAILED)} 失败")
    for item in FAILED:
        print(f"  ✗ {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

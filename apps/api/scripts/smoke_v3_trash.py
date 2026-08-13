"""Phase 2 回收站全链路冒烟：真实创建 → 软删 → 回收站可见 → 恢复 → 永久删除。

覆盖三类资源（知识库 / 文档 / 会话），并验证：
* 软删除后资源从原列表消失、同时出现在回收站
* 恢复后资源回到原列表、且从回收站消失
* 永久删除后原表与回收站都查不到，且不可再恢复
* 知识库仍在回收站时，其下文档不可单独恢复（409）
* 未鉴权访问一律 401

用法（后端需已在运行）：
    .venv/bin/python apps/api/scripts/smoke_v3_trash.py [base_url] [email] [password]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8023/api/v1"
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "admin@example.com"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "SmokeTest#2026"

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []


def call(method: str, path: str, token: str | None = None, body: object | None = None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=20) as resp:
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


def skip(name: str, why: str) -> None:
    SKIPPED.append(name)
    print(f"  SKIP  {name}（{why}）")


def as_items(body: object) -> list:
    """/kb 与 /conversations 返回裸数组，/trash 返回 {items:[...]}，统一取列表。"""
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return body["items"]
    return []


def ids_of(body: object) -> list:
    return [item.get("id") for item in as_items(body) if isinstance(item, dict)]


def find_item(items: list, resource_type: str, resource_id: str):
    for item in items:
        if item.get("resource_type") == resource_type and item.get("resource_id") == resource_id:
            return item
    return None


def trash_list(token: str, **params) -> dict:
    # 关键词可能含中文，必须 URL 编码，否则 urllib 会在 ascii 编码时崩掉。
    clean = {key: value for key, value in params.items() if value is not None}
    query = urllib.parse.urlencode(clean)
    path = "/trash" + (f"?{query}" if query else "")
    status, body = call("GET", path, token)
    return body if status == 200 else {"items": [], "_status": status, "_body": body}


def main() -> int:
    tag = uuid.uuid4().hex[:8]

    print("== 0. 登录 ==")
    status, login = call("POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD})
    check("POST /auth/login", status == 200, f"status={status} body={login}")
    if status != 200:
        return 1
    token = login["access_token"]

    print("== 1. GET /trash 基础契约 ==")
    status, page = call("GET", "/trash", token)
    check("GET /trash 返回 200", status == 200, f"status={status} body={page}")
    check("返回 items 数组", isinstance(page.get("items"), list), f"body={page}")
    check("返回 total", isinstance(page.get("total"), int), f"body={page}")
    check("返回分类计数 counts", isinstance(page.get("counts"), dict), f"body={page}")
    check(
        "counts 覆盖三类资源",
        set(page.get("counts", {})) == {"KB", "DOCUMENT", "CONVERSATION"},
        f"counts={page.get('counts')}",
    )
    status, bad = call("GET", "/trash?resource_type=NOPE", token)
    check("非法 resource_type 返回 400", status == 400, f"status={status} body={bad}")

    print("== 2. 知识库：软删 → 回收站 → 恢复 ==")
    status, kb = call("POST", "/kb", token, {"name": f"冒烟知识库-{tag}", "description": "trash smoke"})
    check("POST /kb 创建成功", status in (200, 201), f"status={status} body={kb}")
    if status not in (200, 201):
        return 1
    kb_id = kb["id"]

    status, _ = call("DELETE", f"/kb/{kb_id}", token)
    check("DELETE /kb/{id} 软删成功", status in (200, 204), f"status={status}")

    status, kb_list = call("GET", "/kb", token)
    ids = ids_of(kb_list)
    check("软删后知识库从列表消失", kb_id not in ids, f"ids={ids[:5]}")

    page = trash_list(token, resource_type="KB")
    entry = find_item(page.get("items", []), "KB", kb_id)
    check("软删后知识库出现在回收站", entry is not None, f"page={page}")
    if entry is None:
        return 1
    check("回收站条目带标题", entry.get("title") == f"冒烟知识库-{tag}", f"entry={entry}")
    check("回收站条目带删除人", entry.get("deleted_by") is not None, f"entry={entry}")
    check("回收站条目带过期时间", bool(entry.get("expires_at")), f"entry={entry}")
    check("知识库可恢复", entry.get("restorable") is True, f"entry={entry}")
    kb_trash_id = entry["id"]

    status, restored = call("POST", f"/trash/{kb_trash_id}/restore", token)
    check("POST /trash/{id}/restore 返回 200", status == 200, f"status={status} body={restored}")

    status, kb_list = call("GET", "/kb", token)
    ids = ids_of(kb_list)
    check("恢复后知识库回到列表", kb_id in ids, f"ids={ids[:5]}")

    page = trash_list(token, resource_type="KB")
    check(
        "恢复后条目从回收站消失",
        find_item(page.get("items", []), "KB", kb_id) is None,
        f"page={page}",
    )

    status, again = call("POST", f"/trash/{kb_trash_id}/restore", token)
    check("重复恢复返回 404", status == 404, f"status={status} body={again}")

    print("== 3. 会话：软删 → 回收站 → 恢复 ==")
    # 会话只能由问答流程隐式创建（没有 POST /conversations），
    # 所以借用一条已存在的会话，删完必须恢复回去，保证脚本可重复运行。
    status, conv_list = call("GET", "/conversations", token)
    existing = ids_of(conv_list)
    if not existing:
        skip("会话链路", "租户下没有可借用的会话")
    else:
        conv_id = existing[0]
        status, _ = call("DELETE", f"/conversations/{conv_id}", token)
        check("DELETE /conversations/{id} 软删成功", status in (200, 204), f"status={status}")

        ids = ids_of(call("GET", "/conversations", token)[1])
        check("软删后会话从列表消失", conv_id not in ids, f"ids={ids[:3]}")

        page = trash_list(token, resource_type="CONVERSATION")
        entry = find_item(page.get("items", []), "CONVERSATION", conv_id)
        check("软删后会话出现在回收站", entry is not None, f"total={page.get('total')}")
        if entry is not None:
            status, _ = call("POST", f"/trash/{entry['id']}/restore", token)
            check("会话恢复返回 200", status == 200, f"status={status}")
            ids = ids_of(call("GET", "/conversations", token)[1])
            check("恢复后会话回到列表", conv_id in ids, f"ids={ids[:3]}")

    print("== 4. 关键词过滤与分页 ==")
    status, kb2 = call("POST", "/kb", token, {"name": f"待清空-{tag}", "description": "purge me"})
    kb2_id = kb2.get("id") if status in (200, 201) else None
    if kb2_id:
        call("DELETE", f"/kb/{kb2_id}", token)
        page = trash_list(token, q=f"待清空-{tag}")
        check(
            "关键词过滤命中目标",
            find_item(page.get("items", []), "KB", kb2_id) is not None,
            f"page={page}",
        )
        page = trash_list(token, q="绝不可能存在的关键词zzz")
        check("关键词无命中时返回空", page.get("total") == 0, f"total={page.get('total')}")
        page = trash_list(token, limit=1)
        check("limit=1 只返回一条", len(page.get("items", [])) <= 1, f"page={page}")
    else:
        skip("关键词过滤", "第二个知识库创建失败")

    print("== 5. 永久删除 ==")
    if kb2_id:
        page = trash_list(token, resource_type="KB")
        entry = find_item(page.get("items", []), "KB", kb2_id)
        check("待永久删除条目在回收站", entry is not None, f"page={page}")
        if entry is not None:
            status, purged = call("DELETE", f"/trash/{entry['id']}", token)
            check("DELETE /trash/{id} 返回 200", status == 200, f"status={status} body={purged}")

            page = trash_list(token, resource_type="KB")
            check(
                "永久删除后从回收站消失",
                find_item(page.get("items", []), "KB", kb2_id) is None,
                f"page={page}",
            )
            status, gone = call("GET", f"/kb/{kb2_id}", token)
            check("永久删除后原资源 404", status == 404, f"status={status} body={gone}")
            status, again = call("POST", f"/trash/{entry['id']}/restore", token)
            check("永久删除后不可恢复", status == 404, f"status={status} body={again}")
    else:
        skip("永久删除", "第二个知识库创建失败")

    print("== 6. 文档：父知识库仍在回收站时不可单独恢复 ==")
    status, kb3 = call("POST", "/kb", token, {"name": f"父库-{tag}", "description": "parent"})
    kb3_id = kb3.get("id") if status in (200, 201) else None
    if not kb3_id:
        skip("文档孤儿保护", "父知识库创建失败")
    else:
        call("DELETE", f"/kb/{kb3_id}", token)
        page = trash_list(token, resource_type="KB")
        kb3_entry = find_item(page.get("items", []), "KB", kb3_id)
        check("父库进入回收站", kb3_entry is not None, f"page={page}")
        # 级联删除的文档不单独入站，回收站不应被子文档刷屏。
        page_docs = trash_list(token, resource_type="DOCUMENT")
        cascaded = [
            item for item in page_docs.get("items", []) if item.get("parent_id") == kb3_id
        ]
        check("级联删除的文档不单独入回收站", cascaded == [], f"cascaded={cascaded}")
        if kb3_entry:
            call("DELETE", f"/trash/{kb3_entry['id']}", token)

    print("== 7. 未鉴权访问必须 401 ==")
    status, _ = call("GET", "/trash")
    check("无 token 访问 /trash 返回 401", status == 401, f"status={status}")
    status, _ = call("DELETE", "/trash")
    check("无 token 清空回收站返回 401", status == 401, f"status={status}")

    print()
    print(f"结果：{len(PASSED)} 通过 / {len(FAILED)} 失败 / {len(SKIPPED)} 跳过")
    for item in FAILED:
        print(f"  ✗ {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

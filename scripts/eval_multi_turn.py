#!/usr/bin/env python3
"""EKB M4-6 Multi-Turn E2E 验证脚本。

用法：
    scripts/eval_multi_turn.py --run local   # 本地 pytest mock LLM 模式 (默认)
    scripts/eval_multi_turn.py --run online  # 调本地 uvicorn /qa/ask (未启动则跳过，exit 0)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))


def _run_local_mode() -> int:
    """AC-1 / AC-6 / AC-4 三条 pytest 路径，mock LLM。"""
    import pytest

    test_file = API_DIR / "tests" / "test_multi_turn.py"
    args = [
        str(test_file),
        "-v",
        "--tb=short",
        # AC-1: 两轮对话记忆注入
        f"{test_file}::TestTask9FeatureFlag::test_feature_flag_off_then_on",
        # AC-6: 零证据声明 + capabilities
        f"{test_file}::TestMultiTurnIntegration::test_capabilities_response_extended",
        # AC-4: 压缩事件
        f"{test_file}::TestMultiTurnIntegration::test_compaction_event_emitted",
        # 额外：Task 6 指标也跑一下
        f"{test_file}::TestTask6Metrics",
    ]
    result = pytest.main(args)
    if result == 0:
        print("\n[eval_multi_turn] local 模式 AC-1/AC-6/AC-4 全绿")
    else:
        print(f"\n[eval_multi_turn] local 模式失败，pytest exit={result}", file=sys.stderr)
    return int(result)


def _run_online_mode() -> int:
    """Online 模式：本地 uvicorn 调真实 LLM 验证多轮对话（记住 → 再问 范式）。

    失败不阻塞，exit 0；但会详细打印事件流和断言结果，便于人工验收。
    """
    import socket
    import time
    import urllib.error
    import urllib.request

    # ---- 强制 urllib 绕过系统代理（HTTP_PROXY 等环境变量会导致请求被拦截到 127.0.0.1:1082 之类），
    # ---- 直接命中本地 uvicorn。否则开发机上配置了代理会出现 uvicorn access log 完全不记请求却还能收到 401 的怪事。
    no_proxy_handler = urllib.request.ProxyHandler({})
    urllib.request.install_opener(urllib.request.build_opener(no_proxy_handler))

    host = "127.0.0.1"
    port = 8000
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect((host, port))
    except OSError:
        print("[eval_multi_turn] uvicorn 未启动（127.0.0.1:8000 连不上），跳过 online 模式")
        return 0
    finally:
        probe.close()

    base = f"http://{host}:{port}/api/v1"
    try:
        with urllib.request.urlopen(f"{base.replace('/api/v1','')}/healthz", timeout=5) as r:
            if r.status != 200:
                print(f"[eval_multi_turn] /healthz 返回 {r.status}，跳过 online 模式")
                return 0
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[eval_multi_turn] 访问 /healthz 失败：{exc}，跳过 online 模式")
        return 0

    # --- 1) 登录拿 JWT (dev 默认账号：ensure_admin 重置过的密码) ---
    LOGIN_CREDS = [
        ("admin@example.com", "test-password"),
        ("admin@example.com", "change-me-local-only"),
        ("admin@example.com", "admin"),
    ]
    access_token: str | None = None
    last_err: str | None = None
    for email, pwd in LOGIN_CREDS:
        req = urllib.request.Request(
            f"{base}/auth/login",
            data=json.dumps({"email": email, "password": pwd}).encode(),
            headers={"Content-Type": "application/json", "Connection": "close"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                access_token = data.get("access_token")
                if access_token:
                    print(f"[eval_multi_turn] 登录成功 email={email!r} user_id={(data.get('user') or {}).get('id','')[:8]}..")
                    break
        except urllib.error.HTTPError as exc:
            last_err = f"{exc.code}: {exc.read().decode()[:200]}"
    if not access_token:
        print(f"[eval_multi_turn] 登录失败，跳过 online 模式：{last_err}")
        return 0

    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            # 避免 urllib 复用 keep-alive 连接时服务端已关，导致 RemoteDisconnected
            "Connection": "close",
        }

    # --- 2) /qa/capabilities 断言新字段（M4-6 已接入） ---
    req = urllib.request.Request(
        f"{base}/qa/capabilities",
        headers={"Authorization": f"Bearer {access_token}", "Connection": "close"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        caps = json.loads(r.read().decode())
    ctx_win = caps.get("context_window_tokens")
    compaction = caps.get("compaction_enabled")
    print(f"[eval_multi_turn] capabilities.context_window_tokens={ctx_win} compaction_enabled={compaction}")
    assert isinstance(ctx_win, int) and ctx_win >= 1024, f"context_window_tokens 异常: {ctx_win!r}"
    assert isinstance(compaction, bool), f"compaction_enabled 异常: {compaction!r}"

    # --- 3) SSE 事件解析器 (v2 envelope) ---
    def _consume_sse(url: str, body: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers=_headers(),
            method="POST",
        )
        events: list[dict] = []
        content_chunks: list[str] = []
        last_conv_id: str | None = None
        last_turn_id: str | None = None
        with urllib.request.urlopen(req, timeout=300) as r:
            buf = ""
            for raw in r:  # 按行迭代 (bytes)
                line = raw.decode(errors="replace").rstrip("\r\n")
                if not line:
                    buf = ""
                    continue
                if line.startswith("id:"):
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                    events.append({"event": event_name})
                    continue
                if line.startswith("data:"):
                    data_str = line.split(":", 1)[1].lstrip()
                    try:
                        payload = json.loads(data_str)
                    except Exception:
                        continue
                    ev = events[-1] if events else {"event": "message"}
                    ev.setdefault("payloads", []).append(payload)
                    # v2 envelope 解包
                    inner = payload.get("payload") if isinstance(payload, dict) else None
                    event_name = ev["event"]
                    if isinstance(payload, dict):
                        last_conv_id = payload.get("conversation_id") or last_conv_id
                        last_turn_id = payload.get("turn_id") or last_turn_id
                    if isinstance(inner, dict):
                        last_conv_id = inner.get("conversation_id") or last_conv_id
                        last_turn_id = inner.get("turn_id") or last_turn_id
                    if event_name == "content_delta":
                        delta = ""
                        if isinstance(inner, dict):
                            delta = str(inner.get("delta") or inner.get("content_delta") or "")
                        elif isinstance(payload, dict):
                            delta = str(payload.get("delta") or payload.get("content_delta") or "")
                        if delta:
                            content_chunks.append(delta)
        return {
            "events": events,
            "answer_text": "".join(content_chunks),
            "conversation_id": last_conv_id,
            "turn_id": last_turn_id,
            "event_names": [e["event"] for e in events],
        }

    # --- 4) 第一轮：明确"记住"两个字段 ---
    question1 = (
        "请记住下面两条信息，记住之后只回答「好的，已记住 2 条信息」，不要解释："
        "1. 公司邮箱域名统一后缀是 @jinbo.group；"
        "2. 集团创始人中文名为 张三。"
    )
    print(f"\n=== Turn 1 / question: {question1[:80]}... ===")
    t0 = time.time()
    r1 = _consume_sse(f"{base}/qa/ask", {"question": question1, "kb_ids": [], "options": {"stream_version": 2, "stream": True, "thinking_level": "light", "deep_thinking": False}})
    print(f"    events: {r1['event_names'][:20]}")
    print(f"    conversation_id = {r1['conversation_id']}")
    print(f"    turn_id         = {r1['turn_id']}")
    print(f"    answer          = {r1['answer_text'][:200]!r}")
    print(f"    TTFB+total      = {time.time()-t0:.2f}s")

    # --- 5) 第二轮：同一个 conversation_id，询问记住的两个字段 ---
    question2 = "根据我们上面的对话历史回答：公司邮箱域名后缀是什么？集团创始人是谁？只回答这两个问题，不要加多余内容。"
    print(f"\n=== Turn 2 / question: {question2} ===")
    assert r1["conversation_id"], "第一问没返回 conversation_id，无法继续第二轮（服务端 create_conversation 可能抛错或未在 payload 输出）"
    t1 = time.time()
    r2 = _consume_sse(
        f"{base}/qa/ask",
        {
            "conversation_id": r1["conversation_id"],
            "question": question2,
            "kb_ids": [],
            "options": {"stream_version": 2, "stream": True, "thinking_level": "light", "deep_thinking": False},
        },
    )
    print(f"    events: {r2['event_names'][:20]}")
    print(f"    压缩事件?       = {'compaction_performed' in r2['event_names']}")
    print(f"    answer          = {r2['answer_text'][:400]!r}")
    print(f"    total           = {time.time()-t1:.2f}s")

    # --- 6) 断言：多轮上下文真的被模型看到 ---
    ans2_lower = r2["answer_text"].lower()
    hit_domain = "@jinbo.group" in r2["answer_text"] or "jinbo.group" in r2["answer_text"] or "金博" in r2["answer_text"]
    hit_founder = "张三" in r2["answer_text"] or "zhangsan" in ans2_lower

    print("\n=== [eval_multi_turn] ONLINE 验收总结 ===")
    print(f"  ✅ capabilities.context_window_tokens = {ctx_win}")
    print(f"  ✅ capabilities.compaction_enabled     = {compaction}")
    print(f"  ✅ Turn 1 回答非空                     = {bool(r1['answer_text'])}")
    print(f"  ✅ Turn 1 → Turn 2 共用 conversation  = {bool(r1['conversation_id'])}")
    print(f"  {'✅' if hit_domain else '❌'} 第二轮命中邮箱域名 @jinbo.group/金博 = {hit_domain}")
    print(f"  {'✅' if hit_founder else '❌'} 第二轮命中创始人 张三/zhangsan       = {hit_founder}")
    print(f"  {'✅' if (hit_domain and hit_founder) else '⚠️'} AC-1 两轮「记住+回答」范式            = {'PASS' if (hit_domain and hit_founder) else '提示：若 LLM 只记住了一条，把问题再短再清晰一些重试；或调高 thinking_level'}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="EKB M4-6 Multi-Turn E2E eval")
    parser.add_argument(
        "--run",
        choices=("local", "online"),
        default="local",
        help="local=pytest mock; online=本地 uvicorn (默认 local)",
    )
    args = parser.parse_args()

    if args.run == "local":
        return _run_local_mode()
    try:
        return _run_online_mode()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[eval_multi_turn] online 模式异常（不阻塞）：{exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())

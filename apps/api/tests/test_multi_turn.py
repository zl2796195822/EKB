from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("EKB_ENV", "test")
os.environ.setdefault("EKB_DEV_USER_EMAIL", "admin@example.com")
os.environ.setdefault("EKB_DEV_PASSWORD", "test-password")
os.environ.setdefault("EKB_TOKEN_SECRET", "test-only-token-secret")

import tempfile
from copy import deepcopy

_TEST_DB = os.path.join(tempfile.gettempdir(), "ekb_multi_turn_test.db")
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)
os.environ["EKB_DATABASE_URL"] = f"sqlite:///{_TEST_DB}"

import dataclasses
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ekb_api import llm as llm_module
from ekb_api.core import config as cfg_mod
from ekb_api.core.config import get_settings
from ekb_api.domain import Message, MessageVisibility
from ekb_api.main import app

client = TestClient(app)


def _login() -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    buf: dict[str, Any] = {}
    for line in text.splitlines():
        if not line.strip():
            if buf:
                events.append(dict(buf))
                buf = {}
            continue
        if line.startswith("id: "):
            buf["id"] = line[4:]
        elif line.startswith("event: "):
            buf["event"] = line[7:]
        elif line.startswith("data: "):
            raw = line[6:]
            try:
                buf["data"] = json.loads(raw)
            except Exception:
                buf["data_raw"] = raw
    if buf:
        events.append(buf)
    return events


def _stream_gen(text: str):
    for ch in text:
        yield ch


def _settings_patch(monkeypatch, **overrides: Any) -> None:
    """patch get_settings 返回在原 settings 上覆盖字段的新 frozen dataclass 实例。

    同时把 settings.llm_enabled / embedding_enabled 这两个 @property 强制返回 True，
    避免因为没有配置真实 provider 而走 demo fallback 分支。
    """
    from ekb_api.core.config import Settings

    orig = get_settings()
    valid_fields = {f.name for f in dataclasses.fields(orig)}
    clean = {k: v for k, v in overrides.items() if k in valid_fields}
    data = {f.name: getattr(orig, f.name) for f in dataclasses.fields(orig)}
    data.update(clean)
    new_settings = type(orig)(**data)

    def _patched():
        return new_settings

    monkeypatch.setattr(cfg_mod, "get_settings", _patched)
    try:
        from ekb_api.routers import qa as qa_ns
        monkeypatch.setattr(qa_ns, "get_settings", _patched)
    except Exception:
        pass
    # 强制把 Settings.llm_enabled / embedding_enabled 两个 property patch 成 True
    monkeypatch.setattr(Settings, "llm_enabled", property(lambda self: True))
    monkeypatch.setattr(Settings, "embedding_enabled", property(lambda self: True))


def _patch_store_conv(monkeypatch) -> None:
    """让 conversation_id 存在时不抛 404，同时 save_message 等操作也不报错。"""
    from ekb_api.store import SqlStore
    from ekb_api.domain import Conversation

    def _fake_get_conv(self, auth, conversation_id):
        return Conversation(
            id=conversation_id or "conv_dummy",
            tenant_id=auth.tenant_id,
            user_id=auth.actor_id,
            title="dummy",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

    def _fake_create_conv(self, auth, title):
        return _fake_get_conv(self, auth, None)

    def _fake_save_msg(self, auth, conversation_id, role, content, **kw):
        return Message(
            id=f"msg_fake_{role}",
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at="2026-01-01T00:00:00Z",
            turn_id=kw.get("turn_id"),
            visibility_state=kw.get("visibility_state", "visible"),
        )

    def _fake_update_msg_content(self, auth, msg_id, content):
        return None

    def _fake_update_msg_visibility(self, auth, msg_id, vis):
        return True

    def _fake_get_msg(self, auth, msg_id):
        return Message(
            id=msg_id, tenant_id="t", conversation_id="c",
            role="ASSISTANT", content="dummy", created_at="2026-01-01T00:00:00Z",
            turn_id=None, visibility_state="visible",
        )

    monkeypatch.setattr(SqlStore, "get_conversation", _fake_get_conv)
    monkeypatch.setattr(SqlStore, "create_conversation", _fake_create_conv)
    monkeypatch.setattr(SqlStore, "save_message", _fake_save_msg)
    monkeypatch.setattr(SqlStore, "create_turn", lambda self, *a, **kw: None)
    monkeypatch.setattr(SqlStore, "increment_turn_seq", lambda self, *a, **kw: None)
    monkeypatch.setattr(SqlStore, "mark_turn_first_visible", lambda self, *a, **kw: None)
    monkeypatch.setattr(SqlStore, "complete_turn", lambda self, *a, **kw: None)
    monkeypatch.setattr(SqlStore, "update_message_content", _fake_update_msg_content)
    monkeypatch.setattr(SqlStore, "update_message_visibility", _fake_update_msg_visibility)
    monkeypatch.setattr(SqlStore, "get_message", _fake_get_msg)
    monkeypatch.setattr(SqlStore, "check_and_increment_qa_quota", lambda self, tid: True)
    monkeypatch.setattr(SqlStore, "write_audit_log", lambda self, **kw: None)
    monkeypatch.setattr(SqlStore, "create_review_item", lambda self, auth, **kw: None)
    monkeypatch.setattr(SqlStore, "is_turn_cancelled", lambda self, tid: False)


def _make_history_messages(
    count: int = 4,
    *,
    extra_hidden: bool = False,
    extra_current_turn: str | None = None,
) -> list[Message]:
    msgs: list[Message] = []
    idx = 0
    for i in range(count // 2):
        idx += 1
        msgs.append(Message(
            id=f"m{idx}", tenant_id="t1", conversation_id="c1",
            role="USER", content=f"历史问题{i+1}",
            created_at=f"2026-01-01T00:0{i+1}:00Z",
            turn_id=f"turn_old_{i+1}", visibility_state=MessageVisibility.VISIBLE.value,
        ))
        idx += 1
        msgs.append(Message(
            id=f"m{idx}", tenant_id="t1", conversation_id="c1",
            role="ASSISTANT", content=f"历史回答{i+1}",
            created_at=f"2026-01-01T00:0{i+5}:00Z",
            turn_id=f"turn_old_{i+1}", visibility_state=MessageVisibility.VISIBLE.value,
        ))
    if extra_hidden:
        idx += 1
        msgs.append(Message(
            id=f"m{idx}", tenant_id="t1", conversation_id="c1",
            role="ASSISTANT", content="(被隐藏的占位消息)",
            created_at="2026-01-01T00:09:00Z",
            turn_id="turn_hidden", visibility_state=MessageVisibility.HIDDEN.value,
        ))
    if extra_current_turn:
        idx += 1
        msgs.append(Message(
            id=f"m{idx}", tenant_id="t1", conversation_id="c1",
            role="USER", content="本轮-用户刚写入",
            created_at="2026-02-01T00:00:01Z",
            turn_id=extra_current_turn, visibility_state=MessageVisibility.VISIBLE.value,
        ))
        idx += 1
        msgs.append(Message(
            id=f"m{idx}", tenant_id="t1", conversation_id="c1",
            role="ASSISTANT", content="本轮-ASSISTANT 占位",
            created_at="2026-02-01T00:00:02Z",
            turn_id=extra_current_turn, visibility_state=MessageVisibility.VISIBLE.value,
        ))
    return msgs


class TestMultiTurnIntegration:
    """Task 5: 6 个多轮对话集成测试（mock 所有外部依赖）。"""

    # ---------- 测试 1: list_messages 加载 + HIDDEN 过滤 + 本轮剔除 ----------

    def test_list_messages_loaded_and_filtered(self, monkeypatch) -> None:
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore

        _patch_store_conv(monkeypatch)
        _settings_patch(monkeypatch, multi_turn_enabled=True)

        token = _login()
        capture: dict[str, Any] = {}
        expected_history_count = 4
        test_self = self

        def _list_msgs(store_self, auth, conversation_id):
            return _make_history_messages(
                count=4, extra_hidden=True, extra_current_turn="turn_TEST_CURRENT_TURN"
            )

        def _gen_stream(question, evidence_texts, **kwargs):
            capture["history"] = kwargs.get("history_messages")
            yield from _stream_gen("好的")

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", _gen_stream)
        monkeypatch.setattr(qa_ns, "new_id", lambda: "TEST_CURRENT_TURN")  # qa.py turn_id = "turn_TEST_CURRENT_TURN"

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "q",
                "kb_ids": [],
                "conversation_id": "c1",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200, resp.text
        hist = capture.get("history")
        assert hist is not None, "multi_turn=True 且有 conversation_id，history_messages 不应为 None"
        assert len(hist) == expected_history_count, (
            f"期望 {expected_history_count} 条可见历史，实际 {len(hist)}: {hist}"
        )
        roles = [m["role"] for m in hist]
        assert roles.count("user") == 2 and roles.count("assistant") == 2, (
            f"2 USER + 2 ASSISTANT，实际 roles={roles}"
        )

    # ---------- 测试 2: 当前 turn 消息被剔除 ----------

    def test_current_turn_messages_excluded(self, monkeypatch) -> None:
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore

        _patch_store_conv(monkeypatch)
        _settings_patch(monkeypatch, multi_turn_enabled=True)

        token = _login()
        capture: dict[str, Any] = {}
        current_content_u = "本轮用户问题-别出现在历史里"
        current_content_a = "本轮ASSISTANT占位-也别出现"
        test_self = self

        def _list_msgs(store_self, auth, conversation_id):
            msgs = _make_history_messages(count=2)
            msgs.append(Message(
                id="mc_u", tenant_id="t1", conversation_id="c1",
                role="USER", content=current_content_u,
                created_at="2026-03-01T00:00:01Z",
                turn_id="turn_TEST_TURN_2", visibility_state="visible",
            ))
            msgs.append(Message(
                id="mc_a", tenant_id="t1", conversation_id="c1",
                role="ASSISTANT", content=current_content_a,
                created_at="2026-03-01T00:00:02Z",
                turn_id="turn_TEST_TURN_2", visibility_state="visible",
            ))
            return msgs

        def _gen_stream(question, evidence_texts, **kwargs):
            capture["history"] = kwargs.get("history_messages")
            yield from _stream_gen("ok")

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", _gen_stream)
        monkeypatch.setattr(qa_ns, "new_id", lambda: "TEST_TURN_2")

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "new_q",
                "kb_ids": [],
                "conversation_id": "c1",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200
        hist = capture.get("history") or []
        concat = " ".join(m["content"] for m in hist)
        assert current_content_u not in concat, "本轮 USER 消息不应出现在 history 里"
        assert current_content_a not in concat, "本轮 ASSISTANT 占位消息不应出现在 history 里"
        assert "历史问题1" in concat or len(hist) >= 1

    # ---------- 测试 3: compaction_performed SSE 事件 ----------

    def test_compaction_event_emitted(self, monkeypatch) -> None:
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore

        _patch_store_conv(monkeypatch)
        _settings_patch(
            monkeypatch,
            multi_turn_enabled=True,
            llm_context_window=80,
            llm_compaction_ratio=0.5,
        )

        token = _login()
        test_self = self

        def _list_msgs(store_self, auth, conversation_id):
            return _make_history_messages(count=8)

        def _patched_mch(*args, **kwargs):
            hist_in = kwargs.get("history_messages") or args[2]
            report = llm_module.CompactionReport(
                rounds_compressed=2,
                tokens_before=500,
                tokens_after=150,
                method="llm_summary",
                detail="test summary",
            )
            return hist_in, report

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs)
        monkeypatch.setattr(qa_ns, "maybe_compact_history", _patched_mch)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", lambda q, e, **kw: _stream_gen("done"))

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "压缩测试问题",
                "kb_ids": [],
                "conversation_id": "c_compact",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200, resp.text
        events = _parse_sse(resp.text)
        compact_events = [e for e in events if e.get("event") == "compaction_performed"]
        assert compact_events, (
            "SSE 流中应包含 compaction_performed 事件；所有事件: "
            + str([e.get("event") for e in events])
        )
        data = compact_events[0].get("data") or {}
        if isinstance(data, dict):
            payload = data.get("payload") if "payload" in data else data
            assert payload.get("rounds_compressed") == 2
            assert payload.get("method") == "llm_summary"

    # ---------- 测试 4: feature flag 关闭时 history_messages=None ----------

    def test_feature_flag_off_no_history(self, monkeypatch) -> None:
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore

        _patch_store_conv(monkeypatch)
        _settings_patch(monkeypatch, multi_turn_enabled=False)

        token = _login()
        capture: dict[str, Any] = {}

        def _gen_stream(question, evidence_texts, **kwargs):
            capture["history"] = kwargs.get("history_messages")
            yield from _stream_gen("X")

        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", _gen_stream)

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "q",
                "kb_ids": [],
                "conversation_id": "c_flag",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200
        assert capture.get("history") is None, (
            f"multi_turn_enabled=False 时 history_messages 应为 None，实际: {capture.get('history')!r}"
        )

    # ---------- 测试 5: capabilities 扩展字段 ----------

    def test_capabilities_response_extended(self) -> None:
        token = _login()
        resp = client.get(
            "/api/v1/qa/capabilities",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "context_window_tokens" in body, "capabilities 响应缺少 context_window_tokens 字段"
        assert "compaction_enabled" in body, "capabilities 响应缺少 compaction_enabled 字段"
        assert isinstance(body["context_window_tokens"], int)
        assert isinstance(body["compaction_enabled"], bool)
        settings = get_settings()
        assert body["context_window_tokens"] == settings.llm_context_window
        assert body["compaction_enabled"] == settings.multi_turn_enabled

    # ---------- 测试 6: 拒答二次确认透传 history_messages ----------

    def test_rejection_confirmation_passes_history(self, monkeypatch) -> None:
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore

        _patch_store_conv(monkeypatch)
        _settings_patch(monkeypatch, multi_turn_enabled=True)

        token = _login()
        capture: dict[str, Any] = {"stream_hist": None, "recheck_hist": None}
        test_self = self

        def _list_msgs(store_self, auth, conversation_id):
            return _make_history_messages(count=4)

        fake_chunks = [object() for _ in range(5)]
        fake_evidences = [f"证据{i}" for i in range(4)]

        def _retrieve(*a, **kw):
            return fake_chunks

        def _stream_returns_refusal(question, evidence_texts, **kwargs):
            capture["stream_hist"] = kwargs.get("history_messages")
            yield from _stream_gen("证据不足，无法确认（还需更多信息）")

        def _recheck_generate_answer(question, evidence_texts, **kwargs):
            capture["recheck_hist"] = kwargs.get("history_messages")
            return "补充确认后，答案是：OK"

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs)
        monkeypatch.setattr(qa_ns, "retrieve", _retrieve)
        monkeypatch.setattr(qa_ns, "_build_kb_evidence", lambda *a, **kw: fake_evidences)
        monkeypatch.setattr(qa_ns, "generate_answer_stream", _stream_returns_refusal)
        monkeypatch.setattr(qa_ns, "generate_answer", _recheck_generate_answer)

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "拒答触发问题",
                "kb_ids": [],
                "conversation_id": "c_rej",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200, resp.text
        stream_hist = capture.get("stream_hist")
        recheck_hist = capture.get("recheck_hist")
        assert stream_hist is not None, "第一轮流式 history_messages 不应为 None"
        assert recheck_hist is not None, (
            "拒答二次确认 generate_answer 没收到 history_messages；"
            f"capture={capture}; events={[e.get('event') for e in _parse_sse(resp.text)]}"
        )
        assert stream_hist == recheck_hist, (
            f"二次确认应透传同一 history：stream={stream_hist!r} recheck={recheck_hist!r}"
        )


class TestTask6Metrics:
    """Task 6: Prometheus 指标 + 日志增强 2 条测试。"""

    def test_metrics_present_after_compaction(self, monkeypatch) -> None:
        """TR-6.1: 触发一次压缩后，QA_COMPACTION_TRIGGERED Counter 增加。"""
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore
        from ekb_api.core import metrics as metrics_mod

        _patch_store_conv(monkeypatch)
        _settings_patch(
            monkeypatch,
            multi_turn_enabled=True,
            llm_context_window=80,
            llm_compaction_ratio=0.5,
        )
        counter_before = dict(metrics_mod.QA_COMPACTION_TRIGGERED._values)
        hist_data_before = {
            k: dict(v) for k, v in metrics_mod.QA_COMPACTION_DURATION_SECONDS._data.items()
        }

        token = _login()

        def _list_msgs(store_self, auth, conversation_id):
            return _make_history_messages(count=10)

        def _patched_mch(*args, **kwargs):
            hist_in = kwargs.get("history_messages") or args[2]
            report = llm_module.CompactionReport(
                rounds_compressed=3,
                tokens_before=600,
                tokens_after=180,
                method="llm_summary",
                detail="test",
            )
            return hist_in, report

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs)
        monkeypatch.setattr(qa_ns, "maybe_compact_history", _patched_mch)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", lambda q, e, **kw: _stream_gen("ok"))

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "压缩触发测试",
                "kb_ids": [],
                "conversation_id": "c_metric_1",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200, resp.text

        key = ("token_threshold", "llm_summary")
        val_after = metrics_mod.QA_COMPACTION_TRIGGERED._values.get(key, 0.0)
        val_before = counter_before.get(key, 0.0)
        assert val_after >= val_before + 1.0, (
            f"QA_COMPACTION_TRIGGERED {key} 未增加：before={val_before} after={val_after}"
        )
        hist_after = metrics_mod.QA_COMPACTION_DURATION_SECONDS._data
        all_keys = set(list(hist_after.keys()) + list(hist_data_before.keys()))
        observed = any(
            hist_after.get(k, {}).get("count", 0) > hist_data_before.get(k, {}).get("count", 0)
            for k in all_keys
        ) or len(hist_after) > len(hist_data_before)
        assert observed, "QA_COMPACTION_DURATION_SECONDS 未记录到任何 observe"

    def test_gauge_updated_on_generation(self, monkeypatch) -> None:
        """TR-6.2: 跑任意一轮对话，QA_HISTORY_TOKENS Gauge.set 至少被调用 1 次。"""
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore
        from ekb_api.core import metrics as metrics_mod

        _patch_store_conv(monkeypatch)
        _settings_patch(monkeypatch, multi_turn_enabled=True)
        metrics_mod.registry.reset()

        token = _login()
        test_self = self
        gauge_calls: list[tuple] = []

        orig_set = metrics_mod.QA_HISTORY_TOKENS.set

        def _spy_set(value, **labels):
            gauge_calls.append((value, dict(labels)))
            return orig_set(value, **labels)

        monkeypatch.setattr(metrics_mod.QA_HISTORY_TOKENS, "set", _spy_set)

        def _list_msgs(store_self, auth, conversation_id):
            return _make_history_messages(count=4)

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", lambda q, e, **kw: _stream_gen("gauge ok"))

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "gauge测试问题",
                "kb_ids": [],
                "conversation_id": "c_gauge_1",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200
        assert len(gauge_calls) >= 1, f"Gauge.set 调用次数应为 >= 1，实际 {len(gauge_calls)}: {gauge_calls}"
        first_call_labels = gauge_calls[0][1]
        assert "tenant_id" in first_call_labels
        assert "conversation_id" in first_call_labels


class TestTask9FeatureFlag:
    """Task 9.C: 灰度开关 off→on 验证。"""

    def test_feature_flag_off_then_on(self, monkeypatch) -> None:
        from ekb_api.routers import qa as qa_ns
        from ekb_api.store import SqlStore

        _patch_store_conv(monkeypatch)
        token = _login()
        test_self = self

        def _list_msgs_with_memory(store_self, auth, conversation_id):
            msgs = _make_history_messages(count=2)
            msgs[0] = Message(
                id="m_mem_u", tenant_id="t1", conversation_id="c_flag",
                role="USER", content="请记住 A=金博集团",
                created_at="2026-01-01T00:01:00Z",
                turn_id="turn_old_mem", visibility_state=MessageVisibility.VISIBLE.value,
            )
            msgs[1] = Message(
                id="m_mem_a", tenant_id="t1", conversation_id="c_flag",
                role="ASSISTANT", content="好的，已记住 A=金博集团",
                created_at="2026-01-01T00:02:00Z",
                turn_id="turn_old_mem", visibility_state=MessageVisibility.VISIBLE.value,
            )
            return msgs

        # --- Step 1: multi_turn_enabled=False，history_messages 应为 None ---
        _settings_patch(monkeypatch, multi_turn_enabled=False)
        captured_step1: dict[str, Any] = {}

        def _gen_stream_capture_1(question, evidence_texts, **kwargs):
            captured_step1["history"] = kwargs.get("history_messages")
            captured_step1["messages_input"] = None
            yield from _stream_gen("A 是什么？单轮模式无法知道")

        monkeypatch.setattr(SqlStore, "list_messages", _list_msgs_with_memory)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", _gen_stream_capture_1)

        resp1 = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "A 是什么？",
                "kb_ids": [],
                "conversation_id": "c_flag",
                "options": {"stream_version": 2},
            },
        )
        assert resp1.status_code == 200
        assert captured_step1.get("history") is None, (
            "Step1 关开关时 history_messages 必须是 None（单轮模式）"
        )

        # --- Step 2: multi_turn_enabled=True，history_messages 包含"金博集团" ---
        _settings_patch(monkeypatch, multi_turn_enabled=True)
        captured_step2: dict[str, Any] = {}

        def _gen_stream_capture_2(question, evidence_texts, **kwargs):
            captured_step2["history"] = kwargs.get("history_messages")
            yield from _stream_gen("A=金博集团（多轮模式正确读到历史）")

        monkeypatch.setattr(qa_ns, "generate_answer_stream", _gen_stream_capture_2)

        resp2 = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "A 是什么？",
                "kb_ids": [],
                "conversation_id": "c_flag",
                "options": {"stream_version": 2},
            },
        )
        assert resp2.status_code == 200
        hist2 = captured_step2.get("history")
        assert hist2 is not None, "Step2 开开关时 history_messages 不应为 None"
        hist_concat = " ".join(m["content"] for m in hist2)
        assert "金博集团" in hist_concat, (
            f"Step2 多轮历史应包含「金博集团」，实际 history: {hist2}"
        )


class TestTask9CancelFlow:
    """Task 9.D: Cancel 流程回归（SSE v2 /qa/turns/{turn_id}/cancel）。"""

    def test_cancel_flow(self, client: TestClient) -> None:
        """发流式请求拿到 turn_id → POST /qa/turns/{turn_id}/cancel → status=cancelled。"""
        sys.path.insert(0, str(Path(__file__).parent))
        try:
            from test_api import login as _api_login
        except Exception:  # noqa: BLE001
            pytest.skip("test_api.login 不可导入，跳过 Cancel 流程端到端测试")
            return
        token = _api_login()

        # 1. 先 mock store 的 cancel 查询返回 True，cancel 端点返回 accepted=True
        from ekb_api.store import SqlStore
        cancelled_turns: set[str] = set()

        def _request_cancel(store_self, auth, turn_id):
            cancelled_turns.add(turn_id)
            return True, "cancelled"

        def _get_turn(store_self, auth, turn_id):
            # 只要返回非 None，端点就认为存在（不检查具体字段）
            return {"turn_id": turn_id, "tenant_id": auth.tenant_id}

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(SqlStore, "request_cancel_turn", _request_cancel)
        monkeypatch.setattr(SqlStore, "get_turn", _get_turn)

        # 2. 先跑一次流式 /qa/ask，从 request 事件里取 turn_id
        captured_turn_id: str | None = None

        def _gen_stream(question, evidence_texts, **kwargs):
            yield AnswerDelta(kind="content_delta", delta="Hello ", text="Hello ")
            yield AnswerDelta(kind="content_delta", delta="world", text="Hello world")
            yield AnswerDelta(kind="done", finish_reason="stop", confidence="high")

        from ekb_api.routers import qa as qa_ns
        _patch_store_conv(monkeypatch)
        monkeypatch.setattr(qa_ns, "retrieve", lambda *a, **kw: [])
        monkeypatch.setattr(qa_ns, "generate_answer_stream", _gen_stream)

        resp = client.post(
            "/api/v1/qa/ask",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={
                "question": "取消测试",
                "kb_ids": [],
                "conversation_id": "c_cancel_1",
                "options": {"stream_version": 2},
            },
        )
        assert resp.status_code == 200
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    obj = json.loads(line[len("data:"):])
                    if isinstance(obj, dict) and obj.get("turn_id"):
                        captured_turn_id = obj["turn_id"]
                        break
                except Exception:
                    continue
        assert captured_turn_id, "流式响应必须至少 emit 一个带 turn_id 的 data 行"

        # 3. 调 cancel 端点
        cancel_resp = client.post(
            f"/api/v1/qa/turns/{captured_turn_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert cancel_resp.status_code == 200, (
            f"取消端点 status_code={cancel_resp.status_code} body={cancel_resp.text}"
        )
        cancel_body = cancel_resp.json()
        assert cancel_body.get("turn_id") == captured_turn_id
        assert cancel_body.get("accepted") is True
        assert cancel_body.get("status") == "cancelled"
        assert captured_turn_id in cancelled_turns

        monkeypatch.undo()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])

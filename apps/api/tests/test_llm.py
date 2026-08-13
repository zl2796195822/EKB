from __future__ import annotations

import pytest

from ekb_api.core.config import get_settings, Settings
from ekb_api.llm import (
    _deep_hint_for,
    _map_role_ekb_to_llm,
    build_messages_for_generation,
    build_system_prompt_template,
    estimate_messages_tokens,
    estimate_text_tokens,
)


class TestEstimateTextTokens:
    def test_estimate_text_tokens_chinese_short(self):
        result = estimate_text_tokens("你好，世界")
        assert 2 <= result <= 10

    def test_estimate_text_tokens_chinese_long(self):
        long_cn = "中" * 1000
        result = estimate_text_tokens(long_cn)
        expected = 1000 / 3 + 2
        lower_bound = expected * 0.8
        upper_bound = expected * 1.2
        assert lower_bound <= result <= upper_bound, (
            f"expected ~{expected:.1f} (±20%), got {result}"
        )

    def test_estimate_text_tokens_english(self):
        text = "Hello world. This is a paragraph about tokenization testing."
        result = estimate_text_tokens(text)
        n_chars = len(text)
        expected = n_chars / 4 + 2
        lower_bound = expected * 0.8
        upper_bound = expected * 1.2
        assert lower_bound <= result <= upper_bound, (
            f"expected ~{expected:.1f} (±20%), got {result}"
        )

    def test_estimate_text_tokens_mixed_code(self):
        text = "if '中文' in x: return 42"
        result = estimate_text_tokens(text)
        assert result >= 4

    def test_estimate_text_tokens_empty(self):
        assert estimate_text_tokens("") == 2


class TestEstimateMessagesTokens:
    def test_estimate_messages_tokens_with_roles(self):
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "system", "content": "C"},
        ]
        total = estimate_messages_tokens(messages)
        sum_contents = (
            estimate_text_tokens("A")
            + estimate_text_tokens("B")
            + estimate_text_tokens("C")
        )
        assert total > sum_contents, (
            f"messages total {total} should exceed pure content sum {sum_contents} "
            "(each message adds +4 role overhead)"
        )


class TestSettingsDefaults:
    def test_settings_defaults(self):
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.multi_turn_enabled is True
        assert settings.llm_context_window == 16384
        assert settings.llm_compaction_ratio == 0.75
        assert settings.compaction_recent_rounds_keep == 6
        assert settings.compaction_summary_ratio_target == 0.6


class TestSettingsEnvOverride:
    def test_settings_env_overrides(self, monkeypatch):
        monkeypatch.setenv("EKB_MULTI_TURN_ENABLED", "false")
        monkeypatch.setenv("EKB_LLM_CONTEXT_WINDOW", "128000")
        monkeypatch.setenv("EKB_LLM_COMPACTION_RATIO", "0.9")
        monkeypatch.setenv("EKB_COMPACTION_RECENT_ROUNDS_KEEP", "10")
        monkeypatch.setenv("EKB_COMPACTION_SUMMARY_RATIO_TARGET", "0.5")
        # 清除 lru_cache，让 get_settings 重新读取 env
        get_settings.cache_clear()
        s = get_settings()
        assert s.multi_turn_enabled is False
        assert s.llm_context_window == 128000
        assert s.llm_compaction_ratio == 0.9
        assert s.compaction_recent_rounds_keep == 10
        assert s.compaction_summary_ratio_target == 0.5


class TestBuildSystemPrompt:
    def test_has_evidence_branch(self):
        result = build_system_prompt_template(
            has_evidence=True,
            thinking_level="medium",
            deep_hint=_deep_hint_for("medium"),
        )
        assert "必须严格基于下面 Context 中的企业知识库证据信息" in result
        assert "证据不足，无法确认" in result
        assert "交叉验证" in result

    def test_no_evidence_branch(self):
        result = build_system_prompt_template(
            has_evidence=False,
            thinking_level="light",
            deep_hint=_deep_hint_for("light"),
        )
        assert "以下回答未参考企业知识库，可能与企业内部规定不一致，仅作通用参考" in result
        assert "交叉验证" not in result

    def test_tenant_name_substitution(self):
        result = build_system_prompt_template(
            has_evidence=True,
            thinking_level="light",
            deep_hint="",
            tenant_name="金博集团",
        )
        assert "服务于 金博集团 内部用户" in result


class TestBuildMessagesForGeneration:
    def test_structure_with_history_and_evidence(self):
        messages = build_messages_for_generation(
            system_prompt="SP",
            history_messages=[
                {"role": "USER", "content": "q1"},
                {"role": "ASSISTANT", "content": "a1"},
                {"role": "USER", "content": "q2"},
                {"role": "ASSISTANT", "content": "a2"},
            ],
            evidence_texts=["e1", "e2"],
            current_question="q3",
        )
        assert len(messages) == 7
        assert messages[0] == {"role": "system", "content": "SP"}
        assert messages[1] == {"role": "user", "content": "q1"}
        assert messages[2] == {"role": "assistant", "content": "a1"}
        assert messages[3] == {"role": "user", "content": "q2"}
        assert messages[4] == {"role": "assistant", "content": "a2"}
        assert messages[5]["role"] == "system"
        assert "[证据1]" in messages[5]["content"]
        assert "[证据2]" in messages[5]["content"]
        assert messages[6] == {"role": "user", "content": "q3"}

    def test_structure_no_history_no_evidence(self):
        messages = build_messages_for_generation(
            system_prompt="SP",
            history_messages=[],
            evidence_texts=[],
            current_question="hi",
        )
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "SP"}
        assert messages[1] == {"role": "user", "content": "hi"}

    def test_role_mapping_strict(self):
        with pytest.raises(ValueError, match="Unknown message role"):
            build_messages_for_generation(
                system_prompt="SP",
                history_messages=[{"role": "GUEST", "content": "x"}],
                evidence_texts=[],
                current_question="q",
            )

    def test_lowercase_role_passthrough(self):
        messages = build_messages_for_generation(
            system_prompt="SP",
            history_messages=[
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"},
            ],
            evidence_texts=[],
            current_question="q",
        )
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"


from unittest.mock import patch, MagicMock
from ekb_api.llm import (
    CompactionReport,
    generate_answer,
    generate_answer_stream,
    maybe_compact_history,
    rewrite_query,
)


class TestGenerateAnswerHistory:
    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_history_none_is_legacy(self, mock_chat, mock_providers):
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "OK"
        generate_answer("q", ["e1", "e2"], history_messages=None)
        assert mock_chat.call_count == 1
        messages = mock_chat.call_args[0][0]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "[证据1]\n" in messages[0]["content"]
        assert "[证据2]\n" in messages[0]["content"]

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_history_nonempty_builds_correct(self, mock_chat, mock_providers):
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "OK"
        history = [{"role": "USER", "content": "q1"}, {"role": "ASSISTANT", "content": "a1"}]
        generate_answer("qnow", ["e1"], history_messages=history)
        messages = mock_chat.call_args[0][0]
        assert len(messages) == 5
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "q1"}
        assert messages[2] == {"role": "assistant", "content": "a1"}
        assert messages[3]["role"] == "system"
        assert "[证据1]" in messages[3]["content"]
        assert messages[-1] == {"role": "user", "content": "qnow"}

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_no_evidence_multi_turn_declaration_prefix_in_system_prompt(self, mock_chat, mock_providers):
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "OK"
        history = [{"role": "USER", "content": "q1"}, {"role": "ASSISTANT", "content": "a1"}]
        generate_answer("qnow", [], history_messages=history)
        messages = mock_chat.call_args[0][0]
        assert messages[0]["role"] == "system"
        assert "以下回答未参考企业知识库，可能与企业内部规定不一致，仅作通用参考" in messages[0]["content"]


class TestGenerateAnswerStreamHistory:
    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat_stream")
    def test_stream_history_none_is_legacy(self, mock_chat_stream, mock_providers):
        mock_providers.return_value = [MagicMock()]
        mock_chat_stream.return_value = iter(["h", "i"])
        result = list(generate_answer_stream("q", ["e"], history_messages=None))
        assert result == ["h", "i"]
        messages = mock_chat_stream.call_args[0][0]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "[证据1]" in messages[0]["content"]

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat_stream")
    def test_stream_history_nonempty_build_equivalent(self, mock_chat_stream, mock_providers):
        mock_providers.return_value = [MagicMock()]
        mock_chat_stream.return_value = iter(["h", "i"])
        history = [{"role": "USER", "content": "q1"}, {"role": "ASSISTANT", "content": "a1"}]
        list(generate_answer_stream("qnow", ["e1"], history_messages=history))
        messages = mock_chat_stream.call_args[0][0]
        assert len(messages) == 5
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "q1"}
        assert messages[2] == {"role": "assistant", "content": "a1"}
        assert messages[3]["role"] == "system"
        assert "[证据1]" in messages[3]["content"]
        assert messages[-1] == {"role": "user", "content": "qnow"}


class TestRewriteQueryUnchanged:
    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_rewrite_query_behavior_stable(self, mock_chat, mock_providers):
        from ekb_api.core.cache import query_rewrite_cache
        query_rewrite_cache.clear()
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = '["重启","运维"]'
        r1 = rewrite_query("如何重启服务器？")
        r2 = rewrite_query("如何重启服务器？")
        r3 = rewrite_query("如何重启服务器？")
        assert r1 == r2 == r3
        assert "如何重启服务器？" in r1
        assert "重启" in r1
        assert "运维" in r1


class TestCompaction:
    def test_no_compaction_under_threshold(self):
        get_settings.cache_clear()
        settings = get_settings()
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！请问有什么可以帮您？"},
        ]
        new_history, report = maybe_compact_history(
            thinking_level="light",
            deep_thinking=False,
            history_messages=history,
            evidence_texts=[],
            current_question="测试问题",
            settings=settings,
        )
        assert report is None
        assert new_history is history or new_history == history

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_compaction_triggered_llm_summary(self, mock_chat, mock_providers, monkeypatch):
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "用户提了A=1,B=2，偏好X"

        get_settings.cache_clear()
        monkeypatch.setenv("EKB_LLM_CONTEXT_WINDOW", "80")
        get_settings.cache_clear()
        settings = get_settings()

        history = []
        for i in range(10):
            history.append({"role": "user", "content": "中" * 100})
            history.append({"role": "assistant", "content": "文" * 100})

        new_history, report = maybe_compact_history(
            thinking_level="light",
            deep_thinking=False,
            history_messages=history,
            evidence_texts=[],
            current_question="当前问题",
            settings=settings,
        )
        assert report is not None
        assert report.rounds_compressed >= 1
        assert report.method == "llm_summary"
        assert report.tokens_after < report.tokens_before

        assert mock_chat.call_count == 1
        chat_messages = mock_chat.call_args[0][0]
        assert len(chat_messages) == 1
        assert chat_messages[0]["role"] == "user"

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_compaction_summary_ratio_guard_hard_truncate(
        self, mock_chat, mock_providers, monkeypatch
    ):
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "长" * 1000

        get_settings.cache_clear()
        monkeypatch.setenv("EKB_LLM_CONTEXT_WINDOW", "80")
        get_settings.cache_clear()
        settings = get_settings()

        history = []
        for i in range(10):
            history.append({"role": "user", "content": "中" * 100})
            history.append({"role": "assistant", "content": "文" * 100})

        new_history, report = maybe_compact_history(
            thinking_level="light",
            deep_thinking=False,
            history_messages=history,
            evidence_texts=[],
            current_question="当前问题",
            settings=settings,
        )
        assert report is not None
        assert report.method == "hard_truncate"
        assert len(new_history) < len(history)

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_recent_rounds_untouched(self, mock_chat, mock_providers, monkeypatch):
        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "用户提了A=1"

        get_settings.cache_clear()
        monkeypatch.setenv("EKB_LLM_CONTEXT_WINDOW", "80")
        monkeypatch.setenv("EKB_COMPACTION_RECENT_ROUNDS_KEEP", "6")
        get_settings.cache_clear()
        settings = get_settings()

        history = []
        for i in range(10):
            history.append({"role": "user", "content": f"user_msg_{i}_" + "中" * 50})
            history.append({"role": "assistant", "content": f"assist_msg_{i}_" + "文" * 50})

        expected_last_12 = history[-12:]

        new_history, report = maybe_compact_history(
            thinking_level="light",
            deep_thinking=False,
            history_messages=history,
            evidence_texts=[],
            current_question="当前问题",
            settings=settings,
        )
        assert report is not None
        new_last_12 = new_history[-12:]
        assert len(new_last_12) == len(expected_last_12)
        for actual, expected in zip(new_last_12, expected_last_12):
            assert actual["role"] == expected["role"]
            assert actual["content"] == expected["content"]

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_older_items_empty_returns_skipped(self, mock_chat, mock_providers, monkeypatch):
        mock_providers.return_value = [MagicMock()]

        get_settings.cache_clear()
        monkeypatch.setenv("EKB_LLM_CONTEXT_WINDOW", "80")
        monkeypatch.setenv("EKB_COMPACTION_RECENT_ROUNDS_KEEP", "6")
        get_settings.cache_clear()
        settings = get_settings()

        history = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]

        new_history, report = maybe_compact_history(
            thinking_level="light",
            deep_thinking=False,
            history_messages=history,
            evidence_texts=[],
            current_question="当前问题",
            settings=settings,
        )
        assert report is not None
        assert report.method == "skipped"
        assert report.rounds_compressed == 0
        assert new_history == history

    @patch("ekb_api.llm.get_runtime_chat_providers")
    @patch("ekb_api.llm.chat")
    def test_hard_truncate_at_most_3_rounds(self, mock_chat, mock_providers, monkeypatch):
        import signal

        mock_providers.return_value = [MagicMock()]
        mock_chat.return_value = "长" * 100000

        get_settings.cache_clear()
        monkeypatch.setenv("EKB_LLM_CONTEXT_WINDOW", "10")
        monkeypatch.setenv("EKB_COMPACTION_RECENT_ROUNDS_KEEP", "2")
        monkeypatch.setenv("EKB_COMPACTION_SUMMARY_RATIO_TARGET", "0.0001")
        get_settings.cache_clear()
        settings = get_settings()

        history = []
        for i in range(250):
            history.append({"role": "user", "content": "中" * 20})
            history.append({"role": "assistant", "content": "文" * 20})

        def _timeout_handler(signum, frame):
            raise TimeoutError("测试超时超过3秒")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(3)
        try:
            new_history, report = maybe_compact_history(
                thinking_level="light",
                deep_thinking=False,
                history_messages=history,
                evidence_texts=[],
                current_question="当前问题",
                settings=settings,
            )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        assert report is not None
        assert report.method == "hard_truncate"
        assert len(new_history) < len(history)

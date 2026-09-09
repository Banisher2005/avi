"""Unit tests for assistant intent detection and conversational synthesizers."""

import pytest

from avi.assistant.intents import (
    AssistantIntentType,
    classify_confirmation,
    detect_assistant_intent,
)
from avi.assistant.synthesizer import (
    format_disk_space_conversational,
    format_processes_conversational,
    format_system_info_conversational,
    get_memory_summary_conversational,
)
from avi.tools.base import ToolResult


class TestAssistantIntentDetection:
    @pytest.mark.parametrize(
        "prompt",
        ["hi", "Hello", "hey!", "good morning", "howdy"],
    )
    def test_detect_greetings(self, prompt):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.GREETING

    @pytest.mark.parametrize(
        "prompt",
        ["what can you do", "what can you do?", "what are your capabilities", "help me"],
    )
    def test_detect_capabilities(self, prompt):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.CAPABILITIES

    @pytest.mark.parametrize(
        "prompt",
        [
            "how much space is left on my laptop",
            "how much space is left on my laptop ?",
            "how much disk space do I have?",
            "check disk space",
            "how much storage is left?",
        ],
    )
    def test_detect_disk_space(self, prompt):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.DISK_SPACE

    @pytest.mark.parametrize(
        "prompt",
        [
            "how much memory is free",
            "how much ram is free?",
            "how much memory do i have",
            "free memory",
            "free ram",
            "check ram",
            "available memory",
            "ram usage",
        ],
    )
    def test_detect_memory_total(self, prompt):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.MEMORY_TOTAL

    @pytest.mark.parametrize(
        "prompt",
        [
            "what is using the most ram?",
            "what's using the most ram",
            "what is using my ram?",
            "which app is using the most memory?",
            "top memory",
        ],
    )
    def test_detect_ram_usage(self, prompt):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.RAM_USAGE

    @pytest.mark.parametrize(
        ("prompt", "expected_secs"),
        [
            ("set a timer for 2 seconds", 2.0),
            ("set a timer for 2 secs", 2.0),
            ("timer 10 mins", 600.0),
            ("set timer for 1.5 minutes", 90.0),
            ("timer 5s", 5.0),
        ],
    )
    def test_detect_timers(self, prompt, expected_secs):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.TIMER
        assert intent.extra.get("duration_seconds") == expected_secs

    @pytest.mark.parametrize(
        ("prompt", "expected_app"),
        [
            ("open brave", "brave"),
            ("open google chrome", "google chrome"),
            ("launch chrome", "chrome"),
            ("open antigravity", "antigravity"),
            ("run code", "code"),
        ],
    )
    def test_detect_open_app(self, prompt, expected_app):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.OPEN_APP
        assert intent.target == expected_app

    @pytest.mark.parametrize(
        ("prompt", "expected_url"),
        [
            ("open youtube.com", "https://youtube.com"),
            ("open https://github.com", "https://github.com"),
            ("open youtube", "https://www.youtube.com"),
            ("open google", "https://www.google.com"),
        ],
    )
    def test_detect_open_url(self, prompt, expected_url):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.OPEN_URL
        assert intent.target == expected_url

    @pytest.mark.parametrize("prompt", ["ss", "take ss", "snip"])
    def test_detect_shorthand_clarifications(self, prompt):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.CLARIFICATION
        assert intent.target == "take a screenshot"
        assert "Did you mean 'take a screenshot'?" in intent.extra.get("message", "")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("yes", True),
            ("y", True),
            ("yeah", True),
            ("yep", True),
            ("yup", True),
            ("sure", True),
            ("correct", True),
            ("do it", True),
            ("yes please", True),
            ("yes, please", True),
            ("yes do it", True),
            ("no", False),
            ("n", False),
            ("nope", False),
            ("not that", False),
            ("no thanks", False),
            ("cancel", False),
            ("stop", False),
            ("hi", None),
            ("what time is it", None),
            ("disk space", None),
            ("yesterday", None),
        ],
    )
    def test_classify_confirmation(self, text, expected):
        assert classify_confirmation(text) == expected

    def test_multi_turn_clarification_confirmation(self):
        from unittest.mock import MagicMock

        last_turn = MagicMock()
        last_turn.intent_type = "CLARIFICATION"
        last_turn.target = "take a screenshot"
        last_turn.response_text = "Did you mean 'take a screenshot'?"
        last_turn.pending_clarification = None

        intent = detect_assistant_intent("yes", last_turn=last_turn)
        assert intent.intent_type == AssistantIntentType.CONFIRMATION
        assert intent.target == "take a screenshot"

        neg_intent = detect_assistant_intent("no", last_turn=last_turn)
        assert neg_intent.intent_type == AssistantIntentType.CANCELLATION

        unrelated = detect_assistant_intent("hi", last_turn=last_turn)
        assert unrelated.intent_type == AssistantIntentType.GREETING


class TestConversationalSynthesizer:
    def test_format_disk_space(self):
        # 154 GB free out of 240 GB
        free_b = 154 * (1024**3)
        total_b = 240 * (1024**3)
        tool_res = ToolResult(
            success=True,
            data={"free_bytes": free_b, "total_bytes": total_b, "path": "/"},
        )
        msg = format_disk_space_conversational(tool_res)
        assert "154 GB free" in msg
        assert "240 GB" in msg
        assert "main drive" in msg

    def test_format_processes_memory(self):
        tool_res = ToolResult(
            success=True,
            data=[
                {"pid": 1234, "name": "llama-server", "memory_percent": 7.6, "cpu_percent": 1.2},
                {"pid": 5678, "name": "chrome", "memory_percent": 4.1, "cpu_percent": 3.5},
            ],
        )
        msg = format_processes_conversational(tool_res, sort_by="memory")
        assert "llama-server is currently using the most memory at about 7.6%" in msg
        assert "followed by chrome" in msg

    def test_format_system_info(self):
        tool_res = ToolResult(
            success=True,
            data={"os": "Linux", "kernel": "6.11.0", "architecture": "x86_64", "cpu_count": 8},
        )
        msg = format_system_info_conversational(tool_res)
        assert "Linux" in msg
        assert "6.11.0" in msg
        assert "8 CPU cores" in msg

    def test_get_memory_summary(self):
        summary = get_memory_summary_conversational()
        assert isinstance(summary, str)
        # On Linux with /proc/meminfo, should include available RAM
        assert "RAM" in summary or "unavailable" in summary

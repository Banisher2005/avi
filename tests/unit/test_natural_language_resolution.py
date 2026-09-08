"""Unit tests for Phase 15 Natural Language Application and Web Destination Resolution."""

import pytest

from avi.apps.destinations import WEB_DESTINATIONS, resolve_web_destination
from avi.apps.resolver import ApplicationResolver
from avi.assistant.intents import AssistantIntentType, detect_assistant_intent


class TestWebDestinationResolution:
    """Test web destination resolution vs desktop application resolution."""

    def test_exact_destination(self):
        res = resolve_web_destination("chatgpt")
        assert res is not None
        name, url, browser = res
        assert name == "ChatGPT"
        assert url == "https://chatgpt.com"
        assert browser is None

    def test_destination_with_browser(self):
        res = resolve_web_destination("chatgpt on chrome")
        assert res is not None
        name, url, browser = res
        assert name == "ChatGPT"
        assert url == "https://chatgpt.com"
        assert browser == "chrome"

    def test_destination_with_typo(self):
        res = resolve_web_destination("caht gpt on chrome")
        assert res is not None
        name, url, browser = res
        assert name == "ChatGPT"
        assert url == "https://chatgpt.com"
        assert browser == "chrome"

    def test_youtube_destination(self):
        res = resolve_web_destination("youtube on firefox")
        assert res is not None
        name, url, browser = res
        assert name == "YouTube"
        assert url == "https://youtube.com"
        assert browser == "firefox"

    def test_intent_detection_chatgpt_on_chrome(self):
        intent = detect_assistant_intent("open chatgpt on chrome")
        assert intent.intent_type == AssistantIntentType.OPEN_URL
        assert intent.target == "https://chatgpt.com"
        assert intent.extra.get("browser") == "chrome"
        assert intent.extra.get("destination_name") == "ChatGPT"

    def test_intent_detection_chatgpt_typo(self):
        intent = detect_assistant_intent("open caht gpt on chrome")
        assert intent.intent_type == AssistantIntentType.OPEN_URL
        assert intent.target == "https://chatgpt.com"
        assert intent.extra.get("browser") == "chrome"

    def test_intent_detection_open_chat_gpt(self):
        intent = detect_assistant_intent("open chat gpt")
        assert intent.intent_type == AssistantIntentType.OPEN_URL
        assert intent.target == "https://chatgpt.com"


class TestFuzzyApplicationResolver:
    """Test fuzzy and typo-tolerant application matching."""

    @pytest.fixture
    def mock_resolver(self):
        resolver = ApplicationResolver()
        # Seed known test apps
        resolver._desktop_cache = {
            "firefox": {
                "name": "Firefox",
                "exec": "/usr/bin/firefox",
                "generic_name": "Web Browser",
                "path": "/usr/share/applications/firefox.desktop",
            },
            "sublime-text": {
                "name": "Sublime Text",
                "exec": "/usr/bin/subl",
                "generic_name": "Text Editor",
                "path": "/usr/share/applications/sublime_text.desktop",
            },
        }
        return resolver

    def test_fuzzy_app_resolution(self, mock_resolver):
        res_firefox = mock_resolver.resolve("fierfox")
        assert res_firefox.is_resolved is True
        assert res_firefox.canonical_name == "Firefox"

        res_sublime = mock_resolver.resolve("sublme")
        assert res_sublime.is_resolved is True
        assert res_sublime.canonical_name == "Sublime Text"

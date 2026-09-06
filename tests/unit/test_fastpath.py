"""Tests for deterministic command-template routing."""

from avi.config import Config
from avi.core.fastpath import resolve_command_template
from avi.core.router import Router
from avi.providers.base import BaseProvider


class FailingProvider(BaseProvider):
    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        raise AssertionError("LLM must not be invoked for a fast-path command template")
        yield ""

    def generate_full(self, prompt, system_prompt=None, context=None):
        raise AssertionError("LLM must not be invoked for a fast-path command template")

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "test"

    @property
    def last_metrics(self):
        return None

    @property
    def last_context(self):
        return None


def test_command_templates_cover_common_queries():
    assert resolve_command_template("what command shows the current directory?") == "pwd"
    assert resolve_command_template("what command lists the files here?") == "ls -la"
    assert resolve_command_template("how do I check disk space?") == "df -h"
    assert resolve_command_template("what command shows running processes?") == "ps aux"
    assert resolve_command_template("what command shows my current git branch?") == "git branch --show-current"
    assert resolve_command_template("what command checks git status?") == "git status"
    assert resolve_command_template("what command shows recent git commits?") == "git log --oneline -10"
    assert resolve_command_template("what command shows listening ports?") == "ss -tulpn"


def test_unknown_request_is_not_fast_path():
    assert resolve_command_template("how do I deploy this application?") is None


def test_router_bypasses_llm_for_command_template():
    router = Router(Config.load(), provider=FailingProvider())
    assert router.route_full("what command shows the current directory?").text == "pwd"
    assert router.last_metrics is not None


def test_router_preserves_llm_path_for_non_template():
    class Provider(FailingProvider):
        def generate(self, prompt, system_prompt=None, context=None, stream=True):
            yield "model response"

        def generate_full(self, prompt, system_prompt=None, context=None):
            from avi.providers.base import ProviderResponse
            return ProviderResponse(text="model response", context=None)

    router = Router(Config.load(), provider=Provider())
    assert router.route_full("how should I structure this Python package?").text == "model response"

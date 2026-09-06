"""Unit and integration tests for complete command proposal, safety, and execution flow."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from avi.config import Config
from avi.core.router import Router
from avi.core.session import InteractiveSession
from avi.execution.models import CommandRequest, ExecutionResult
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics


class MockProposalProvider(BaseProvider):
    def __init__(self, response_text: str):
        self.response_text = response_text
        self._metrics = ResponseMetrics(total_duration_ms=50.0)

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        yield self.response_text

    def generate_full(self, prompt, system_prompt=None, context=None):
        return ProviderResponse(text=self.response_text, metrics=self._metrics)

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "mock-proposal-model"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return None


def test_safe_command_executes_automatically(tmp_path):
    config = Config.load()
    provider = MockProposalProvider("COMMAND: pwd")
    router = Router(config, provider=provider)

    in_stream = io.StringIO("where am I working?\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    code = session.run()
    assert code == 0
    output = out_stream.getvalue()
    # Should execute pwd without asking for confirmation
    assert "Execute? [y/N]" not in output
    assert str(Path.cwd().resolve()) in output


def test_confirm_command_accepted_executes(tmp_path):
    config = Config.load()
    target_file = tmp_path / "test.tmp"
    target_file.write_text("temporary content")
    assert target_file.is_file()

    provider = MockProposalProvider(f"COMMAND: rm {target_file}")
    router = Router(config, provider=provider)

    # User answers 'y' to prompt
    in_stream = io.StringIO("delete file\ny\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    code = session.run()
    assert code == 0
    output = out_stream.getvalue()
    assert "Execute? [y/N]" in output
    assert not target_file.exists()


def test_confirm_command_rejected_cancels(tmp_path):
    config = Config.load()
    target_file = tmp_path / "important.txt"
    target_file.write_text("do not delete")

    provider = MockProposalProvider(f"COMMAND: rm {target_file}")
    router = Router(config, provider=provider)

    # User answers 'n' to prompt
    in_stream = io.StringIO("delete file\nn\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    code = session.run()
    assert code == 0
    output = out_stream.getvalue()
    assert "Execute? [y/N]" in output
    assert "Execution cancelled." in output
    assert target_file.is_file()


def test_confirm_command_arbitrary_text_rejected_by_default(tmp_path):
    # Default is NO, so answers like 'sure' or 'yes please' must NOT confirm
    config = Config.load()
    target_file = tmp_path / "file.txt"
    target_file.write_text("data")

    provider = MockProposalProvider(f"COMMAND: rm {target_file}")
    router = Router(config, provider=provider)

    in_stream = io.StringIO("delete file\nyes please\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    code = session.run()
    assert code == 0
    output = out_stream.getvalue()
    assert "Execution cancelled." in output
    assert target_file.is_file()


def test_blocked_command_refused_no_execution(tmp_path):
    config = Config.load()
    provider = MockProposalProvider("COMMAND: rm -rf /")
    router = Router(config, provider=provider)

    # Mock execute_command to be sure it is NEVER invoked
    with patch.object(router, "execute_command") as mock_exec:
        in_stream = io.StringIO("delete everything\nexit\n")
        out_stream = io.StringIO()

        session = InteractiveSession(
            router=router,
            config=config,
            history_path=tmp_path / "history",
            in_stream=in_stream,
            out_stream=out_stream,
        )

        code = session.run()
        assert code == 0
        output = out_stream.getvalue()
        assert "[Blocked:" in output
        assert "Execute? [y/N]" not in output
        mock_exec.assert_not_called()


def test_fast_path_ram_uses_processes_tool():
    config = Config.load()
    router = Router(config)
    result = router.check_fast_path("what is using the most RAM?")
    assert result is not None
    assert "PID" in result
    assert "MEMORY" in result or "%" in result


def test_fast_path_branch_uses_git_tool():
    config = Config.load()
    router = Router(config)
    result = router.check_fast_path("show my current branch")
    assert result is not None
    assert "feature/safe-command-execution" in result


def test_malformed_proposal_not_executed(tmp_path):
    config = Config.load()
    # Model generates malformed command syntax with unclosed quote
    provider = MockProposalProvider("COMMAND: rm \"unclosed quote")
    router = Router(config, provider=provider)

    with patch.object(router, "execute_command") as mock_exec:
        in_stream = io.StringIO("do action\nexit\n")
        out_stream = io.StringIO()

        session = InteractiveSession(
            router=router,
            config=config,
            history_path=tmp_path / "history",
            in_stream=in_stream,
            out_stream=out_stream,
        )

        code = session.run()
        assert code == 0
        output = out_stream.getvalue()
        mock_exec.assert_not_called()

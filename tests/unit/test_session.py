"""Unit tests for InteractiveSession."""

import io
from unittest.mock import patch

from avi.config import Config
from avi.core.router import Router
from avi.core.session import InteractiveSession
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.providers.ollama import OllamaConnectionError


class MockInteractiveProvider(BaseProvider):
    def __init__(self):
        self._metrics = ResponseMetrics(total_duration_ms=45.0)
        self._last_context = None

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        if "name" in prompt:
            self._last_context = [1, 2, 3, 4]
            yield "Alice"
        else:
            self._last_context = [1, 2, 3]
            yield "Hello "
            yield "there!"

    def generate_full(self, prompt, system_prompt=None, context=None):
        chunks = list(self.generate(prompt, system_prompt, context, stream=False))
        return ProviderResponse(
            text="".join(chunks), metrics=self._metrics, context=self._last_context
        )

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "mock-model"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return self._last_context


def test_session_startup_and_exit(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    in_stream = io.StringIO("exit\n")
    out_stream = io.StringIO()
    err_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
        err_stream=err_stream,
    )

    code = session.run()
    assert code == 0
    output = out_stream.getvalue()
    assert "AVI Interactive Session" in output
    assert "Type 'exit', 'quit', 'clear', or 'history'" in output


def test_session_quit_command(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    in_stream = io.StringIO("quit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    assert session.run() == 0


def test_session_clear_command(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    in_stream = io.StringIO("clear\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    assert session.run() == 0
    assert "\033[H\033[2J" in out_stream.getvalue()


def test_session_history_command(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    in_stream = io.StringIO("history\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    assert session.run() == 0
    output = out_stream.getvalue()
    assert "history" in output.lower()


def test_session_empty_input_handling(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    in_stream = io.StringIO("   \n\n\nexit\n")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    assert session.run() == 0


def test_session_multiturn_conversation_context(tmp_path):
    config = Config.load()
    provider = MockInteractiveProvider()
    router = Router(config, provider=provider)

    in_stream = io.StringIO("tell me a story\nwhat is my name?\nexit\n")
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
    assert session.context == [1, 2, 3, 4]
    output = out_stream.getvalue()
    assert "Hello there!" in output
    assert "Alice" in output


def test_session_eof_handling(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    # Empty stream produces immediate EOF
    in_stream = io.StringIO("")
    out_stream = io.StringIO()

    session = InteractiveSession(
        router=router,
        config=config,
        history_path=tmp_path / "history",
        in_stream=in_stream,
        out_stream=out_stream,
    )

    assert session.run() == 0


def test_session_ctrl_c_during_stream_cancels_turn(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    def interrupt_generator(*args, **kwargs):
        yield "Starting answer..."
        raise KeyboardInterrupt()

    with patch.object(router, "route", side_effect=interrupt_generator):
        in_stream = io.StringIO("tell me a story\nexit\n")
        out_stream = io.StringIO()

        session = InteractiveSession(
            router=router,
            config=config,
            history_path=tmp_path / "history",
            in_stream=in_stream,
            out_stream=out_stream,
        )

        assert session.run() == 0
        output = out_stream.getvalue()
        assert "[Interrupted]" in output


def test_session_ollama_error_does_not_terminate_session(tmp_path):
    config = Config.load()
    provider = MockInteractiveProvider()
    router = Router(config, provider=provider)

    call_count = 0

    def route_with_error(prompt, context=None, stream=True):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OllamaConnectionError("Connection to Ollama failed")
        yield "Recovered!"

    with patch.object(router, "route", side_effect=route_with_error):
        in_stream = io.StringIO("query1\nquery2\nexit\n")
        out_stream = io.StringIO()
        err_stream = io.StringIO()

        session = InteractiveSession(
            router=router,
            config=config,
            history_path=tmp_path / "history",
            in_stream=in_stream,
            out_stream=out_stream,
            err_stream=err_stream,
        )

        assert session.run() == 0
        assert "Connection to Ollama failed" in err_stream.getvalue()
        assert "Recovered!" in out_stream.getvalue()


def test_session_history_failure_resilience(tmp_path):
    config = Config.load()
    router = Router(config, provider=MockInteractiveProvider())

    in_stream = io.StringIO("exit\n")
    out_stream = io.StringIO()

    # Read-only path / unwriteable directory mock
    unwritable_path = tmp_path / "unwritable" / "history"

    with patch("readline.write_history_file", side_effect=PermissionError("Permission denied")):
        session = InteractiveSession(
            router=router,
            config=config,
            history_path=unwritable_path,
            in_stream=in_stream,
            out_stream=out_stream,
        )
        # Should complete cleanly without raising
        assert session.run() == 0

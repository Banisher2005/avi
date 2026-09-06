"""Unit tests for AVI CLI entry point."""

from unittest.mock import MagicMock, patch

import pytest

from avi import __version__
from avi.cli import main
from avi.providers.base import ProviderResponse, ResponseMetrics
from avi.providers.ollama import OllamaConnectionError, OllamaModelNotFoundError


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert f"avi {__version__}" in captured.out or f"avi {__version__}" in captured.err


def test_cli_short_version(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["-v"])
    assert exc_info.value.code == 0


def test_cli_no_args_launches_interactive_session():
    with patch("avi.cli.InteractiveSession") as mock_session_cls:
        mock_instance = MagicMock()
        mock_instance.run.return_value = 0
        mock_session_cls.return_value = mock_instance

        code = main([])
        assert code == 0
        mock_session_cls.assert_called_once()
        mock_instance.run.assert_called_once()


def test_cli_successful_prompt(capsys):
    mock_router = MagicMock()
    mock_router.route.return_value = iter(["p", "wd"])
    mock_router.last_metrics = None

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["what command shows current directory?"])
        assert code == 0
        captured = capsys.readouterr()
        assert "pwd" in captured.out


def test_cli_timing_flag(capsys):
    mock_router = MagicMock()
    mock_router.route.return_value = iter(["pwd"])
    mock_router.last_metrics = ResponseMetrics(total_duration_ms=135.0)

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["-t", "what command shows current directory?"])
        assert code == 0
        captured = capsys.readouterr()
        assert "pwd" in captured.out
        assert "135 ms" in captured.err


def test_cli_no_stream_mode(capsys):
    mock_router = MagicMock()
    mock_router.route_full.return_value = ProviderResponse(text="pwd")
    mock_router.last_metrics = None

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["--no-stream", "what command shows current directory?"])
        assert code == 0
        captured = capsys.readouterr()
        assert "pwd" in captured.out


def test_cli_connection_error(capsys):
    mock_router = MagicMock()
    mock_router.route.side_effect = OllamaConnectionError(
        "Could not connect to Ollama at http://127.0.0.1:11434"
    )

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["what command shows current directory?"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Error: Could not connect to Ollama" in captured.err


def test_cli_model_not_found_error(capsys):
    mock_router = MagicMock()
    mock_router.route.side_effect = OllamaModelNotFoundError(
        "Model 'missing' not found in Ollama"
    )

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["what command shows current directory?"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Error: Model 'missing' not found" in captured.err


def test_cli_keyboard_interrupt(capsys):
    mock_router = MagicMock()
    mock_router.route.side_effect = KeyboardInterrupt

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["what command shows current directory?"])
        assert code == 130
        captured = capsys.readouterr()
        assert "Aborted." in captured.err


def test_cli_unexpected_error_boundary(capsys):
    mock_router = MagicMock()
    mock_router.route.side_effect = RuntimeError("Something completely unexpected happened")

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["trigger unexpected error"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Unexpected error: Something completely unexpected happened" in captured.err


def test_cli_provider_flag(capsys):
    captured_config = []

    def mock_router_init(cfg):
        captured_config.append(cfg)
        r = MagicMock()
        r.check_fast_path.return_value = None
        r.route.return_value = iter(["ok"])
        r.last_metrics = None
        return r

    with patch("avi.cli.Router", side_effect=mock_router_init):
        code = main(["-p", "antigravity", "some prompt"])
        assert code == 0
        assert len(captured_config) == 1
        assert captured_config[0].provider == "antigravity"


def test_cli_provider_error(capsys):
    from avi.providers import ProviderError

    mock_router = MagicMock()
    mock_router.check_fast_path.return_value = None
    mock_router.route.side_effect = ProviderError("Backend model failed")

    with patch("avi.cli.Router", return_value=mock_router):
        code = main(["trigger provider error"])
        assert code == 1
        captured = capsys.readouterr()
        assert "Error: Backend model failed" in captured.err

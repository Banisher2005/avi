"""Unit tests for AVI CLI entry point."""

import sys
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


def test_cli_no_args_shows_help(capsys):
    code = main([])
    assert code == 0
    captured = capsys.readouterr()
    assert "usage: avi" in captured.out


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

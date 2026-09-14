"""Integration tests for AVI Phase 12: PowerToys-inspired Command Palette and Instant Commands."""

import io
from unittest.mock import MagicMock, patch

from avi.apps.registry import ApplicationEntry
from avi.cli import main
from avi.commands.models import PaletteState
from avi.commands.resolver import CommandResolver
from avi.core.session import InteractiveSession
from avi.reliability.models import OperationStatus


# ============================================================================
# 1. Deterministic Slash Commands & Zero-AI Isolation
# ============================================================================


def test_slash_commands_execute_offline_without_ollama():
    """Verify instant slash commands execute deterministically without any LLM/network calls."""
    resolver = CommandResolver()

    # Mock Ollama provider to guarantee it is NEVER invoked
    with patch("avi.providers.ollama.OllamaProvider.generate") as mock_ollama:
        # 1. /calc
        results_calc = resolver.resolve("/calc 27 * 43")
        assert len(results_calc) > 0
        calc_res = resolver.execute_result(results_calc[0])
        assert calc_res.success is True
        assert calc_res.status == OperationStatus.COMPLETED
        assert "1161" in (calc_res.user_message or str(calc_res.result))

        # 2. /ram
        results_ram = resolver.resolve("/ram")
        assert len(results_ram) > 0
        ram_res = resolver.execute_result(results_ram[0])
        assert ram_res.success is True
        assert any(term in (ram_res.user_message or str(ram_res.result)) for term in ("RAM", "Memory", "GB"))

        # 3. /cpu
        results_cpu = resolver.resolve("/cpu")
        assert len(results_cpu) > 0
        cpu_res = resolver.execute_result(results_cpu[0])
        assert cpu_res.success is True
        assert "CPU" in (cpu_res.user_message or str(cpu_res.result))

        # 4. /disk
        results_disk = resolver.resolve("/disk")
        assert len(results_disk) > 0
        disk_res = resolver.execute_result(results_disk[0])
        assert disk_res.success is True
        assert any(term in (disk_res.user_message or str(disk_res.result)) for term in ("Disk", "Storage", "GB"))

        # 5. /health
        results_health = resolver.resolve("/health")
        assert len(results_health) > 0
        health_res = resolver.execute_result(results_health[0])
        assert health_res.success is True
        assert any(term in (health_res.user_message or str(health_res.result)) for term in ("Diagnostics", "Health", "Operational"))

        # 6. /files
        results_files = resolver.resolve("/files pyproject.toml")
        assert len(results_files) > 0
        files_res = resolver.execute_result(results_files[0])
        assert files_res.success is True

        # 7. /help
        results_help = resolver.resolve("/help")
        assert len(results_help) > 0
        help_res = resolver.execute_result(results_help[0])
        assert help_res.success is True
        assert "/calc" in (help_res.user_message or str(help_res.result))

        # Guarantee zero calls were made to Ollama
        mock_ollama.assert_not_called()


def test_instant_inline_math_without_slash():
    """Verify inline arithmetic expressions are evaluated without /calc prefix."""
    resolver = CommandResolver()
    results = resolver.resolve("15 * 8")
    assert len(results) > 0
    top = results[0]
    assert top.action_type == "calculate"
    assert top.subtitle == "= 120"

    supervised_res = resolver.execute_result(top)
    assert supervised_res.success is True
    assert "120" in (supervised_res.user_message or str(supervised_res.result))


# ============================================================================
# 2. CLI Single-Shot Integration Tests
# ============================================================================


def test_cli_single_shot_slash_calc(capsys):
    """Test single-shot CLI invocation: avi '/calc 27 * 43'."""
    exit_code = main(["/calc 27 * 43"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "1161" in captured.out


def test_cli_single_shot_slash_ram(capsys):
    """Test single-shot CLI invocation: avi '/ram'."""
    exit_code = main(["/ram"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert any(term in captured.out for term in ("RAM", "Memory", "GB"))


def test_cli_single_shot_slash_help(capsys):
    """Test single-shot CLI invocation: avi '/help'."""
    exit_code = main(["/help"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "/calc" in captured.out
    assert "/ram" in captured.out


# ============================================================================
# 3. Interactive CLI Session Slash Command Integration
# ============================================================================


def test_interactive_cli_session_slash_command():
    """Test interactive CLI session resolving slash command directly in REPL loop."""
    input_stream = io.StringIO("/calc 100 / 4\nexit\n")
    output_stream = io.StringIO()

    mock_router = MagicMock()
    mock_config = MagicMock()
    mock_orchestrator = MagicMock()

    session = InteractiveSession(
        router=mock_router,
        config=mock_config,
        in_stream=input_stream,
        out_stream=output_stream,
        orchestrator=mock_orchestrator,
    )
    exit_code = session.run()

    assert exit_code == 0
    output = output_stream.getvalue()
    assert "25" in output


# ============================================================================
# 4. Supervised Application Launching & History Tracking
# ============================================================================


def test_application_launch_through_supervisor():
    """Test launching an application via palette result executes under supervisor."""
    resolver = CommandResolver()

    # Create dummy app
    dummy_app = ApplicationEntry(
        id="test-editor",
        canonical_name="test editor",
        display_name="Test Editor",
        executable="/usr/bin/nano",
        icon="text-editor",
        categories=["Utility", "TextEditor"],
        aliases=["editor", "nano"],
    )

    resolver.app_registry._applications[dummy_app.id] = dummy_app
    resolver.app_registry._discovered = True

    # Search for app
    results = resolver.resolve("nano")
    assert len(results) > 0
    app_res = next((r for r in results if r.id == f"app:{dummy_app.id}"), None)
    assert app_res is not None
    assert app_res.action_type == "launch"

    # Execute launch under supervisor with mocked subprocess
    with patch("subprocess.Popen") as mock_popen, patch("shutil.which", return_value="/usr/bin/nano"):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        exec_res = resolver.execute_result(app_res)
        assert exec_res.success is True
        mock_popen.assert_called_once()
        assert exec_res.user_message == "Launched Test Editor."

        # Verify usage history recorded launch
        boost = resolver.usage_history.get_score_boost(app_res.id)
        assert boost > 0.0


# ============================================================================
# 5. Palette State Lifecycle
# ============================================================================


def test_palette_state_transitions():
    """Verify palette state enumeration conforms to the required lifecycle."""
    assert PaletteState.CLOSED.value == "CLOSED"
    assert PaletteState.OPEN.value == "OPEN"
    assert PaletteState.SEARCHING.value == "SEARCHING"
    assert PaletteState.SHOWING_RESULTS.value == "SHOWING_RESULTS"
    assert PaletteState.EXECUTING.value == "EXECUTING"
    assert PaletteState.ERROR.value == "ERROR"

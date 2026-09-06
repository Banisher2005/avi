"""Integration tests verifying CLI routing paths for native desktop capabilities (Phase 12.1).

Ensures that user requests such as 'take a screenshot' and 'increase volume'
are routed directly to native desktop capabilities instead of falling through to
the LLM and generating unsafe or blocked shell commands.
"""

from unittest.mock import MagicMock, patch

import pytest

from avi.cli import main
from avi.config import Config
from avi.orchestrator.orchestrator import AssistantOrchestrator


class TestCliRoutingIntegration:
    """End-to-end integration tests for CLI routing."""

    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Set up standard mocks to isolate CLI tests without launching real apps or external calls."""
        with patch("avi.config.Config.load") as mock_conf:
            cfg = Config(provider="ollama", show_timing=False)
            mock_conf.return_value = cfg
            yield cfg

    def test_cli_screenshot_routes_to_native_capability(self, capsys, tmp_path):
        """'avi "take a screenshot"' must route to desktop.screenshot capability, not LLM."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() should not be called for screenshot intent"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.screenshot.ScreenshotCapability._capture_via_portal",
                return_value=True,
            ),
            patch(
                "avi.capabilities.desktop.screenshot.ScreenshotCapability._resolve_destination"
            ) as mock_dest,
        ):
            test_png = tmp_path / "test_screen.png"
            test_png.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            )
            mock_dest.return_value = test_png

            code = main(["take a screenshot"])

        assert code == 0
        captured = capsys.readouterr()
        assert "Captured screenshot" in captured.out or "Screenshot" in captured.out
        assert "sudo" not in captured.out
        assert "xclip" not in captured.out
        assert "[Blocked:" not in captured.out

    @pytest.mark.parametrize(
        "prompt",
        [
            "screenshot",
            "take a screenshot",
            "capture the screen",
            "capture my screen",
            "take a full screenshot",
        ],
    )
    def test_cli_screenshot_phrasing_variations(self, prompt):
        """Screenshot phrasing variations should all be recognized as assistant requests."""
        orch = AssistantOrchestrator(config=Config())
        assert orch.is_assistant_request(prompt) is True

    def test_cli_volume_increase_routes_to_native_capability(self, capsys):
        """'avi "increase volume"' must route to desktop.volume.set, not LLM shell generator."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() should not be called for volume intent"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/wpctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="Volume: 0.50", stderr="")

            code = main(["increase volume"])

        assert code == 0
        captured = capsys.readouterr()
        assert "Volume increased" in captured.out
        assert "sudo" not in captured.out
        assert "amixer" not in captured.out
        assert "[Blocked:" not in captured.out

    def test_cli_volume_decrease_routes_to_native_capability(self, capsys):
        """'avi "decrease volume"' must route to desktop.volume.set without sudo."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() should not be called for volume intent"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/wpctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="Volume: 0.50", stderr="")

            code = main(["decrease volume"])

        assert code == 0
        captured = capsys.readouterr()
        assert "Volume decreased" in captured.out
        assert "sudo" not in captured.out
        assert "[Blocked:" not in captured.out

    def test_cli_mute_and_unmute_routes_to_native_capability(self, capsys):
        """'mute volume' and 'unmute volume' must route to native desktop.volume.set."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError("Router.route() should not be called")

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/wpctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

            code_mute = main(["mute volume"])
            assert code_mute == 0
            captured_mute = capsys.readouterr()
            assert "muted" in captured_mute.out.lower()
            assert "sudo" not in captured_mute.out

            code_unmute = main(["unmute volume"])
            assert code_unmute == 0
            captured_unmute = capsys.readouterr()
            assert "unmuted" in captured_unmute.out.lower()
            assert "sudo" not in captured_unmute.out

    @pytest.mark.parametrize(
        "prompt",
        [
            "increase volume",
            "decrease volume",
            "turn up the volume",
            "turn down the volume",
            "make it louder",
            "make it quieter",
            "mute volume",
            "unmute",
            "set volume to 60%",
            "what is the volume",
        ],
    )
    def test_cli_volume_phrasing_variations(self, prompt):
        """Volume phrasing variations must all be classified as assistant requests."""
        orch = AssistantOrchestrator(config=Config())
        assert orch.is_assistant_request(prompt) is True

    def test_cli_media_control_routes_to_native_capability(self, capsys):
        """'play music' and 'pause music' route to desktop.media.control without LLM."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError("Router.route() should not be called")

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/playerctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

            code = main(["pause music"])
            assert code == 0
            captured = capsys.readouterr()
            assert "pause" in captured.out.lower()
            assert "sudo" not in captured.out

    def test_cli_non_assistant_shell_request_routes_to_router(self, capsys):
        """Generic command requests must NOT be intercepted by assistant orchestrator."""
        orch = AssistantOrchestrator(config=Config())
        assert orch.is_assistant_request("generate a command to find python files") is False
        assert orch.is_assistant_request("ls -la /var/log") is False
        assert orch.is_assistant_request("curl -I https://google.com") is False

    # ── Phase 12.2 Part 4: Typo & Near-Match Tests ────────────────────────

    @pytest.mark.parametrize(
        ("prompt", "expected_suggestion"),
        [
            ("incease volume", "increase volume"),
            ("increse volume", "increase volume"),
            ("increse vol", "increase volume"),
            ("turn volme up", "turn volume up"),
            ("mutee volume", "mute volume"),
            ("unmutee volume", "unmute volume"),
        ],
    )
    def test_cli_typo_requests_offer_clarification_without_shell(
        self, capsys, prompt, expected_suggestion
    ):
        """Typos in desktop commands must offer clarification and NEVER generate shell commands."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for desktop typos"
        )

        with patch("avi.cli.Router", return_value=mock_router):
            code = main([prompt])

        assert code == 0
        captured = capsys.readouterr()
        assert "Did you mean" in captured.out
        assert expected_suggestion in captured.out
        assert "sudo" not in captured.out
        assert "hdparm" not in captured.out
        assert "Command:" not in captured.out
        assert "[Blocked:" not in captured.out
        assert "Execute? [y/N]" not in captured.out

    # ── Phase 12.2 Part 5: Mute & Unmute Natural Language Variants ────────

    @pytest.mark.parametrize(
        "prompt",
        [
            "mute",
            "mute volume",
            "mute my volume",
            "mute the volume",
            "silence the volume",
        ],
    )
    def test_cli_mute_natural_variants_route_to_capability(self, capsys, prompt):
        """All natural variants of mute must route directly to desktop.volume.set (action=mute)."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for mute intent"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/wpctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

            code = main([prompt])

        assert code == 0
        captured = capsys.readouterr()
        assert "muted" in captured.out.lower()
        assert "sudo" not in captured.out
        assert "amixer" not in captured.out
        assert "Command:" not in captured.out

    @pytest.mark.parametrize(
        "prompt",
        [
            "unmute",
            "unmute volume",
            "unmute my volume",
            "unmute the volume",
            "turn sound back on",
        ],
    )
    def test_cli_unmute_natural_variants_route_to_capability(self, capsys, prompt):
        """All natural variants of unmute must route directly to desktop.volume.set (action=unmute)."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for unmute intent"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/wpctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

            code = main([prompt])

        assert code == 0
        captured = capsys.readouterr()
        assert "unmuted" in captured.out.lower()
        assert "sudo" not in captured.out
        assert "amixer" not in captured.out
        assert "Command:" not in captured.out

    # ── Phase 12.2 Part 6 & 7: UI Runtime & Single-Instance Tests ─────────

    def test_cli_activate_delegates_with_system_fallback(self):
        """'avi activate' must invoke AviApp with allow_system_fallback=True."""
        with patch("avi.ui.AviApp.run") as mock_app_run:
            mock_app_run.return_value = 0
            code = main(["activate"])

        assert code == 0
        mock_app_run.assert_called_once_with(allow_system_fallback=True)

    def test_ui_app_sets_pythonpath_on_system_fallback(self):
        """AviApp.run() must inject src into PYTHONPATH when delegating to system Python."""
        from avi.ui.app import AviApp
        from avi.ui.detector import GtkEnvironmentReport

        fake_report = GtkEnvironmentReport(
            python_executable="/home/user/.venv/bin/python3",
            python_version="3.12.0",
            is_virtualenv=True,
            gi_importable=False,
            gtk4_available=False,
            display_available=True,
            system_python_has_gtk4=True,
            system_python_path="/usr/bin/python3",
            system_python_version="3.14.0",
            diagnostic_message="test",
        )

        app = AviApp(router=MagicMock(), config=Config())
        with (
            patch("avi.ui.app._GTK_AVAILABLE", False),
            patch("avi.ui.app.diagnose_gtk_environment", return_value=fake_report),
            patch("avi.ui.app.subprocess.run") as mock_subproc,
        ):
            mock_subproc.return_value = MagicMock(returncode=0)
            code = app.run(allow_system_fallback=True)

        assert code == 0
        mock_subproc.assert_called_once()
        call_cmd = mock_subproc.call_args[0][0]
        call_env = mock_subproc.call_args[1]["env"]
        assert call_cmd == ["/usr/bin/python3", "-m", "avi.cli", "ui"]
        assert "PYTHONPATH" in call_env
        assert "src" in call_env["PYTHONPATH"]

"""Integration tests for Phase 13.2: JARVIS Interaction Hardening & Qwen3 4B Migration.

Verifies:
- Bounded typo normalization & fuzzy intent matching for native desktop commands
- Native actions NEVER drop into LLM shell fallback or dangerous shell commands
- Bare application launching (chrome, brave, firefox, spotify, antigravity)
- Uninstalled application reporting ("I couldn't find <app> installed.")
- Desktop folder launching (open Downloads, downloads, downlods)
- Multi-turn cross-process session continuity with XDG state persistence
- Rejection of "it" as filesystem path when no recent search state exists
- Distinct routing between native actions and valid shell commands (e.g. systemctl)
- Safety boundary enforcement on dangerous commands (rm -rf /, sudo)
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from avi.apps.resolver import ApplicationResolution
from avi.assistant.intents import AssistantIntentType, detect_assistant_intent
from avi.cli import main
from avi.config import Config
from avi.execution.models import CommandRequest
from avi.orchestrator.models import ConversationTurn
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.retrieval.models import SearchResult
from avi.safety.engine import SafetyEngine
from avi.safety.models import RiskLevel
from avi.session.state import load_session_state, save_session_state


class TestJarvisInteractionHardening:
    """Integration test suite for JARVIS-style native interaction hardening."""

    @pytest.fixture(autouse=True)
    def setup_config(self):
        """Mock config loading with qwen3:4b default model."""
        with patch("avi.config.Config.load") as mock_load:
            cfg = Config(provider="ollama", model="qwen3:4b", show_timing=False)
            mock_load.return_value = cfg
            yield cfg

    # ── 1. Activation Typo Routing ──────────────────────────────────────────

    @pytest.mark.parametrize("typo_arg", ["activate", "activaite", "activte", "actvate"])
    def test_cli_activation_typos_route_to_ui(self, typo_arg):
        """Activation variations and typos must route to UI activation without shell execution."""
        with patch("avi.cli.run_ui") as mock_ui:
            mock_ui.return_value = 0
            code = main([typo_arg])
            assert code == 0
            mock_ui.assert_called_once()
            assert mock_ui.call_args[1]["is_activate"] is True

    def test_activation_never_routes_to_shell(self):
        """Intent classifier must identify activation as ACTIVATE or CLARIFICATION, never shell."""
        for typo in ["activate", "activaite", "activte", "actvate"]:
            intent = detect_assistant_intent(typo)
            assert intent.intent_type in (
                AssistantIntentType.ACTIVATE,
                AssistantIntentType.CLARIFICATION,
            )
            assert "systemctl" not in intent.target

    # ── 2. Bare Application Launching ───────────────────────────────────────

    @pytest.mark.parametrize("app_name", ["chrome", "brave", "firefox", "spotify", "antigravity"])
    def test_bare_app_name_routes_to_open_app(self, app_name):
        """Bare application names must route directly to OPEN_APP intent."""
        intent = detect_assistant_intent(app_name)
        assert intent.intent_type == AssistantIntentType.OPEN_APP
        assert intent.target.lower() == app_name

    def test_uninstalled_app_reports_cleanly_without_shell(self, capsys):
        """Uninstalled app must report 'I couldn't find <app> installed.' without shell fallback."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for uninstalled app"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch("avi.apps.resolver.ApplicationResolver.resolve") as mock_resolve,
        ):
            mock_resolve.return_value = ApplicationResolution(
                requested_name="NonexistentApp",
                canonical_name="NonexistentApp",
                executable=None,
                desktop_entry=None,
                platform="linux",
                installed=False,
                confidence=0.0,
            )
            code = main(["open NonexistentApp"])

        assert code == 0
        captured = capsys.readouterr()
        assert "I couldn't find NonexistentApp installed." in captured.out
        assert "sudo" not in captured.out
        assert "apt" not in captured.out

    # ── 3. Screenshot Typo Routing ──────────────────────────────────────────

    @pytest.mark.parametrize(
        "prompt", ["screenshot", "take screenshot", "screeenshot", "screnshot"]
    )
    def test_screenshot_typos_route_natively_without_shell(self, capsys, tmp_path, prompt):
        """Screenshot typos must route natively to desktop.screenshot or clarification, never shell."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for screenshot typos"
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
            test_png = tmp_path / "screen.png"
            test_png.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            )
            mock_dest.return_value = test_png

            code = main([prompt])

        assert code == 0
        captured = capsys.readouterr()
        assert any(
            phrase in captured.out
            for phrase in ["Captured screenshot", "Screenshot", "Did you mean 'screenshot'?"]
        )
        assert "systemctl" not in captured.out
        assert "sudo" not in captured.out

    # ── 4. Volume Typos & Variations ────────────────────────────────────────

    @pytest.mark.parametrize(
        "prompt",
        ["volume up", "volum up", "incrase volume", "louder"],
    )
    def test_volume_variations_and_typos_route_natively(self, capsys, prompt):
        """Volume variations and typos must route natively to volume set or clarification, never shell."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for volume"
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
        assert any(
            phrase in captured.out.lower()
            for phrase in ["volume", "increased", "did you mean", "louder"]
        )
        assert "amixer" not in captured.out
        assert "sudo" not in captured.out

    # ── 5. Directory Variations (Downloads) ──────────────────────────────────

    @pytest.mark.parametrize("prompt", ["open Downloads", "downloads", "downlods"])
    def test_downloads_variations_route_to_open_dir(self, prompt):
        """Downloads folder variations must resolve to OPEN_DIR or clarification."""
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type in (
            AssistantIntentType.OPEN_DIR,
            AssistantIntentType.CLARIFICATION,
        )
        if intent.intent_type == AssistantIntentType.OPEN_DIR:
            assert "Downloads" in intent.target

    # ── 6. Multi-turn Session Persistence & 'open it' ────────────────────────

    def test_open_it_without_prior_session_reports_cleanly(self, capsys):
        """'open it' with no recent results must report absence of results and NEVER treat 'it' as a file."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            "Router.route() must NOT be called for 'open it'"
        )

        with patch("avi.cli.Router", return_value=mock_router):
            code = main(["open it"])

        assert code == 0
        captured = capsys.readouterr()
        assert "I don't have a recent result to open." in captured.out
        assert "/home/" not in captured.out
        assert "/it" not in captured.out

    def test_cross_process_multiturn_search_then_open_it(self, capsys):
        """CLI process 1 (search) persists results; CLI process 2 ('open it') opens the first result."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError("Router.route() should not be called")

        fake_results = [
            SearchResult(
                id="res-1",
                title="Building Local AI Agents",
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                source="youtube",
                description="A complete guide to building local AI agents",
            )
        ]

        # Step 1: Process 1 performs a smart recommendation / search and persists state
        turn1 = ConversationTurn(
            turn_id=1,
            user_query="find videos about local AI agents",
            intent_type=AssistantIntentType.YOUTUBE_RECOMMEND,
            response_text="Here is a recommended video: Building Local AI Agents",
            search_results=fake_results,
            timestamp=time.time(),
        )
        assert save_session_state(turn1) is True

        # Verify state was written to XDG state path
        loaded_turn = load_session_state()
        assert loaded_turn is not None
        assert len(loaded_turn.search_results) == 1
        assert loaded_turn.search_results[0].title == "Building Local AI Agents"

        # Step 2: Separate CLI process executes 'open it'
        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch("avi.actions.system.webbrowser.open", return_value=True) as mock_browser,
        ):
            code = main(["open it"])

        assert code == 0
        captured = capsys.readouterr()
        assert 'Opening "Building Local AI Agents"' in captured.out
        mock_browser.assert_called_once_with("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_expired_session_state_rejects_open_it(self, capsys):
        """Expired session state (> 30 min) must not be used by 'open it'."""
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError("Router.route() should not be called")

        # Save turn with old timestamp (31 minutes ago)
        old_time = time.time() - 1900.0
        turn_old = ConversationTurn(
            turn_id=1,
            user_query="find videos",
            intent_type=AssistantIntentType.YOUTUBE_SEARCH,
            response_text="Results",
            search_results=[
                SearchResult(
                    id="old-1",
                    title="Old Video",
                    url="https://youtube.com/watch?v=old",
                    source="youtube",
                    description="",
                )
            ],
            timestamp=old_time,
        )
        assert save_session_state(turn_old) is True

        with patch("avi.cli.Router", return_value=mock_router):
            code = main(["open it"])

        assert code == 0
        captured = capsys.readouterr()
        assert "I don't have a recent result to open." in captured.out

    # ── 7. Shell Fallback & Safety Boundary Distinction ─────────────────────

    def test_rm_rf_root_blocked_by_safety_engine(self, capsys):
        """'rm -rf /' must be intercepted and blocked by the SafetyEngine, never executed."""
        safety = SafetyEngine()
        proposal = CommandRequest(program="rm", args=["-rf", "/"])
        assessment = safety.evaluate(proposal)
        assert assessment.is_blocked is True
        assert assessment.level == RiskLevel.BLOCK

    def test_systemctl_routes_to_shell_proposal(self):
        """'systemctl enable lightdm' is an explicit shell command and should not be hijacked as desktop intent."""
        orch = AssistantOrchestrator(config=Config(provider="ollama"))
        assert orch.is_assistant_request("systemctl enable lightdm") is False

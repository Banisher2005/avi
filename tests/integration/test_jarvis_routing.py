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
    def test_screenshot_typos_route_natively_without_shell(
        self, capsys, tmp_path, prompt, monkeypatch
    ):
        """Screenshot typos must route natively to desktop.screenshot or clarification, never shell."""
        monkeypatch.setenv("DISPLAY", ":0")
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


class TestPhase133JarvisFastPathAndYouTubeReliability:
    """Comprehensive integration tests for Phase 13.3: Fast Path, YouTube & Reliability."""

    @pytest.fixture(autouse=True)
    def setup_config(self):
        with patch("avi.config.Config.load") as mock_load:
            cfg = Config(provider="ollama", model="qwen3:4b", show_timing=False)
            mock_load.return_value = cfg
            yield cfg

    # ── 1. YouTube Intent Resolution ────────────────────────────────────────

    @pytest.mark.parametrize(
        ("prompt", "expected_query"),
        [
            ("open youtube mkbhd", "mkbhd"),
            ("open mkbhd on youtube", "mkbhd"),
            ("open youtube and search mkbdh", "mkbdh"),
            ("search youtube for mkbhd", "mkbhd"),
            ("youtube mkbhd", "mkbhd"),
        ],
    )
    def test_youtube_search_variations(self, prompt, expected_query):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.YOUTUBE_SEARCH
        assert intent.extra.get("query") == expected_query
        assert "not installed" not in intent.target.lower()

    def test_pure_open_youtube_routes_to_url(self):
        intent = detect_assistant_intent("open youtube")
        assert intent.intent_type == AssistantIntentType.OPEN_URL
        assert intent.target == "https://www.youtube.com"

    # ── 2. Volume Grammar & Unmuting ────────────────────────────────────────

    @pytest.mark.parametrize(
        ("prompt", "expected_action", "expected_delta", "expected_level"),
        [
            ("increase volume by 10", "raise", 10, None),
            ("decrease volume by 10", "lower", 10, None),
            ("increase volume to max", "set", None, 100),
            ("set volume to max", "set", None, 100),
            ("set volume to min", "set", None, 0),
        ],
    )
    def test_volume_grammar(self, prompt, expected_action, expected_delta, expected_level):
        intent = detect_assistant_intent(prompt)
        assert intent.intent_type == AssistantIntentType.VOLUME_SET
        assert intent.extra.get("action") == expected_action
        if expected_delta is not None:
            assert intent.extra.get("delta") == expected_delta
        if expected_level is not None:
            assert intent.extra.get("level") == expected_level

    def test_volume_increase_unmutes_sink(self):
        from avi.capabilities.desktop.system_controls import VolumeSetCapability

        cap = VolumeSetCapability()
        with (
            patch(
                "avi.capabilities.desktop.system_controls.shutil.which",
                return_value="/usr/bin/wpctl",
            ),
            patch("avi.capabilities.desktop.system_controls.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            res = cap.execute(action="raise", delta=5)
            assert res.success is True
            # Verify set-mute 0 was called
            mute_calls = [
                c for c in mock_run.call_args_list if "set-mute" in c[0][0] and "0" in c[0][0]
            ]
            assert len(mute_calls) >= 1

    # ── 3. Fast Deterministic YouTube Recommend ─────────────────────────────

    def test_youtube_recommend_fast_deterministic(self):
        from avi.capabilities.models import CapabilityResult, ExecutionStatus

        results = [
            SearchResult(
                id="yt_res_1",
                title="Building Local AI Agents",
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                source="youtube",
                channel="AI Hub",
                duration="12:30",
            )
        ]
        mock_router = MagicMock()
        mock_router.provider = MagicMock()
        mock_router.provider.__class__.__name__ = "OllamaProvider"
        mock_router.provider.generate_full.side_effect = AssertionError(
            "Ollama generate_full must not be called"
        )

        orch = AssistantOrchestrator(config=Config(provider="ollama"), router=mock_router)
        with patch.object(orch.capabilities, "execute") as mock_exec:
            mock_exec.return_value = CapabilityResult(
                success=True,
                status=ExecutionStatus.SUCCESS,
                data={"query": "building local AI agents", "search_results": results},
            )
            t_start = time.perf_counter()
            res = orch.handle(
                "find me the best YouTube video about building local AI agents",
                auto_execute_actions=True,
            )
            elapsed = time.perf_counter() - t_start

        assert elapsed < 1.0
        assert "Best match" in res.text
        assert res.selected_result is not None
        assert res.selected_result.id == "yt_res_1"

    # ── 4. Performance Invariants: No LLM Invocation for Native Actions ──────

    @pytest.mark.parametrize(
        "cmd",
        [
            "chrome",
            "screenshot",
            "increase volume",
            "mute",
            "downloads",
        ],
    )
    def test_performance_invariants_no_llm_invocation(self, cmd, capsys):
        mock_router = MagicMock()
        mock_router.route.side_effect = AssertionError(
            f"LLM router.route() was called for native cmd: {cmd}"
        )
        mock_router.route_full.side_effect = AssertionError(
            f"LLM router.route_full() was called for native cmd: {cmd}"
        )

        with (
            patch("avi.cli.Router", return_value=mock_router),
            patch("avi.apps.resolver.ApplicationResolver.resolve") as mock_app,
            patch("avi.capabilities.desktop.screenshot.ScreenshotCapability.execute") as mock_shot,
            patch(
                "avi.capabilities.desktop.system_controls.VolumeSetCapability.execute"
            ) as mock_vol,
        ):
            mock_app.return_value = ApplicationResolution(
                requested_name="chrome",
                canonical_name="Google Chrome",
                executable="/usr/bin/google-chrome",
                desktop_entry="google-chrome.desktop",
                platform="linux",
                installed=True,
                confidence=1.0,
            )
            mock_shot.return_value = MagicMock(
                success=True, message="Captured screenshot", data={"path": "/tmp/s.png"}
            )
            mock_vol.return_value = MagicMock(success=True, message="Volume adjusted", data={})

            code = main([cmd])
            assert code == 0
            mock_router.route.assert_not_called()
            mock_router.route_full.assert_not_called()

    # ── 5. CLI Activation Visible Response & Headless Detection ─────────────

    def test_activation_with_display_prints_activating(self, capsys):
        with (
            patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False),
            patch("avi.ui.app.AviApp.run", return_value=0),
        ):
            code = main(["activaite"])
            assert code == 0
            captured = capsys.readouterr()
            assert "Activating AVI...\n" in captured.out

    def test_activation_headless_prints_guidance(self, capsys):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("avi.ui.app.AviApp.run", return_value=0),
        ):
            code = main(["activate"])
            assert code == 1
            captured = capsys.readouterr()
            assert "No display server detected" in captured.out

    # ── 6. ResponseMetrics Breakdown Fields ─────────────────────────────────

    def test_response_metrics_breakdown_fields(self):
        from avi.providers.models import ResponseMetrics

        m = ResponseMetrics(
            total_duration_ms=150.0,
            intent_duration_ms=10.0,
            capability_duration_ms=40.0,
            retrieval_duration_ms=80.0,
            time_to_first_token_ms=50.0,
        )
        assert m.intent_duration_ms == 10.0
        assert m.capability_duration_ms == 40.0
        assert m.retrieval_duration_ms == 80.0
        assert m.time_to_first_token_ms == 50.0

    # ── 7. Phase 13.4: GUI Natural Language Normalization & Deictic Resolution ─

    def test_clean_natural_language_input_variants(self):
        from avi.assistant.intents import clean_natural_language_input

        assert (
            clean_natural_language_input(
                'avi "find me the best YouTube video about building local AI agents"'
            )
            == "find me the best YouTube video about building local AI agents"
        )
        assert clean_natural_language_input("avi chrome") == "chrome"
        assert clean_natural_language_input('"increase volume"') == "increase volume"
        assert (
            clean_natural_language_input("avi: increase volume to max") == "increase volume to max"
        )
        assert clean_natural_language_input("avi, screenshot") == "screenshot"
        assert clean_natural_language_input("open youtube mkbhd") == "open youtube mkbhd"
        assert clean_natural_language_input("open it") == "open it"

    def test_orchestrator_handles_cli_prefixed_youtube_request(self):
        from avi.retrieval.models import SearchResult

        orch = AssistantOrchestrator(config=Config(provider="ollama"))
        mock_results = [
            SearchResult(
                id="v123",
                title="Local AI Agents with Ollama",
                url="https://www.youtube.com/watch?v=v123",
                source="youtube",
                channel="AI Lab",
                duration="12:30",
            )
        ]

        def _mock_exec(cap_name, **kwargs):
            if cap_name == "web.youtube.search_results":
                return MagicMock(success=True, error=None, data={"search_results": mock_results})
            return MagicMock(success=True, error=None, data={})

        with patch.object(orch.capabilities, "execute", side_effect=_mock_exec):
            res = orch.handle('avi "find me the best YouTube video about building local AI agents"')
            assert res.text
            assert "What would you like me to search for on YouTube?" not in res.text
            assert "Local AI Agents with Ollama" in res.text
            assert res.selected_result is not None
            assert res.selected_result.id == "v123"

    def test_deictic_open_it_after_youtube_opens_selected_result(self):
        from avi.retrieval.models import SearchResult

        orch = AssistantOrchestrator(config=Config(provider="ollama"))
        mock_results = [
            SearchResult(
                id="dQw4w9WgXcQ",
                title="Local AI Agents with Ollama",
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                source="youtube",
                channel="AI Lab",
                duration="12:30",
            )
        ]

        opened_urls = []

        def _mock_exec(cap_name, **kwargs):
            if cap_name == "web.youtube.search_results":
                return MagicMock(success=True, error=None, data={"search_results": mock_results})
            if cap_name in ("desktop.url.open", "desktop.open_url", "open_url"):
                opened_urls.append(kwargs.get("url"))
                return MagicMock(success=True, error=None, message="URL opened", data={})
            return MagicMock(success=True, error=None, data={})

        with patch.object(orch.capabilities, "execute", side_effect=_mock_exec):
            # 1. Search YouTube
            res1 = orch.handle("find me the best YouTube video about building local AI agents")
            assert res1.selected_result is not None

            # 2. "open it"
            res2 = orch.handle("open it")
            assert "Opening" in res2.text
            assert "Local AI Agents with Ollama" in res2.text
            assert opened_urls == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]

    def test_deictic_open_it_without_prior_turn_does_not_open_user_it_path(self):
        orch = AssistantOrchestrator(config=Config(provider="ollama"))
        with patch(
            "avi.capabilities.desktop.app_launcher.OpenUrlCapability.execute"
        ) as mock_open_url:
            res = orch.handle("open it")
            # Should ask for clarification, never open /home/.../it or execute app 'it'
            assert "What would you like me to open?" in res.text or "specify" in res.text.lower()
            mock_open_url.assert_not_called()

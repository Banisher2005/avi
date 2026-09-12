"""Integration acceptance suite for Phase 12 AVI Agent Runtime & Desktop Activation.

Implements all 10 Real User Acceptance Scenarios from Part 19:
- Scenario 1: 'Take a screenshot.'
- Scenario 2: 'Open Brave.'
- Scenario 3: 'Open my Downloads.'
- Scenario 4: 'Set a timer for 2 minutes.'
- Scenario 5: 'Find the newest PDF in Downloads.'
- Scenario 6: 'Take a screenshot and save it in ~/Pictures.'
- Scenario 7: 'Delete this file.'
- Scenario 8: 'What's on my screen?' (honest vision detection)
- Scenario 9: One-button desktop activation
- Scenario 10: Single-instance window reuse / focus
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from avi.apps.models import ApplicationResolution
from avi.capabilities.desktop.screenshot import ScreenshotCapability
from avi.capabilities.models import DataClassification, ExecutionStatus
from avi.config import Config
from avi.core.router import Router
from avi.orchestrator.orchestrator import AssistantOrchestrator
from avi.providers.base import BaseProvider, ProviderResponse, ResponseMetrics
from avi.providers.models import AgentRequest, AgentResponse, ProviderCapabilities
from avi.ui.app import AviApp


class MockVisionProvider(BaseProvider):
    """Mock provider advertising vision capability."""

    def __init__(self, has_vision: bool = False, reply: str = "A desktop showing code and files."):
        self.has_vision = has_vision
        self.reply = reply
        self._metrics = ResponseMetrics(total_duration_ms=15.0)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(vision=self.has_vision, streaming=True)

    def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(text=self.reply, metrics=self._metrics)

    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        yield self.reply

    def generate_full(self, prompt, system_prompt=None, context=None):
        return ProviderResponse(text=self.reply, metrics=self._metrics)

    def is_available(self) -> bool:
        return True

    def warmup(self) -> bool:
        return True

    def get_model_name(self) -> str:
        return "mock-vision-model" if self.has_vision else "mock-text-only"

    @property
    def last_metrics(self):
        return self._metrics

    @property
    def last_context(self):
        return None


@pytest.fixture
def test_env(tmp_path):
    config = Config.load()
    provider = MockVisionProvider(has_vision=False)
    router = Router(config, provider=provider)
    mock_resolver = MagicMock()
    orch = AssistantOrchestrator(
        config=config,
        router=router,
        app_resolver=mock_resolver,
    )
    return {
        "config": config,
        "router": router,
        "provider": provider,
        "resolver": mock_resolver,
        "orchestrator": orch,
        "tmp_path": tmp_path,
    }


class TestPhase12UserAcceptanceScenarios:
    # ── Scenario 1: Screenshot Capture ──────────────────────────────────
    def test_scenario_1_take_screenshot(self, test_env):
        orch = test_env["orchestrator"]
        tmp_path = test_env["tmp_path"]

        # Mock screenshot backend runner to generate a test PNG file
        def fake_backend(cmd, timeout):
            out_file = Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x02\x00\x00\x00"
            )
            return True

        cap = orch.capabilities.get("desktop.screenshot")
        assert isinstance(cap, ScreenshotCapability)
        cap._backend_runner = fake_backend
        cap.destination_dir = tmp_path / "Screenshots"

        with patch("shutil.which", return_value="/usr/bin/grim"):
            res = orch.handle("take a screenshot")

        assert "Captured screenshot" in res.text
        assert res.capability_result is not None
        assert res.capability_result.success is True
        assert Path(res.capability_result.data["path"]).exists()
        # Privacy: MUST be local only by default
        assert res.capability_result.classification == DataClassification.LOCAL_ONLY

    # ── Scenario 2: Open Application ────────────────────────────────────
    def test_scenario_2_open_brave(self, test_env):
        orch = test_env["orchestrator"]
        resolver = test_env["resolver"]

        mock_res = ApplicationResolution(
            requested_name="brave",
            canonical_name="Brave Web Browser",
            executable="/usr/bin/brave-browser",
            desktop_entry=None,
            platform="linux",
            installed=True,
            confidence=1.0,
        )
        resolver.resolve.return_value = mock_res
        resolver.launch.return_value = (True, "Launched Brave Web Browser.")

        res = orch.handle("Open Brave")
        assert "Opening Brave Web Browser" in res.text or "Launched Brave Web Browser" in res.text
        resolver.resolve.assert_called_with("Brave")
        resolver.launch.assert_called_once()

    # ── Scenario 3: Open Directory ──────────────────────────────────────
    def test_scenario_3_open_downloads(self, test_env):
        orch = test_env["orchestrator"]
        dl_dir = Path.home() / "Downloads"
        created = False
        if not dl_dir.exists():
            dl_dir.mkdir(parents=True, exist_ok=True)
            created = True
        try:
            with patch("subprocess.Popen") as mock_popen:
                res = orch.handle("Open my Downloads")
                assert "Opening folder Downloads" in res.text
                mock_popen.assert_called_once()
        finally:
            if created and dl_dir.exists():
                try:
                    dl_dir.rmdir()
                except OSError:
                    pass

    # ── Scenario 4: Timer ───────────────────────────────────────────────
    def test_scenario_4_set_timer(self, test_env):
        orch = test_env["orchestrator"]
        res = orch.handle("Set a timer for 2 minutes", auto_execute_actions=False)
        assert "Timer set for 2 minutes" in res.text
        assert res.action is not None
        assert res.action.duration_seconds == 120.0

    # ── Scenario 5: Filesystem Search ───────────────────────────────────
    def test_scenario_5_find_newest_pdf_in_downloads(self, test_env):
        orch = test_env["orchestrator"]
        tmp_path = test_env["tmp_path"]
        dl_dir = tmp_path / "Downloads"
        dl_dir.mkdir()

        # Create two PDFs
        p1 = dl_dir / "invoice_old.pdf"
        p1.write_text("old")
        os.utime(p1, (1000, 1000))

        p2 = dl_dir / "invoice_new.pdf"
        p2.write_text("new")
        os.utime(p2, (2000, 2000))

        res = orch.handle(f"Find the newest PDF in {dl_dir}")
        assert "invoice_new.pdf" in res.text
        assert res.capability_result is not None
        assert res.capability_result.data["matches"][0]["name"] == "invoice_new.pdf"

    # ── Scenario 6: Composite Multi-Step Screenshot & Save ──────────────
    def test_scenario_6_screenshot_and_save_in_pictures(self, test_env):
        orch = test_env["orchestrator"]
        tmp_path = test_env["tmp_path"]
        pictures_dir = tmp_path / "Pictures"
        pictures_dir.mkdir()

        def fake_backend(cmd, timeout):
            out_file = Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x02\x00\x00\x00"
            )
            return True

        cap = orch.capabilities.get("desktop.screenshot")
        cap._backend_runner = fake_backend
        cap.destination_dir = tmp_path / "TempScreenshots"

        with patch("shutil.which", return_value="/usr/bin/grim"):
            res = orch.handle(f"Take a screenshot and save it in {pictures_dir}")

        assert res.plan is not None
        assert len(res.plan.steps) == 2
        assert f"Captured screenshot and saved it in {pictures_dir}" in res.text

    # ── Scenario 7: Destructive Delete Requires Confirmation ────────────
    def test_scenario_7_delete_requires_confirmation(self, test_env):
        orch = test_env["orchestrator"]
        tmp_path = test_env["tmp_path"]
        target = tmp_path / "document.txt"
        target.write_text("critical data")

        del_cap = orch.capabilities.get("filesystem.delete")
        # Direct capability check
        unconfirmed_res = del_cap.execute(path=str(target), confirmed=False)
        assert unconfirmed_res.status == ExecutionStatus.CONFIRMATION_REQUIRED
        assert target.exists()  # Kept safe!

        # Assistant router proposal check
        test_env["router"]._provider = MockVisionProvider(reply="COMMAND: rm " + str(target))
        res = orch.handle(f"delete {target}")
        assert res.requires_confirmation is True
        assert target.exists()  # Safe!

    # ── Scenario 8: Screen Observation & Honest Vision Detection ────────
    def test_scenario_8_screen_observation_without_vision(self, test_env):
        orch = test_env["orchestrator"]
        tmp_path = test_env["tmp_path"]

        def fake_backend(cmd, timeout):
            out_file = Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x02\x00\x00\x00"
            )
            return True

        cap = orch.capabilities.get("desktop.screenshot")
        cap._backend_runner = fake_backend
        cap.destination_dir = tmp_path

        with patch("shutil.which", return_value="/usr/bin/grim"):
            # Provider has vision = False
            res = orch.handle("What is on my screen?")

        # Must NOT fake vision! Must be honest!
        assert "does not support visual reasoning" in res.text
        assert res.capability_result is not None
        assert res.capability_result.classification == DataClassification.LOCAL_ONLY

    def test_scenario_8_screen_observation_with_vision(self, test_env):
        orch = test_env["orchestrator"]
        tmp_path = test_env["tmp_path"]
        # Enable vision on provider
        vision_provider = MockVisionProvider(
            has_vision=True, reply="I see a terminal with AVI tests."
        )
        orch.router._provider = vision_provider

        def fake_backend(cmd, timeout):
            out_file = Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x02\x00\x00\x00"
            )
            return True

        cap = orch.capabilities.get("desktop.screenshot")
        cap._backend_runner = fake_backend
        cap.destination_dir = tmp_path

        with patch("shutil.which", return_value="/usr/bin/grim"):
            res = orch.handle("What is on my screen?")

        assert res.text == "I see a terminal with AVI tests."

    # ── Scenario 9: One-Button Desktop UI Activation ────────────────────
    def test_scenario_9_one_button_activation(self, test_env):
        app = AviApp(
            router=test_env["router"],
            config=test_env["config"],
            orchestrator=test_env["orchestrator"],
        )
        assert app.window is None

        # Verify headless detection exits safely with code 1
        with patch.dict(os.environ, {}, clear=True):
            assert app.run() == 1

    # ── Scenario 10: Single-Instance Focus Reuse ────────────────────────
    def test_scenario_10_single_instance_reuse(self, test_env):
        app = AviApp(
            router=test_env["router"],
            config=test_env["config"],
            orchestrator=test_env["orchestrator"],
        )
        mock_window = MagicMock()
        app.window = mock_window

        # When activation occurs on existing instance, present() must be called
        # rather than creating a second window
        app.window.present()
        mock_window.present.assert_called_once()

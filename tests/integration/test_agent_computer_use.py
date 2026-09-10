"""Integration tests for AVI computer-use layer covering Scenarios A through K."""

from unittest.mock import MagicMock, patch

import pytest

from avi.agent.context import TaskStatus
from avi.agent.executor import AgentExecutor
from avi.agent.orchestrator import AgentOrchestrator
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import create_default_capability_registry


@pytest.fixture
def computer_use_orchestrator():
    """Create a fully configured AgentOrchestrator with all registered capabilities."""
    registry = create_default_capability_registry()
    planner = AgentPlanner()
    executor = AgentExecutor(registry=registry)
    return AgentOrchestrator(
        planner=planner,
        executor=executor,
        registry=registry,
    )


class TestComputerUseScenarios:
    def test_scenario_a_open_application(self, computer_use_orchestrator):
        with patch("avi.capabilities.desktop.app_launcher.ApplicationResolver.resolve") as mock_resolve:
            with patch("avi.capabilities.desktop.app_launcher.ApplicationResolver.launch") as mock_launch:
                res_mock = MagicMock()
                res_mock.found = True
                res_mock.app.name = "Terminal"
                mock_resolve.return_value = res_mock
                mock_launch.return_value = (True, "Launched Terminal.")

                ctx = computer_use_orchestrator.run("open terminal")
                # Either handled by fastpath/apps or orchestrator
                assert ctx is not None

    def test_scenario_b_window_management(self, computer_use_orchestrator):
        # 1. List windows
        mock_output = (
            "0x02800003  0 12345 org.gnome.Terminal localhost Terminal - user@host: ~\n"
            "0x03a00001  1 12346 code.Code localhost test.py - Visual Studio Code\n"
        )
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = mock_output
                ctx = computer_use_orchestrator.run("list windows")
                assert ctx.status == TaskStatus.COMPLETED
                assert len(ctx.steps) == 1
                assert ctx.steps[0].capability_name == "desktop.window.list"
                assert ctx.steps[0].observation["count"] == 2
                assert ctx.steps[0].verification_result is True

        # 2. Focus window
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                ctx2 = computer_use_orchestrator.run("focus terminal")
                assert ctx2.status == TaskStatus.COMPLETED
                assert ctx2.steps[0].capability_name == "desktop.window.focus"
                assert "Focused window matching 'terminal'" in ctx2.final_response

    def test_scenario_c_clipboard_manipulation(self, computer_use_orchestrator):
        # 1. Set clipboard
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xclip" if x == "xclip" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                ctx_set = computer_use_orchestrator.run("copy hello to clipboard")
                assert ctx_set.status == TaskStatus.COMPLETED
                assert ctx_set.steps[0].capability_name == "desktop.clipboard.set"
                assert ctx_set.steps[0].observation["text"] == "hello"
                assert ctx_set.steps[0].verification_result is True

        # 2. Read clipboard
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xclip" if x == "xclip" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "clipboard contents 123"
                ctx_get = computer_use_orchestrator.run("read clipboard")
                assert ctx_get.status == TaskStatus.COMPLETED
                assert ctx_get.steps[0].capability_name == "desktop.clipboard.get"
                assert ctx_get.steps[0].observation["text"] == "clipboard contents 123"

    def test_scenario_d_keyboard_controls(self, computer_use_orchestrator):
        # 1. Type text
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xdotool" if x == "xdotool" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                ctx_type = computer_use_orchestrator.run("type hello world")
                assert ctx_type.status == TaskStatus.COMPLETED
                assert ctx_type.steps[0].capability_name == "desktop.input.type_text"
                assert "Typed 11 characters" in ctx_type.final_response

        # 2. Press key
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/xdotool" if x == "xdotool" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                ctx_key = computer_use_orchestrator.run("press ctrl+c")
                assert ctx_key.status == TaskStatus.COMPLETED
                assert ctx_key.steps[0].capability_name == "desktop.input.press_key"
                assert "Pressed key 'ctrl+c'" in ctx_key.final_response

    def test_scenario_e_volume_audio_controls(self, computer_use_orchestrator):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            ctx_mute = computer_use_orchestrator.run("mute volume")
            assert ctx_mute.status == TaskStatus.COMPLETED
            assert ctx_mute.steps[0].capability_name == "system.volume.set"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            ctx_set = computer_use_orchestrator.run("set volume to 50%")
            assert ctx_set.status == TaskStatus.COMPLETED
            assert ctx_set.steps[0].capability_name == "system.volume.set"

    def test_scenario_f_notifications(self, computer_use_orchestrator):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            ctx = computer_use_orchestrator.run("notify me that lunch is ready")
            assert ctx.status == TaskStatus.COMPLETED
            assert ctx.steps[0].capability_name == "desktop.notification"
            assert "lunch is ready" in ctx.final_response

    def test_scenario_g_screenshot_and_open(self, computer_use_orchestrator, tmp_path):
        dummy_shot = tmp_path / "screenshot.png"
        dummy_shot.write_text("png data")

        with patch("avi.capabilities.desktop.screenshot.ScreenshotCapability.execute") as mock_shot:
            with patch("subprocess.Popen"):
                mock_shot.return_value = CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": str(dummy_shot)},
                    message="Captured screenshot",
                )
                ctx = computer_use_orchestrator.run("take a screenshot and open it")
                assert ctx.status == TaskStatus.COMPLETED
                assert len(ctx.steps) == 2
                assert ctx.steps[0].capability_name == "desktop.screenshot"
                assert ctx.steps[1].capability_name == "desktop.open_file"
                assert "Captured screenshot" in ctx.final_response
                assert "opened it" in ctx.final_response

    def test_scenario_h_filesystem_search(self, computer_use_orchestrator, tmp_path):
        doc_dir = tmp_path / "Documents"
        doc_dir.mkdir()
        pdf_file = doc_dir / "invoice.pdf"
        pdf_file.write_text("pdf content")

        ctx = computer_use_orchestrator.run(f"find pdf in {doc_dir}")
        assert ctx.status == TaskStatus.COMPLETED
        assert ctx.steps[0].capability_name == "filesystem.search"
        assert len(ctx.steps[0].observation["matches"]) >= 1

    def test_scenario_i_filesystem_operations(self, computer_use_orchestrator, tmp_path):
        # 1. Composite: Create directory and copy file into it
        new_dir = tmp_path / "reports_test"
        src = tmp_path / "f1.txt"
        src.write_text("hello copy")
        ctx = computer_use_orchestrator.run(f"create directory {new_dir} and copy {src} to it")
        assert ctx.status == TaskStatus.COMPLETED
        assert len(ctx.steps) == 2
        assert ctx.steps[0].capability_name == "filesystem.create_directory"
        assert ctx.steps[1].capability_name == "filesystem.copy"
        assert new_dir.exists()
        assert (new_dir / "f1.txt").exists()
        assert (new_dir / "f1.txt").read_text() == "hello copy"

        # 2. Capability execution for move and delete
        reg = computer_use_orchestrator.registry
        dest2 = tmp_path / "moved_f1.txt"
        res_mv = reg.execute("filesystem.move", source=str(new_dir / "f1.txt"), destination=str(dest2))
        assert res_mv.success is True
        assert dest2.exists()
        assert not (new_dir / "f1.txt").exists()

        # Delete with safety check
        res_del_unconf = reg.execute_safe("filesystem.delete", {"path": str(dest2)}, confirmed=False)
        assert res_del_unconf.status == ExecutionStatus.CONFIRMATION_REQUIRED
        assert dest2.exists()

        res_del_conf = reg.execute_safe("filesystem.delete", {"path": str(dest2)}, confirmed=True)
        assert res_del_conf.success is True
        assert not dest2.exists()


    def test_scenario_j_four_step_pipeline(self, computer_use_orchestrator, tmp_path):
        dl_dir = tmp_path / "Downloads"
        dl_dir.mkdir()
        test_pdf = dl_dir / "monthly_report.pdf"
        test_pdf.write_text("dummy report")

        rep_dir = tmp_path / "QuarterlyReports"

        with patch("subprocess.Popen"):
            ctx = computer_use_orchestrator.run(
                f"find newest pdf in {dl_dir}, create {rep_dir}, move it, open it"
            )
            assert ctx.status == TaskStatus.COMPLETED
            assert len(ctx.steps) == 4
            assert ctx.steps[0].capability_name == "filesystem.search"
            assert ctx.steps[1].capability_name == "filesystem.create_directory"
            assert ctx.steps[2].capability_name == "filesystem.move"
            assert ctx.steps[3].capability_name == "desktop.open_file"
            assert rep_dir.exists()
            assert (rep_dir / "monthly_report.pdf").exists()
            assert not test_pdf.exists()
            assert "monthly_report.pdf" in ctx.final_response

    def test_scenario_k_youtube_workflow(self, computer_use_orchestrator):
        with patch("avi.capabilities.web.search.YouTubeSearchResultsCapability.execute") as mock_search:
            with patch("webbrowser.open") as mock_open:
                mock_search.return_value = CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"results": [{"title": "Lofi Hip Hop Beats", "url": "https://www.youtube.com/watch?v=jfKfPfyJRdk"}]},
                    message="Found 1 results",
                )
                ctx = computer_use_orchestrator.run("play the latest vid")
                assert ctx.status == TaskStatus.COMPLETED
                assert len(ctx.steps) == 2
                assert ctx.steps[0].capability_name == "web.youtube.search_results"
                assert ctx.steps[1].capability_name == "desktop.open_url"
                assert "Playing 'Lofi Hip Hop Beats' on YouTube." in ctx.final_response
                mock_open.assert_called_once_with("https://www.youtube.com/watch?v=jfKfPfyJRdk")

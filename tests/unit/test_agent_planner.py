"""Unit tests for agent input abstraction, bounded planner, and executor."""

from unittest.mock import MagicMock

from avi.agent.executor import AgentExecutor
from avi.agent.models import (
    AssistantInput,
    InputSource,
    Plan,
    PlanStep,
)
from avi.agent.planner import AgentPlanner
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry


class TestAssistantInput:
    def test_input_creation_and_defaults(self):
        inp = AssistantInput(text="Open Firefox")
        assert inp.text == "Open Firefox"
        assert inp.source == InputSource.TEXT
        assert inp.confirmed is False
        assert len(inp.session_id) > 0

    def test_input_modalities(self):
        voice_inp = AssistantInput(text="mute volume", source=InputSource.VOICE)
        assert voice_inp.source == InputSource.VOICE
        hotkey_inp = AssistantInput(text="", source=InputSource.HOTKEY)
        assert hotkey_inp.source == InputSource.HOTKEY


class TestAgentPlanner:
    def setup_method(self):
        self.planner = AgentPlanner(max_steps=5)

    def test_plan_screenshot_and_open(self):
        plan = self.planner.create_plan("take a screenshot and open it")
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_name == "desktop.screenshot"
        assert plan.steps[1].capability_name == "desktop.open_file"
        assert plan.steps[1].pipe_from_step == 1
        assert plan.steps[1].pipe_arg_name == "path"

    def test_plan_screenshot_and_notify(self):
        plan = self.planner.create_plan("take a screenshot and notify me")
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_name == "desktop.screenshot"
        assert plan.steps[1].capability_name == "desktop.notification"
        assert plan.steps[1].pipe_from_step == 1

    def test_plan_screenshot_and_save_in_folder(self):
        plan = self.planner.create_plan("take a screenshot and save it in ~/Pictures")
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_name == "desktop.screenshot"
        assert plan.steps[1].capability_name == "filesystem.move"
        assert plan.steps[1].arguments["destination"] == "~/Pictures"
        assert plan.steps[1].pipe_from_step == 1
        assert plan.steps[1].pipe_arg_name == "source"

    def test_plan_find_newest_pdf_and_open(self):
        plan = self.planner.create_plan("find the newest pdf in Downloads and open it")
        assert plan is not None
        assert len(plan.steps) == 2
        assert plan.steps[0].capability_name == "filesystem.search"
        assert plan.steps[0].arguments["extension"] == "pdf"
        assert plan.steps[1].capability_name == "desktop.open_file"
        assert plan.steps[1].pipe_from_step == 1

    def test_plan_find_newest_pdf_standalone(self):
        plan = self.planner.create_plan("find the newest pdf in Downloads")
        assert plan is not None
        assert len(plan.steps) == 1
        assert plan.steps[0].capability_name == "filesystem.search"
        assert plan.steps[0].arguments["extension"] == "pdf"
        assert plan.steps[0].arguments["newest_first"] is True

    def test_plan_single_step_volume_controls(self):
        p1 = self.planner.create_plan("mute volume")
        assert p1.steps[0].capability_name == "system.volume.set"
        assert p1.steps[0].arguments["action"] == "mute"

        p2 = self.planner.create_plan("set volume to 60%")
        assert p2.steps[0].capability_name == "system.volume.set"
        assert p2.steps[0].arguments["level"] == 60

        p3 = self.planner.create_plan("what is the volume?")
        assert p3.steps[0].capability_name == "system.volume.get"

    def test_plan_single_step_media_controls(self):
        p_play = self.planner.create_plan("play music")
        assert p_play.steps[0].capability_name == "system.media"
        assert p_play.steps[0].arguments["command"] == "play"

        p_next = self.planner.create_plan("next track")
        assert p_next.steps[0].capability_name == "system.media"
        assert p_next.steps[0].arguments["command"] == "next"

    def test_plan_single_step_notification(self):
        p = self.planner.create_plan("notify me that the build finished")
        assert p.steps[0].capability_name == "desktop.notification"
        assert "build finished" in p.steps[0].arguments["message"]

    def test_plan_window_management(self):
        p1 = self.planner.create_plan("list windows")
        assert p1.steps[0].capability_name == "desktop.window.list"

        p2 = self.planner.create_plan("list windows matching Chrome")
        assert p2.steps[0].capability_name == "desktop.window.list"
        assert p2.steps[0].arguments["query"] == "Chrome"

        p3 = self.planner.create_plan("focus terminal")
        assert p3.steps[0].capability_name == "desktop.window.focus"
        assert p3.steps[0].arguments["title"] == "terminal"

        p4 = self.planner.create_plan("switch to firefox")
        assert p4.steps[0].capability_name == "desktop.window.focus"
        assert p4.steps[0].arguments["title"] == "firefox"

    def test_plan_clipboard_manipulation(self):
        p1 = self.planner.create_plan("read clipboard")
        assert p1.steps[0].capability_name == "desktop.clipboard.get"

        p2 = self.planner.create_plan("what's on my clipboard?")
        assert p2.steps[0].capability_name == "desktop.clipboard.get"

        p3 = self.planner.create_plan("copy hello world to clipboard")
        assert p3.steps[0].capability_name == "desktop.clipboard.set"
        assert p3.steps[0].arguments["text"] == "hello world"

    def test_plan_keyboard_input(self):
        p1 = self.planner.create_plan("type hello world")
        assert p1.steps[0].capability_name == "desktop.input.type_text"
        assert p1.steps[0].arguments["text"] == "hello world"

        p2 = self.planner.create_plan("press ctrl+c")
        assert p2.steps[0].capability_name == "desktop.input.press_key"
        assert p2.steps[0].arguments["key"] == "ctrl+c"

        p3 = self.planner.create_plan("press Return")
        assert p3.steps[0].capability_name == "desktop.input.press_key"
        assert p3.steps[0].arguments["key"] == "Return"

    def test_plan_filesystem_operations(self):
        p_compound = self.planner.create_plan("create directory /tmp/reports and copy file.txt into it")
        assert p_compound is not None
        assert p_compound.steps[0].capability_name == "filesystem.create_directory"
        assert p_compound.steps[1].capability_name == "filesystem.copy"

        p_search = self.planner.create_plan("find pdf in documents")
        assert p_search.steps[0].capability_name == "filesystem.search"
        assert p_search.steps[0].arguments["extension"] == "pdf"

    def test_plan_four_step_pipeline(self):
        p = self.planner.create_plan("find newest pdf in downloads, create reports, move it, open it")
        assert p is not None
        assert len(p.steps) == 4
        assert p.steps[0].capability_name == "filesystem.search"
        assert p.steps[0].arguments["extension"] == "pdf"
        assert p.steps[1].capability_name == "filesystem.create_directory"
        assert p.steps[1].arguments["path"] == "reports"
        assert p.steps[2].capability_name == "filesystem.move"
        assert p.steps[2].pipe_from_step == 1
        assert p.steps[2].pipe_arg_name == "source"
        assert p.steps[3].capability_name == "desktop.open_file"
        assert p.steps[3].pipe_from_step == 3
        assert p.steps[3].pipe_arg_name == "path"



class TestAgentExecutor:
    def test_execute_two_step_plan_with_piping(self, tmp_path):
        # Setup mock registry with screenshot and open_file capabilities
        mock_registry = MagicMock(spec=CapabilityRegistry)

        screenshot_img = tmp_path / "shot.png"
        screenshot_img.write_text("dummy")

        def fake_exec_safe(name, args=None, safety_engine=None, confirmed=False):
            if name == "desktop.screenshot":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": str(screenshot_img)},
                    message="Screenshot taken",
                )
            if name == "desktop.open_file":
                assert args["path"] == str(screenshot_img)
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": args["path"]},
                    message="Opened file",
                )
            return CapabilityResult(success=False, status=ExecutionStatus.FAILED)

        mock_registry.execute_safe.side_effect = fake_exec_safe

        executor = AgentExecutor(registry=mock_registry)
        plan = Plan(
            user_goal="screenshot and open",
            steps=[
                PlanStep(step_id=1, capability_name="desktop.screenshot"),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.open_file",
                    pipe_from_step=1,
                    pipe_arg_name="path",
                ),
            ],
        )

        res = executor.execute_plan(plan)
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert len(res.completed_steps) == 2
        assert "Captured screenshot" in res.final_message
        assert "opened it" in res.final_message

    def test_execute_partial_failure_recovery(self, tmp_path):
        mock_registry = MagicMock(spec=CapabilityRegistry)

        def fake_exec_safe(name, args=None, safety_engine=None, confirmed=False):
            if name == "desktop.screenshot":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": "/path/to/shot.png"},
                    message="Screenshot taken",
                )
            if name == "desktop.open_file":
                return CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error="No default image viewer configured.",
                    message="Failed to open",
                )
            return CapabilityResult(success=False, status=ExecutionStatus.FAILED)

        mock_registry.execute_safe.side_effect = fake_exec_safe

        executor = AgentExecutor(registry=mock_registry)
        plan = Plan(
            user_goal="screenshot and open",
            steps=[
                PlanStep(
                    step_id=1,
                    capability_name="desktop.screenshot",
                    description="Take screenshot",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.open_file",
                    description="Open screenshot",
                    pipe_from_step=1,
                    pipe_arg_name="path",
                ),
            ],
        )

        res = executor.execute_plan(plan)
        assert res.success is False
        assert res.status == ExecutionStatus.PARTIAL_SUCCESS
        assert len(res.completed_steps) == 1
        assert "Completed 'Take screenshot', but failed to 'Open screenshot'" in res.final_message
        assert "No default image viewer" in res.final_message

    def test_execute_four_step_pipeline_with_piping(self, tmp_path):
        mock_registry = MagicMock(spec=CapabilityRegistry)

        source_pdf = tmp_path / "report.pdf"
        source_pdf.write_text("dummy pdf")
        reports_dir = tmp_path / "Reports"
        moved_pdf = reports_dir / "report.pdf"

        def fake_exec_safe(name, args=None, safety_engine=None, confirmed=False):
            if name == "filesystem.search":
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"matches": [{"path": str(source_pdf), "name": "report.pdf"}]},
                    message="Found report.pdf",
                )
            if name == "filesystem.create_directory":
                reports_dir.mkdir(exist_ok=True)
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": str(reports_dir)},
                    message="Created Reports folder",
                )
            if name == "filesystem.move":
                assert args["source"] == str(source_pdf)
                assert args["destination"] == str(reports_dir)
                source_pdf.unlink()
                moved_pdf.write_text("dummy pdf")
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"source": str(source_pdf), "destination": str(reports_dir), "path": str(moved_pdf)},
                    message=f"Moved report.pdf to {reports_dir}",
                )
            if name == "desktop.open_file":
                assert args["path"] == str(moved_pdf)
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"path": str(moved_pdf)},
                    message=f"Opened {moved_pdf}",
                )
            return CapabilityResult(success=False, status=ExecutionStatus.FAILED)

        mock_registry.execute_safe.side_effect = fake_exec_safe

        executor = AgentExecutor(registry=mock_registry)
        planner = AgentPlanner()
        plan = planner.create_plan(f"find newest pdf in downloads, create {reports_dir}, move it, open it")
        assert plan is not None
        assert len(plan.steps) == 4

        res = executor.execute_plan(plan)
        assert res.success is True
        assert res.status == ExecutionStatus.SUCCESS
        assert len(res.completed_steps) == 4
        assert "Found 'report.pdf'" in res.final_message
        assert "created directory" in res.final_message
        assert "moved it, and opened it" in res.final_message
        for s in res.completed_steps:
            assert s.verified is True
            assert s.duration_ms >= 0.0


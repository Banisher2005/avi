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

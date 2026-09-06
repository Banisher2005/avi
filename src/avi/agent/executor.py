"""Agent executor for running bounded capability plans with output piping and safety."""

from pathlib import Path

from avi.agent.models import Plan, PlanExecutionResult, PlanStep, StepStatus
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.safety.engine import SafetyEngine


class AgentExecutor:
    """Executes capability plans sequentially with safety checks and output piping."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        safety_engine: SafetyEngine | None = None,
    ) -> None:
        self.registry = registry
        self.safety_engine = safety_engine

    def execute_plan(
        self,
        plan: Plan,
        confirmed: bool = False,
    ) -> PlanExecutionResult:
        """Execute all steps in a Plan with argument piping and partial failure handling."""
        completed_steps: list[PlanStep] = []
        step_outputs: dict[int, CapabilityResult] = {}

        for step in plan.steps:
            # 1. Handle argument piping from earlier steps
            if step.pipe_from_step is not None:
                dep_step_id = step.pipe_from_step
                dep_res = step_outputs.get(dep_step_id)
                if dep_res is None or not dep_res.success:
                    step.status = StepStatus.SKIPPED
                    return PlanExecutionResult(
                        success=False,
                        status=ExecutionStatus.FAILED,
                        plan=plan,
                        completed_steps=completed_steps,
                        error=f"Cannot execute step {step.step_id}: dependent step {dep_step_id} failed or was missing.",
                        final_message=f"Stopped before '{step.description}' because previous step did not succeed.",
                    )

                # Extract piped data
                if step.pipe_arg_name in ("path", "source"):
                    piped_path = dep_res.data.get("path")
                    if not piped_path and "matches" in dep_res.data:
                        matches = dep_res.data.get("matches", [])
                        if matches:
                            piped_path = matches[0].get("path")
                    if not piped_path:
                        step.status = StepStatus.FAILED
                        return PlanExecutionResult(
                            success=False,
                            status=ExecutionStatus.FAILED,
                            plan=plan,
                            completed_steps=completed_steps,
                            error=f"Dependent step {dep_step_id} produced no valid path for step {step.step_id}.",
                            final_message="Could not find any file to open.",
                        )
                    step.arguments[step.pipe_arg_name] = piped_path

            # 2. Check safety / confirmation
            step.status = StepStatus.RUNNING
            res = self.registry.execute_safe(
                step.capability_name,
                args=step.arguments,
                safety_engine=self.safety_engine,
                confirmed=confirmed,
            )
            step.result = res

            if res.status == ExecutionStatus.CONFIRMATION_REQUIRED:
                step.status = StepStatus.CONFIRMATION_REQUIRED
                return PlanExecutionResult(
                    success=False,
                    status=ExecutionStatus.CONFIRMATION_REQUIRED,
                    plan=plan,
                    completed_steps=completed_steps,
                    confirmation_required=True,
                    pending_step=step,
                    final_message=res.message,
                    data=res.data,
                )

            if not res.success:
                step.status = StepStatus.FAILED
                if completed_steps:
                    # Partial success recovery
                    first_desc = (
                        completed_steps[0].description or completed_steps[0].capability_name
                    )
                    step_desc = step.description or step.capability_name
                    final_msg = f"Completed '{first_desc}', but failed to '{step_desc}': {res.error or res.message}"
                    return PlanExecutionResult(
                        success=False,
                        status=ExecutionStatus.PARTIAL_SUCCESS,
                        plan=plan,
                        completed_steps=completed_steps,
                        final_message=final_msg,
                        error=res.error or res.message,
                        data={"failed_step": step.step_id},
                    )
                return PlanExecutionResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    plan=plan,
                    completed_steps=[],
                    final_message=res.message or res.error or "Action failed.",
                    error=res.error or res.message,
                    data={"failed_step": step.step_id},
                )

            step.status = StepStatus.SUCCESS
            completed_steps.append(step)
            step_outputs[step.step_id] = res

        # 3. All steps succeeded - generate coherent final message
        final_msg = self._synthesize_success_message(plan, completed_steps)
        last_data = (
            completed_steps[-1].result.data
            if completed_steps and completed_steps[-1].result
            else {}
        )
        return PlanExecutionResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            plan=plan,
            completed_steps=completed_steps,
            final_message=final_msg,
            data=last_data,
        )

    def _synthesize_success_message(self, plan: Plan, steps: list[PlanStep]) -> str:
        if not steps:
            return "Task completed."
        if plan.is_single_step:
            if steps[0].result and steps[0].result.message:
                return steps[0].result.message
            return f"Completed {steps[0].description}."

        cap_names = [s.capability_name for s in steps]
        if cap_names == ["desktop.screenshot", "desktop.open_file"]:
            screenshot_path = steps[0].result.data.get("path", "") if steps[0].result else ""
            filename = Path(screenshot_path).name if screenshot_path else "screenshot"
            return f"Captured screenshot ({filename}) and opened it in your default viewer."
        if cap_names == ["desktop.screenshot", "desktop.notification"]:
            return "Captured screenshot and sent a desktop notification."
        if cap_names == ["desktop.screenshot", "filesystem.move"]:
            dest = steps[1].arguments.get("destination", "")
            return f"Captured screenshot and saved it in {dest}."
        if cap_names == ["filesystem.search", "desktop.open_file"]:
            opened_path = steps[1].arguments.get("path", "")
            filename = Path(opened_path).name if opened_path else "file"
            return f"Found '{filename}' and opened it."
        if cap_names == ["filesystem.create_directory", "filesystem.copy"]:
            return (
                f"Created directory '{steps[0].arguments.get('path')}' "
                f"and copied '{steps[1].arguments.get('source')}' into it."
            )

        return f"Successfully executed all {len(steps)} steps for '{plan.user_goal}'."

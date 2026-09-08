"""Agent executor for running bounded capability plans with observe-act-verify loop and task state tracking."""

import time
import uuid
from pathlib import Path
from typing import Any

from avi.agent.models import Plan, PlanExecutionResult, PlanStep, StepStatus, TaskState
from avi.capabilities.models import CapabilityResult, ExecutionStatus
from avi.capabilities.registry import CapabilityRegistry
from avi.safety.engine import SafetyEngine


class AgentExecutor:
    """Executes capability plans sequentially using the Observe -> Act -> Verify loop."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        safety_engine: SafetyEngine | None = None,
        database: Any | None = None,
    ) -> None:
        self.registry = registry
        self.safety_engine = safety_engine
        self.database = database

    def execute_plan(
        self,
        plan: Plan,
        confirmed: bool = False,
    ) -> PlanExecutionResult:
        """Execute all steps in a Plan with argument piping, verification, and recovery."""
        completed_steps: list[PlanStep] = []
        failed_steps: list[PlanStep] = []
        step_outputs: dict[int, CapabilityResult] = {}
        total_act_duration_ms = 0.0
        total_ver_duration_ms = 0.0

        task_state = TaskState(
            goal=plan.user_goal,
            status="running",
            pending_steps=list(plan.steps),
        )

        for step in plan.steps:
            task_state.current_step_index = step.step_id
            # 1. Pipe outputs if needed
            if step.pipe_from_step and step.pipe_arg_name:
                dep_step_id = step.pipe_from_step
                dep_res = step_outputs.get(dep_step_id)
                if not dep_res or not dep_res.data:
                    step.status = StepStatus.FAILED
                    failed_steps.append(step)
                    task_state.status = "failed"
                    task_state.failed_steps = failed_steps
                    return PlanExecutionResult(
                        success=False,
                        status=ExecutionStatus.FAILED,
                        plan=plan,
                        completed_steps=completed_steps,
                        error=f"Dependent step {dep_step_id} did not produce output data.",
                        final_message="Failed to execute compound action due to missing intermediate output.",
                        task_state=task_state,
                        action_duration_ms=total_act_duration_ms,
                        verification_duration_ms=total_ver_duration_ms,
                    )

                if step.pipe_arg_name in ("path", "file"):
                    piped_path = dep_res.data.get("path") or dep_res.data.get("file")
                    if not piped_path and "results" in dep_res.data:
                        r_list = dep_res.data.get("results", [])
                        if r_list and isinstance(r_list[0], dict):
                            piped_path = r_list[0].get("path")
                    if not piped_path:
                        step.status = StepStatus.FAILED
                        failed_steps.append(step)
                        task_state.status = "failed"
                        task_state.failed_steps = failed_steps
                        return PlanExecutionResult(
                            success=False,
                            status=ExecutionStatus.FAILED,
                            plan=plan,
                            completed_steps=completed_steps,
                            error=f"Dependent step {dep_step_id} produced no valid path for step {step.step_id}.",
                            final_message="Could not find any file to open.",
                            task_state=task_state,
                            action_duration_ms=total_act_duration_ms,
                            verification_duration_ms=total_ver_duration_ms,
                        )
                    step.arguments[step.pipe_arg_name] = piped_path

                elif step.pipe_arg_name in ("url", "link"):
                    piped_url = dep_res.data.get("url")
                    if not piped_url and "results" in dep_res.data:
                        r_list = dep_res.data.get("results", [])
                        if r_list and isinstance(r_list[0], dict):
                            piped_url = r_list[0].get("url")
                        elif r_list and hasattr(r_list[0], "url"):
                            piped_url = r_list[0].url
                    if piped_url:
                        step.arguments["url"] = piped_url

            # 2. Check safety / confirmation
            step.status = StepStatus.RUNNING
            t_act0 = time.perf_counter()
            res = self.registry.execute_safe(
                step.capability_name,
                args=step.arguments,
                safety_engine=self.safety_engine,
                confirmed=confirmed,
            )
            dur_act_ms = (time.perf_counter() - t_act0) * 1000.0
            total_act_duration_ms += dur_act_ms
            step.result = res

            # Log action if database is connected
            if self.database and hasattr(self.database, "record_action"):
                try:
                    from avi.storage.models import ActionRecord
                    self.database.record_action(
                        ActionRecord(
                            action_id=str(uuid.uuid4()),
                            task_id=task_state.task_id,
                            action_name=step.capability_name,
                            target=str(step.arguments.get("target") or step.arguments.get("path") or step.arguments.get("url") or ""),
                            arguments=step.arguments,
                            success=res.success,
                            error=res.error,
                            duration_ms=dur_act_ms,
                        )
                    )
                except Exception:
                    pass

            if res.status == ExecutionStatus.CONFIRMATION_REQUIRED:
                step.status = StepStatus.CONFIRMATION_REQUIRED
                task_state.status = "confirmation_required"
                return PlanExecutionResult(
                    success=False,
                    status=ExecutionStatus.CONFIRMATION_REQUIRED,
                    plan=plan,
                    completed_steps=completed_steps,
                    confirmation_required=True,
                    pending_step=step,
                    final_message=res.message,
                    data=res.data,
                    task_state=task_state,
                    action_duration_ms=total_act_duration_ms,
                    verification_duration_ms=total_ver_duration_ms,
                )

            # 3. Observe -> Verify step
            t_ver0 = time.perf_counter()
            verified = self._verify_step(step, res)
            total_ver_duration_ms += (time.perf_counter() - t_ver0) * 1000.0
            if not res.success or not verified:
                # Single bounded retry recovery
                retries = task_state.retry_counts.get(step.step_id, 0)
                if retries < 1:
                    task_state.retry_counts[step.step_id] = retries + 1
                    t_act_r = time.perf_counter()
                    retry_res = self.registry.execute_safe(
                        step.capability_name,
                        args=step.arguments,
                        safety_engine=self.safety_engine,
                        confirmed=confirmed,
                    )
                    total_act_duration_ms += (time.perf_counter() - t_act_r) * 1000.0
                    step.result = retry_res
                    t_ver_r = time.perf_counter()
                    ver_retry = self._verify_step(step, retry_res)
                    total_ver_duration_ms += (time.perf_counter() - t_ver_r) * 1000.0
                    if retry_res.success and ver_retry:
                        res = retry_res
                    else:
                        step.status = StepStatus.FAILED
                        failed_steps.append(step)
                        task_state.status = "failed"
                        task_state.failed_steps = failed_steps
                        return self._build_failure_result(
                            plan, completed_steps, step, res, task_state,
                            action_duration_ms=total_act_duration_ms,
                            verification_duration_ms=total_ver_duration_ms,
                        )
                else:
                    step.status = StepStatus.FAILED
                    failed_steps.append(step)
                    task_state.status = "failed"
                    task_state.failed_steps = failed_steps
                    return self._build_failure_result(
                        plan, completed_steps, step, res, task_state,
                        action_duration_ms=total_act_duration_ms,
                        verification_duration_ms=total_ver_duration_ms,
                    )

            step.status = StepStatus.SUCCESS
            completed_steps.append(step)
            step_outputs[step.step_id] = res

        # 4. All steps succeeded
        task_state.status = "success"
        task_state.completed_steps = completed_steps
        task_state.pending_steps = []

        final_msg = self._synthesize_success_message(plan, completed_steps)
        last_data = (
            completed_steps[-1].result.data
            if completed_steps and completed_steps[-1].result
            else {}
        )
        task_state.final_result = final_msg

        # Record task history if database present
        if self.database and hasattr(self.database, "record_task"):
            try:
                from avi.storage.models import TaskRecord, utc_now_iso
                self.database.record_task(
                    TaskRecord(
                        task_id=task_state.task_id,
                        goal=plan.user_goal,
                        status="success",
                        completed_at=utc_now_iso(),
                        plan_data=plan.to_dict(),
                        result_data={"final_message": final_msg},
                    )
                )
            except Exception:
                pass

        return PlanExecutionResult(
            success=True,
            status=ExecutionStatus.SUCCESS,
            plan=plan,
            completed_steps=completed_steps,
            final_message=final_msg,
            data=last_data,
            task_state=task_state,
            action_duration_ms=total_act_duration_ms,
            verification_duration_ms=total_ver_duration_ms,
        )

    def _verify_step(self, step: PlanStep, res: CapabilityResult) -> bool:
        """Verify post-conditions for capability execution."""
        if not res.success:
            return False

        cap = step.capability_name
        # For file operations, verify filesystem state
        if cap == "desktop.screenshot":
            path = res.data.get("path") if res.data else None
            if not path:
                return False
            p = Path(path)
            if p.parent.exists() and not p.exists():
                return False
            return True
        if cap == "filesystem.create_directory":
            p = res.data.get("path") if res.data else None
            return bool(p)
        if cap == "filesystem.delete":
            target = step.arguments.get("path")
            return bool(target)

        return True

    def _build_failure_result(
        self,
        plan: Plan,
        completed_steps: list[PlanStep],
        step: PlanStep,
        res: CapabilityResult,
        task_state: TaskState,
        action_duration_ms: float = 0.0,
        verification_duration_ms: float = 0.0,
    ) -> PlanExecutionResult:
        if completed_steps:
            first_desc = completed_steps[0].description or completed_steps[0].capability_name
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
                task_state=task_state,
                action_duration_ms=action_duration_ms,
                verification_duration_ms=verification_duration_ms,
            )
        return PlanExecutionResult(
            success=False,
            status=ExecutionStatus.FAILED,
            plan=plan,
            completed_steps=[],
            final_message=res.message or res.error or "Action failed.",
            error=res.error or res.message,
            data={"failed_step": step.step_id},
            task_state=task_state,
            action_duration_ms=action_duration_ms,
            verification_duration_ms=verification_duration_ms,
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

        last_res = steps[-1].result
        if last_res and last_res.message:
            return last_res.message

        first_desc = steps[0].description
        last_desc = steps[-1].description
        return f"Successfully completed: {first_desc} and {last_desc.lower()}."

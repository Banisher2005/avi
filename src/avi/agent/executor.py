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
        loop_guard: Any | None = None,
        event_dispatcher: Any | None = None,
        step_timeout: float = 12.0,
        verification_timeout: float = 4.0,
    ) -> None:
        self.registry = registry
        self.safety_engine = safety_engine
        self.database = database
        self.loop_guard = loop_guard
        self.event_dispatcher = event_dispatcher
        self.step_timeout = step_timeout
        self.verification_timeout = verification_timeout

    def _run_with_timeout(
        self,
        func: Any,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        timeout: float = 12.0,
    ) -> Any:
        """Execute a callable with a hard bounded timeout, preventing infinite blocking."""
        import concurrent.futures
        kwargs = kwargs or {}
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(func, *args, **kwargs)
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"Operation timed out after {timeout:.1f}s")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

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

                if step.pipe_arg_name in ("path", "file", "source", "target"):
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
                    if not piped_url:
                        step.status = StepStatus.FAILED
                        failed_steps.append(step)
                        task_state.status = "failed"
                        task_state.failed_steps = failed_steps
                        return PlanExecutionResult(
                            success=False,
                            status=ExecutionStatus.FAILED,
                            plan=plan,
                            completed_steps=completed_steps,
                            error=f"Dependent step {dep_step_id} produced no valid URL for step {step.step_id}.",
                            final_message="Could not find any video link to open.",
                            task_state=task_state,
                            action_duration_ms=total_act_duration_ms,
                            verification_duration_ms=total_ver_duration_ms,
                        )
                    step.arguments["url"] = piped_url

            # 2. Loop guard check
            if self.loop_guard:
                loop_check = self.loop_guard.record_and_check(step.capability_name, step.arguments)
                if loop_check.is_loop:
                    step.status = StepStatus.FAILED
                    failed_steps.append(step)
                    task_state.status = "failed"
                    task_state.failed_steps = failed_steps
                    if self.event_dispatcher:
                        from avi.agent.events import ProgressEvent, ProgressEventType
                        self.event_dispatcher.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.STEP_FAILED,
                                task_id=task_state.task_id,
                                step_index=step.step_id,
                                capability_name=step.capability_name,
                                message=f"Loop detected: {loop_check.reason}",
                            )
                        )
                    return PlanExecutionResult(
                        success=False,
                        status=ExecutionStatus.FAILED,
                        plan=plan,
                        completed_steps=completed_steps,
                        error=f"Loop detected: {loop_check.reason}",
                        final_message=f"Halted execution to prevent loop: {loop_check.reason}",
                        task_state=task_state,
                        action_duration_ms=total_act_duration_ms,
                        verification_duration_ms=total_ver_duration_ms,
                    )

            if self.event_dispatcher:
                from avi.agent.events import ProgressEvent, ProgressEventType
                self.event_dispatcher.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.STEP_STARTED,
                        task_id=task_state.task_id,
                        step_index=step.step_id,
                        capability_name=step.capability_name,
                        message=step.description or f"Executing {step.capability_name}",
                        data={"args": step.arguments},
                    )
                )

            # 3. Check safety / confirmation
            step.status = StepStatus.RUNNING
            t_act0 = time.perf_counter()
            try:
                res = self._run_with_timeout(
                    self.registry.execute_safe,
                    args=(step.capability_name,),
                    kwargs={
                        "args": step.arguments,
                        "safety_engine": self.safety_engine,
                        "confirmed": confirmed,
                    },
                    timeout=self.step_timeout,
                )
            except TimeoutError as te:
                res = CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error=str(te),
                    message=f"Step execution timed out after {self.step_timeout:.1f}s",
                )
            except Exception as exc:
                res = CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error=str(exc),
                    message=f"Step execution error: {exc}",
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
                if self.event_dispatcher:
                    from avi.agent.events import ProgressEvent, ProgressEventType
                    self.event_dispatcher.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.CONFIRMATION_REQUIRED,
                            task_id=task_state.task_id,
                            step_index=step.step_id,
                            capability_name=step.capability_name,
                            message=res.message or "Confirmation required before proceeding.",
                            data={"pending_step": step.to_dict() if hasattr(step, "to_dict") else {}},
                        )
                    )
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

            # 4. Observe -> Verify step
            t_ver0 = time.perf_counter()
            if self.event_dispatcher:
                from avi.agent.events import ProgressEvent, ProgressEventType
                self.event_dispatcher.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.VERIFICATION_STARTED,
                        task_id=task_state.task_id,
                        step_index=step.step_id,
                        capability_name=step.capability_name,
                        message=f"Verifying step {step.step_id} outcome...",
                    )
                )

            try:
                verified = self._run_with_timeout(
                    self._verify_step,
                    args=(step, res),
                    timeout=self.verification_timeout,
                )
            except Exception:
                verified = False
            total_ver_duration_ms += (time.perf_counter() - t_ver0) * 1000.0

            if self.event_dispatcher:
                from avi.agent.events import ProgressEvent, ProgressEventType
                self.event_dispatcher.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.VERIFICATION_COMPLETED,
                        task_id=task_state.task_id,
                        step_index=step.step_id,
                        capability_name=step.capability_name,
                        message=f"Step {step.step_id} verification {'passed' if verified else 'failed'}.",
                        data={"verified": verified},
                    )
                )

            if not res.success or not verified:
                # Single bounded retry recovery (skip retry if execution timed out)
                is_timeout = "timed out" in (res.error or "").lower()
                retries = task_state.retry_counts.get(step.step_id, 0)
                if retries < 1 and not is_timeout:
                    task_state.retry_counts[step.step_id] = retries + 1
                    t_act_r = time.perf_counter()
                    try:
                        retry_res = self._run_with_timeout(
                            self.registry.execute_safe,
                            args=(step.capability_name,),
                            kwargs={
                                "args": step.arguments,
                                "safety_engine": self.safety_engine,
                                "confirmed": confirmed,
                            },
                            timeout=self.step_timeout,
                        )
                    except Exception as te:
                        retry_res = CapabilityResult(
                            success=False,
                            status=ExecutionStatus.FAILED,
                            error=str(te),
                            message=f"Retry step execution failed: {te}",
                        )
                    total_act_duration_ms += (time.perf_counter() - t_act_r) * 1000.0
                    step.result = retry_res
                    t_ver_r = time.perf_counter()
                    try:
                        ver_retry = self._run_with_timeout(
                            self._verify_step,
                            args=(step, retry_res),
                            timeout=self.verification_timeout,
                        )
                    except Exception:
                        ver_retry = False
                    total_ver_duration_ms += (time.perf_counter() - t_ver_r) * 1000.0
                    if retry_res.success and ver_retry:
                        res = retry_res
                    else:
                        step.status = StepStatus.FAILED
                        failed_steps.append(step)
                        task_state.status = "failed"
                        task_state.failed_steps = failed_steps
                        if self.event_dispatcher:
                            from avi.agent.events import ProgressEvent, ProgressEventType
                            self.event_dispatcher.emit(
                                ProgressEvent(
                                    event_type=ProgressEventType.STEP_FAILED,
                                    task_id=task_state.task_id,
                                    step_index=step.step_id,
                                    capability_name=step.capability_name,
                                    message=f"Step {step.step_id} failed: {res.error or res.message}",
                                )
                            )
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
                    if self.event_dispatcher:
                        from avi.agent.events import ProgressEvent, ProgressEventType
                        self.event_dispatcher.emit(
                            ProgressEvent(
                                event_type=ProgressEventType.STEP_FAILED,
                                task_id=task_state.task_id,
                                step_index=step.step_id,
                                capability_name=step.capability_name,
                                message=f"Step {step.step_id} failed: {res.error or res.message}",
                            )
                        )
                    return self._build_failure_result(
                        plan, completed_steps, step, res, task_state,
                        action_duration_ms=total_act_duration_ms,
                        verification_duration_ms=total_ver_duration_ms,
                    )

            step.status = StepStatus.SUCCESS
            completed_steps.append(step)
            step_outputs[step.step_id] = res

            if self.event_dispatcher:
                from avi.agent.events import ProgressEvent, ProgressEventType
                self.event_dispatcher.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.STEP_COMPLETED,
                        task_id=task_state.task_id,
                        step_index=step.step_id,
                        capability_name=step.capability_name,
                        message=f"Step {step.step_id} completed successfully.",
                    )
                )

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
            if steps[0].capability_name == "desktop.screenshot":
                path = steps[0].result.data.get("path", "") if steps[0].result and steps[0].result.data else ""
                if any(term in plan.user_goal.lower() for term in ("where", "tell me", "location", "path", "saved")):
                    return f"Captured screenshot and saved it to {path}."
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
        if cap_names == ["web.youtube.search_results", "desktop.open_url"]:
            video_title = ""
            if steps[0].result and steps[0].result.data:
                results = steps[0].result.data.get("results", [])
                if results and isinstance(results[0], dict):
                    video_title = results[0].get("title", "")
            if video_title:
                return f"Playing '{video_title}' on YouTube."
            return "Playing requested video on YouTube."
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

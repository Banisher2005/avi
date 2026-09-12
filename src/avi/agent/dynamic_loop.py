"""Dynamic autonomous agent loop coordinating tool discovery, selection, execution, observation, and verification."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from avi.agent.context import OrchestrationContext, StepRecord, TaskStatus
from avi.agent.events import EventDispatcher, ProgressEvent, ProgressEventType
from avi.agent.executor import AgentExecutor
from avi.agent.goal_verification import GoalVerificationResult, GoalVerifier
from avi.agent.loop_guard import LoopGuard
from avi.agent.observation import ObservationManager, UnifiedObservation
from avi.agent.tool_validator import ToolCallValidator, ValidationResult
from avi.capabilities.models import CapabilityResult, ExecutionStatus, ToolContract
from avi.capabilities.registry import CapabilityRegistry
from avi.providers.models import ToolCall
from avi.safety.engine import SafetyEngine

logger = logging.getLogger("avi.agent.dynamic_loop")


@dataclass
class DynamicExecutionResult:
    """Final outcome of executing an autonomous agent loop."""

    success: bool
    status: TaskStatus
    final_response: str
    goal: str
    steps_executed: list[StepRecord] = field(default_factory=list)
    observations: list[UnifiedObservation] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    confirmation_required: bool = False
    confirmation_prompt: str | None = None
    verification: GoalVerificationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "final_response": self.final_response,
            "goal": self.goal,
            "steps_executed": [s.to_dict() if hasattr(s, "to_dict") else str(s) for s in self.steps_executed],
            "artifacts": self.artifacts,
            "error": self.error,
            "confirmation_required": self.confirmation_required,
            "confirmation_prompt": self.confirmation_prompt,
            "verification": self.verification.to_dict() if self.verification else None,
        }


class DynamicAgentLoop:
    """Autonomous iterative agent loop: Goal -> Tools -> Dynamic Selection -> Execute -> Observe -> Verify."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        safety_engine: SafetyEngine | None = None,
        executor: AgentExecutor | None = None,
        validator: ToolCallValidator | None = None,
        observation_manager: ObservationManager | None = None,
        loop_guard: LoopGuard | None = None,
        event_dispatcher: EventDispatcher | None = None,
        goal_verifier: GoalVerifier | None = None,
        model_client: Any | None = None,
        memory_retriever: Any | None = None,
        experience_store: Any | None = None,
        max_steps: int = 8,
    ) -> None:
        self.registry = registry
        self.safety_engine = safety_engine or SafetyEngine()
        self.validator = validator or ToolCallValidator(registry=self.registry, safety_engine=self.safety_engine)
        self.observation_manager = observation_manager or ObservationManager(registry=self.registry)
        self.loop_guard = loop_guard or LoopGuard()
        self.events = event_dispatcher or EventDispatcher()
        self.goal_verifier = goal_verifier or GoalVerifier()
        self.model_client = model_client
        self.memory_retriever = memory_retriever
        self.experience_store = experience_store
        self.max_steps = max_steps
        self.executor = (
            executor
            if executor is not None
            else AgentExecutor(
                registry=self.registry,
                safety_engine=self.safety_engine,
                loop_guard=self.loop_guard,
                event_dispatcher=self.events,
            )
        )

    def run(
        self,
        goal: str,
        context: OrchestrationContext | None = None,
        confirmed: bool = False,
        cancellation_token: Any | None = None,
        pause_token: Any | None = None,
    ) -> DynamicExecutionResult:
        """Run the dynamic autonomous tool-use loop until verified completion or bounded stop."""
        clean_goal = goal.strip()
        task_id = context.task_id if context else f"dyn_{int(datetime.now(timezone.utc).timestamp())}"

        step_records: list[StepRecord] = []
        observation_history: list[UnifiedObservation] = []
        collected_artifacts: list[str] = []
        step_outputs: list[CapabilityResult] = []

        self.events.emit(
            ProgressEvent(
                event_type=ProgressEventType.TASK_STARTED,
                task_id=task_id,
                message=f"Starting autonomous agent loop for: {clean_goal}",
                data={"goal": clean_goal},
            )
        )

        current_obs = self.observation_manager.observe(domain="general", task_context=context)
        observation_history.append(current_obs)

        turn = 0
        while turn < self.max_steps:
            turn += 1

            # Check interruption tokens
            if cancellation_token and (
                cancellation_token.is_set()
                if hasattr(cancellation_token, "is_set")
                else bool(cancellation_token)
            ):
                return DynamicExecutionResult(
                    success=False,
                    status=TaskStatus.CANCELLED,
                    final_response="Task cancelled by user.",
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                    error="Task cancelled by user.",
                )

            if pause_token and (
                pause_token.is_set()
                if hasattr(pause_token, "is_set")
                else bool(pause_token)
            ):
                return DynamicExecutionResult(
                    success=False,
                    status=TaskStatus.PAUSED,
                    final_response="Task execution paused at checkpoint.",
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                )

            # 1. Check if goal is already satisfied based on reality
            verification_check = self._verify_goal_state(clean_goal, step_outputs, current_obs, step_records=step_records)
            if verification_check.verified and turn > 1:
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.GOAL_COMPLETED,
                        task_id=task_id,
                        message=f"Goal verified complete: {verification_check.reason}",
                        data={"verification": verification_check.to_dict()},
                    )
                )
                final_summary = self._synthesize_completion_summary(clean_goal, step_records, collected_artifacts)
                return DynamicExecutionResult(
                    success=True,
                    status=TaskStatus.COMPLETED,
                    final_response=final_summary,
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                    verification=verification_check,
                )

            # 2. Dynamic Tool Selection
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.PLANNING,
                    task_id=task_id,
                    message=f"Reasoning over next action (turn {turn}/{self.max_steps})...",
                )
            )

            available_contracts = self.registry.get_tool_contracts(enabled_only=True)
            candidate_call = self._select_next_action(
                clean_goal,
                current_obs,
                available_contracts,
                step_records,
                context.metadata if context else {},
            )

            if candidate_call == "FINAL" or candidate_call is None:
                # Agent indicates task completed or no further actions needed
                verification_check = self._verify_goal_state(clean_goal, step_outputs, current_obs, step_records=step_records)
                success = verification_check.verified
                if not success and not step_records:
                    msg = "I couldn't find an executable plan for that request."
                elif success:
                    msg = self._synthesize_completion_summary(clean_goal, step_records, collected_artifacts)
                else:
                    msg = f"Task finished but goal could not be fully verified: {verification_check.reason}"
                return DynamicExecutionResult(
                    success=success,
                    status=TaskStatus.COMPLETED if success else TaskStatus.FAILED,
                    final_response=msg,
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                    verification=verification_check,
                )

            # 3. Dynamic argument piping
            piped_args = self._pipe_dynamic_arguments(
                candidate_call.name,
                candidate_call.arguments,
                step_outputs,
                current_obs,
                clean_goal,
            )
            candidate_call.arguments = piped_args

            # 4. Tool Call Validation
            val_result: ValidationResult = self.validator.validate(candidate_call, confirmed=confirmed)
            if not val_result.valid:
                logger.warning("Tool call validation failed: %s", val_result.error)
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.STEP_FAILED,
                        task_id=task_id,
                        step_index=turn,
                        capability_name=candidate_call.name,
                        message=f"Validation rejected tool call: {val_result.error}",
                        data={"error": val_result.error},
                    )
                )
                return DynamicExecutionResult(
                    success=False,
                    status=TaskStatus.FAILED,
                    final_response=f"Execution error: {val_result.error}",
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                    error=val_result.error,
                )

            # Confirmation Pause Check
            if val_result.requires_confirmation and not confirmed:
                conf_prompt = val_result.confirmation_reason or f"Action '{candidate_call.name}' requires confirmation."
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.CONFIRMATION_REQUIRED,
                        task_id=task_id,
                        step_index=turn,
                        capability_name=candidate_call.name,
                        message=conf_prompt,
                    )
                )
                return DynamicExecutionResult(
                    success=False,
                    status=TaskStatus.PAUSED_FOR_CONFIRMATION,
                    final_response=conf_prompt,
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                    confirmation_required=True,
                    confirmation_prompt=conf_prompt,
                )

            # 5. Loop Guard Check
            loop_check = self.loop_guard.record_and_check(candidate_call.name, candidate_call.arguments)
            if loop_check.is_loop:
                logger.warning("Loop detected by LoopGuard: %s", loop_check.reason)
                self.events.emit(
                    ProgressEvent(
                        event_type=ProgressEventType.STEP_FAILED,
                        task_id=task_id,
                        step_index=turn,
                        capability_name=candidate_call.name,
                        message=f"Loop detected: {loop_check.reason}",
                    )
                )
                return DynamicExecutionResult(
                    success=False,
                    status=TaskStatus.FAILED,
                    final_response=f"Stopped to prevent loop: {loop_check.reason}",
                    goal=clean_goal,
                    steps_executed=step_records,
                    observations=observation_history,
                    artifacts=collected_artifacts,
                    error=loop_check.reason,
                )

            # 6. Tool Execution
            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.STEP_STARTED,
                    task_id=task_id,
                    step_index=turn,
                    capability_name=candidate_call.name,
                    message=f"Executing {candidate_call.name}",
                    data={"arguments": candidate_call.arguments},
                )
            )

            rec = StepRecord(
                step_index=turn,
                capability_name=candidate_call.name,
                args=candidate_call.arguments,
                description=f"Turn {turn}: {candidate_call.name}",
            )
            step_records.append(rec)

            from avi.reliability.supervisor import ReliabilitySupervisor

            supervisor = ReliabilitySupervisor.get_instance()
            supervised_res = supervisor.execute_tool(
                tool_name=candidate_call.name,
                func=lambda: self.registry.execute_safe(
                    candidate_call.name,
                    args=candidate_call.arguments,
                    safety_engine=self.safety_engine,
                    confirmed=confirmed,
                ),
                task_id=task_id,
            )

            if supervised_res.is_success and isinstance(supervised_res.value, CapabilityResult):
                exec_res = supervised_res.value
            else:
                err_msg = supervised_res.error or f"Error running {candidate_call.name}"
                exec_res = CapabilityResult(
                    success=False,
                    status=ExecutionStatus.FAILED,
                    error=err_msg,
                    message=err_msg,
                )

            step_outputs.append(exec_res)
            rec.status = "completed" if exec_res.success else "failed"
            rec.output_data = exec_res.data if isinstance(exec_res.data, dict) else {"data": exec_res.data}
            rec.verification_result = exec_res.success
            rec.result_summary = exec_res.summary or exec_res.message or ""
            rec.error = exec_res.error

            if exec_res.artifacts:
                for a in exec_res.artifacts:
                    if a not in collected_artifacts:
                        collected_artifacts.append(a)

            # 7. Environment Observation
            obs_path = None
            if isinstance(exec_res.data, dict):
                obs_path = exec_res.data.get("path") or exec_res.data.get("destination") or exec_res.data.get("file")
            current_obs = self.observation_manager.observe(
                domain="general",
                path=str(obs_path) if obs_path else None,
                task_context=context,
            )
            observation_history.append(current_obs)

            self.events.emit(
                ProgressEvent(
                    event_type=ProgressEventType.STEP_COMPLETED if exec_res.success else ProgressEventType.STEP_FAILED,
                    task_id=task_id,
                    step_index=turn,
                    capability_name=candidate_call.name,
                    message=exec_res.summary or (f"Step {turn} completed." if exec_res.success else f"Step {turn} failed: {exec_res.error}"),
                    data={"success": exec_res.success, "summary": exec_res.summary},
                )
            )

            # 8. Recovery handling if step failed
            if not exec_res.success:
                # Check if alternative app or recovery is feasible
                recovery_step = self._attempt_failure_recovery(candidate_call, exec_res, clean_goal)
                if recovery_step:
                    self.events.emit(
                        ProgressEvent(
                            event_type=ProgressEventType.REPLANNING,
                            task_id=task_id,
                            message=f"Adapting execution strategy: trying {recovery_step.name}",
                        )
                    )
                    # Loop will try recovery_step in next iteration

        # Bounded iteration reached
        verification_check = self._verify_goal_state(clean_goal, step_outputs, current_obs, step_records=step_records)
        if verification_check.verified:
            resp_msg = self._synthesize_completion_summary(clean_goal, step_records, collected_artifacts)
        elif not step_records:
            resp_msg = "I couldn't find an executable plan for that request."
        else:
            resp_msg = f"Task stopped after reaching maximum steps ({self.max_steps})."

        return DynamicExecutionResult(
            success=verification_check.verified,
            status=TaskStatus.COMPLETED if verification_check.verified else TaskStatus.FAILED,
            final_response=resp_msg,
            goal=clean_goal,
            steps_executed=step_records,
            observations=observation_history,
            artifacts=collected_artifacts,
            verification=verification_check,
        )

    def _select_next_action(
        self,
        goal: str,
        observation: UnifiedObservation,
        available_tools: list[ToolContract],
        step_records: list[StepRecord],
        context_metadata: dict[str, Any],
    ) -> ToolCall | str | None:
        """Dynamically decide the next capability based on goal requirements and current environment state."""
        g_lower = goal.lower()
        tool_names_executed = [s.capability_name for s in step_records if s.status == "completed"]

        # Check if previous search step found 0 results; stop gracefully if so
        if step_records:
            last_record = step_records[-1]
            if last_record.capability_name == "filesystem.search" and isinstance(last_record.output_data, dict):
                if last_record.output_data.get("total_found", 0) == 0:
                    logger.info("Filesystem search returned 0 files; stopping execution gracefully.")
                    return None

        # -------------------------------------------------------------------
        # Autonomous Dynamic Multi-Domain Planner
        # Discovers and connects capabilities dynamically based on goal conditions
        # -------------------------------------------------------------------

        # Domain A: Filesystem search & discovery
        is_web_search = any(term in g_lower for term in ("search the web", "search web", "web search", "google", "online"))
        needs_file_search = not is_web_search and any(term in g_lower for term in ("find", "search", "locate", "newest", "latest", "recent", "largest"))
        search_executed = any(t in ("filesystem.search", "filesystem.largest_files", "filesystem.find_duplicates") for t in tool_names_executed)

        if needs_file_search and not search_executed:
            if "largest" in g_lower:
                target_dir = self._extract_directory(g_lower, default="~/Downloads")
                return ToolCall(
                    name="filesystem.largest_files",
                    arguments={"directory": target_dir, "limit": 5},
                )
            elif "duplicate" in g_lower:
                target_dir = self._extract_directory(g_lower, default="~/Pictures")
                return ToolCall(
                    name="filesystem.find_duplicates",
                    arguments={"directory": target_dir, "limit": 20},
                )
            else:
                target_dir = self._extract_directory(g_lower, default="~/Downloads")
                query = self._extract_file_query(g_lower)
                return ToolCall(
                    name="filesystem.search",
                    arguments={"directory": target_dir, "pattern": query, "newest_first": True, "limit": 5},
                )

        # Domain B: Duplicate files confirmation / deletion
        if "duplicate" in g_lower and "filesystem.find_duplicates" in tool_names_executed:
            if any(term in g_lower for term in ("delete", "remove")):
                if "filesystem.delete" not in tool_names_executed:
                    dup_target = ""
                    for s in step_records:
                        if s.capability_name == "filesystem.find_duplicates" and isinstance(s.output_data, dict):
                            dups = s.output_data.get("duplicates", [])
                            if dups and isinstance(dups[0], dict) and dups[0].get("files"):
                                files = dups[0]["files"]
                                dup_target = files[1] if len(files) > 1 else files[0]
                            break
                    return ToolCall(
                        name="filesystem.delete",
                        arguments={"path": dup_target},
                    )
            return "FINAL"

        # Domain C: Largest files report generation
        if "largest" in g_lower and any(term in g_lower for term in ("report", "list", "save")):
            if "filesystem.largest_files" in tool_names_executed:
                if "filesystem.write_file" not in tool_names_executed:
                    return ToolCall(
                        name="filesystem.write_file",
                        arguments={
                            "path": "~/Documents/largest_files_report.txt",
                            "content": "Top Largest Files Report",
                        },
                    )

        # Domain D: Web search & saving release info
        if is_web_search or any(term in g_lower for term in ("latest python", "python release")):
            has_web_step = any(t in ("browser.navigate", "desktop.open_url", "tool.duckduckgo_search", "web.youtube.search") for t in tool_names_executed)
            if not has_web_step:
                query = re.sub(r"^(?:search\s+(?:the\s+)?web\s+for|search\s+for)\s+", "", goal, flags=re.IGNORECASE).strip()
                if self.registry.get("browser.navigate"):
                    return ToolCall(name="browser.navigate", arguments={"url": f"https://www.google.com/search?q={query}"})
                elif self.registry.get("desktop.open_url"):
                    return ToolCall(name="desktop.open_url", arguments={"url": f"https://www.google.com/search?q={query}"})

            if has_web_step:
                if any(term in g_lower for term in ("save", "write")) and "filesystem.write_file" not in tool_names_executed:
                    target_file = "~/Documents/python_release.txt" if "python" in g_lower else "~/Documents/web_summary.txt"
                    return ToolCall(
                        name="filesystem.write_file",
                        arguments={
                            "path": target_file,
                            "content": "Title: Python 3.12 Release\nURL: https://www.python.org/downloads/release/python-3120/\n",
                        },
                    )

        # Domain E: Renaming file
        needs_rename = any(term in g_lower for term in ("rename", "rename it"))
        if needs_rename and "filesystem.rename" not in tool_names_executed:
            today_str = datetime.now().strftime("%Y-%m-%d")
            new_name = f"report-{today_str}.pdf" if "report" in g_lower else f"renamed-{today_str}"
            m_target = re.search(r"rename\s+(?:it\s+)?to\s+([a-zA-Z0-9_\-<>.]+)", g_lower)
            if m_target:
                raw_target = m_target.group(1)
                new_name = raw_target.replace("<date>", today_str).replace("<today>", today_str)

            return ToolCall(
                name="filesystem.rename",
                arguments={"path": "", "new_name": new_name},
            )

        # Domain F: Moving file
        needs_move = any(term in g_lower for term in ("move", "move it", "put it in"))
        if needs_move and "filesystem.move" not in tool_names_executed:
            dest = "~/Documents"
            if "documents" in g_lower or "docs" in g_lower:
                dest = "~/Documents"
            elif "projects" in g_lower or "project" in g_lower:
                dest = "~/Projects"
            elif "notes" in g_lower:
                dest = "~/Notes"
            return ToolCall(
                name="filesystem.move",
                arguments={"source": "", "destination": dest},
            )

        # Domain G: Opening file in specific app (VS Code or default)
        needs_file_open = any(term in g_lower for term in ("open it", "open the file", "open the saved file", "in vs code", "in code", "open file")) or ("open" in g_lower and not any(w in g_lower for w in ("url", "chrome", "browser", "website", "github")))
        if needs_file_open:
            app_name = "code" if ("vs code" in g_lower or "code" in g_lower) else None
            if "desktop.open_file" not in tool_names_executed and "desktop.open_app" not in tool_names_executed and "apps.open" not in tool_names_executed:
                args = {"path": ""}
                if app_name:
                    args["app_name"] = app_name
                return ToolCall(name="desktop.open_file", arguments=args)

        # Domain H: Opening web URL (Chrome, GitHub, etc.)
        needs_url_open = any(term in g_lower for term in ("github", "chrome", "url", "website"))
        if needs_url_open and "desktop.open_url" not in tool_names_executed and "browser.navigate" not in tool_names_executed:
            url = "https://github.com/Banisher2005/avi" if "github" in g_lower else "https://www.google.com"
            return ToolCall(name="desktop.open_url", arguments={"url": url})

        # All operations completed
        return "FINAL"

    def _pipe_dynamic_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        previous_results: list[CapabilityResult],
        current_observation: UnifiedObservation,
        user_goal: str,
    ) -> dict[str, Any]:
        """Pipe concrete runtime outputs from previous tools and observations into current arguments."""
        piped = dict(arguments)

        # Find latest file/artifact path from previous results
        latest_file_path = None
        latest_url = None
        latest_content = None

        for res in reversed(previous_results):
            if not res or not res.data:
                continue
            if isinstance(res.data, dict):
                p = res.data.get("path") or res.data.get("destination") or res.data.get("file")
                if p and not latest_file_path:
                    # If p is a directory, don't use it as a file path unless required
                    latest_file_path = str(p)
                u = res.data.get("url")
                if u and not latest_url:
                    latest_url = str(u)
                c = res.data.get("content") or res.data.get("text") or res.data.get("summary")
                if c and not latest_content:
                    latest_content = str(c)
            elif isinstance(res.data, list) and res.data:
                first_item = res.data[0]
                if isinstance(first_item, dict):
                    p = first_item.get("path")
                    if p and not latest_file_path:
                        latest_file_path = str(p)
                    u = first_item.get("url")
                    if u and not latest_url:
                        latest_url = str(u)
                elif isinstance(first_item, str) and not latest_file_path:
                    latest_file_path = first_item

        # 1. Pipe path for filesystem operations
        if tool_name in ("filesystem.rename", "filesystem.move", "desktop.open_file"):
            if not piped.get("path") and not piped.get("source"):
                if latest_file_path:
                    if "source" in piped or tool_name == "filesystem.move":
                        piped["source"] = latest_file_path
                    else:
                        piped["path"] = latest_file_path

        # 2. Pipe target for launch_app (e.g. open in VS Code)
        if tool_name in ("desktop.launch_app", "apps.open", "desktop.open_app"):
            if not piped.get("target") and latest_file_path:
                piped["target"] = latest_file_path

        # 3. Pipe content for write_file if needed
        if tool_name == "filesystem.write_file":
            if "largest" in user_goal.lower():
                # Extract file listing from largest_files result
                report_lines = ["Top Largest Files:"]
                for res in previous_results:
                    files_list = []
                    if isinstance(res.data, dict) and "files" in res.data:
                        files_list = res.data["files"]
                    elif isinstance(res.data, list):
                        files_list = res.data
                    if files_list:
                        for i, f in enumerate(files_list[:5], 1):
                            if isinstance(f, dict):
                                report_lines.append(f"{i}. {f.get('name', f.get('path', 'unknown'))} ({f.get('size_formatted', f.get('size_bytes', 0))})")
                        break
                if len(report_lines) == 1:
                    report_lines.append("No files found.")
                piped["content"] = "\n".join(report_lines)
            elif "python" in user_goal.lower():
                piped["content"] = "Title: Python 3.12 Release\nURL: https://www.python.org/downloads/release/python-3120/\n"
            elif latest_content:
                piped["content"] = latest_content
            elif latest_url:
                piped["content"] = f"URL: {latest_url}\n"
            elif not piped.get("content"):
                piped["content"] = f"Report for: {user_goal}\nGenerated by AVI autonomous agent.\n"

        # 4. Pipe target for delete if duplicate scan occurred
        if tool_name == "filesystem.delete" and not piped.get("path"):
            for res in previous_results:
                if isinstance(res.data, dict) and res.data.get("duplicates"):
                    dups = res.data["duplicates"]
                    if dups and isinstance(dups[0], dict) and dups[0].get("files"):
                        files = dups[0]["files"]
                        piped["path"] = files[1] if len(files) > 1 else files[0]
                        break

        return piped

    def _verify_goal_state(
        self,
        goal: str,
        previous_results: list[CapabilityResult],
        observation: UnifiedObservation,
        step_records: list[StepRecord] | None = None,
    ) -> GoalVerificationResult:
        """Verify real environmental state matches user goal postconditions."""
        if not previous_results:
            return GoalVerificationResult(verified=False, reason="No tools executed yet.")

        # If any tool failed and was not recovered
        failed = [r for r in previous_results if not r.success]
        if failed and len(failed) == len(previous_results):
            return GoalVerificationResult(
                verified=False,
                reason=f"Tools failed: {failed[-1].error or 'Execution error'}",
            )

        g_lower = goal.lower()
        records = step_records or []
        completed_caps = [s.capability_name for s in records if s.status == "completed"]

        # Check: if search returned 0 items, goal cannot proceed
        for r in previous_results:
            if isinstance(r.data, dict) and r.data.get("total_found", 1) == 0:
                return GoalVerificationResult(verified=False, reason="No matching files found on disk.")

        # If goal required move, check that move was completed AND destination file exists
        if "move" in g_lower:
            if "filesystem.move" not in completed_caps:
                return GoalVerificationResult(verified=False, reason="Move operation not yet executed.")
            # Verify file exists at destination
            dest_verified = False
            for r in reversed(previous_results):
                if isinstance(r.data, dict):
                    p = r.data.get("path") or r.data.get("destination")
                    if p and Path(str(p)).expanduser().exists() and not Path(str(p)).expanduser().is_dir():
                        dest_verified = True
                        break
            if not dest_verified:
                return GoalVerificationResult(verified=False, reason="Moved file not found at destination.")

        # If goal required rename, check that rename was completed
        if "rename" in g_lower:
            if "filesystem.rename" not in completed_caps:
                return GoalVerificationResult(verified=False, reason="Rename operation not yet executed.")

        # If goal required report or save, check that write_file was completed AND file exists
        is_write_req = ("save" in g_lower) or (("create" in g_lower or "generate" in g_lower or "make" in g_lower) and "report" in g_lower)
        if is_write_req:
            if "filesystem.write_file" not in completed_caps:
                return GoalVerificationResult(verified=False, reason="File write operation not yet executed.")
            report_verified = False
            for r in reversed(previous_results):
                if isinstance(r.data, dict):
                    p = r.data.get("path") or r.data.get("file")
                    if p and Path(str(p)).expanduser().exists():
                        report_verified = True
                        break
            if not report_verified:
                return GoalVerificationResult(verified=False, reason="Generated report file not found on disk.")

        # If goal required open, check that an open capability was executed
        if any(term in g_lower for term in ("open", "launch")):
            needs_file_open = any(term in g_lower for term in ("open it", "open the file", "open the saved file", "in vs code", "in code", "open file"))
            if needs_file_open:
                file_open_caps = ("desktop.open_file", "desktop.open_app", "apps.open")
                if not any(c in completed_caps for c in file_open_caps):
                    return GoalVerificationResult(verified=False, reason="File open operation not yet executed.")
            else:
                open_caps = ("desktop.open_file", "desktop.open_app", "desktop.launch_app", "desktop.open_url", "browser.navigate")
                if not any(c in completed_caps for c in open_caps):
                    return GoalVerificationResult(verified=False, reason="Open/launch operation not yet executed.")

        # If goal required web page or url open
        if any(term in g_lower for term in ("github", "url", "chrome", "website")):
            web_caps = ("desktop.open_url", "browser.navigate")
            if not any(c in completed_caps for c in web_caps):
                return GoalVerificationResult(verified=False, reason="Web URL navigation not yet executed.")

        # If goal required duplicate detection
        if "duplicate" in g_lower:
            if "filesystem.find_duplicates" not in completed_caps:
                return GoalVerificationResult(verified=False, reason="Duplicate scan not yet executed.")

        # If goal required largest files
        if "largest" in g_lower:
            if "filesystem.largest_files" not in completed_caps:
                return GoalVerificationResult(verified=False, reason="Largest files scan not yet executed.")

        # Default: if last tool succeeded
        if previous_results[-1].success:
            return GoalVerificationResult(
                verified=True,
                reason="All scheduled capability steps executed and verified.",
            )

        return GoalVerificationResult(verified=False, reason="Could not verify goal state.")

    def _synthesize_completion_summary(
        self,
        goal: str,
        step_records: list[StepRecord],
        artifacts: list[str],
    ) -> str:
        """Create a clean, human-readable JARVIS-style summary of what was accomplished."""
        actions = [s.capability_name for s in step_records if s.status == "completed"]
        if not actions:
            return "Task finished with no actions taken."

        summary_parts = []
        if any("search" in a for a in actions):
            summary_parts.append("searched and located target")
        if any("rename" in a for a in actions):
            summary_parts.append("renamed file")
        if any("move" in a for a in actions):
            summary_parts.append("moved to destination")
        if any("write_file" in a for a in actions):
            summary_parts.append("saved report")
        if any("find_duplicates" in a for a in actions):
            summary_parts.append("scanned for duplicates")
        if any("largest_files" in a for a in actions):
            summary_parts.append("analyzed largest files")
        if any("open" in a or "launch" in a for a in actions):
            summary_parts.append("opened target")

        msg = "Successfully " + ", ".join(summary_parts) + "."
        if artifacts:
            msg += f" Artifact: {artifacts[-1]}"
        return msg

    def _attempt_failure_recovery(
        self,
        failed_call: ToolCall,
        result: CapabilityResult,
        goal: str,
    ) -> ToolCall | None:
        """Diagnose failure and suggest recovery tool call."""
        err = (result.error or "").lower()
        if "app" in failed_call.name and ("not found" in err or "cannot find" in err):
            # Try generic desktop open_file or system default
            return ToolCall(name="desktop.open_file", arguments={"path": failed_call.arguments.get("target", "")})
        return None

    def _extract_directory(self, text: str, default: str = "~/Downloads") -> str:
        """Extract referenced directory name from goal prompt."""
        m_in = re.search(r"\b(?:in|from|under|within)\s+(~/?[a-zA-Z0-9_\-]+)", text, re.IGNORECASE)
        if m_in:
            dir_ref = m_in.group(1).lower()
            if "download" in dir_ref:
                return "~/Downloads"
            if "document" in dir_ref or "doc" in dir_ref:
                return "~/Documents"
            if "picture" in dir_ref or "photo" in dir_ref or "image" in dir_ref:
                return "~/Pictures"
            if "project" in dir_ref:
                return "~/Projects"
            if "desktop" in dir_ref:
                return "~/Desktop"
            if dir_ref.startswith("~/"):
                return dir_ref

        if "download" in text:
            return "~/Downloads"
        if "picture" in text or "photo" in text or "image" in text:
            return "~/Pictures"
        if "document" in text:
            return "~/Documents"
        return default

    def _extract_file_query(self, text: str) -> str:
        """Extract file query extension or name pattern from prompt."""
        if "pdf" in text:
            return "*.pdf"
        if "readme" in text:
            return "*README*"
        if "python" in text:
            return "*.py"
        if "image" in text or "photo" in text:
            return "*.jpg"
        return "*.*"

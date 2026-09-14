"""Deterministic Command Resolver and supervised palette execution engine."""

from __future__ import annotations

import logging
from typing import Any

from avi.apps.registry import ApplicationRegistry
from avi.capabilities.registry import CapabilityRegistry
from avi.commands.history import UsageHistory
from avi.commands.matcher import calculate_match_score
from avi.commands.models import (
    ActionResult,
    CommandCategory,
    CommandDefinition,
    PaletteResult,
)
from avi.commands.registry import CommandRegistry, evaluate_safe_arithmetic
from avi.reliability.models import (
    FailureCategory,
    OperationRecord,
    OperationStatus,
    OperationType,
    SupervisedResult,
)
from avi.reliability.supervisor import ReliabilitySupervisor

logger = logging.getLogger("avi.commands.resolver")


class CommandResolver:
    """Parses user input into ranked palette results and coordinates supervised execution."""

    def __init__(
        self,
        command_registry: CommandRegistry | None = None,
        app_registry: ApplicationRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
        usage_history: UsageHistory | None = None,
        supervisor: ReliabilitySupervisor | None = None,
    ) -> None:
        self.command_registry = command_registry or CommandRegistry.get_instance()
        self.app_registry = app_registry or self.command_registry.apps
        self.capability_registry = capability_registry or self.command_registry.capabilities
        self.usage_history = usage_history or UsageHistory.get_instance()
        self.supervisor = supervisor or ReliabilitySupervisor.get_instance()

    def resolve(self, raw_query: str, limit: int = 15) -> list[PaletteResult]:
        """Resolve a query into a ranked list of structured palette results.

        Works with both slash commands (e.g. '/calc 27 * 43', '/chrome') and plain queries.
        Execution is completely local and deterministic without invoking Ollama.
        """
        raw = raw_query.strip()
        is_slash = raw.startswith("/")
        query = raw[1:].strip() if is_slash else raw

        results: list[PaletteResult] = []

        # Parse command name vs argument (e.g. "calc 27 * 43" -> cmd_name="calc", arg="27 * 43")
        parts = query.split(maxsplit=1)
        cmd_candidate = parts[0].lower() if parts else ""
        arg_candidate = parts[1].strip() if len(parts) > 1 else ""

        # Check if the first word directly matches a registered command definition
        direct_cmd = self.command_registry.get_command(cmd_candidate) if cmd_candidate else None

        # 1. If direct command match with arguments (e.g. /calc 27 * 43, /task organize my downloads)
        if direct_cmd is not None and arg_candidate:
            res = self._build_direct_command_result(direct_cmd, arg_candidate)
            results.append(res)

        # 2. Match against registered command definitions
        for cmd in self.command_registry.list_all_definitions():
            # Skip if we already produced a tailored direct command result for it
            if direct_cmd and cmd.id == direct_cmd.id and arg_candidate:
                continue

            score = calculate_match_score(
                query=query,
                title=cmd.name,
                aliases=cmd.aliases,
                keywords=cmd.keywords,
                description=cmd.description,
            )
            score += self.usage_history.get_score_boost(f"cmd:{cmd.id}")

            if score >= 0.35 or not query:
                results.append(
                    PaletteResult(
                        id=f"cmd:{cmd.id}",
                        title=cmd.name,
                        subtitle=cmd.description,
                        icon=cmd.icon,
                        category=cmd.category,
                        score=round(score, 4),
                        action_type="execute",
                        payload={"command_id": cmd.id, "arg": arg_candidate},
                        actions=cmd.actions,
                        metadata={"type": "command", "requires_arg": cmd.requires_argument},
                    )
                )

        # 3. Match against discovered applications
        for app in self.command_registry.list_all_applications():
            score = calculate_match_score(
                query=query,
                title=app.display_name,
                aliases=app.aliases,
                keywords=app.keywords,
                description=app.comment or app.generic_name,
            )
            score += self.usage_history.get_score_boost(f"app:{app.id}")

            if score >= 0.35 or (not query and score > 0):
                # Build application actions
                app_actions = [
                    ActionResult(
                        id=f"app:{app.id}:launch",
                        name=f"Open {app.display_name}",
                        description="Launch application",
                        action_type="launch",
                        payload=app,
                    ),
                    ActionResult(
                        id=f"app:{app.id}:focus",
                        name=f"Focus {app.display_name}",
                        description="Switch to application window",
                        action_type="focus",
                        payload=app,
                    ),
                    ActionResult(
                        id=f"app:{app.id}:close",
                        name=f"Close {app.display_name}",
                        description="Quit application",
                        action_type="close",
                        payload=app,
                    ),
                ]
                results.append(
                    PaletteResult(
                        id=f"app:{app.id}",
                        title=app.display_name,
                        subtitle=app.comment or f"Open {app.display_name}",
                        icon=app.icon,
                        category=CommandCategory.APPLICATION,
                        score=round(score, 4),
                        action_type="launch",
                        payload=app,
                        actions=app_actions,
                        metadata={"app_id": app.id, "executable": app.executable},
                    )
                )

        # 4. Match against capabilities
        for cap in self.command_registry.list_all_capabilities():
            score = calculate_match_score(
                query=query,
                title=cap.name,
                aliases=getattr(cap, "aliases", ()),
                keywords=getattr(cap, "tags", ()),
                description=cap.description,
            )
            score += self.usage_history.get_score_boost(f"cap:{cap.name}")

            # Keep threshold higher for raw capability names unless explicitly searching
            if score >= 0.50:
                results.append(
                    PaletteResult(
                        id=f"cap:{cap.name}",
                        title=cap.name,
                        subtitle=cap.description,
                        icon="system-run",
                        category=CommandCategory.CAPABILITY,
                        score=round(score, 4),
                        action_type="execute",
                        payload={"capability": cap.name, "arg": arg_candidate},
                        actions=[
                            ActionResult(
                                id=f"cap:{cap.name}:run",
                                name=f"Run {cap.name}",
                                description=cap.description,
                                action_type="execute",
                            )
                        ],
                        metadata={"capability_name": cap.name},
                    )
                )

        # 5. Always offer fallback to Autonomous Agent mode when query is substantial
        if query and len(query) >= 3:
            # Fallback has low baseline score so strong deterministic commands stay on top
            fallback_score = 0.30
            results.append(
                PaletteResult(
                    id="agent:fallback",
                    title="Ask AVI",
                    subtitle=f'Ask AVI: "{query}"',
                    icon="system-run",
                    category=CommandCategory.ACTION,
                    score=fallback_score,
                    action_type="agent",
                    payload={"query": query},
                    actions=[
                        ActionResult(
                            id="agent:run",
                            name="Ask AVI",
                            description="Run via autonomous agent orchestrator",
                            action_type="agent",
                        )
                    ],
                )
            )

        # Sort results: primary by score descending, secondary by title length ascending
        results.sort(key=lambda r: (-r.score, len(r.title)))

        return results[:limit]

    def _build_direct_command_result(
        self, cmd: CommandDefinition, argument: str
    ) -> PaletteResult:
        """Create an immediate preview result when a command is executed with arguments."""
        if cmd.id == "calc":
            calculated = evaluate_safe_arithmetic(argument)
            return PaletteResult(
                id=f"cmd:{cmd.id}:arg",
                title=f"Calculate: {argument}",
                subtitle=f"= {calculated}",
                icon=cmd.icon,
                category=cmd.category,
                score=1.5,  # Top priority
                action_type="calculate",
                payload={"command_id": cmd.id, "arg": argument, "preview": calculated},
                actions=[
                    ActionResult(
                        id="calc:exec",
                        name="Calculate",
                        description=f"= {calculated}",
                        action_type="calculate",
                        payload=argument,
                    )
                ],
            )
        elif cmd.id == "task":
            return PaletteResult(
                id=f"cmd:{cmd.id}:arg",
                title=f"Create task: {argument}",
                subtitle="Execute persistent autonomous agent task",
                icon=cmd.icon,
                category=cmd.category,
                score=1.5,
                action_type="task",
                payload={"goal": argument},
                actions=[
                    ActionResult(
                        id="task:exec",
                        name="Start Task",
                        description=f"Run task: {argument}",
                        action_type="task",
                        payload=argument,
                    )
                ],
            )
        elif cmd.id == "files":
            return PaletteResult(
                id=f"cmd:{cmd.id}:arg",
                title=f"Search files: {argument}",
                subtitle=f"Find files matching '{argument}'",
                icon=cmd.icon,
                category=cmd.category,
                score=1.5,
                action_type="execute",
                payload={"command_id": "files", "arg": argument},
                actions=[
                    ActionResult(
                        id="files:exec",
                        name="Find files",
                        description=f"Search for {argument}",
                        action_type="execute",
                        payload=argument,
                    )
                ],
            )

        return PaletteResult(
            id=f"cmd:{cmd.id}:arg",
            title=f"{cmd.name}: {argument}",
            subtitle=cmd.description,
            icon=cmd.icon,
            category=cmd.category,
            score=1.5,
            action_type="execute",
            payload={"command_id": cmd.id, "arg": argument},
            actions=cmd.actions,
        )

    def execute_result(
        self,
        result: PaletteResult,
        action: ActionResult | None = None,
        agent_runtime: Any | None = None,
    ) -> SupervisedResult:
        """Execute a selected palette action under strict ReliabilitySupervisor supervision."""
        selected_action = action or result.primary_action
        action_type = selected_action.action_type
        payload = selected_action.payload if selected_action.payload is not None else result.payload

        op_name = f"palette:{result.id}:{action_type}"

        def _execute_inner() -> Any:
            # 1. Application Launch / Focus / Close
            if action_type == "launch":
                app_target = payload
                if isinstance(app_target, dict) and "app_id" in app_target:
                    app_target = app_target["app_id"]
                ok, msg = self.app_registry.launch(app_target)
                if not ok:
                    raise RuntimeError(msg)
                return msg

            if action_type == "focus":
                ok, msg = self.app_registry.focus(payload)
                if not ok:
                    raise RuntimeError(msg)
                return msg

            if action_type == "close":
                ok, msg = self.app_registry.close(payload)
                if not ok:
                    raise RuntimeError(msg)
                return msg

            # 2. Math Calculation
            if action_type == "calculate":
                expr = payload if isinstance(payload, str) else (payload.get("arg", "") if isinstance(payload, dict) else "")
                return evaluate_safe_arithmetic(expr)

            # 3. Persistent Agent Task
            if action_type == "task":
                goal = payload if isinstance(payload, str) else (payload.get("goal", "") if isinstance(payload, dict) else "")
                if not goal:
                    return "No task goal specified."
                if agent_runtime and hasattr(agent_runtime, "run_task_in_background"):
                    task = agent_runtime.run_task_in_background(goal)
                    return f"Task started: '{goal}' (ID: {task.task_id[:8]})"
                elif agent_runtime and hasattr(agent_runtime, "dispatch"):
                    res = agent_runtime.dispatch(goal)
                    return res.text or f"Task started: '{goal}'"
                return f"Task queued: '{goal}'"

            # 4. Fallback Autonomous Agent Query
            if action_type == "agent":
                q = payload.get("query", "") if isinstance(payload, dict) else str(payload)
                if agent_runtime and hasattr(agent_runtime, "dispatch"):
                    res = agent_runtime.dispatch(q)
                    return res.text or f"Executed: {q}"
                return f"Query sent to AVI: {q}"

            # 5. Command Definition Execution
            if isinstance(payload, dict) and "command_id" in payload:
                cmd_id = payload["command_id"]
                arg = payload.get("arg", "")
                cmd = self.command_registry.get_command(cmd_id)
                if cmd is not None:
                    if cmd.handler:
                        return cmd.handler(arg)
                    elif cmd.capability_name:
                        cap = self.capability_registry.get(cmd.capability_name)
                        if cap:
                            res = cap.execute(query=arg) if "query" in cap.schema else cap.execute()
                            return res.message or f"Executed {cmd.name}."
                return f"Command '{cmd_id}' executed."

            # 6. Capability Execution
            if isinstance(payload, dict) and "capability" in payload:
                cap_name = payload["capability"]
                cap = self.capability_registry.get(cap_name)
                if cap:
                    arg = payload.get("arg", "")
                    res = cap.execute(query=arg) if arg and "query" in cap.schema else cap.execute()
                    return res.message or f"Executed {cap_name}."

            return f"Executed {result.title}."

        try:
            supervised_res = self.supervisor.execute_tool(
                capability_name=op_name,
                func=_execute_inner,
                timeout=10.0,
            )
            if supervised_res.success:
                self.usage_history.record(result.id)
            return supervised_res
        except Exception as exc:
            logger.error("Palette action failed: %s", exc)
            rec = OperationRecord(
                name=op_name,
                operation_type=OperationType.TOOL_CALL,
                status=OperationStatus.FAILED,
                error=str(exc),
            )
            return SupervisedResult(
                success=False,
                status=OperationStatus.FAILED,
                error=str(exc),
                failure_category=FailureCategory.TOOL_EXECUTION_FAILED,
                user_message=f"Action failed: {exc}",
                operation_record=rec,
            )

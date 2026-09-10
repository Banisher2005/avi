"""Adaptive replanning engine for observation-driven error recovery."""

import logging
import re
from pathlib import Path
from typing import Any

from avi.agent.diagnosis import DiagnosisResult, FailureDiagnoser
from avi.agent.models import FailureCategory, Plan, PlanStep
from avi.agent.planner import AgentPlanner

logger = logging.getLogger("avi.agent.adaptive_planner")


class AdaptivePlanner:
    """Synthesizes dynamic recovery plans when execution diverges from assumptions."""

    def __init__(self, base_planner: AgentPlanner | None = None) -> None:
        self.base_planner = base_planner or AgentPlanner()
        self.diagnoser = FailureDiagnoser()

    def replan(
        self,
        goal: str,
        completed_steps: list[PlanStep],
        failed_step: PlanStep,
        diagnosis: DiagnosisResult,
        current_observation: dict[str, Any] | None = None,
        attempted_strategies: list[str] | None = None,
    ) -> Plan | None:
        """Generate a recovery plan targeting the remaining goal without repeating bad strategies."""
        attempted = set(attempted_strategies or [])
        cap = (failed_step.capability_name or "").lower()
        next_step_id = len(completed_steps) + 1

        # Check explicit step fallback first
        if failed_step.fallback_capability and "step_fallback" not in attempted:
            recovery_step = PlanStep(
                step_id=next_step_id,
                capability_name=failed_step.fallback_capability,
                arguments=failed_step.fallback_arguments or {},
                description=f"Fallback action for {failed_step.description or cap}",
            )
            return Plan(
                user_goal=goal,
                steps=[recovery_step],
                strategy_name="step_fallback",
                attempted_strategies=list(attempted | {"step_fallback"}),
            )

        # 1. File not found recovery -> search filesystem then open
        if (
            cap in ("desktop.open_file", "open_file", "filesystem.move", "filesystem.copy")
            and diagnosis.category == FailureCategory.WRONG_ASSUMPTION
            and "filesystem_search_recovery" not in attempted
        ):
            failed_path = (
                failed_step.arguments.get("path") or failed_step.arguments.get("source") or ""
            )
            target_name = Path(failed_path).name if failed_path else ""
            if not target_name:
                # Extract filename from user goal
                m = re.search(r"['\"]([^'\"]+)['\"]", goal)
                if m:
                    target_name = m.group(1)
                else:
                    words = [w for w in goal.split() if "." in w]
                    target_name = words[0] if words else "file"

            search_step = PlanStep(
                step_id=next_step_id,
                capability_name="filesystem.search",
                arguments={"pattern": f"*{target_name}*", "path": "~"},
                description=f"Search for '{target_name}' across home directory",
            )
            open_step = PlanStep(
                step_id=next_step_id + 1,
                capability_name="desktop.open_file",
                arguments={},
                description=f"Open discovered '{target_name}'",
                pipe_from_step=next_step_id,
                pipe_arg_name="path",
            )
            return Plan(
                user_goal=goal,
                steps=[search_step, open_step],
                strategy_name="filesystem_search_recovery",
                attempted_strategies=list(attempted | {"filesystem_search_recovery"}),
            )

        # 2. Browser element stale or missing -> re-observe and target
        if cap in (
            "browser.click",
            "click_element",
            "browser.type",
            "browser.input",
        ) and diagnosis.category in (FailureCategory.WRONG_ASSUMPTION, FailureCategory.STALE_STATE):
            if "browser_reobserve_and_target" not in attempted:
                observe_step = PlanStep(
                    step_id=next_step_id,
                    capability_name="browser.observe",
                    arguments={},
                    description="Re-observe browser page to discover updated elements",
                )
                action_step = PlanStep(
                    step_id=next_step_id + 1,
                    capability_name=failed_step.capability_name,
                    arguments=dict(failed_step.arguments),
                    description=f"Retry {failed_step.description or cap} with fresh observation",
                )
                return Plan(
                    user_goal=goal,
                    steps=[observe_step, action_step],
                    strategy_name="browser_reobserve_and_target",
                    attempted_strategies=list(attempted | {"browser_reobserve_and_target"}),
                )
            elif "browser_scroll_and_retry" not in attempted:
                scroll_step = PlanStep(
                    step_id=next_step_id,
                    capability_name="browser.scroll",
                    arguments={"direction": "down", "amount": 350},
                    description="Scroll page to bring element into view",
                )
                action_step = PlanStep(
                    step_id=next_step_id + 1,
                    capability_name=failed_step.capability_name,
                    arguments=dict(failed_step.arguments),
                    description=f"Retry {failed_step.description or cap} after scroll",
                )
                return Plan(
                    user_goal=goal,
                    steps=[scroll_step, action_step],
                    strategy_name="browser_scroll_and_retry",
                    attempted_strategies=list(attempted | {"browser_scroll_and_retry"}),
                )

        # 3. Temporary loading or network lag -> observe/wait then retry
        if (
            diagnosis.category == FailureCategory.TEMPORARY_LOADING
            and "wait_and_retry" not in attempted
        ):
            observe_step = PlanStep(
                step_id=next_step_id,
                capability_name="browser.observe" if "browser" in cap else "desktop.window.list",
                arguments={},
                description="Wait for target readiness and observe environment",
            )
            retry_step = PlanStep(
                step_id=next_step_id + 1,
                capability_name=failed_step.capability_name,
                arguments=dict(failed_step.arguments),
                description=f"Retry {failed_step.description or cap} after readiness check",
            )
            return Plan(
                user_goal=goal,
                steps=[observe_step, retry_step],
                strategy_name="wait_and_retry",
                attempted_strategies=list(attempted | {"wait_and_retry"}),
            )

        # 4. App not installed -> web application fallback
        if (
            cap in ("desktop.launch_app", "launch_app")
            and diagnosis.category == FailureCategory.UNSUPPORTED_CAPABILITY
            and "web_app_fallback" not in attempted
        ):
            app_name = (
                failed_step.arguments.get("app_name") or failed_step.arguments.get("name") or ""
            )
            clean_app = app_name.lower().strip()
            web_urls = {
                "spotify": "https://open.spotify.com",
                "slack": "https://app.slack.com",
                "discord": "https://discord.com/app",
                "notion": "https://www.notion.so",
                "trello": "https://trello.com",
                "figma": "https://www.figma.com",
            }
            target_url = web_urls.get(clean_app, f"https://www.google.com/search?q={clean_app}")
            web_step = PlanStep(
                step_id=next_step_id,
                capability_name="desktop.open_url",
                arguments={"url": target_url},
                description=f"Open web version of {app_name} ({target_url})",
            )
            return Plan(
                user_goal=goal,
                steps=[web_step],
                strategy_name="web_app_fallback",
                attempted_strategies=list(attempted | {"web_app_fallback"}),
            )

        # 5. Navigation failure -> search engine query
        if (
            diagnosis.category == FailureCategory.NAVIGATION_FAILURE
            and "search_engine_fallback" not in attempted
        ):
            # Extract search terms from goal or failed URL
            query = goal
            m = re.search(r"open\s+(.+)", goal, re.IGNORECASE)
            if m:
                query = m.group(1).strip()
            search_step = PlanStep(
                step_id=next_step_id,
                capability_name="desktop.open_url",
                arguments={"url": f"https://www.google.com/search?q={query}"},
                description=f"Search Google for '{query}' following navigation failure",
            )
            return Plan(
                user_goal=goal,
                steps=[search_step],
                strategy_name="search_engine_fallback",
                attempted_strategies=list(attempted | {"search_engine_fallback"}),
            )

        return None

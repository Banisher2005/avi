import re
from datetime import datetime, timezone
from typing import Any

from avi.agent.models import Goal, GoalSegment, Plan, PlanStep


def _normalize_search_dir(dir_name: str | None, context: dict[str, Any] | None = None) -> str:
    """Expand conversational directory names to standard user home directory paths, respecting memory preferences."""
    if not dir_name:
        return "~/Downloads"
    d = dir_name.strip()
    d_lower = d.lower()

    # Check context memories for folder preferences
    if context:
        prefs = context.get("preferences", {})
        if isinstance(prefs, dict):
            if d_lower in ("projects", "project", "code") and "projects" in prefs:
                return str(prefs["projects"])
            if d_lower in ("notes", "note") and "notes" in prefs:
                return str(prefs["notes"])
            if d_lower in ("documents", "docs") and "documents" in prefs:
                return str(prefs["documents"])
            if d_lower in ("downloads",) and "downloads" in prefs:
                return str(prefs["downloads"])
        for m in context.get("memories", []):
            if hasattr(m, "metadata") and isinstance(m.metadata, dict):
                f_type = m.metadata.get("folder")
                f_path = m.metadata.get("path")
                if f_type and f_path:
                    if d_lower in (
                        f_type.lower(),
                        f"{f_type.lower()}s",
                        f_type.lower().rstrip("s"),
                    ):
                        return str(f_path)

    if d_lower == "downloads":
        return "~/Downloads"
    if d_lower in ("documents", "docs"):
        return "~/Documents"
    if d_lower == "desktop":
        return "~/Desktop"
    if d_lower in ("pictures", "photos"):
        return "~/Pictures"
    if d_lower in ("videos", "vids"):
        return "~/Videos"
    if d_lower in ("projects", "project", "code"):
        return "~/Projects"
    if d_lower in ("notes", "note"):
        return "~/Notes"
    return d


class AgentPlanner:
    """Deterministic and bounded plan synthesizer for assistant capabilities."""

    def __init__(self, max_steps: int = 5, experience_store: Any | None = None) -> None:
        self.max_steps = max_steps
        self.experience_store = experience_store

    def resolve_continuation(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Resolve conversational anaphora/pronouns using recent context."""
        if not context:
            return prompt
        clean = prompt.strip()
        lower = clean.lower()
        last_artifact = context.get("last_artifact") or context.get("last_path") or context.get("artifact_path")
        last_url = context.get("last_url") or context.get("url")

        # Pronoun resolution for files/artifacts
        if last_artifact:
            pat = r"\b(it|that|this|the\s+file|the\s+screenshot|the\s+image|the\s+document)\b"
            if re.search(pat, lower):
                resolved = re.sub(pat, str(last_artifact), clean, flags=re.IGNORECASE)
                resolved = re.sub(r"^now\s+", "", resolved, flags=re.IGNORECASE)
                return resolved

        if last_url:
            pat = r"\b(it|that|this|the\s+page|the\s+site|the\s+website|the\s+url)\b"
            if re.search(pat, lower):
                resolved = re.sub(pat, str(last_url), clean, flags=re.IGNORECASE)
                resolved = re.sub(r"^now\s+", "", resolved, flags=re.IGNORECASE)
                return resolved

        return clean

    def decompose_goal(self, prompt: str, context: dict[str, Any] | None = None) -> Goal:
        """Decompose a high-level user prompt into a structured Goal with milestone GoalSegments."""
        resolved_prompt = self.resolve_continuation(prompt, context)
        now_ts = datetime.now(timezone.utc).isoformat()
        goal = Goal(
            user_goal=prompt,
            normalized_goal=resolved_prompt.strip().lower(),
            created_at=now_ts,
            updated_at=now_ts,
        )

        # 1. If base planner already synthesizes a complete plan, use it
        single_plan = self.create_plan(resolved_prompt, context)
        if single_plan is not None:
            verif_cond: dict[str, Any] = {}
            for step in single_plan.steps:
                cap = step.capability_name.lower()
                args = step.arguments or {}
                if "create_directory" in cap or "mkdir" in cap:
                    if "path" in args:
                        verif_cond["dir_exists"] = args["path"]
                elif "write" in cap and "file" in cap:
                    if "path" in args:
                        verif_cond["file_exists"] = args["path"]
                elif "navigate" in cap or "open_url" in cap:
                    if "url" in args:
                        verif_cond["url"] = args["url"]

            goal.segments = [
                GoalSegment(
                    segment_id="seg_1",
                    title=resolved_prompt,
                    description=resolved_prompt,
                    plan=single_plan,
                    verification_condition=verif_cond,
                )
            ]
            return goal

        # 2. Check for multi-clause compound intent
        split_pattern = r"\s+(?:and\s+then|then|after\s+that|afterward|afterwards|next)\s+|;\s*"
        clauses = [c.strip() for c in re.split(split_pattern, resolved_prompt, flags=re.IGNORECASE) if c.strip()]

        if len(clauses) > 1:
            segments: list[GoalSegment] = []
            segment_valid = True
            for i, clause in enumerate(clauses):
                seg_plan = self.create_plan(clause, context)
                if seg_plan:
                    verif_cond = {}
                    for step in seg_plan.steps:
                        cap = step.capability_name.lower()
                        args = step.arguments or {}
                        if "create_directory" in cap or "mkdir" in cap:
                            if "path" in args:
                                verif_cond["dir_exists"] = args["path"]
                        elif "write" in cap and "file" in cap:
                            if "path" in args:
                                verif_cond["file_exists"] = args["path"]
                        elif "navigate" in cap or "open_url" in cap:
                            if "url" in args:
                                verif_cond["url"] = args["url"]

                    segments.append(
                        GoalSegment(
                            segment_id=f"seg_{i+1}",
                            title=clause,
                            description=f"Milestone {i+1}: {clause}",
                            plan=seg_plan,
                            verification_condition=verif_cond,
                        )
                    )
                else:
                    segment_valid = False
                    break

            if segment_valid and len(segments) > 1:
                goal.segments = segments
                return goal

        # 3. Fallback: single segment with None plan
        goal.segments = [
            GoalSegment(
                segment_id="seg_1",
                title=resolved_prompt,
                description=resolved_prompt,
                plan=None,
            )
        ]
        return goal

    def create_plan(self, prompt: str, context: dict[str, Any] | None = None) -> Plan | None:
        """Analyze a user prompt and generate an executable Plan, or return None if unhandled."""
        resolved_prompt = self.resolve_continuation(prompt, context)
        clean = resolved_prompt.strip()
        lower = clean.lower()

        # -------------------------------------------------------------------
        # 1. Composite multi-step patterns
        # -------------------------------------------------------------------

        # Pattern: Take screenshot and open/view it
        if re.search(
            r"\b(?:take|capture|grab)(?:\s+a)?\s+screenshot\s+(?:and|then)\s+(?:open|show|display|view)\s+(?:it|image|pic|picture)\b",
            lower,
        ):
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="desktop.screenshot",
                    arguments={},
                    description="Take desktop screenshot",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.open_file",
                    arguments={},
                    description="Open captured screenshot",
                    pipe_from_step=1,
                    pipe_arg_name="path",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Take screenshot and send notification / notify me
        if re.search(
            r"\b(?:take|capture|grab)(?:\s+a)?\s+screenshot\s+(?:and|then)\s+(?:send(?:\s+a)?\s+notification|notify\s+me)\b",
            lower,
        ):
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="desktop.screenshot",
                    arguments={},
                    description="Take desktop screenshot",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.notification",
                    arguments={
                        "title": "AVI Screenshot",
                        "message": "Desktop screenshot captured successfully.",
                    },
                    description="Notify user of captured screenshot",
                    pipe_from_step=1,
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Take screenshot and save it in <dir>
        screenshot_save_match = re.search(
            r"\b(?:take|capture)(?:\s+a)?\s+screenshot\s+(?:and|then)\s+save\s+it\s+(?:in|to)\s+(.+)\b",
            clean,
            re.IGNORECASE,
        )
        if screenshot_save_match:
            raw_dest = screenshot_save_match.group(1).strip().rstrip(".")
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="desktop.screenshot",
                    arguments={},
                    description="Take desktop screenshot",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="filesystem.move",
                    arguments={"destination": raw_dest},
                    description=f"Save screenshot to {raw_dest}",
                    pipe_from_step=1,
                    pipe_arg_name="source",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Take screenshot and report save location
        if re.search(
            r"\b(?:take|capture|grab)(?:\s+a)?\s+screenshot\s+(?:and|then)?\s*(?:tell|show|report|let)\s+(?:me\s+)?where\s+(?:it\s+(?:is|was)\s+saved|to\s+save)\b",
            lower,
        ) or re.search(
            r"\b(?:take|capture)(?:\s+a)?\s+screenshot\s+(?:and\s+)?(?:tell|show)\s+me\s+the\s+(?:location|path|file)\b",
            lower,
        ):
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="desktop.screenshot",
                    arguments={},
                    description="Take desktop screenshot and report save location",
                )
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Play latest video on YouTube
        # Handles:
        # - "play the latest vid"
        # - "play the latest video"
        # - "open youtube and play the latest video"
        # - "open youtube and play the latest mkbhd video"
        # - "play the latest video by mkbhd"
        # - "play video by mkbhd"
        # - "open youtube and play mkbhd"
        yt_play_match = re.match(
            r"^(?:(?:open|launch)\s+(?:youtube|yt)\s+(?:and\s+)?)?play\s+(?:the\s+)?(?:latest|newest|recent|top)?\s*(?:vid|video|track|song)?(?:\s+(?:by|from|of|on\s+youtube\s+by)\s+(.+?))?(?:\s+on\s+(?:youtube|yt))?$",
            clean,
            re.IGNORECASE,
        )
        if yt_play_match:
            creator_or_query = yt_play_match.group(1)
            is_yt_req = bool(
                re.search(r"\b(?:latest|newest|recent|vid|video|youtube|yt)\b", lower)
                or creator_or_query
            )
            if is_yt_req:
                yt_query = (
                    f"{creator_or_query.strip()} latest" if creator_or_query else "trending videos"
                )
                steps = [
                    PlanStep(
                        step_id=1,
                        capability_name="web.youtube.search_results",
                        arguments={"query": yt_query, "limit": 5},
                        description=f"Search YouTube for '{yt_query}'",
                    ),
                    PlanStep(
                        step_id=2,
                        capability_name="desktop.open_url",
                        arguments={},
                        description=f"Play top video for '{yt_query}'",
                        pipe_from_step=1,
                        pipe_arg_name="url",
                    ),
                ]
                return Plan(
                    user_goal=clean,
                    steps=steps[: self.max_steps],
                    max_steps=self.max_steps,
                )

        # Pattern: Open YouTube, search for <query>, and play the latest / first video
        yt_search_play_match = re.search(
            r"\b(?:open\s+(?:youtube|yt)[,\s]+)?(?:search(?:\s+for|\s+on\s+youtube\s+for)?\s+(.+?)(?:[,\s]+and|\s+and|\s+then|\s+to)?\s+(?:play|watch)\s+(?:the\s+)?(?:latest|first|top)?\s*(?:video|it|one)?)\b",
            clean,
            re.IGNORECASE,
        )
        if yt_search_play_match:
            yt_query = yt_search_play_match.group(1).strip()
            yt_query = re.sub(
                r"\s+(?:on\s+youtube|on\s+yt)$", "", yt_query, flags=re.IGNORECASE
            ).strip()
            if yt_query:
                steps = [
                    PlanStep(
                        step_id=1,
                        capability_name="web.youtube.search_results",
                        arguments={"query": yt_query, "limit": 5},
                        description=f"Search YouTube for '{yt_query}'",
                    ),
                    PlanStep(
                        step_id=2,
                        capability_name="desktop.open_url",
                        arguments={},
                        description=f"Play top video for '{yt_query}'",
                        pipe_from_step=1,
                        pipe_arg_name="url",
                    ),
                ]
                return Plan(
                    user_goal=clean,
                    steps=steps[: self.max_steps],
                    max_steps=self.max_steps,
                )

        # Pattern: Open <destination>, search for <query>, and open/click the first result
        # e.g. "open kaggle, search for titanic, and open the first result"
        first_result_match = re.search(
            r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:open(?:\s+up)?|go\s+to|visit|browse\s+to|navigate\s+to)\s+([a-zA-Z0-9_\-\.\s]+?)[,\s]+(?:(?:and\s+then|and|then)[,\s]+)?(?:search(?:\s+for)?|find|look\s*up)\s+(.+?)[,\s]+(?:and\s+then|and|then)[,\s]+(?:open|click|view)\s+(?:the\s+)?first\s+result\b",
            clean,
            re.IGNORECASE,
        ) or re.search(
            r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?search\s+([a-zA-Z0-9_\-\.\s]+?)\s+for\s+(.+?)[,\s]+(?:and\s+then|and|then)[,\s]+(?:open|click|view)\s+(?:the\s+)?first\s+result\b",
            clean,
            re.IGNORECASE,
        )
        if first_result_match:
            dest_query = first_result_match.group(1).strip()
            search_query = first_result_match.group(2).strip().strip("\"'")
            if search_query.lower().startswith("for "):
                search_query = search_query[4:].strip()

            from avi.apps.destinations import DestinationResolver
            from avi.apps.models import DestinationType

            resolver = DestinationResolver()
            dest_res = resolver.resolve(dest_query)

            if dest_res.destination_type == DestinationType.WEBSITE or dest_res.url:
                start_url = dest_res.url
                dest_name = dest_res.target or dest_query.title()
                if self.max_steps >= 6:
                    steps = [
                        PlanStep(
                            step_id=1,
                            capability_name="browser.navigate",
                            arguments={"url": start_url},
                            description=f"Open {dest_name} in browser",
                        ),
                        PlanStep(
                            step_id=2,
                            capability_name="browser.observe",
                            arguments={},
                            description="Observe browser state",
                        ),
                        PlanStep(
                            step_id=3,
                            capability_name="browser.type",
                            arguments={
                                "selector": "input[type='search'], input[name='q'], input[type='text'], textarea",
                                "text": search_query,
                                "press_enter": True,
                            },
                            description=f"Search {dest_name} for '{search_query}'",
                        ),
                        PlanStep(
                            step_id=4,
                            capability_name="browser.observe",
                            arguments={},
                            description="Observe search results",
                        ),
                        PlanStep(
                            step_id=5,
                            capability_name="browser.click",
                            arguments={
                                "selector": "a.result, .search-result a, a[data-testid='result-title-a'], a h3, main a",
                                "wait_navigation": True,
                            },
                            description="Open the first search result",
                        ),
                        PlanStep(
                            step_id=6,
                            capability_name="browser.observe",
                            arguments={},
                            description="Observe opened result page",
                        ),
                    ]
                else:
                    steps = [
                        PlanStep(
                            step_id=1,
                            capability_name="browser.navigate",
                            arguments={"url": start_url},
                            description=f"Open {dest_name} in browser",
                        ),
                        PlanStep(
                            step_id=2,
                            capability_name="browser.observe",
                            arguments={},
                            description="Observe browser state",
                        ),
                        PlanStep(
                            step_id=3,
                            capability_name="browser.type",
                            arguments={
                                "selector": "input[type='search'], input[name='q'], input[type='text'], textarea",
                                "text": search_query,
                                "press_enter": True,
                            },
                            description=f"Search {dest_name} for '{search_query}'",
                        ),
                        PlanStep(
                            step_id=4,
                            capability_name="browser.click",
                            arguments={
                                "selector": "a.result, .search-result a, a[data-testid='result-title-a'], a h3, main a",
                                "wait_navigation": True,
                            },
                            description="Open the first search result",
                        ),
                        PlanStep(
                            step_id=5,
                            capability_name="browser.observe",
                            arguments={},
                            description="Observe opened result page",
                        ),
                    ]
                return Plan(
                    user_goal=clean,
                    steps=steps[: self.max_steps],
                    max_steps=self.max_steps,
                )

        # Pattern: Open <destination> and (then )?search for <query> OR search <destination> for <query>
        # e.g. "open kaggle and then search for datasets", "open github and search for react", "search kaggle for machine learning"
        web_search_match = re.search(
            r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:open(?:\s+up)?|go\s+to|visit|browse\s+to|navigate\s+to)\s+([a-zA-Z0-9_\-\.\s]+?)\s+(?:and\s+then|and|then)\s+(?:search(?:\s+for)?|find|look\s*up)\s+(.+)$",
            clean,
            re.IGNORECASE,
        ) or re.search(
            r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?search\s+([a-zA-Z0-9_\-\.]+?)\s+for\s+(.+)$",
            clean,
            re.IGNORECASE,
        )
        if web_search_match:
            dest_query = web_search_match.group(1).strip()
            search_query = web_search_match.group(2).strip().strip("\"'")
            if search_query.lower().startswith("for "):
                search_query = search_query[4:].strip()

            from avi.apps.destinations import DestinationResolver
            from avi.apps.models import DestinationType

            resolver = DestinationResolver()
            dest_res = resolver.resolve(dest_query)

            if dest_res.destination_type == DestinationType.WEBSITE or dest_res.url:
                start_url = dest_res.url
                dest_name = dest_res.target or dest_query.title()
                search_url = resolver.get_search_url(dest_query, search_query)

                steps = [
                    PlanStep(
                        step_id=1,
                        capability_name="browser.navigate",
                        arguments={"url": start_url},
                        description=f"Open {dest_name} in browser",
                    ),
                    PlanStep(
                        step_id=2,
                        capability_name="browser.observe",
                        arguments={},
                        description="Observe browser state",
                    ),
                    PlanStep(
                        step_id=3,
                        capability_name="browser.navigate",
                        arguments={"url": search_url},
                        description=f"Search {dest_name} for '{search_query}'",
                    ),
                ]
                return Plan(
                    user_goal=clean,
                    steps=steps[: self.max_steps],
                    max_steps=self.max_steps,
                )

        # Pattern: Press <key> in browser
        browser_key_match = re.search(
            r"^(?:in\s+(?:the\s+)?browser[,\s]+)?(?:please\s+|can\s+you\s+)?press\s+(\w+)(?:\s+key)?(?:\s+in\s+(?:the\s+)?browser)?$",
            clean,
            re.IGNORECASE,
        )
        if browser_key_match and ("browser" in lower or "key" in lower):
            k = browser_key_match.group(1).strip()
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="browser.press_key",
                    arguments={"key": k},
                    description=f"Press {k} in browser",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="browser.observe",
                    arguments={},
                    description="Observe browser state after key press",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: In browser, click <target> / click <target> in browser
        browser_click_match = re.search(
            r"^(?:in\s+(?:the\s+)?browser[,\s]+)?(?:please\s+|can\s+you\s+)?click(?:\s+on)?\s+(?:element\s+#?(\d+)|([^\n]+?))\s*(?:in\s+(?:the\s+)?browser)?$",
            clean,
            re.IGNORECASE,
        )
        if browser_click_match and ("browser" in lower or "element" in lower):
            el_id = browser_click_match.group(1)
            raw_target = browser_click_match.group(2)
            args = {}
            if el_id:
                args["element_id"] = int(el_id)
                desc = f"Click element #{el_id}"
            elif raw_target:
                raw_clean = raw_target.strip().strip("\"'")
                if raw_clean.isdigit():
                    args["element_id"] = int(raw_clean)
                    desc = f"Click element #{raw_clean}"
                elif raw_clean.startswith(("#", ".", "[")):
                    args["selector"] = raw_clean
                    desc = f"Click selector '{raw_clean}'"
                else:
                    args["text"] = raw_clean
                    desc = f"Click '{raw_clean}'"
            else:
                args = {}
                desc = "Click element in browser"

            if args:
                steps = [
                    PlanStep(
                        step_id=1,
                        capability_name="browser.click",
                        arguments=args,
                        description=desc,
                    ),
                    PlanStep(
                        step_id=2,
                        capability_name="browser.observe",
                        arguments={},
                        description="Observe browser state after click",
                    ),
                ]
                return Plan(
                    user_goal=clean,
                    steps=steps[: self.max_steps],
                    max_steps=self.max_steps,
                )

        # Pattern: Type <text> into <target> in browser
        browser_type_match = re.search(
            r"^(?:in\s+(?:the\s+)?browser[,\s]+)?(?:please\s+|can\s+you\s+)?type\s+([\"'].+?[\"']|[^\s]+)\s+(?:into|in)\s+(?:element\s+#?(\d+)|([^\n]+?))\s*(?:in\s+(?:the\s+)?browser)?$",
            clean,
            re.IGNORECASE,
        )
        if browser_type_match and ("browser" in lower or "element" in lower or "into" in lower):
            text_val = browser_type_match.group(1).strip().strip("\"'")
            el_id = browser_type_match.group(2)
            raw_target = browser_type_match.group(3)
            args = {"text": text_val}
            if el_id:
                args["element_id"] = int(el_id)
            elif raw_target:
                t_clean = raw_target.strip().strip("\"'")
                if t_clean.isdigit():
                    args["element_id"] = int(t_clean)
                elif t_clean.startswith(("#", ".", "[")):
                    args["selector"] = t_clean
                else:
                    args["selector"] = (
                        f"input[placeholder*='{t_clean}' i], input[name*='{t_clean}' i]"
                    )
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="browser.type",
                    arguments=args,
                    description=f"Type '{text_val}' into browser input",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="browser.observe",
                    arguments={},
                    description="Observe browser state after typing",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Scroll <dir> in browser
        browser_scroll_match = re.search(
            r"^(?:in\s+(?:the\s+)?browser[,\s]+)?(?:please\s+|can\s+you\s+)?scroll\s+(down|up|top|bottom)(?:\s+(?:the\s+)?page)?\s*(?:in\s+(?:the\s+)?browser)?$",
            clean,
            re.IGNORECASE,
        )
        if browser_scroll_match:
            s_dir = browser_scroll_match.group(1).strip().lower()
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="browser.scroll",
                    arguments={"direction": s_dir},
                    description=f"Scroll {s_dir} in browser",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="browser.observe",
                    arguments={},
                    description="Observe browser state after scrolling",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Find newest <ext/file> in <dir> and open it
        newest_match = re.search(
            r"\b(?:find|search(?:\s+for)?)(?:\s+the)?\s+(?:newest|latest)\s+(\w+)(?:\s+(?:in|under)\s+([^\s]+))?\s+(?:and|then)\s+(?:open|view|show)\s+(?:it|file)\b",
            clean,
            re.IGNORECASE,
        )
        if newest_match:
            ext = newest_match.group(1).lstrip(".").lower()
            search_path = _normalize_search_dir(newest_match.group(2))
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="filesystem.search",
                    arguments={
                        "extension": ext,
                        "path": search_path,
                        "newest_first": True,
                        "limit": 1,
                    },
                    description=f"Find newest {ext} in {search_path}",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="desktop.open_file",
                    arguments={},
                    description="Open found file",
                    pipe_from_step=1,
                    pipe_arg_name="path",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: 4-step pipeline: Find newest <ext> in <dir>, create <dir2>, move it, and open it
        pipeline_4step_match = re.search(
            r"\b(?:find|search(?:\s+for)?)(?:\s+the)?\s+(?:newest|latest)\s+(\w+)(?:\s+(?:in|under)\s+([^\s,]+))?[,\s]+create\s+(?:a\s+)?(?:directory\s+|folder\s+)?([^\s,]+)[,\s]+(?:then\s+)?move\s+it(?:\s+there)?[,\s]+(?:and\s+)?(?:then\s+)?(?:open|view)\s+(?:it|file)\b",
            clean,
            re.IGNORECASE,
        )
        if pipeline_4step_match:
            ext = pipeline_4step_match.group(1).lstrip(".").lower()
            search_path = _normalize_search_dir(pipeline_4step_match.group(2))
            new_dir = pipeline_4step_match.group(3).strip()
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="filesystem.search",
                    arguments={
                        "extension": ext,
                        "path": search_path,
                        "newest_first": True,
                        "limit": 1,
                    },
                    description=f"Find newest {ext} in {search_path}",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="filesystem.create_directory",
                    arguments={"path": new_dir},
                    description=f"Create folder {new_dir}",
                ),
                PlanStep(
                    step_id=3,
                    capability_name="filesystem.move",
                    arguments={"destination": new_dir},
                    description=f"Move {ext} to {new_dir}",
                    pipe_from_step=1,
                    pipe_arg_name="source",
                ),
                PlanStep(
                    step_id=4,
                    capability_name="desktop.open_file",
                    arguments={},
                    description="Open moved file",
                    pipe_from_step=3,
                    pipe_arg_name="path",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Create directory and copy file into it
        mkdir_copy_match = re.search(
            r"\bcreate\s+(?:a\s+)?(?:directory|folder)\s+(?:called|named\s+)?([^\s]+)\s+(?:and|then)\s+copy\s+([^\s]+)\s+(?:in|into|to)\s+it\b",
            clean,
            re.IGNORECASE,
        )
        if mkdir_copy_match:
            dir_name = mkdir_copy_match.group(1)
            src_file = mkdir_copy_match.group(2)
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="filesystem.create_directory",
                    arguments={"path": dir_name},
                    description=f"Create folder {dir_name}",
                ),
                PlanStep(
                    step_id=2,
                    capability_name="filesystem.copy",
                    arguments={"source": src_file, "destination": dir_name},
                    description=f"Copy {src_file} to {dir_name}",
                ),
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # Pattern: Find the newest <ext> in <dir> (standalone)
        find_newest = re.search(
            r"\b(?:find|search(?:\s+for)?)(?:\s+the)?\s+(?:newest|latest)\s+(\w+)(?:\s+(?:in|under)\s+([^\s\?]+))?\b",
            clean,
            re.IGNORECASE,
        )
        if find_newest and not re.search(r"\b(?:and|then)\b", lower):
            ext = find_newest.group(1).lstrip(".").lower()
            search_path = _normalize_search_dir(find_newest.group(2))
            steps = [
                PlanStep(
                    step_id=1,
                    capability_name="filesystem.search",
                    arguments={
                        "extension": ext,
                        "path": search_path,
                        "newest_first": True,
                        "limit": 5,
                    },
                    description=f"Find newest {ext} in {search_path}",
                )
            ]
            return Plan(
                user_goal=clean,
                steps=steps[: self.max_steps],
                max_steps=self.max_steps,
            )

        # -------------------------------------------------------------------
        # 2. Single-step capability patterns
        # -------------------------------------------------------------------

        # Single-step: Open local file (path with slash or extension)
        open_file_match = re.match(
            r"^(?:please\s+)?(?:open|view|show|display)(?:\s+(?:the|this|my))?\s+(?:file\s+)?([/~][^\s]+|[^\s]+\.(?:png|jpg|jpeg|pdf|txt|md|csv|py|json|html|log|sh))$",
            clean,
            re.IGNORECASE,
        )
        if open_file_match:
            file_target = open_file_match.group(1)
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.open_file",
                        arguments={"path": file_target},
                        description=f"Open file {file_target}",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Screenshot
        if re.match(
            r"^(?:please\s+)?(?:take(?:\s+a)?(?:\s+full)?\s+screenshot(?:\s+(?:of|for)\s+(?:my\s+)?(?:screen|desktop))?|capture(?:\s+the|\s+a|\s+my)?\s+screen(?:\s+shot)?|screenshot)$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.screenshot",
                        arguments={},
                        description="Take desktop screenshot",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Volume controls
        if re.match(
            r"^(?:please\s+)?(?:mute|silence)(?:\s+(?:the|my|our)\s+|\s+)?(?:volume|audio|sound)?$|"
            r"^(?:please\s+)?turn\s+off\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio|volume)$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.volume.set",
                        arguments={"action": "mute"},
                        description="Mute system audio",
                    )
                ],
            )
        if re.match(
            r"^(?:please\s+)?unmute(?:\s+(?:the|my|our)\s+|\s+)?(?:volume|audio|sound)?$|"
            r"^(?:please\s+)?turn\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio)\s+back\s+on$|"
            r"^(?:please\s+)?turn\s+(?:on\s+)?(?:the\s+|my\s+|our\s+)?(?:sound|audio)\s+on$|"
            r"^(?:please\s+)?turn\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio)\s+on$|"
            r"^(?:please\s+)?restore\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio|volume)$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.volume.set",
                        arguments={"action": "unmute"},
                        description="Unmute system audio",
                    )
                ],
            )
        set_vol_match = re.match(
            r"^(?:please\s+)?(?:set|change)\s+(?:the\s+)?volume(?:\s+to)?\s+(\d+)%?$", lower
        )
        if set_vol_match:
            level = int(set_vol_match.group(1))
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.volume.set",
                        arguments={"level": level},
                        description=f"Set volume to {level}%",
                    )
                ],
            )
        if re.match(
            r"^(?:please\s+)?(?:turn\s+up|raise|increase|boost)(?:\s+(?:the\s+)?volume)?(?:\s+(?:by\s+)?\d+%?)?$|"
            r"^volume\s+up(?:\s+(?:by\s+)?\d+%?)?$|"
            r"^(?:make\s+it\s+)?louder(?:\s+(?:by\s+)?\d+%?)?$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.volume.set",
                        arguments={"action": "raise"},
                        description="Raise volume by 5%",
                    )
                ],
            )
        if re.match(
            r"^(?:please\s+)?(?:turn\s+down|lower|decrease|reduce)(?:\s+(?:the\s+)?volume)?(?:\s+(?:by\s+)?\d+%?)?$|"
            r"^volume\s+down(?:\s+(?:by\s+)?\d+%?)?$|"
            r"^(?:make\s+it\s+)?quieter(?:\s+(?:by\s+)?\d+%?)?$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.volume.set",
                        arguments={"action": "lower"},
                        description="Lower volume by 5%",
                    )
                ],
            )
        if re.match(
            r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?volume|check\s+(?:the\s+)?volume|get\s+volume)\??$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.volume.get",
                        arguments={},
                        description="Get current volume level",
                    )
                ],
            )

        # Media controls
        if re.match(
            r"^(?:please\s+)?(?:play|resume)(?:\s+(?:the\s+)?(?:music|track|song|media|player))?$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.media",
                        arguments={"command": "play"},
                        description="Play media",
                    )
                ],
            )
        if re.match(
            r"^(?:please\s+)?(?:pause|stop)(?:\s+(?:the\s+)?(?:music|track|song|media|player))?$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.media",
                        arguments={"command": "pause"},
                        description="Pause media",
                    )
                ],
            )
        if re.match(
            r"^(?:please\s+)?(?:next\s+(?:track|song|music)|skip(?:\s+track|\s+song)?)$", lower
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.media",
                        arguments={"command": "next"},
                        description="Skip to next media track",
                    )
                ],
            )
        if re.match(r"^(?:please\s+)?(?:previous|prev)\s+(?:track|song|music)$", lower):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="system.media",
                        arguments={"command": "previous"},
                        description="Go to previous media track",
                    )
                ],
            )

        # Notification
        notif_match = re.match(
            r"^(?:please\s+)?(?:notify\s+me|send(?:\s+a)?\s+notification)(?:\s+(?:that|with|saying))?\s+(.+)$",
            clean,
            re.IGNORECASE,
        )
        if notif_match:
            msg = notif_match.group(1).strip()
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.notification",
                        arguments={"message": msg, "title": "AVI Notification"},
                        description="Send desktop notification",
                    )
                ],
            )

        # YouTube search
        yt_match = (
            re.match(
                r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:search|serch|look\s*up)\s+(?:on\s+)?(?:youtube|youtub|yotube)\s*(?:for)?\s+(.+)$",
                clean,
                re.IGNORECASE,
            )
            or re.match(
                r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:find(?:\s+me)?|search(?:\s+for)?|look\s*up|show(?:\s+me)?|watch)\s+(?:(?:videos?|clips?|tutorials?)\s+(?:about|on|for|of)\s+|video\s+(?:about|on|for|of)\s+)?(.+?)\s+(?:on|in)\s+(?:youtube|youtub|yotube)$",
                clean,
                re.IGNORECASE,
            )
            or re.match(
                r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:find(?:\s+me)?|search(?:\s+for)?|look\s*up|show(?:\s+me)?|watch)\s+(?:(?:videos?|clips?|tutorials?)\s+)?(?:on|in)\s+(?:youtube|youtub|yotube)\s+(?:(?:about|on|for|of)\s+)?(.+)$",
                clean,
                re.IGNORECASE,
            )
            or re.match(
                r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:open|launch)\s+(?:youtube|youtub|yotube)\s+(?:and\s+)?(?:search|find|look\s*up)\s*(?:for)?\s+(.+)$",
                clean,
                re.IGNORECASE,
            )
        )
        if yt_match:
            raw_q = yt_match.group(1).strip().strip("\"'")
            for pfx in ("for ", "about ", "on "):
                if raw_q.lower().startswith(pfx):
                    raw_q = raw_q[len(pfx) :].strip()
            if raw_q and raw_q.lower() not in (
                "something",
                "anything",
                "stuff",
                "videos",
                "a video",
                "video",
                "tutorials",
                "clips",
            ):
                return Plan(
                    user_goal=clean,
                    steps=[
                        PlanStep(
                            step_id=1,
                            capability_name="web.youtube.search",
                            arguments={"query": raw_q},
                            description=f"Search YouTube for {raw_q}",
                        )
                    ],
                )

        # -------------------------------------------------------------------
        # 3. Computer-use single-step patterns (clipboard, window, input, filesystem)
        # -------------------------------------------------------------------

        # Clipboard set
        if re.search(
            r"\b(?:copy|set)\s+(.+?)\s+(?:to|in|into)\s+(?:the\s+)?clipboard\b",
            clean,
            re.IGNORECASE,
        ) or re.search(r"\bcopy\s+to\s+clipboard\s+(.+)$", clean, re.IGNORECASE):
            clip_match = re.search(
                r"\b(?:copy|set)\s+(.+?)\s+(?:to|in|into)\s+(?:the\s+)?clipboard\b",
                clean,
                re.IGNORECASE,
            )
            if not clip_match:
                clip_match = re.search(r"\bcopy\s+to\s+clipboard\s+(.+)$", clean, re.IGNORECASE)
            text_to_copy = clip_match.group(1).strip().strip("\"'")
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.clipboard.set",
                        arguments={"text": text_to_copy},
                        description=f"Copy '{text_to_copy}' to clipboard",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Clipboard get
        if re.match(
            r"^(?:please\s+)?(?:read|get|show|check|view|inspect)\s+(?:the\s+)?clipboard(?:\s+content)?$|"
            r"^(?:what(?:'s|\s+is)\s+(?:on|in)\s+(?:the\s+|my\s+)?clipboard)\??$",
            lower,
        ):
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.clipboard.get",
                        arguments={},
                        description="Read clipboard content",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Window list
        win_list_match = re.match(
            r"^(?:please\s+)?(?:list|show|get|display)\s+(?:open\s+|active\s+|all\s+)?windows(?:\s+(?:matching|with|called|for)\s+(.+))?$",
            clean,
            re.IGNORECASE,
        )
        if win_list_match:
            win_q = win_list_match.group(1).strip() if win_list_match.group(1) else ""
            args = {"query": win_q} if win_q else {}
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.window.list",
                        arguments=args,
                        description=f"List windows{' matching ' + win_q if win_q else ''}",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Window focus
        win_focus_match = re.match(
            r"^(?:please\s+)?(?:focus|switch\s+to|activate|bring\s+to\s+front)\s+(?:the\s+)?(?:window\s+)?(.+)$",
            clean,
            re.IGNORECASE,
        )
        if win_focus_match:
            target_win = win_focus_match.group(1).strip()
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.window.focus",
                        arguments={"title": target_win},
                        description=f"Focus window '{target_win}'",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Keyboard typing
        type_match = re.match(
            r"^(?:please\s+)?(?:type\s+text|type)\s+(.+)$",
            clean,
            re.IGNORECASE,
        )
        if type_match:
            text_to_type = type_match.group(1).strip().strip("\"'")
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.input.type_text",
                        arguments={"text": text_to_type},
                        description=f"Type text '{text_to_type}'",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Keyboard key press
        press_match = re.match(
            r"^(?:please\s+)?(?:press(?:\s+key)?|hit(?:\s+key)?|send\s+key)\s+(.+)$",
            clean,
            re.IGNORECASE,
        )
        if press_match:
            key_name = press_match.group(1).strip().strip("\"'")
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="desktop.input.press_key",
                        arguments={"key": key_name},
                        description=f"Press key '{key_name}'",
                    )
                ],
                max_steps=self.max_steps,
            )

        # Filesystem search (general "find <query> in <dir>" or "search for <query> in <dir>")
        fs_search_match = re.match(
            r"^(?:please\s+)?(?:find|search(?:\s+for)?)\s+(?:files?\s+matching\s+|files?\s+with\s+|all\s+)?([^\s]+)\s+(?:in|under)\s+([^\s]+)$",
            clean,
            re.IGNORECASE,
        )
        if fs_search_match and not re.search(r"\b(?:and|then|youtube|yt)\b", lower):
            q_or_ext = fs_search_match.group(1).strip()
            target_dir = _normalize_search_dir(fs_search_match.group(2))
            args = {"path": target_dir, "limit": 10}
            if q_or_ext.startswith(".") or q_or_ext.lower() in (
                "pdf",
                "png",
                "jpg",
                "txt",
                "py",
                "md",
                "csv",
            ):
                args["extension"] = q_or_ext.lstrip(".")
            else:
                args["query"] = q_or_ext
            return Plan(
                user_goal=clean,
                steps=[
                    PlanStep(
                        step_id=1,
                        capability_name="filesystem.search",
                        arguments=args,
                        description=f"Search for '{q_or_ext}' in {target_dir}",
                    )
                ],
                max_steps=self.max_steps,
            )

        return None

    def replan(
        self,
        goal: str,
        completed_steps: list[PlanStep],
        failed_step: PlanStep,
        diagnosis: Any,
        current_observation: dict[str, Any] | None = None,
        attempted_strategies: list[str] | None = None,
    ) -> Plan | None:
        """Synthesize an adaptive recovery plan for the remaining goal."""
        from avi.agent.adaptive_planner import AdaptivePlanner

        adaptive = AdaptivePlanner(base_planner=self)
        return adaptive.replan(
            goal=goal,
            completed_steps=completed_steps,
            failed_step=failed_step,
            diagnosis=diagnosis,
            current_observation=current_observation,
            attempted_strategies=attempted_strategies,
        )

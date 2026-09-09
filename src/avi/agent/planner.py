"""Agent planner for decomposing user intents into bounded capability plans."""

import re
from typing import Any

from avi.agent.models import Plan, PlanStep


def _normalize_search_dir(dir_name: str | None) -> str:
    """Expand conversational directory names to standard user home directory paths."""
    if not dir_name:
        return "~/Downloads"
    d = dir_name.strip()
    d_lower = d.lower()
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
    return d


class AgentPlanner:
    """Deterministic and bounded plan synthesizer for assistant capabilities."""

    def __init__(self, max_steps: int = 5) -> None:
        self.max_steps = max_steps

    def create_plan(self, prompt: str, context: dict[str, Any] | None = None) -> Plan | None:
        """Analyze a user prompt and generate an executable Plan, or return None if unhandled."""
        clean = prompt.strip()
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
                yt_query = f"{creator_or_query.strip()} latest" if creator_or_query else "trending videos"
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
            yt_query = re.sub(r"\s+(?:on\s+youtube|on\s+yt)$", "", yt_query, flags=re.IGNORECASE).strip()
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
        if re.search(r"\b(?:copy|set)\s+(.+?)\s+(?:to|in|into)\s+(?:the\s+)?clipboard\b", clean, re.IGNORECASE) or re.search(r"\bcopy\s+to\s+clipboard\s+(.+)$", clean, re.IGNORECASE):
            clip_match = re.search(r"\b(?:copy|set)\s+(.+?)\s+(?:to|in|into)\s+(?:the\s+)?clipboard\b", clean, re.IGNORECASE)
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
            if q_or_ext.startswith(".") or q_or_ext.lower() in ("pdf", "png", "jpg", "txt", "py", "md", "csv"):
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

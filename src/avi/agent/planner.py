"""Agent planner for decomposing user intents into bounded capability plans."""

import re
from typing import Any

from avi.agent.models import Plan, PlanStep


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

        # Pattern: Find newest <ext/file> in <dir> and open it
        newest_match = re.search(
            r"\b(?:find|search(?:\s+for)?)(?:\s+the)?\s+(?:newest|latest)\s+(\w+)(?:\s+(?:in|under)\s+([^\s]+))?\s+(?:and|then)\s+(?:open|view|show)\s+(?:it|file)\b",
            clean,
            re.IGNORECASE,
        )
        if newest_match:
            ext = newest_match.group(1).lstrip(".")
            search_path = newest_match.group(2) or "~"
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
            ext = find_newest.group(1).lstrip(".")
            search_path = find_newest.group(2) or "~/Downloads"
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

        return None

"""Deterministic natural-language intent recognition for assistant actions and tools."""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# Regex pattern for timer requests
# Examples: "set a timer for 2 seconds", "timer for 10 mins", "timer 5s", "set timer 1 minute for tea"
_TIMER_RE = re.compile(
    r"^(?:please\s+)?(?:set\s+(?:a\s+)?|start\s+(?:a\s+)?|create\s+(?:a\s+)?)?"
    r"timer\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*"
    r"(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)"
    r"(?:\s+(?:called|labeled|for)\s+(.+))?$",
    re.IGNORECASE,
)

# Open / launch intent prefix
_OPEN_PREFIX_RE = re.compile(
    r"^(?:please\s+)?(?:open(?:\s+up)?|launch|start|run)\s+(.+)$",
    re.IGNORECASE,
)

# Common top-level domains and web identifiers
_URL_DOMAIN_RE = re.compile(
    r"^(?:https?://)?(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|edu|gov|co|ai|dev|app|me|info|tv)(?:/[^\s]*)?$",
    re.IGNORECASE,
)

# Common websites that can be referenced by name
_POPULAR_WEBSITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://www.github.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "wikipedia": "https://www.wikipedia.org",
    "gmail": "https://mail.google.com",
    "hackernews": "https://news.ycombinator.com",
    "hacker news": "https://news.ycombinator.com",
}


class AssistantIntentType(str, Enum):
    GREETING = "GREETING"
    CAPABILITIES = "CAPABILITIES"
    SMALL_TALK = "SMALL_TALK"
    COURTESY = "COURTESY"
    CLARIFICATION = "CLARIFICATION"
    DISK_SPACE = "DISK_SPACE"
    MEMORY_TOTAL = "MEMORY_TOTAL"
    RAM_USAGE = "RAM_USAGE"
    CPU_USAGE = "CPU_USAGE"
    PROCESSES = "PROCESSES"
    SYSTEM_INFO = "SYSTEM_INFO"
    TIMER = "TIMER"
    INVALID_REQUEST = "INVALID_REQUEST"
    OPEN_URL = "OPEN_URL"
    OPEN_FILE = "OPEN_FILE"
    OPEN_DIR = "OPEN_DIR"
    OPEN_APP = "OPEN_APP"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DetectedIntent:
    """Result of assistant intent analysis."""

    intent_type: AssistantIntentType
    raw_prompt: str
    target: str = ""
    extra: dict = None

    def __post_init__(self):
        if self.extra is None:
            object.__setattr__(self, "extra", {})


# Deictic and ambiguous phrasing requiring clarification
_AMBIGUOUS_DEICTIC_PATTERNS = {
    "open it": (
        "open",
        "What would you like me to open? You can specify an application name, file path, directory, or website URL.",
    ),
    "open this": (
        "open",
        "What would you like me to open? You can specify an application name, file path, directory, or website URL.",
    ),
    "open that": (
        "open",
        "What would you like me to open? You can specify an application name, file path, directory, or website URL.",
    ),
    "launch it": (
        "open",
        "What would you like me to launch? You can specify an application name or website URL.",
    ),
    "start it": ("open", "What would you like me to start?"),
    "run it": ("open", "What command or program would you like me to run?"),
    "delete it": ("delete", "Please specify the file or directory you would like to delete."),
    "delete that": ("delete", "Please specify the file or directory you would like to delete."),
    "delete this": ("delete", "Please specify the file or directory you would like to delete."),
    "remove it": ("delete", "Please specify the file or directory you would like to remove."),
    "remove that": ("delete", "Please specify the file or directory you would like to remove."),
    "rm that": ("delete", "Please specify the file or directory you would like to delete."),
    "open the project": (
        "project",
        "Which project folder would you like to open? Please provide a path or project name.",
    ),
    "open project": (
        "project",
        "Which project folder would you like to open? Please provide a path or project name.",
    ),
    "clean it up": (
        "clean",
        "What would you like to clean up? For example, temporary files, package caches, or a specific directory.",
    ),
    "clean it": (
        "clean",
        "What would you like to clean up? For example, temporary files, package caches, or a specific directory.",
    ),
    "clean that up": (
        "clean",
        "What would you like to clean up? For example, temporary files, package caches, or a specific directory.",
    ),
    "clean up": (
        "clean",
        "What would you like to clean up? For example, temporary files, package caches, or a specific directory.",
    ),
    "send this": ("send", "What would you like to send and where?"),
    "send it": ("send", "What would you like to send and where?"),
    "close it": ("close", "Which application or window would you like to close?"),
    "close that": ("close", "Which application or window would you like to close?"),
    "show it": ("general", "Could you specify what you would like to see?"),
}

# Regex to detect any timer attempt, valid or invalid
_TIMER_ATTEMPT_RE = re.compile(
    r"^(?:please\s+)?(?:set\s+(?:a\s+)?|start\s+(?:a\s+)?|create\s+(?:a\s+)?)?timer(?:\s+(.*))?$",
    re.IGNORECASE,
)


def resolve_desktop_folder(target: str) -> Path | None:
    """Resolve natural desktop folder expressions to real directory paths.

    Examples:
      'my Documents folder' -> ~/Documents
      'downloads' -> ~/Downloads
      'my desktop' -> ~/Desktop
      'home' -> ~
    """
    clean = target.strip()
    clean = re.sub(r"^(?:my|the|our)\s+", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+(?:folder|directory|dir)$", "", clean, flags=re.IGNORECASE).strip().lower()

    home = Path.home()
    alias_map: dict[str, Path] = {
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "desktop": home / "Desktop",
        "pictures": home / "Pictures",
        "videos": home / "Videos",
        "music": home / "Music",
        "projects": home / "Projects",
        "home": home,
    }

    if clean in alias_map:
        return alias_map[clean]
    return None


def parse_duration_seconds(amount_str: str, unit_str: str) -> float:
    """Convert amount and unit string into total seconds."""
    val = float(amount_str)
    unit = unit_str.lower().rstrip("s")
    if unit in ("s", "sec", "second"):
        return val
    elif unit in ("m", "min", "minute"):
        return val * 60.0
    elif unit in ("h", "hr", "hour"):
        return val * 3600.0
    return val


def detect_assistant_intent(prompt: str, last_turn: Any | None = None) -> DetectedIntent:
    """Analyze prompt and detect if it maps to a native assistant capability."""
    s = prompt.strip().rstrip("?.!").strip()
    if not s:
        return DetectedIntent(intent_type=AssistantIntentType.UNKNOWN, raw_prompt=prompt)
    lower = s.lower()

    # 1. Ambiguous deictic requests (clarification required)
    if lower in _AMBIGUOUS_DEICTIC_PATTERNS:
        kind, msg = _AMBIGUOUS_DEICTIC_PATTERNS[lower]
        return DetectedIntent(
            intent_type=AssistantIntentType.CLARIFICATION,
            raw_prompt=prompt,
            target=lower,
            extra={"clarification_type": kind, "message": msg},
        )

    # 2. Conversational Follow-up based on last turn
    if last_turn is not None:
        last_intent = getattr(last_turn, "intent_type", None)
        last_intent_val = getattr(last_intent, "value", str(last_intent))

        if last_intent_val in ("DISK_SPACE", AssistantIntentType.DISK_SPACE.value):
            if lower in (
                "what is taking up the most space",
                "what's taking up the most space",
                "what's using the most space",
                "what is using the most space",
                "where is my space going",
                "what is using the space",
            ):
                return DetectedIntent(
                    intent_type=AssistantIntentType.DISK_SPACE,
                    raw_prompt=prompt,
                    extra={"follow_up": "disk_breakdown"},
                )

        if last_intent_val in (
            "MEMORY_TOTAL",
            "RAM_USAGE",
            AssistantIntentType.MEMORY_TOTAL.value,
            AssistantIntentType.RAM_USAGE.value,
        ):
            if lower in (
                "which app is using the most",
                "which app is using the most memory",
                "which app is using the most ram",
                "what is using the most",
                "who is using it",
                "what's using it",
            ):
                return DetectedIntent(
                    intent_type=AssistantIntentType.RAM_USAGE,
                    raw_prompt=prompt,
                )

        if last_intent_val in ("CPU_USAGE", AssistantIntentType.CPU_USAGE.value):
            if lower in (
                "which app is using the most",
                "which app is using the most cpu",
                "what is using the most",
                "who is using it",
            ):
                return DetectedIntent(
                    intent_type=AssistantIntentType.CPU_USAGE,
                    raw_prompt=prompt,
                )

    # 3. Greetings
    if lower in (
        "hi",
        "hello",
        "hey",
        "howdy",
        "greetings",
        "good morning",
        "good afternoon",
        "good evening",
        "yo",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.GREETING, raw_prompt=prompt)

    # 4. Courtesy / pleasantries
    if lower in (
        "thanks",
        "thank you",
        "thank you very much",
        "thx",
        "many thanks",
        "cheers",
        "appreciate it",
        "thank you so much",
        "thanks a lot",
        "ty",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.COURTESY, raw_prompt=prompt)

    # 5. Capabilities / help
    if lower in (
        "what can you do",
        "what can you do for me",
        "what are your capabilities",
        "who are you",
        "help",
        "help me",
        "can you help me",
        "how can you help",
        "what is avi",
        "tell me what you can do",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.CAPABILITIES, raw_prompt=prompt)

    # 6. Small talk
    if lower in (
        "what's up",
        "whats up",
        "what is up",
        "how are you",
        "how are you doing",
        "how's it going",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.SMALL_TALK, raw_prompt=prompt)

    # 7. Disk space queries (conversational)
    if any(
        phrase in lower
        for phrase in (
            "how much space is left",
            "how much space do i have",
            "how much disk space",
            "how much storage is left",
            "how much storage do i have",
            "check disk space",
            "disk space left",
            "free disk space",
            "free storage",
            "storage left",
            "storage space",
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.DISK_SPACE, raw_prompt=prompt)

    # 8. RAM / Memory overview (conversational total/free)
    if any(
        phrase in lower
        for phrase in (
            "how much memory is free",
            "how much ram is free",
            "how much memory do i have",
            "how much ram do i have",
            "how much memory left",
            "how much ram left",
            "how much memory is available",
            "how much ram is available",
            "free memory",
            "free ram",
            "check memory",
            "check ram",
            "memory available",
            "available memory",
            "available ram",
            "ram available",
            "memory usage",
            "ram usage",
            "how much memory is used",
            "how much ram is used",
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.MEMORY_TOTAL, raw_prompt=prompt)

    # 9. RAM / Memory process queries (conversational)
    if any(
        phrase in lower
        for phrase in (
            "what is using the most ram",
            "what's using the most ram",
            "what is using my ram",
            "what's using my ram",
            "what is using the most memory",
            "what's using the most memory",
            "which app is using the most ram",
            "which app is using the most memory",
            "top memory",
            "who is using my ram",
            "top memory processes",
            "what process is using the most ram",
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.RAM_USAGE, raw_prompt=prompt)

    # 10. CPU queries (conversational)
    if any(
        phrase in lower
        for phrase in (
            "what is using the most cpu",
            "what's using the most cpu",
            "which app is using the most cpu",
            "top cpu",
            "high cpu",
            "what is using my cpu",
            "who is using my cpu",
            "top cpu processes",
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.CPU_USAGE, raw_prompt=prompt)

    # 11. Running processes queries
    if lower in (
        "what is running on my computer",
        "what's running on my computer",
        "what is running",
        "what's running",
        "show running processes",
        "list running processes",
        "what processes are running",
        "running processes",
        "show processes",
        "list processes",
        "show all processes",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.PROCESSES, raw_prompt=prompt)

    # 12. System info queries
    if lower in (
        "what operating system am i on",
        "what is my operating system",
        "what is my os",
        "system info",
        "system information",
        "what kernel am i running",
        "what is my linux version",
        "os info",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.SYSTEM_INFO, raw_prompt=prompt)

    # 13. Timers (valid & invalid syntax)
    timer_match = _TIMER_RE.match(s)
    if timer_match:
        amount_str, unit_str, label = timer_match.groups()
        dur = parse_duration_seconds(amount_str, unit_str)
        if dur <= 0:
            return DetectedIntent(
                intent_type=AssistantIntentType.INVALID_REQUEST,
                raw_prompt=prompt,
                extra={
                    "reason": "duration_non_positive",
                    "message": "Timer duration must be greater than zero. For example: 'set a timer for 5 minutes'.",
                },
            )
        return DetectedIntent(
            intent_type=AssistantIntentType.TIMER,
            raw_prompt=prompt,
            extra={"duration_seconds": dur, "label": label or ""},
        )

    # If the user attempted a timer command but syntax was invalid (e.g. "set a timer for banana")
    if _TIMER_ATTEMPT_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.INVALID_REQUEST,
            raw_prompt=prompt,
            extra={
                "reason": "invalid_timer_syntax",
                "message": "Please specify a valid duration for the timer, such as 'set a timer for 5 minutes' or 'timer 30 seconds'.",
            },
        )

    # 14. Open / Launch requests
    open_match = _OPEN_PREFIX_RE.match(s)
    if open_match:
        target = open_match.group(1).strip()
        target_lower = target.lower()

        # Check ambiguous targets inside open syntax ("open it", "open the project", etc.)
        if target_lower in ("it", "this", "that"):
            return DetectedIntent(
                intent_type=AssistantIntentType.CLARIFICATION,
                raw_prompt=prompt,
                target=target,
                extra={
                    "clarification_type": "open",
                    "message": "What would you like me to open? You can specify an application name, file path, directory, or website URL.",
                },
            )
        if target_lower in ("project", "the project", "a project"):
            return DetectedIntent(
                intent_type=AssistantIntentType.CLARIFICATION,
                raw_prompt=prompt,
                target=target,
                extra={
                    "clarification_type": "project",
                    "message": "Which project folder would you like to open? Please provide a path or project name.",
                },
            )
        if target_lower in ("file", "the file", "a file"):
            return DetectedIntent(
                intent_type=AssistantIntentType.CLARIFICATION,
                raw_prompt=prompt,
                target=target,
                extra={
                    "clarification_type": "file",
                    "message": "Please specify the file name or path you would like to open.",
                },
            )
        if target_lower in ("folder", "the folder", "directory", "the directory"):
            return DetectedIntent(
                intent_type=AssistantIntentType.CLARIFICATION,
                raw_prompt=prompt,
                target=target,
                extra={
                    "clarification_type": "folder",
                    "message": "Please specify the folder or directory path you would like to open.",
                },
            )

        # Check popular websites
        if target_lower in _POPULAR_WEBSITES:
            return DetectedIntent(
                intent_type=AssistantIntentType.OPEN_URL,
                raw_prompt=prompt,
                target=_POPULAR_WEBSITES[target_lower],
            )

        # Check URL or domain
        if target_lower.startswith(("http://", "https://")) or _URL_DOMAIN_RE.match(target):
            url = target if target.startswith(("http://", "https://")) else f"https://{target}"
            return DetectedIntent(
                intent_type=AssistantIntentType.OPEN_URL,
                raw_prompt=prompt,
                target=url,
            )

        # Check desktop folder aliases (e.g. "my Documents folder", "downloads", "my desktop")
        folder_alias = resolve_desktop_folder(target)
        if folder_alias is not None:
            return DetectedIntent(
                intent_type=AssistantIntentType.OPEN_DIR,
                raw_prompt=prompt,
                target=str(folder_alias),
            )

        # Check local file or directory
        expanded_path = Path(target).expanduser()
        if expanded_path.exists():
            if expanded_path.is_dir():
                return DetectedIntent(
                    intent_type=AssistantIntentType.OPEN_DIR,
                    raw_prompt=prompt,
                    target=str(expanded_path),
                )
            else:
                return DetectedIntent(
                    intent_type=AssistantIntentType.OPEN_FILE,
                    raw_prompt=prompt,
                    target=str(expanded_path),
                )

        # Otherwise, treat target as an application to resolve
        return DetectedIntent(
            intent_type=AssistantIntentType.OPEN_APP,
            raw_prompt=prompt,
            target=target,
        )

    return DetectedIntent(intent_type=AssistantIntentType.UNKNOWN, raw_prompt=prompt)

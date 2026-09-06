"""Deterministic natural-language intent recognition for assistant actions and tools."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re

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
    DISK_SPACE = "DISK_SPACE"
    MEMORY_TOTAL = "MEMORY_TOTAL"
    RAM_USAGE = "RAM_USAGE"
    CPU_USAGE = "CPU_USAGE"
    PROCESSES = "PROCESSES"
    SYSTEM_INFO = "SYSTEM_INFO"
    TIMER = "TIMER"
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


def detect_assistant_intent(prompt: str) -> DetectedIntent:
    """Analyze prompt and detect if it maps to a native assistant capability."""
    s = prompt.strip().rstrip("?.!").strip()
    lower = s.lower()

    # 1. Greetings
    if lower in ("hi", "hello", "hey", "howdy", "greetings", "good morning", "good afternoon", "good evening"):
        return DetectedIntent(intent_type=AssistantIntentType.GREETING, raw_prompt=prompt)

    # 2. Capabilities / help
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

    # 3. Small talk
    if lower in ("what's up", "whats up", "what is up", "how are you", "how are you doing", "how's it going"):
        return DetectedIntent(intent_type=AssistantIntentType.SMALL_TALK, raw_prompt=prompt)

    # 4. Disk space queries (conversational)
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
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.DISK_SPACE, raw_prompt=prompt)

    # 5. RAM / Memory overview (conversational total/free)
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
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.MEMORY_TOTAL, raw_prompt=prompt)

    # 6. RAM / Memory process queries (conversational)
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
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.RAM_USAGE, raw_prompt=prompt)

    # 6. CPU queries (conversational)
    if any(
        phrase in lower
        for phrase in (
            "what is using the most cpu",
            "what's using the most cpu",
            "which app is using the most cpu",
            "top cpu",
            "high cpu",
        )
    ):
        return DetectedIntent(intent_type=AssistantIntentType.CPU_USAGE, raw_prompt=prompt)

    # 7. Running processes queries
    if lower in (
        "show running processes",
        "list running processes",
        "what processes are running",
        "running processes",
        "show processes",
        "list processes",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.PROCESSES, raw_prompt=prompt)

    # 8. System info queries
    if lower in (
        "what operating system am i on",
        "what is my operating system",
        "what is my os",
        "system info",
        "system information",
        "what kernel am i running",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.SYSTEM_INFO, raw_prompt=prompt)

    # 9. Timers
    timer_match = _TIMER_RE.match(s)
    if timer_match:
        amount_str, unit_str, label = timer_match.groups()
        dur = parse_duration_seconds(amount_str, unit_str)
        return DetectedIntent(
            intent_type=AssistantIntentType.TIMER,
            raw_prompt=prompt,
            extra={"duration_seconds": dur, "label": label or ""},
        )

    # 10. Open / Launch requests
    open_match = _OPEN_PREFIX_RE.match(s)
    if open_match:
        target = open_match.group(1).strip()
        target_lower = target.lower()

        # Check if target is a known popular website name
        if target_lower in _POPULAR_WEBSITES:
            return DetectedIntent(
                intent_type=AssistantIntentType.OPEN_URL,
                raw_prompt=prompt,
                target=_POPULAR_WEBSITES[target_lower],
            )

        # Check if target is a URL or domain
        if target_lower.startswith(("http://", "https://")) or _URL_DOMAIN_RE.match(target):
            url = target if target.startswith(("http://", "https://")) else f"https://{target}"
            return DetectedIntent(
                intent_type=AssistantIntentType.OPEN_URL,
                raw_prompt=prompt,
                target=url,
            )

        # Check if target is a local file or directory
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

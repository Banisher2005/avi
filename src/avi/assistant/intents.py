import difflib
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
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:open(?:\s+up)?|launch|start|run)\s+(.+)$",
    re.IGNORECASE,
)

# Screenshot capture intent
_SCREENSHOT_RE = re.compile(
    r"^(?:please\s+)?(?:take(?:\s+a)?\s+screen\s*shot|capture(?:\s+(?:the|my))?\s+screen|screen\s*shot|grab(?:\s+a)?\s+screen\s*shot)$",
    re.IGNORECASE,
)

# Volume controls
_MUTE_RE = re.compile(
    r"^(?:please\s+)?(?:mute|silence)(?:\s+(?:the|my|our)\s+|\s+)?(?:volume|audio|sound)?$|"
    r"^(?:please\s+)?turn\s+off\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio|volume)$",
    re.IGNORECASE,
)

_UNMUTE_RE = re.compile(
    r"^(?:please\s+)?unmute(?:\s+(?:the|my|our)\s+|\s+)?(?:volume|audio|sound)?$|"
    r"^(?:please\s+)?turn\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio)\s+back\s+on$|"
    r"^(?:please\s+)?turn\s+(?:on\s+)?(?:the\s+|my\s+|our\s+)?(?:sound|audio)\s+on$|"
    r"^(?:please\s+)?turn\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio)\s+on$|"
    r"^(?:please\s+)?restore\s+(?:the\s+|my\s+|our\s+)?(?:sound|audio|volume)$",
    re.IGNORECASE,
)

_VOL_MAX_RE = re.compile(
    r"^(?:please\s+)?(?:(?:increase|set|turn|raise|boost)\s+(?:(?:the|my|our)\s+)?(?:volume|audio|sound)?\s*(?:to|at)?\s*(?:max|maximum|100%?)|volume\s+(?:to\s+)?(?:max|maximum|100%?)|max\s+volume)$",
    re.IGNORECASE,
)

_VOL_MIN_RE = re.compile(
    r"^(?:please\s+)?(?:(?:decrease|lower|set|turn|reduce)\s+(?:(?:the|my|our)\s+)?(?:volume|audio|sound)?\s*(?:to|at)?\s*(?:min|minimum|0%?)|volume\s+(?:to\s+)?(?:min|minimum|0%?)|min\s+volume)$",
    re.IGNORECASE,
)

_VOL_BY_UP_RE = re.compile(
    r"^(?:please\s+)?(?:increase|raise|boost|turn\s+up)\s+(?:(?:the|my|our)\s+)?(?:volume|audio|sound)?\s*(?:by)?\s*(\d+)%?$",
    re.IGNORECASE,
)

_VOL_BY_DOWN_RE = re.compile(
    r"^(?:please\s+)?(?:decrease|lower|reduce|turn\s+down)\s+(?:(?:the|my|our)\s+)?(?:volume|audio|sound)?\s*(?:by)?\s*(\d+)%?$",
    re.IGNORECASE,
)

_VOL_SET_RE = re.compile(
    r"^(?:please\s+)?(?:(?:set|change|increase|decrease|turn|adjust)\s+(?:(?:the|my|our)\s+)?(?:volume|audio|sound)\s+(?:to|at)?\s*(\d+)%?|volume\s+(?:to\s+)?(\d+)%?)$",
    re.IGNORECASE,
)

_VOL_UP_RE = re.compile(
    r"^(?:please\s+)?(?:turn\s+(?:(?:the|my|our)\s+)?volume\s+up|"
    r"turn\s+it\s+up|"
    r"turn\s+up(?:\s+(?:the|my|our))?\s+volume|"
    r"raise(?:\s+(?:the|my|our))?\s+volume|"
    r"increase(?:\s+(?:the|my|our))?\s+volume|"
    r"boost(?:\s+(?:the|my|our))?\s+volume|"
    r"volume\s+up|"
    r"(?:make\s+it\s+)?louder)$",
    re.IGNORECASE,
)

_VOL_DOWN_RE = re.compile(
    r"^(?:please\s+)?(?:turn\s+(?:(?:the|my|our)\s+)?volume\s+down|"
    r"turn\s+it\s+down|"
    r"turn\s+down(?:\s+(?:the|my|our))?\s+volume|"
    r"lower(?:\s+(?:the|my|our))?\s+volume|"
    r"decrease(?:\s+(?:the|my|our))?\s+volume|"
    r"reduce(?:\s+(?:the|my|our))?\s+volume|"
    r"volume\s+down|"
    r"(?:make\s+it\s+)?(?:quieter|softer))$",
    re.IGNORECASE,
)

_VOL_GET_RE = re.compile(
    r"^(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:volume|audio\s+level)|check\s+(?:(?:the|my|our)\s+)?(?:volume|audio)|get\s+(?:(?:the|my|our)\s+)?volume|current\s+volume|volume\s+level|how\s+loud\s+is\s+it)\??$",
    re.IGNORECASE,
)

# Media playback controls
_MEDIA_RE = re.compile(
    r"^(?:please\s+)?(?:pause\s+(?:music|song|playback|audio|video)|stop\s+(?:music|playback)|resume\s+(?:music|playback)|play\s+(?:music|playback)|next\s+(?:song|track)|previous\s+(?:song|track))$",
    re.IGNORECASE,
)

# YouTube search intent patterns
_YOUTUBE_PREFIX_SEARCH_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:"
    r"(?:search|serch|look\s*up)\s+(?:on\s+)?(?:youtube|youtub|yotube)\s*(?:for)?|"
    r"(?:open|launch)\s+(?:youtube|youtub|yotube)\s+(?:and\s+)?(?:search|find|look\s*up)\s*(?:for)?|"
    r"(?:open|launch)\s+(?:youtube|youtub|yotube)\s*(?:for)?|"
    r"(?:youtube|youtub|yotube)\s*(?:search|serch)?\s*(?:for)?"
    r")"
    r"(?:\s+(.+))?$",
    re.IGNORECASE,
)

_YOUTUBE_SUFFIX_SEARCH_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:find(?:\s+me)?|search(?:\s+for)?|look\s*up|show(?:\s+me)?|watch|open|launch|play)\s+"
    r"(?:(?:videos?|clips?|tutorials?)\s+(?:about|on|for|of)\s+|video\s+(?:about|on|for|of)\s+)?"
    r"(.+?)"
    r"\s+(?:on|in)\s+(?:youtube|youtub|yotube)$",
    re.IGNORECASE,
)

_YOUTUBE_INFIX_SEARCH_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:find(?:\s+me)?|search(?:\s+for)?|look\s*up|show(?:\s+me)?|watch|open|launch|play)\s+"
    r"(?:(?:videos?|clips?|tutorials?)\s+)?"
    r"(?:on|in)\s+(?:youtube|youtub|yotube)\s+"
    r"(?:(?:about|on|for|of)\s+)?"
    r"(.+)$",
    re.IGNORECASE,
)

_YOUTUBE_QUESTION_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:"
    # alt 1: "which/what YouTube videos about X look best?"
    r"(?:which|what)\s+(?:(?:youtube\s+)?(?:videos?|tutorials?)|(?:videos?|tutorials?)\s+(?:on|in)\s+youtube)\b"
    r"(?:\s+(?:about|on|for|of)\s+)?(.+?)(?:\s+(?:look\s+best|are\s+best|look\s+good|to\s+watch))?$"
    r"|"
    # alt 2: "what's/what is the best YouTube video on X"
    r"what'?s?\s+(?:the\s+)?(?:best|top|good|great)\s+(?:youtube\s+)?(?:videos?|tutorials?)\s+(?:on|about|for|of)\s+(.+)$"
    r")",
    re.IGNORECASE,
)

_YOUTUBE_GENERIC_VIDEO_RE = re.compile(
    r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:find(?:\s+me)?|search(?:\s+for)?|look\s*up|show(?:\s+me)?|recommend(?:\s+me)?|suggest(?:\s+me)?|watch|get(?:\s+me)?)\s+"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:(?:good|great|best|top|short|recommended|beginner(?:-friendly)?)\s+)?"
    r"(?:(?:youtube\s+)?videos?|tutorials?|clips?)\s+"
    r"(?:(?:about|on|for|of)\s+)"
    r"(.+)$",
    re.IGNORECASE,
)

# Common top-level domains and web identifiers
_URL_DOMAIN_RE = re.compile(
    r"^(?:https?://)?(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|edu|gov|co|ai|dev|app|me|info|tv)(?:/[^\s]*)?$",
    re.IGNORECASE,
)

# Deterministic greeting recognition (supports repeated characters: hiiiiii, hellooo, heyyy, etc.)
_GREETING_RE = re.compile(
    r"^(?:"
    r"h+[i!]+[ye]*|"               # hi, hii, hiiiiii, hie, hi!
    r"h+e+y+|"                    # hey, heyy, heyyy
    r"h+e+l+l*o+|"                # helo, hello, helloo, hellooooo
    r"y+o+|"                      # yo, yoo, yooo
    r"h+o+w+d+y+|"                # howdy
    r"w+a+s+s+u+p+|"              # wassup
    r"s+u+p+|"                    # sup
    r"h+i+y+a+|"                  # hiya
    r"greetings|"                 # greetings
    r"good\s+(?:morning|afternoon|evening|day|night)|"
    r"what'?s\s+up"
    r")"
    r"(?:\s+(?:avi|there|assistant|bot|friend|everyone))?$",
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
    ACTIVATE = "ACTIVATE"
    SCREENSHOT = "SCREENSHOT"
    VOLUME_SET = "VOLUME_SET"
    VOLUME_GET = "VOLUME_GET"
    MEDIA_CONTROL = "MEDIA_CONTROL"
    YOUTUBE_SEARCH = "YOUTUBE_SEARCH"
    YOUTUBE_RECOMMEND = "YOUTUBE_RECOMMEND"
    OPEN_SEARCH_RESULT = "OPEN_SEARCH_RESULT"
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
        "I don't have a recent result to open. Please specify an application name, file path, directory, or website URL.",
    ),
    "open this": (
        "open",
        "I don't have a recent result to open. Please specify an application name, file path, directory, or website URL.",
    ),
    "open that": (
        "open",
        "I don't have a recent result to open. Please specify an application name, file path, directory, or website URL.",
    ),
    "open that one": (
        "open",
        "I don't have a recent result to open. Please specify an application name, file path, directory, or website URL.",
    ),
    "play it": (
        "open",
        "I don't have a recent result to open. Please specify what you'd like to play or search for videos first.",
    ),
    "play that": (
        "open",
        "I don't have a recent result to open. Please specify what you'd like to play or search for videos first.",
    ),
    "watch it": (
        "open",
        "I don't have a recent result to open. Please specify what you'd like to watch or search for videos first.",
    ),
    "watch that": (
        "open",
        "I don't have a recent result to open. Please specify what you'd like to watch or search for videos first.",
    ),
    "use it": (
        "open",
        "I don't have a recent result to open. Please specify an application or search result.",
    ),
    "use the first one": (
        "open",
        "I don't have a recent result to open. Please search for videos or results first.",
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


_CANONICAL_DESKTOP_TARGETS = [
    "activate",
    "activate avi",
    "take a screenshot",
    "take screenshot",
    "screenshot",
    "capture screen",
    "increase volume",
    "turn volume up",
    "volume up",
    "raise volume",
    "louder",
    "decrease volume",
    "turn volume down",
    "volume down",
    "lower volume",
    "softer",
    "quieter",
    "mute volume",
    "mute",
    "unmute volume",
    "unmute",
    "restore sound",
    "open downloads",
    "downloads",
    "open documents",
    "documents",
    "open pictures",
    "pictures",
    "open desktop",
    "desktop",
    "pause music",
    "play music",
    "next track",
    "previous track",
    "stop music",
    "search youtube",
]

_BOUNDED_DESKTOP_INTENT_MAP = {
    # Activation
    "activate": (AssistantIntentType.ACTIVATE, {}),
    "activate avi": (AssistantIntentType.ACTIVATE, {}),
    # Screenshot
    "take a screenshot": (AssistantIntentType.SCREENSHOT, {}),
    "take screenshot": (AssistantIntentType.SCREENSHOT, {}),
    "screenshot": (AssistantIntentType.SCREENSHOT, {}),
    "capture screen": (AssistantIntentType.SCREENSHOT, {}),
    # Volume up
    "increase volume": (AssistantIntentType.VOLUME_SET, {"action": "raise", "delta": 5}),
    "turn volume up": (AssistantIntentType.VOLUME_SET, {"action": "raise", "delta": 5}),
    "volume up": (AssistantIntentType.VOLUME_SET, {"action": "raise", "delta": 5}),
    "raise volume": (AssistantIntentType.VOLUME_SET, {"action": "raise", "delta": 5}),
    "louder": (AssistantIntentType.VOLUME_SET, {"action": "raise", "delta": 5}),
    # Volume down
    "decrease volume": (AssistantIntentType.VOLUME_SET, {"action": "lower", "delta": 5}),
    "turn volume down": (AssistantIntentType.VOLUME_SET, {"action": "lower", "delta": 5}),
    "volume down": (AssistantIntentType.VOLUME_SET, {"action": "lower", "delta": 5}),
    "lower volume": (AssistantIntentType.VOLUME_SET, {"action": "lower", "delta": 5}),
    "softer": (AssistantIntentType.VOLUME_SET, {"action": "lower", "delta": 5}),
    "quieter": (AssistantIntentType.VOLUME_SET, {"action": "lower", "delta": 5}),
    # Mute / Unmute
    "mute volume": (AssistantIntentType.VOLUME_SET, {"action": "mute"}),
    "mute": (AssistantIntentType.VOLUME_SET, {"action": "mute"}),
    "unmute volume": (AssistantIntentType.VOLUME_SET, {"action": "unmute"}),
    "unmute": (AssistantIntentType.VOLUME_SET, {"action": "unmute"}),
    "restore sound": (AssistantIntentType.VOLUME_SET, {"action": "unmute"}),
    # Folders
    "open downloads": (AssistantIntentType.OPEN_DIR, {"folder": "Downloads"}),
    "downloads": (AssistantIntentType.OPEN_DIR, {"folder": "Downloads"}),
    "open documents": (AssistantIntentType.OPEN_DIR, {"folder": "Documents"}),
    "documents": (AssistantIntentType.OPEN_DIR, {"folder": "Documents"}),
    "open pictures": (AssistantIntentType.OPEN_DIR, {"folder": "Pictures"}),
    "pictures": (AssistantIntentType.OPEN_DIR, {"folder": "Pictures"}),
    "open desktop": (AssistantIntentType.OPEN_DIR, {"folder": "Desktop"}),
    "desktop": (AssistantIntentType.OPEN_DIR, {"folder": "Desktop"}),
    # Media
    "pause music": (AssistantIntentType.MEDIA_CONTROL, {"action": "pause"}),
    "play music": (AssistantIntentType.MEDIA_CONTROL, {"action": "play"}),
    "next track": (AssistantIntentType.MEDIA_CONTROL, {"action": "next"}),
    "previous track": (AssistantIntentType.MEDIA_CONTROL, {"action": "previous"}),
    "stop music": (AssistantIntentType.MEDIA_CONTROL, {"action": "stop"}),
    # YouTube Search
    "search youtube": (
        AssistantIntentType.CLARIFICATION,
        {
            "clarification_type": "youtube_search",
            "message": "What would you like me to search for on YouTube?",
        },
    ),
}


def _is_recommendation_request(prompt: str) -> bool:
    """Check if prompt asks for qualitative video recommendation rather than simple search."""
    lower = prompt.lower()
    return bool(
        re.search(
            r"\b(?:good|great|best|top|recommended|recommend|suggest|short|beginner(?:-friendly)?|under\s+\d+\s+minutes?|under\s+an\s+hour)\b",
            lower,
        )
        or re.search(r"^(?:which|what)\s+(?:youtube\s+videos?|videos?\s+on\s+youtube)\b", lower)
    )


def _extract_constraints(prompt: str) -> dict[str, Any]:
    """Extract verified constraints from prompt."""
    constraints: dict[str, Any] = {}
    m_dur = re.search(r"under\s+(\d+)\s+minutes?", prompt, re.IGNORECASE)
    if m_dur:
        constraints["max_minutes"] = int(m_dur.group(1))
        constraints["max_seconds"] = int(m_dur.group(1)) * 60
    elif re.search(r"under\s+an\s+hour", prompt, re.IGNORECASE):
        constraints["max_minutes"] = 60
        constraints["max_seconds"] = 3600

    if re.search(r"\bbeginner(?:-friendly)?\b", prompt, re.IGNORECASE):
        constraints["beginner"] = True
    return constraints


def _clean_recommend_query(query: str) -> str:
    """Clean out recommendation qualifiers from search query string."""
    s = query.strip().strip("\"'")
    s = re.sub(r"\s+under\s+(?:\d+\s+minutes?|an\s+hour)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+look\s+(?:good|best|great)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+(?:are|is)\s+best$", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"^(?:(?:a|an|the)\s+)?(?:good|great|best|top|recommended|beginner(?:-friendly)?)\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\s+(?:on|in)\s+youtube$", "", s, flags=re.IGNORECASE)
    return s.strip().strip("\"'")


def _extract_youtube_search_intent(prompt: str) -> DetectedIntent | None:
    """Extract YouTube search or recommendation intent and query."""
    s = prompt.strip().rstrip("?.!").strip()
    lower = s.lower()
    lower_core = re.sub(r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", lower).strip()

    # If it is strictly "open youtube", preserve for OPEN_URL
    if lower_core in (
        "open youtube",
        "launch youtube",
        "start youtube",
        "run youtube",
        "open up youtube",
    ):
        return None

    # Check question form: e.g. "Which YouTube videos about PipeWire look best?"
    # or "What's the best YouTube video on machine learning?"
    m_q = _YOUTUBE_QUESTION_RE.match(s)
    if m_q:
        # group(1) from first alt (which/what videos...) or group(2) from second alt (what's the best...)
        raw_q = (m_q.group(1) or m_q.group(2) or "").strip()
        clean_q = _clean_recommend_query(raw_q)
        return DetectedIntent(
            intent_type=AssistantIntentType.YOUTUBE_RECOMMEND,
            raw_prompt=prompt,
            target=clean_q,
            extra={"query": clean_q, "constraints": _extract_constraints(s)},
        )

    # Check generic video form: e.g. "Find a video about Linux AI agents under 20 minutes"
    m_gen = _YOUTUBE_GENERIC_VIDEO_RE.match(s)
    if m_gen:
        raw_q = m_gen.group(1).strip()
        # Strip leading "on YouTube" / "YouTube about" artifacts from greedy capture
        raw_q = re.sub(
            r"^(?:on\s+)?youtube\s+(?:about|for|on|in)\s+", "", raw_q, flags=re.IGNORECASE
        )
        raw_q = re.sub(r"^(?:on\s+)?youtube\s+", "", raw_q, flags=re.IGNORECASE)
        clean_q = _clean_recommend_query(raw_q)
        if _is_recommendation_request(s):
            return DetectedIntent(
                intent_type=AssistantIntentType.YOUTUBE_RECOMMEND,
                raw_prompt=prompt,
                target=clean_q,
                extra={"query": clean_q, "constraints": _extract_constraints(s)},
            )
        else:
            return DetectedIntent(
                intent_type=AssistantIntentType.YOUTUBE_SEARCH,
                raw_prompt=prompt,
                target=clean_q,
                extra={"query": clean_q},
            )

    query = None

    # Check Suffix first: e.g. "find Linux tutorials on YouTube"
    m_suffix = _YOUTUBE_SUFFIX_SEARCH_RE.match(s)
    if m_suffix:
        query = m_suffix.group(1).strip()

    # Check Infix: e.g. "find videos on YouTube about building local AI agents"
    if query is None:
        m_infix = _YOUTUBE_INFIX_SEARCH_RE.match(s)
        if m_infix:
            query = m_infix.group(1).strip()

    # Check Prefix: e.g. "search YouTube for Linux tutorials", "open youtube mkbhd"
    if query is None:
        m_prefix = _YOUTUBE_PREFIX_SEARCH_RE.match(s)
        if m_prefix:
            raw_q = m_prefix.group(1).strip() if m_prefix.group(1) else ""
            for pfx in ("for ", "about ", "on "):
                if raw_q.lower().startswith(pfx):
                    raw_q = raw_q[len(pfx) :].strip()
            prefix_matched = m_prefix.group(0).lower()
            if not raw_q:
                # Explicit search request with no query -> clarification
                if any(w in prefix_matched for w in ("search", "serch", "find", "look up")):
                    return DetectedIntent(
                        intent_type=AssistantIntentType.CLARIFICATION,
                        raw_prompt=prompt,
                        target="youtube",
                        extra={
                            "clarification_type": "youtube_search",
                            "message": "What would you like me to search for on YouTube?",
                        },
                    )
                # Pure open/launch without query -> fall through to OPEN_URL
                query = None
            else:
                query = raw_q

    if query is not None:
        clean_q = query.strip().strip("\"'")
        # Check if query is empty or generic placeholder
        if not clean_q or clean_q.lower() in (
            "something",
            "anything",
            "stuff",
            "videos",
            "a video",
            "video",
            "tutorials",
            "clips",
        ):
            return DetectedIntent(
                intent_type=AssistantIntentType.CLARIFICATION,
                raw_prompt=prompt,
                target="youtube",
                extra={
                    "clarification_type": "youtube_search",
                    "message": "What would you like me to search for on YouTube?",
                },
            )

        if _is_recommendation_request(s):
            cleaned = _clean_recommend_query(clean_q)
            return DetectedIntent(
                intent_type=AssistantIntentType.YOUTUBE_RECOMMEND,
                raw_prompt=prompt,
                target=cleaned,
                extra={"query": cleaned, "constraints": _extract_constraints(s)},
            )

        return DetectedIntent(
            intent_type=AssistantIntentType.YOUTUBE_SEARCH,
            raw_prompt=prompt,
            target=clean_q,
            extra={"query": clean_q},
        )

    return None


def _normalize_phrase_for_fuzzy(p: str) -> str:
    s = p.strip().lower().rstrip("?.!")
    s = re.sub(r"^(?:please\s+)?", "", s)
    s = re.sub(r"\b(?:the|my|our|a)\b", "", s)
    return " ".join(s.split())


def resolve_fuzzy_desktop_intent(prompt: str) -> DetectedIntent | None:
    """Bounded typo normalization for known AVI native intents only.

    CRITICAL SECURITY RULE:
    Strictly bounded to known desktop capabilities and actions. Never matches arbitrary shell commands.
    High confidence (>= 0.80 ratio or exact token match) resolves directly to the native intent.
    Moderate confidence (>= 0.65 ratio) resolves to clarification asking what the user meant.
    Low confidence (< 0.65 ratio) returns None.
    """
    clean = _normalize_phrase_for_fuzzy(prompt)
    if not clean or len(clean) < 3:
        return None

    best_ratio = 0.0
    best_entry: tuple[str, AssistantIntentType, dict[str, Any]] | None = None

    for target_phrase, (intent_type, extra) in _BOUNDED_DESKTOP_INTENT_MAP.items():
        norm_target = _normalize_phrase_for_fuzzy(target_phrase)
        if clean == norm_target:
            best_ratio = 1.0
            best_entry = (target_phrase, intent_type, extra)
            break

        ratio = difflib.SequenceMatcher(None, clean, norm_target).ratio()

        clean_tokens = clean.split()
        target_tokens = norm_target.split()
        if len(clean_tokens) == len(target_tokens) and len(clean_tokens) >= 1:
            token_ratios = [
                difflib.SequenceMatcher(None, ct, tt).ratio()
                for ct, tt in zip(clean_tokens, target_tokens)
            ]
            avg_token_ratio = sum(token_ratios) / len(token_ratios)
            ratio = max(ratio, avg_token_ratio)

        if ratio > best_ratio:
            best_ratio = ratio
            best_entry = (target_phrase, intent_type, extra)

    if best_ratio >= 0.80 and best_entry is not None:
        target_phrase, intent_type, extra = best_entry
        target_val = ""
        if intent_type == AssistantIntentType.OPEN_DIR and "folder" in extra:
            folder_path = resolve_desktop_folder(extra["folder"])
            target_val = str(folder_path) if folder_path else ""
        return DetectedIntent(
            intent_type=intent_type,
            raw_prompt=prompt,
            target=target_val,
            extra=dict(extra),
        )
    elif best_ratio >= 0.65 and best_entry is not None:
        target_phrase, _, _ = best_entry
        if target_phrase in ("activate", "activate avi"):
            msg = "Did you mean activate AVI?"
        else:
            msg = f"Did you mean '{target_phrase}'?"
        return DetectedIntent(
            intent_type=AssistantIntentType.CLARIFICATION,
            raw_prompt=prompt,
            target=target_phrase,
            extra={
                "clarification_type": "typo",
                "message": msg,
                "suggested": target_phrase,
            },
        )

    return None


def find_desktop_typo(prompt: str) -> str | None:
    """Detect obvious typos or near-matches for supported native desktop commands."""
    clean = _normalize_phrase_for_fuzzy(prompt)
    if not clean or len(clean) < 3:
        return None

    matches = difflib.get_close_matches(clean, _CANONICAL_DESKTOP_TARGETS, n=1, cutoff=0.65)
    if matches:
        return matches[0]

    tokens = clean.split()
    for target in _CANONICAL_DESKTOP_TARGETS:
        target_clean = _normalize_phrase_for_fuzzy(target)
        target_tokens = target_clean.split()
        if len(tokens) == len(target_tokens) and len(tokens) > 1:
            token_ratios = [
                difflib.SequenceMatcher(None, ct, tt).ratio()
                for ct, tt in zip(tokens, target_tokens)
            ]
            if sum(token_ratios) / len(token_ratios) >= 0.70:
                return target

    return None


def clean_natural_language_input(text: str) -> str:
    """Normalize user input by removing CLI invocation prefixes and wrapping quotes.

    Ensures input is pure natural language whether entered in CLI or GUI.
    E.g.:
      avi "increase volume" -> increase volume
      avi chrome -> chrome
      "find me youtube" -> find me youtube
      avi "find me the best YouTube video" -> find me the best YouTube video
    """
    s = text.strip()
    if not s:
        return ""

    # Strip outer quotes if enclosed
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()

    # Strip leading 'avi' command prefix (e.g. "avi ", "avi: ", "avi, ")
    if re.match(r"^avi[\s,:]+", s, re.IGNORECASE):
        s = re.sub(r"^avi[\s,:]+", "", s, count=1, flags=re.IGNORECASE).strip()

    # Strip outer quotes again if prompt was avi "..."
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()

    return s


def detect_assistant_intent(prompt: str, last_turn: Any | None = None) -> DetectedIntent:
    """Analyze prompt and detect if it maps to a native assistant capability."""
    cleaned = clean_natural_language_input(prompt)
    s = cleaned.rstrip("?.!").strip()
    if not s:
        return DetectedIntent(intent_type=AssistantIntentType.UNKNOWN, raw_prompt=prompt)
    lower = s.lower()

    # 1. Conversational Follow-up based on last turn
    if last_turn is not None:
        last_intent = getattr(last_turn, "intent_type", None)
        last_intent_val = getattr(last_intent, "value", str(last_intent))

        # Search results deictic open / re-ranking follow-up
        has_results = bool(
            getattr(last_turn, "search_results", None)
            or getattr(last_turn, "selected_result", None)
        )
        if has_results:
            m_deictic = re.match(
                r"^(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:open|play|watch|use)\s+(?:it|that|that\s+one|that\s+video|the\s+video|the\s+best\s+one|the\s+first\s+one|the\s+1st\s+one|the\s+second\s+one|the\s+2nd\s+one|the\s+third\s+one|the\s+3rd\s+one|result\s+[1-5]|[1-5])$",
                lower,
            )
            if m_deictic or lower in (
                "open it",
                "open that",
                "open that one",
                "open that video",
                "open the best one",
                "open the first one",
                "play it",
                "play that",
                "watch it",
                "watch that",
                "use it",
                "use the first one",
            ):
                idx = 0
                if any(w in lower for w in ("second", "2nd", " 2")):
                    idx = 1
                elif any(w in lower for w in ("third", "3rd", " 3")):
                    idx = 2
                elif any(w in lower for w in ("fourth", "4th", " 4")):
                    idx = 3
                elif any(w in lower for w in ("fifth", "5th", " 5")):
                    idx = 4
                return DetectedIntent(
                    intent_type=AssistantIntentType.OPEN_SEARCH_RESULT,
                    raw_prompt=prompt,
                    target=str(idx),
                    extra={"index": idx},
                )

            if re.search(
                r"\b(?:which\s+(?:one|video)\s+is\s+(?:best|better|recommended)|which\s+one\s+for\s+a\s+beginner|which\s+one\s+looks\s+best)\b",
                lower,
            ):
                return DetectedIntent(
                    intent_type=AssistantIntentType.YOUTUBE_RECOMMEND,
                    raw_prompt=prompt,
                    target="previous_results",
                    extra={
                        "from_history": True,
                        "constraints": _extract_constraints(prompt),
                    },
                )

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

        if last_intent_val in ("CLARIFICATION", AssistantIntentType.CLARIFICATION.value) and (
            getattr(last_turn, "target", "") == "youtube"
            or "search for on YouTube" in getattr(last_turn, "response_text", "")
        ):
            clean_followup = s.strip("\"'")
            if clean_followup and clean_followup.lower() not in (
                "nothing",
                "nevermind",
                "cancel",
                "no",
                "stop",
            ):
                return DetectedIntent(
                    intent_type=AssistantIntentType.YOUTUBE_SEARCH,
                    raw_prompt=prompt,
                    target=clean_followup,
                    extra={"query": clean_followup},
                )

    # 2. Ambiguous deictic requests without context (clarification required)
    if lower in _AMBIGUOUS_DEICTIC_PATTERNS:
        kind, msg = _AMBIGUOUS_DEICTIC_PATTERNS[lower]
        return DetectedIntent(
            intent_type=AssistantIntentType.CLARIFICATION,
            raw_prompt=prompt,
            target=lower,
            extra={"clarification_type": kind, "message": msg},
        )

    # 2.5 Activation intent
    if lower in (
        "activate",
        "activate avi",
        "launch avi",
        "open avi",
        "activaite",
        "activte",
        "actvate",
        "activat",
    ):
        return DetectedIntent(intent_type=AssistantIntentType.ACTIVATE, raw_prompt=prompt)

    # 3. Greetings (fast deterministic recognition)
    if _GREETING_RE.match(lower) or lower in (
        "hi",
        "hello",
        "hey",
        "howdy",
        "greetings",
        "good morning",
        "good afternoon",
        "good evening",
        "yo",
        "sup",
        "hiya",
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

    # 13.5. YouTube Search requests (takes precedence over generic open)
    yt_intent = _extract_youtube_search_intent(s)
    if yt_intent is not None:
        return yt_intent

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

        # Handle deictic targets like "it", "that"
        if target_lower in ("it", "that", "this", "them", "that one", "that video"):
            if last_turn is not None:
                has_res = bool(
                    getattr(last_turn, "search_results", None)
                    or getattr(last_turn, "selected_result", None)
                )
                if has_res:
                    return DetectedIntent(
                        intent_type=AssistantIntentType.OPEN_SEARCH_RESULT,
                        raw_prompt=prompt,
                        target="0",
                        extra={"index": 0},
                    )
                action_path = None
                cap_res = getattr(last_turn, "capability_result", None)
                if cap_res and hasattr(cap_res, "data") and isinstance(cap_res.data, dict):
                    action_path = cap_res.data.get("path")
                if not action_path and getattr(last_turn, "response_text", None):
                    m_png = re.search(
                        r"(/home/[^\s]+\.png|~/[^\s]+\.png|/[^\s]+\.png)",
                        last_turn.response_text,
                    )
                    if m_png:
                        action_path = Path(m_png.group(1)).expanduser()
                if action_path and Path(action_path).exists():
                    return DetectedIntent(
                        intent_type=AssistantIntentType.OPEN_FILE,
                        raw_prompt=prompt,
                        target=str(action_path),
                    )
            return DetectedIntent(
                intent_type=AssistantIntentType.CLARIFICATION,
                raw_prompt=prompt,
                target=target,
                extra={
                    "clarification_type": "deictic",
                    "message": "What would you like me to open?",
                },
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

    # 15. Desktop Screenshot
    if _SCREENSHOT_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.SCREENSHOT,
            raw_prompt=prompt,
        )

    # 16. Volume Controls
    if _MUTE_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "mute"},
        )
    if _UNMUTE_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "unmute"},
        )
    if _VOL_MAX_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "set", "level": 100},
        )
    if _VOL_MIN_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "set", "level": 0},
        )
    vol_by_up = _VOL_BY_UP_RE.match(s)
    if vol_by_up:
        d = int(vol_by_up.group(1))
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "raise", "delta": d},
        )
    vol_by_down = _VOL_BY_DOWN_RE.match(s)
    if vol_by_down:
        d = int(vol_by_down.group(1))
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "lower", "delta": d},
        )
    vol_set_match = _VOL_SET_RE.match(s)
    if vol_set_match:
        lvl_str = vol_set_match.group(1) or vol_set_match.group(2)
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "set", "level": int(lvl_str)},
        )
    if _VOL_UP_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "raise", "delta": 5},
        )
    if _VOL_DOWN_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_SET,
            raw_prompt=prompt,
            extra={"action": "lower", "delta": 5},
        )
    if _VOL_GET_RE.match(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.VOLUME_GET,
            raw_prompt=prompt,
        )

    # 17. Media Controls
    media_match = _MEDIA_RE.match(s)
    if media_match:
        lower_media = s.lower()
        action = "play"
        if "pause" in lower_media:
            action = "pause"
        elif "stop" in lower_media:
            action = "stop"
        elif "next" in lower_media:
            action = "next"
        elif "prev" in lower_media:
            action = "previous"
        elif "resume" in lower_media or "play" in lower_media:
            action = "play"
        return DetectedIntent(
            intent_type=AssistantIntentType.MEDIA_CONTROL,
            raw_prompt=prompt,
            extra={"action": action},
        )

    # 18. Bare desktop folders (e.g. "downloads", "documents", "desktop", "pictures")
    folder_alias = resolve_desktop_folder(s)
    if folder_alias is not None:
        return DetectedIntent(
            intent_type=AssistantIntentType.OPEN_DIR,
            raw_prompt=prompt,
            target=str(folder_alias),
        )

    # 19. Bare application commands (e.g. "chrome", "brave", "firefox", "spotify", "antigravity")
    from avi.apps.resolver import DEFAULT_ALIASES, ApplicationResolver

    if lower in DEFAULT_ALIASES or ApplicationResolver().is_known_app(s):
        return DetectedIntent(
            intent_type=AssistantIntentType.OPEN_APP,
            raw_prompt=prompt,
            target=s,
        )

    # 20. Obvious near-match / typo of supported native desktop commands
    typo_suggestion = find_desktop_typo(s)
    if typo_suggestion:
        msg = f"Did you mean '{typo_suggestion}'?"
        if typo_suggestion in ("activate", "activate avi"):
            msg = "Did you mean activate AVI?"
        return DetectedIntent(
            intent_type=AssistantIntentType.CLARIFICATION,
            raw_prompt=prompt,
            extra={
                "clarification_type": "typo",
                "message": msg,
                "suggested": typo_suggestion,
            },
        )

    # 21. Bounded typo normalization / fuzzy intent matching for native desktop commands
    fuzzy_intent = resolve_fuzzy_desktop_intent(s)
    if fuzzy_intent is not None:
        return fuzzy_intent

    return DetectedIntent(intent_type=AssistantIntentType.UNKNOWN, raw_prompt=prompt)

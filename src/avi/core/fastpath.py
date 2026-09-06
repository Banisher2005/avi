"""Deterministic command-template fast-path for common shell requests."""

import re


_COMMAND_TEMPLATES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(what\s+command\s+shows?|how\s+do\s+i\s+show)\s+(the\s+)?current\s+directory$", re.I), "pwd"),
    (re.compile(r"^(what\s+command\s+(lists?|shows?)|how\s+do\s+i\s+(list|show))\s+(the\s+)?files(\s+here|\s+in\s+this\s+directory)?$", re.I), "ls -la"),
    (re.compile(r"^(what\s+command\s+(checks?|shows?)|how\s+do\s+i\s+check)\s+(disk\s+space|disk\s+usage)$", re.I), "df -h"),
    (re.compile(r"^(what\s+command\s+(shows?|lists?)|how\s+do\s+i\s+(show|list))\s+(running\s+)?processes$", re.I), "ps aux"),
    (re.compile(r"^(what\s+command\s+shows?|how\s+do\s+i\s+show)\s+(my\s+)?current\s+git\s+branch$", re.I), "git branch --show-current"),
    (re.compile(r"^(what\s+command\s+(checks?|shows?)|how\s+do\s+i\s+check)\s+(git\s+status|repository\s+status)$", re.I), "git status"),
    (re.compile(r"^(what\s+command\s+(shows?|lists?)|how\s+do\s+i\s+(show|list))\s+(recent\s+)?git\s+commits$", re.I), "git log --oneline -10"),
    (re.compile(r"^(what\s+command\s+shows?|how\s+do\s+i\s+show)\s+(the\s+)?current\s+date$", re.I), "date"),
    (re.compile(r"^(what\s+command\s+shows?|how\s+do\s+i\s+show)\s+(the\s+)?current\s+user$", re.I), "whoami"),
    (re.compile(r"^(what\s+command\s+shows?|how\s+do\s+i\s+show)\s+(the\s+)?operating\s+system$", re.I), "uname -a"),
    (re.compile(r"^(what\s+command\s+(finds?|checks?)|how\s+do\s+i\s+(find|check))\s+(the\s+)?python\s+path$", re.I), "which python3"),
    (re.compile(r"^(what\s+command\s+(checks?|shows?)|how\s+do\s+i\s+check)\s+(listening\s+)?(tcp\s+)?ports$", re.I), "ss -tulpn"),
    (re.compile(r"^(what\s+command\s+(shows?|checks?)|how\s+do\s+i\s+(show|check))\s+(git\s+)?changes$", re.I), "git diff"),
)


def resolve_command_template(prompt: str) -> str | None:
    """Resolve a common command-syntax request without invoking the LLM."""
    normalized = prompt.strip().rstrip("?.!").strip()
    for pattern, command in _COMMAND_TEMPLATES:
        if pattern.fullmatch(normalized):
            return command
    return None

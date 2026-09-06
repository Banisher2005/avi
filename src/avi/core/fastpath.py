"""Deterministic command template fast-path resolver for AVI (Phase 6).

Recognizes unambiguous terminal intents and produces structured CommandRequest
objects without invoking an LLM. All generated commands strictly pass through
the Phase 5 SafetyEngine before execution.
"""

from dataclasses import dataclass
import re
from typing import Callable, Sequence

from avi.execution.models import CommandRequest

# Unsafe shell characters that disqualify parameters from deterministic templates
SHELL_UNSAFE_PATTERN = re.compile(r"[;&|`$><\n\r\x00]")


def is_safe_parameter(value: str) -> bool:
    """Validate that an extracted parameter contains no shell syntax or metacharacters."""
    if not value or not value.strip():
        return False
    val = value.strip()
    # Reject shell metacharacters
    if SHELL_UNSAFE_PATTERN.search(val):
        return False
    # Reject unclosed quotes
    if val.count('"') % 2 != 0 or val.count("'") % 2 != 0:
        return False
    # Reject dangerous substitutions or backslash escapes
    if "$(" in val or "`" in val or "\\" in val:
        return False
    return True


@dataclass(frozen=True)
class FastPathMatch:
    """Result of a successful deterministic intent resolution."""

    intent: str
    request: CommandRequest


class IntentTemplate:
    """Represents a deterministic intent pattern mapped to a CommandRequest builder."""

    def __init__(
        self,
        name: str,
        patterns: Sequence[re.Pattern],
        builder: Callable[[re.Match], CommandRequest | None],
        description: str = "",
    ) -> None:
        self.name = name
        self.patterns = patterns
        self.builder = builder
        self.description = description

    def match(self, normalized_prompt: str) -> CommandRequest | None:
        """Attempt to match normalized prompt and build a structured CommandRequest."""
        for pattern in self.patterns:
            m = pattern.match(normalized_prompt)
            if m:
                req = self.builder(m)
                if req is not None:
                    return req
        return None


class FastPathRegistry:
    """Registry of deterministic command templates and intent handlers."""

    def __init__(self) -> None:
        self._templates: list[IntentTemplate] = []
        self._register_default_templates()

    def register(self, template: IntentTemplate) -> None:
        """Register a new intent template."""
        self._templates.append(template)

    def resolve(self, prompt: str) -> CommandRequest | None:
        """Resolve a prompt to a CommandRequest, or return None if ambiguous/unmatched."""
        match = self.resolve_match(prompt)
        return match.request if match is not None else None

    def resolve_match(self, prompt: str) -> FastPathMatch | None:
        """Resolve a prompt to a FastPathMatch, or return None if ambiguous/unmatched."""
        if not prompt or not prompt.strip():
            return None

        # Normalize prompt: lowercase, strip surrounding whitespace and trailing punctuation
        normalized = prompt.strip().rstrip("?.!").strip()
        if not normalized:
            return None

        for template in self._templates:
            req = template.match(normalized)
            if req is not None:
                return FastPathMatch(intent=template.name, request=req)

        return None

    def list_templates(self) -> list[IntentTemplate]:
        """Return all registered templates."""
        return list(self._templates)

    def _register_default_templates(self) -> None:
        """Register all default deterministic intent templates."""
        # 1. Current Working Directory (pwd)
        self.register(
            IntentTemplate(
                name="current_directory",
                patterns=[
                    re.compile(
                        r"^(?:(?:what\s+(?:is\s+)?)?(?:the\s+|my\s+)?(?:current\s+(?:working\s+)?|working\s+)directory|what\s+is\s+(?:my\s+|the\s+)?directory|pwd|where\s+am\s+i|print\s+working\s+directory)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="pwd", args=[]),
                description="Print current working directory",
            )
        )

        # 2. List Files (ls -la)
        self.register(
            IntentTemplate(
                name="list_files",
                patterns=[
                    re.compile(
                        r"^(?:list\s+(?:all\s+)?files(?:\s+here|\s+in\s+this\s+directory)?|show\s+(?:all\s+)?files(?:\s+here|\s+in\s+this\s+directory)?|what\s+files\s+are\s+here|ls(?:\s+-la)?)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="ls", args=["-la"]),
                description="List all files in current directory with details",
            )
        )

        # 3. Disk Usage (df -h)
        self.register(
            IntentTemplate(
                name="disk_usage",
                patterns=[
                    re.compile(
                        r"^(?:(?:show\s+)?disk\s+usage|check\s+disk\s+space|how\s+much\s+disk\s+space\s+(?:do\s+i\s+have|is\s+left)|disk\s+space|df\s+-h)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="df", args=["-h"]),
                description="Check available disk space",
            )
        )

        # 4. Running Processes (ps aux)
        self.register(
            IntentTemplate(
                name="processes",
                patterns=[
                    re.compile(
                        r"^(?:(?:show\s+)?(?:current\s+|running\s+)?processes|list\s+(?:current\s+|running\s+)?processes|running\s+processes|top\s+processes|what\s+processes\s+are\s+running|ps\s+aux)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="ps", args=["aux"]),
                description="List running processes",
            )
        )

        # 5. Git Branch (git branch --show-current)
        self.register(
            IntentTemplate(
                name="git_branch",
                patterns=[
                    re.compile(
                        r"^(?:(?:what\s+(?:is\s+)?(?:my\s+|the\s+)?|show\s+(?:my\s+|the\s+)?)?(?:current\s+)?git\s+branch(?:\s+--show-current)?|(?:what\s+(?:is\s+)?(?:my\s+|the\s+)?|show\s+(?:my\s+|the\s+)?)?current\s+branch|what\s+(?:git\s+)?branch\s+am\s+i\s+on|git\s+branch(?:\s+--show-current)?)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="git", args=["branch", "--show-current"]),
                description="Show current Git branch",
            )
        )

        # 6. Git Status (git status)
        self.register(
            IntentTemplate(
                name="git_status",
                patterns=[
                    re.compile(
                        r"^(?:git\s+status|check\s+git\s+status|is\s+(?:the\s+|my\s+)?repo\s+clean|is\s+git\s+clean|show\s+git\s+status|repo\s+status)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="git", args=["status"]),
                description="Show Git repository status",
            )
        )

        # 7. Git Commits (git log --oneline -N)
        def build_git_log(match: re.Match) -> CommandRequest | None:
            raw_count = None
            for g in match.groups():
                if g:
                    raw_count = g
                    break
            if raw_count:
                try:
                    count = int(raw_count)
                    if count <= 0 or count > 1000:
                        return None
                    return CommandRequest(program="git", args=["log", "--oneline", f"-{count}"])
                except ValueError:
                    return None
            return CommandRequest(program="git", args=["log", "--oneline", "-10"])

        self.register(
            IntentTemplate(
                name="git_commits",
                patterns=[
                    re.compile(
                        r"^(?:show\s+(?:the\s+)?(?:last|recent)\s+(\d+)\s+git\s+commits|show\s+last\s+(\d+)\s+commits|last\s+(\d+)\s+(?:git\s+)?commits|git\s+log\s+-(\d+))$",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"^(?:recent\s+commits|git\s+log|show\s+(?:recent\s+|git\s+)?commits|git\s+history|show\s+git\s+history)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=build_git_log,
                description="Show recent Git commits",
            )
        )

        # 8. Git Changes / Diff (git diff)
        self.register(
            IntentTemplate(
                name="git_diff",
                patterns=[
                    re.compile(
                        r"^(?:git\s+diff|show\s+git\s+diff|git\s+changes|show\s+git\s+changes|show\s+uncommitted\s+changes|show\s+diff)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="git", args=["diff"]),
                description="Show Git working directory diff",
            )
        )

        # 9. Current Date (date)
        self.register(
            IntentTemplate(
                name="current_date",
                patterns=[
                    re.compile(
                        r"^(?:current\s+date|what\s+is\s+(?:today\'?s|the)\s+date|today\'?s\s+date|show\s+date|current\s+time|date)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="date", args=[]),
                description="Show current date and time",
            )
        )

        # 10. Current User (whoami)
        self.register(
            IntentTemplate(
                name="current_user",
                patterns=[
                    re.compile(
                        r"^(?:whoami|current\s+user|who\s+am\s+i|what\s+user\s+am\s+i|show\s+current\s+user)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="whoami", args=[]),
                description="Show active username",
            )
        )

        # 11. Operating System (uname -a)
        self.register(
            IntentTemplate(
                name="system_info",
                patterns=[
                    re.compile(
                        r"^(?:uname\s+-a|operating\s+system|what\s+os\s+is\s+this|system\s+info|show\s+system\s+info|show\s+system\s+information|show\s+environment\s+info|show\s+environment/system\s+information)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="uname", args=["-a"]),
                description="Show operating system information",
            )
        )

        # 12. Python Path (which python3)
        self.register(
            IntentTemplate(
                name="python_path",
                patterns=[
                    re.compile(
                        r"^(?:which\s+python3?|where\s+is\s+python3?|python\s+path|show\s+python\s+path)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="which", args=["python3"]),
                description="Find python3 binary path",
            )
        )

        # 13. Listening Ports (ss -tulpn)
        self.register(
            IntentTemplate(
                name="listening_ports",
                patterns=[
                    re.compile(
                        r"^(?:ss\s+-tulpn|(?:show\s+)?listening\s+(?:tcp\s+)?ports|open\s+ports|show\s+open\s+ports|check\s+listening\s+ports)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="ss", args=["-tulpn"]),
                description="Show listening network ports",
            )
        )

        # 14. Check Python Version (python3 --version)
        self.register(
            IntentTemplate(
                name="python_version",
                patterns=[
                    re.compile(
                        r"^(?:check\s+python\s+version|what\s+is\s+the\s+python\s+version|show\s+python\s+version|python\s+version|python3?\s+--version)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="python3", args=["--version"]),
                description="Check Python version",
            )
        )

        # 15. Check Node Version (node --version)
        self.register(
            IntentTemplate(
                name="node_version",
                patterns=[
                    re.compile(
                        r"^(?:check\s+node\s+version|what\s+is\s+the\s+node\s+version|show\s+node\s+version|node\s+version|node\s+--version)$",
                        re.IGNORECASE,
                    ),
                ],
                builder=lambda m: CommandRequest(program="node", args=["--version"]),
                description="Check Node.js version",
            )
        )

        # 16. Find Files by Size (find . -type f -size +<size>)
        def build_find_by_size(match: re.Match) -> CommandRequest | None:
            num = match.group(1)
            raw_unit = (match.group(2) or "m").upper()
            unit_map = {
                "KB": "k",
                "K": "k",
                "MB": "M",
                "M": "M",
                "GB": "G",
                "G": "G",
                "B": "c",
            }
            flag = unit_map.get(raw_unit, "M")
            return CommandRequest(
                program="find",
                args=[".", "-type", "f", "-size", f"+{num}{flag}"],
            )

        self.register(
            IntentTemplate(
                name="find_by_size",
                patterns=[
                    re.compile(
                        r"^find\s+files\s+(?:larger|bigger)\s+than\s+(\d+)\s*(kb|mb|gb|k|m|g|b)?$",
                        re.IGNORECASE,
                    ),
                ],
                builder=build_find_by_size,
                description="Find files larger than specified size",
            )
        )

        # 17. Find Files by Language / Extension (find . -name "*.ext")
        lang_ext_map = {
            "python": "*.py",
            "javascript": "*.js",
            "js": "*.js",
            "typescript": "*.ts",
            "ts": "*.ts",
            "rust": "*.rs",
            "go": "*.go",
            "c++": "*.cpp",
            "cpp": "*.cpp",
            "html": "*.html",
            "json": "*.json",
            "markdown": "*.md",
            "md": "*.md",
        }

        def build_find_lang(match: re.Match) -> CommandRequest | None:
            lang = match.group(1).lower()
            pattern = lang_ext_map.get(lang)
            if pattern:
                return CommandRequest(program="find", args=[".", "-name", pattern])
            return None

        self.register(
            IntentTemplate(
                name="find_by_language",
                patterns=[
                    re.compile(
                        r"^find\s+(python|javascript|js|typescript|ts|rust|go|c\+\+|cpp|html|json|markdown|md)\s+files$",
                        re.IGNORECASE,
                    ),
                ],
                builder=build_find_lang,
                description="Find source files by programming language",
            )
        )

        # 18. Find Files Modified Today / Recently (find . -mtime N)
        def build_find_modified(match: re.Match) -> CommandRequest | None:
            if match.re.pattern.startswith(r"^find\s+files\s+modified\s+in"):
                days = match.group(1)
                return CommandRequest(program="find", args=[".", "-mtime", f"-{days}"])
            return CommandRequest(program="find", args=[".", "-mtime", "0"])

        self.register(
            IntentTemplate(
                name="find_modified",
                patterns=[
                    re.compile(
                        r"^(?:find\s+files\s+modified\s+today|show\s+files\s+modified\s+today|files\s+modified\s+today)$",
                        re.IGNORECASE,
                    ),
                    re.compile(
                        r"^find\s+files\s+modified\s+in\s+the\s+last\s+(\d+)\s+days$",
                        re.IGNORECASE,
                    ),
                ],
                builder=build_find_modified,
                description="Find files modified recently",
            )
        )

        # 19. Grep for pattern in files (grep -r -- <pattern> .)
        def build_grep_in_files(match: re.Match) -> CommandRequest | None:
            pattern_str = match.group(1) or match.group(2) or match.group(3)
            if not pattern_str or not is_safe_parameter(pattern_str):
                return None
            return CommandRequest(program="grep", args=["-r", "--", pattern_str, "."])

        self.register(
            IntentTemplate(
                name="grep_in_files",
                patterns=[
                    re.compile(
                        r"^(?:grep|search)\s+for\s+(?:\"([^\"]+)\"|\'([^\']+)\'|(\S+))(?:\s+in\s+files|\s+here)?$",
                        re.IGNORECASE,
                    ),
                ],
                builder=build_grep_in_files,
                description="Recursively search for pattern across files",
            )
        )


# Command-syntax templates for "what command shows/lists..." queries
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


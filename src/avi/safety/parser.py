"""Command string parsing and shell syntax safety detection."""

import re
import shlex
from dataclasses import dataclass, field

COMPOUND_TOKENS = frozenset({"&&", "||", ";", "|", "|&", "&"})
REDIRECTION_TOKENS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "&>"})
FORK_BOMB_PATTERN = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")
SUBSTITUTION_PATTERN = re.compile(r"(\$\([^\)]*\)|`[^`]*`)")


@dataclass
class ParsedCommand:
    """Structured representation of a parsed command string."""

    raw: str
    program: str = ""
    args: list[str] = field(default_factory=list)
    is_empty: bool = False
    is_malformed: bool = False
    is_fork_bomb: bool = False
    has_substitution: bool = False
    has_compound: bool = False
    has_redirection: bool = False
    compound_operators: list[str] = field(default_factory=list)
    redirection_operators: list[str] = field(default_factory=list)
    error: str | None = None


def parse_command_safety(command_str: str) -> ParsedCommand:
    """Analyze a command string for shell metacharacters and extract tokens safely."""
    raw = command_str.strip()
    result = ParsedCommand(raw=command_str)

    if not raw:
        result.is_empty = True
        result.error = "Empty command"
        return result

    # 1. Detect fork bombs
    if FORK_BOMB_PATTERN.search(raw):
        result.is_fork_bomb = True
        result.error = "Catastrophic fork bomb detected"
        return result

    # 2. Detect shell substitutions: $(...) or `...`
    if SUBSTITUTION_PATTERN.search(command_str):
        result.has_substitution = True

    # 3. Detect unescaped newlines which separate compound commands
    if "\n" in command_str or "\r" in command_str:
        result.has_compound = True
        result.compound_operators.append("\\n")

    # 4. Tokenize punctuation with shlex to catch unquoted shell metacharacters
    try:
        lexer = shlex.shlex(command_str, posix=True, punctuation_chars=True)
        tokens = list(lexer)
    except ValueError as e:
        result.is_malformed = True
        result.error = f"Malformed command syntax: {e}"
        return result

    compound_ops = [t for t in tokens if t in COMPOUND_TOKENS]
    if compound_ops:
        result.has_compound = True
        result.compound_operators.extend(compound_ops)

    redirect_ops = [t for t in tokens if t in REDIRECTION_TOKENS]
    if redirect_ops:
        result.has_redirection = True
        result.redirection_operators.extend(redirect_ops)

    # 5. Extract program and arguments via standard posix split
    try:
        clean_tokens = shlex.split(command_str)
        if clean_tokens:
            result.program = clean_tokens[0]
            result.args = clean_tokens[1:]
        else:
            result.is_empty = True
            result.error = "Empty command"
    except ValueError as e:
        result.is_malformed = True
        result.error = f"Malformed command syntax: {e}"

    return result

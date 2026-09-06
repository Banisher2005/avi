"""Deterministic safety classification engine for AVI commands.

Evaluates proposed commands and classifies them into:
- SAFE: read-only, benign commands that can be executed automatically
- CONFIRM: state-modifying commands that require explicit user confirmation
- BLOCK: catastrophic, malicious, or malformed commands that are strictly refused
"""

import os
from pathlib import Path
from typing import Sequence

from avi.execution.models import CommandRequest
from avi.safety.models import RiskLevel, SafetyAssessment
from avi.safety.parser import ParsedCommand, parse_command_safety

# Critical system directories that must never be targeted by destructive operations
CRITICAL_SYSTEM_PATHS = frozenset({
    "/",
    "/*",
    "*",
    "/etc",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/root",
    "~",
    "~/*",
    "$HOME",
    "/home",
})

# Read-only programs allowed without confirmation (provided arguments are safe)
SAFE_READ_ONLY_PROGRAMS = frozenset({
    "pwd",
    "ls",
    "du",
    "df",
    "ps",
    "uname",
    "whoami",
    "uptime",
    "cat",
    "head",
    "tail",
    "wc",
    "which",
    "whereis",
    "echo",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "file",
    "stat",
    "ss",
    "free",
})

# Known state-modifying programs requiring user confirmation
CONFIRM_MODIFYING_PROGRAMS = frozenset({
    "rm",
    "mv",
    "cp",
    "mkdir",
    "rmdir",
    "touch",
    "truncate",
    "chmod",
    "chown",
    "chgrp",
    "kill",
    "pkill",
    "killall",
    "systemctl",
    "service",
    "tar",
    "unzip",
    "gzip",
    "gunzip",
    "bzip2",
    "xz",
    "sed",
    "awk",
    "apt",
    "apt-get",
    "dnf",
    "yum",
    "pacman",
    "zypper",
    "pip",
    "npm",
    "yarn",
    "cargo",
    "pnpm",
    "ln",
})

# Destructive storage formatters/partitioners
BLOCKED_FORMAT_PROGRAMS = frozenset({
    "mkfs",
    "mkfs.ext4",
    "mkfs.ext3",
    "mkfs.ext2",
    "mkfs.xfs",
    "mkfs.btrfs",
    "mkfs.vfat",
    "wipefs",
    "fdisk",
    "parted",
    "gdisk",
    "sfdisk",
})

# Power management / shutdown
BLOCKED_POWER_PROGRAMS = frozenset({
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
})


class SafetyEngine:
    """Independent, deterministic command safety classifier."""

    def evaluate(self, command: str | CommandRequest) -> SafetyAssessment:
        """Evaluate a command string or CommandRequest and return a SafetyAssessment."""
        if isinstance(command, CommandRequest):
            # If already structured, build command line for safety checking
            cmd_str = command.command_line
        else:
            cmd_str = str(command)

        parsed = parse_command_safety(cmd_str)
        return self._classify(parsed)

    def _classify(self, parsed: ParsedCommand) -> SafetyAssessment:
        cmd_str = parsed.raw.strip()

        # 1. Empty command
        if parsed.is_empty:
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason="Empty command cannot be executed.",
                command=cmd_str,
            )

        # 2. Malformed command syntax (e.g. unclosed quotes)
        if parsed.is_malformed:
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason=f"Malformed command syntax: {parsed.error}",
                command=cmd_str,
            )

        # 3. Fork bombs
        if parsed.is_fork_bomb:
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason="Destructive fork bomb pattern detected.",
                command=cmd_str,
            )

        # 4. Shell substitutions $(...) or `...`
        if parsed.has_substitution:
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason="Command substitution ($() or `) cannot bypass safety classification.",
                command=cmd_str,
            )

        # 5. Compound commands (&&, ||, ;, |, \\n, &)
        if parsed.has_compound:
            op_str = ", ".join(parsed.compound_operators)
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason=f"Compound command or pipeline detected ({op_str}). Compound chaining is not permitted.",
                command=cmd_str,
            )

        # 6. Redirection operators (>, >>, <)
        if parsed.has_redirection:
            op_str = ", ".join(parsed.redirection_operators)
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason=f"Shell redirection operator ({op_str}) detected. File redirections are not permitted.",
                command=cmd_str,
            )

        program = parsed.program
        args = parsed.args

        # 7. Sudo / su privilege escalation
        if program in ("sudo", "su"):
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason="Direct sudo/su privilege escalation is not permitted.",
                command=cmd_str,
            )

        # 8. High-confidence destructive storage formatters
        if program in BLOCKED_FORMAT_PROGRAMS or program.startswith("mkfs."):
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason=f"Destructive disk formatting command {program} is blocked.",
                command=cmd_str,
            )

        # 9. Power management
        if program in BLOCKED_POWER_PROGRAMS:
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason=f"System power command {program} is blocked.",
                command=cmd_str,
            )
        if program == "init" and any(arg in ("0", "6") for arg in args):
            return SafetyAssessment(
                level=RiskLevel.BLOCK,
                reason="System init shutdown/reboot runlevel is blocked.",
                command=cmd_str,
            )

        # 10. `dd` writing to raw devices
        if program == "dd":
            for arg in args:
                if arg.startswith("of=/dev/"):
                    target = arg.split("=", 1)[1]
                    if target not in ("/dev/null", "/dev/zero"):
                        return SafetyAssessment(
                            level=RiskLevel.BLOCK,
                            reason=f"Writing to raw block device ({arg}) with dd is blocked.",
                            command=cmd_str,
                        )

        # 11. `rm` safety analysis
        if program == "rm":
            is_recursive = any(
                arg in ("-r", "-R", "--recursive")
                or (arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg))
                for arg in args
            )
            # Check target operands (ignoring flags)
            targets = [arg for arg in args if not arg.startswith("-")]

            for target in targets:
                normalized_target = target.rstrip("/")
                if not normalized_target:
                    normalized_target = "/"

                if normalized_target in CRITICAL_SYSTEM_PATHS:
                    return SafetyAssessment(
                        level=RiskLevel.BLOCK,
                        reason=f"Catastrophic deletion of root or critical path {target} is blocked.",
                        command=cmd_str,
                    )
                # Check for /* or root targets
                if target in ("/*", "/*.*", "/."):
                    return SafetyAssessment(
                        level=RiskLevel.BLOCK,
                        reason="Catastrophic deletion of root directory contents is blocked.",
                        command=cmd_str,
                    )

            # Non-root rm requires confirmation
            return SafetyAssessment(
                level=RiskLevel.CONFIRM,
                reason="File removal can modify or delete filesystem data.",
                command=cmd_str,
            )

        # 12. Recursive chmod/chown targeting root
        if program in ("chmod", "chown", "chgrp"):
            is_recursive = any(
                arg in ("-R", "--recursive")
                or (arg.startswith("-") and not arg.startswith("--") and ("R" in arg))
                for arg in args
            )
            targets = [arg for arg in args if not arg.startswith("-")]
            if is_recursive and any(t in ("/", "/*") for t in targets):
                return SafetyAssessment(
                    level=RiskLevel.BLOCK,
                    reason="Recursive permission change on root directory is blocked.",
                    command=cmd_str,
                )
            return SafetyAssessment(
                level=RiskLevel.CONFIRM,
                reason=f"Modifying permissions or ownership with {program} requires confirmation.",
                command=cmd_str,
            )

        # 13. `find` safety analysis
        if program == "find":
            # Check for action/modifying flags
            has_action = False
            for arg in args:
                if arg in ("-delete", "-exec", "-execdir", "-ok", "-okdir"):
                    has_action = True
                    break

            if has_action:
                # If find has -delete targeting root
                if "-delete" in args:
                    targets = [a for a in args if not a.startswith("-")]
                    if any(t in ("/", "/*") for t in targets):
                        return SafetyAssessment(
                            level=RiskLevel.BLOCK,
                            reason="find with -delete targeting root directory is blocked.",
                            command=cmd_str,
                        )
                return SafetyAssessment(
                    level=RiskLevel.CONFIRM,
                    reason="find command containing action flags (-exec, -delete) requires confirmation.",
                    command=cmd_str,
                )
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="find without action flags is read-only.",
                command=cmd_str,
            )

        # 14. `date` with setting flags
        if program == "date":
            if any(arg in ("-s", "--set") for arg in args):
                return SafetyAssessment(
                    level=RiskLevel.CONFIRM,
                    reason="Changing system date/time requires confirmation.",
                    command=cmd_str,
                )
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="date is read-only.",
                command=cmd_str,
            )

        # 15. `git` safety analysis
        if program == "git":
            return self._classify_git(args, cmd_str)

        # 16. Safe read-only programs allowlist
        if program in SAFE_READ_ONLY_PROGRAMS:
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason=f"Command {program} is on the read-only allowlist.",
                command=cmd_str,
            )

        # 17. Version queries for standard developer runtimes
        if program in ("python", "python3", "node", "ruby", "perl", "rustc", "go", "cargo"):
            if len(args) == 1 and args[0] in ("--version", "-v", "-V", "version"):
                return SafetyAssessment(
                    level=RiskLevel.SAFE,
                    reason=f"{program} version query is read-only.",
                    command=cmd_str,
                )

        # 18. Known modifying programs
        if program in CONFIRM_MODIFYING_PROGRAMS:
            return SafetyAssessment(
                level=RiskLevel.CONFIRM,
                reason=f"Command {program} can modify system or filesystem state.",
                command=cmd_str,
            )

        # 18. Fail-closed default: unrecognized commands require confirmation
        return SafetyAssessment(
            level=RiskLevel.CONFIRM,
            reason=f"Unrecognized command {program} requires confirmation (fail-closed policy).",
            command=cmd_str,
        )

    def _classify_git(self, args: list[str], cmd_str: str) -> SafetyAssessment:
        """Deterministic classification for Git subcommands."""
        if not args:
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="git without arguments shows help (read-only).",
                command=cmd_str,
            )

        # Extract git subcommand (skipping global flags like -C, -c, etc.)
        subcmd: str | None = None
        subcmd_idx = -1
        skip_next = False

        for idx, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if arg in ("-C", "-c", "--git-dir", "--work-tree"):
                skip_next = True
                continue
            if arg.startswith("-"):
                continue
            subcmd = arg
            subcmd_idx = idx
            break

        if subcmd is None:
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="git flags without subcommand are read-only.",
                command=cmd_str,
            )

        sub_args = args[subcmd_idx + 1 :]

        # 1. Read-only Git subcommands
        if subcmd == "status":
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="git status is read-only.",
                command=cmd_str,
            )

        if subcmd == "branch":
            # Branch deletion or moving modifies state
            if any(a in ("-d", "-D", "-m", "-M", "--delete", "--move") for a in sub_args):
                return SafetyAssessment(
                    level=RiskLevel.CONFIRM,
                    reason="git branch deletion or renaming modifies repository state.",
                    command=cmd_str,
                )
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="git branch listing is read-only.",
                command=cmd_str,
            )

        if subcmd in ("log", "diff", "show", "rev-parse", "describe"):
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason=f"git {subcmd} is read-only.",
                command=cmd_str,
            )

        if subcmd == "remote":
            if any(a in ("add", "remove", "rm", "rename", "set-url", "set-head") for a in sub_args):
                return SafetyAssessment(
                    level=RiskLevel.CONFIRM,
                    reason="git remote modification modifies repository state.",
                    command=cmd_str,
                )
            return SafetyAssessment(
                level=RiskLevel.SAFE,
                reason="git remote listing is read-only.",
                command=cmd_str,
            )

        # 2. Modifying Git subcommands
        return SafetyAssessment(
            level=RiskLevel.CONFIRM,
            reason=f"git {subcmd} can modify repository state or history.",
            command=cmd_str,
        )

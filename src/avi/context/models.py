"""Data models for terminal and repository context."""

from dataclasses import dataclass


@dataclass
class TerminalContext:
    """Current terminal environment context."""

    cwd: str
    os_name: str
    shell: str

    def format_text(self) -> str:
        """Format as concise key-value lines."""
        return f"[terminal]\ncwd={self.cwd}\nshell={self.shell}\nos={self.os_name}"


@dataclass
class GitContext:
    """Git repository context."""

    is_repo: bool
    root: str | None = None
    branch: str | None = None
    is_dirty: bool = False
    untracked_count: int = 0
    modified_count: int = 0

    def format_text(self) -> str:
        """Format as concise key-value lines."""
        if not self.is_repo:
            return "[git]\nis_repo=false"

        status_str = "dirty" if self.is_dirty else "clean"
        lines = [
            "[git]",
            f"branch={self.branch or 'HEAD'}",
            f"status={status_str}",
        ]
        if self.modified_count > 0:
            lines.append(f"modified_files={self.modified_count}")
        if self.untracked_count > 0:
            lines.append(f"untracked_files={self.untracked_count}")
        return "\n".join(lines)


@dataclass
class PreviousCommandContext:
    """Previous terminal command execution context."""

    command: str
    exit_code: int | None = None
    output: str | None = None

    def format_text(self) -> str:
        """Format as concise key-value lines."""
        lines = [
            "[previous_command]",
            f"command={self.command}",
        ]
        if self.exit_code is not None:
            lines.append(f"exit_code={self.exit_code}")
        if self.output:
            # Keep output concise to protect prompt size
            clean_output = self.output.strip()
            if len(clean_output) > 500:
                clean_output = clean_output[:500] + "... [truncated]"
            lines.append(f"output={clean_output}")
        return "\n".join(lines)


@dataclass
class ContextSnapshot:
    """Snapshot containing selected context components."""

    terminal: TerminalContext | None = None
    git: GitContext | None = None
    previous_command: PreviousCommandContext | None = None

    def is_empty(self) -> bool:
        """Check if any context component is present."""
        return self.terminal is None and self.git is None and self.previous_command is None

    def to_prompt_context(self) -> str:
        """Assemble structured, concise context block for model input."""
        sections: list[str] = []
        if self.terminal:
            sections.append(self.terminal.format_text())
        if self.git:
            sections.append(self.git.format_text())
        if self.previous_command:
            sections.append(self.previous_command.format_text())

        if not sections:
            return ""

        return "\n\n".join(sections)

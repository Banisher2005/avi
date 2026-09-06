"""Interactive REPL session for AVI."""

import os
import sys
from pathlib import Path
from typing import Any, TextIO

try:
    import readline
except ImportError:
    readline = None  # type: ignore

from avi import __version__
from avi.config import Config
from avi.core.router import Router
from avi.execution import CommandRequest
from avi.providers.ollama import OllamaError

DEFAULT_HISTORY_PATH = Path.home() / ".local" / "share" / "avi" / "history"
MAX_HISTORY_LENGTH = 1000


class InteractiveSession:
    """Manages a stateful, interactive terminal session with AVI."""

    def __init__(
        self,
        router: Router,
        config: Config,
        history_path: Path | None = None,
        in_stream: TextIO | None = None,
        out_stream: TextIO | None = None,
        err_stream: TextIO | None = None,
    ) -> None:
        self.router = router
        self.config = config
        self.history_path = history_path or DEFAULT_HISTORY_PATH
        self.in_stream = in_stream or sys.stdin
        self.out_stream = out_stream or sys.stdout
        self.err_stream = err_stream or sys.stderr
        self.context: Any | None = None
        self._setup_readline()

    def _setup_readline(self) -> None:
        """Initialize readline history and auto-complete settings if available."""
        if readline is None:
            return

        readline.set_history_length(MAX_HISTORY_LENGTH)
        if self.history_path.is_file():
            try:
                readline.read_history_file(str(self.history_path))
            except (OSError, PermissionError):
                pass

    def _save_history(self) -> None:
        """Persist command history to disk safely."""
        if readline is None:
            return

        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            readline.write_history_file(str(self.history_path))
        except (OSError, PermissionError):
            pass

    def _handle_local_command(self, cmd: str) -> bool:
        """Handle internal session commands without calling the LLM.

        Returns True if the command was handled, False otherwise.
        """
        lower = cmd.lower()

        if lower in ("exit", "quit"):
            return True  # Caller checks exit/quit to break

        if lower == "clear":
            # Clear terminal screen cleanly via ANSI escapes
            self.out_stream.write("\033[H\033[2J")
            self.out_stream.flush()
            return True

        if lower == "history":
            self._show_history()
            return True

        return False

    def _show_history(self) -> None:
        """Display interactive command history."""
        if readline is None:
            self.out_stream.write("History not available (readline missing).\n")
            self.out_stream.flush()
            return

        length = readline.get_current_history_length()
        if length == 0:
            self.out_stream.write("No history available.\n")
        else:
            for i in range(1, length + 1):
                item = readline.get_history_item(i)
                self.out_stream.write(f"  {i:4d}  {item}\n")
        self.out_stream.flush()

    def run(self) -> int:
        """Execute the interactive REPL loop until exit or EOF."""
        # 1. Non-blocking model warmup
        self.router.warmup()

        # 2. Print greeting header
        self.out_stream.write(
            f"AVI Interactive Session (v{__version__})\n"
            "Type 'exit', 'quit', 'clear', or 'history'. Press Ctrl+C or Ctrl+D to exit.\n\n"
        )
        self.out_stream.flush()

        try:
            while True:
                try:
                    # Read input using standard input() to leverage readline
                    if self.in_stream is sys.stdin:
                        line = input("AVI > ")
                    else:
                        self.out_stream.write("AVI > ")
                        self.out_stream.flush()
                        line = self.in_stream.readline()
                        if not line:
                            break
                        line = line.rstrip("\r\n")

                except EOFError:
                    # Ctrl+D at prompt
                    self.out_stream.write("\n")
                    self.out_stream.flush()
                    break
                except KeyboardInterrupt:
                    # Ctrl+C at prompt
                    self.out_stream.write("\n")
                    self.out_stream.flush()
                    break

                stripped = line.strip()
                if not stripped:
                    continue

                # Add non-empty command to readline history
                if readline is not None:
                    readline.add_history(stripped)

                # Check for exit/quit
                if stripped.lower() in ("exit", "quit"):
                    break

                # Check for other local commands
                if self._handle_local_command(stripped):
                    continue

                # Process user query through Router & Provider
                self._process_turn(stripped)

        finally:
            self._save_history()

        return 0

    def _read_confirmation(self, cmd_str: str) -> bool:
        """Prompt user for explicit y/n confirmation. Default answer is NO."""
        self.out_stream.write(
            f"\nCommand:\n{cmd_str}\n\nThis command can modify your filesystem.\n\nExecute? [y/N] "
        )
        self.out_stream.flush()

        if self.in_stream is sys.stdin:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                return False
        else:
            line = self.in_stream.readline()
            if not line:
                return False
            line = line.rstrip("\r\n")

        return line.strip().lower() == "y"

    def _handle_proposal(self, request: CommandRequest) -> None:
        """Pass command request through SafetyEngine and execute or confirm."""
        assessment = self.router.evaluate_command(request)

        if assessment.is_blocked:
            self.out_stream.write(
                f"\nCommand:\n{request.command_line}\n\n[Blocked: {assessment.reason}]\n"
            )
            self.out_stream.flush()
            return

        if assessment.requires_confirmation:
            if not self._read_confirmation(request.command_line):
                self.out_stream.write("Execution cancelled.\n")
                self.out_stream.flush()
                return

        result = self.router.execute_command(request)
        display = result.format_display()
        if display:
            self.out_stream.write(f"{display}\n")
            self.out_stream.flush()

    def _process_turn(self, query: str) -> None:
        """Dispatch a single conversation turn to the router with signal handling."""
        try:
            # 1. Deterministic fast-path check
            fast_result = self.router.check_fast_path(query)
            if isinstance(fast_result, str):
                self.out_stream.write(fast_result.rstrip("\n") + "\n")
                self.out_stream.flush()
                self._show_timing()
                return

            # 2. Query model provider
            if self.config.stream:
                buffered = ""
                stream_iter = iter(self.router.route(query, context=self.context, stream=True))
                is_proposal = False

                for chunk in stream_iter:
                    buffered += chunk
                    clean_buf = buffered.strip().upper()
                    if any(clean_buf.startswith(p) for p in ("COMMAND:", "PROPOSAL:", "```JSON", '{"', "{")):
                        is_proposal = True
                        break
                    if len(buffered.strip()) >= 12:
                        break

                if is_proposal:
                    full_text = buffered + "".join(stream_iter)
                    proposal = self.router.parse_command_proposal(full_text)
                    if isinstance(proposal, CommandRequest):
                        self._handle_proposal(proposal)
                    else:
                        self.out_stream.write(full_text.rstrip("\n") + "\n")
                        self.out_stream.flush()
                else:
                    self.out_stream.write(buffered)
                    self.out_stream.flush()
                    last_char = buffered[-1] if buffered else ""
                    for chunk in stream_iter:
                        self.out_stream.write(chunk)
                        self.out_stream.flush()
                        if chunk:
                            last_char = chunk[-1]
                    if last_char and last_char != "\n":
                        self.out_stream.write("\n")
                        self.out_stream.flush()
            else:
                resp = self.router.route_full(query, context=self.context)
                proposal = self.router.parse_command_proposal(resp.text)
                if isinstance(proposal, CommandRequest):
                    self._handle_proposal(proposal)
                elif resp.text:
                    self.out_stream.write(resp.text.rstrip("\n") + "\n")
                    self.out_stream.flush()

            # Update conversation context for next turn
            self.context = self.router.last_context
            self._show_timing()

        except KeyboardInterrupt:
            # Ctrl+C during streaming cancels active turn without terminating session
            self.out_stream.write("\n[Interrupted]\n")
            self.out_stream.flush()
        except OllamaError as err:
            self.err_stream.write(f"Error: {err}\n")
            self.err_stream.flush()

    def _show_timing(self) -> None:
        """Display elapsed timing if enabled."""
        if self.config.show_timing:
            metrics = self.router.last_metrics
            if metrics is not None and metrics.total_duration_ms is not None:
                self.err_stream.write(f"[Response: {metrics.total_duration_ms:.0f} ms]\n")
                self.err_stream.flush()

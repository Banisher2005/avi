"""Interactive REPL session for AVI."""

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
from avi.providers import OllamaError, ProviderError

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
        orchestrator: Any | None = None,
    ) -> None:
        self.router = router
        self.config = config
        self.history_path = history_path or DEFAULT_HISTORY_PATH
        self.in_stream = in_stream or sys.stdin
        self.out_stream = out_stream or sys.stdout
        self.err_stream = err_stream or sys.stderr
        self.context: Any | None = None
        self.orchestrator = orchestrator
        if self.orchestrator is None:
            from avi.orchestrator import AssistantOrchestrator

            self.orchestrator = AssistantOrchestrator(config=self.config, router=self.router)
        from avi.agent.runtime import AgentRuntime

        self.runtime = AgentRuntime(
            config=self.config,
            router=self.router,
            assistant_orchestrator=self.orchestrator,
        )
        if hasattr(self.runtime.events, "subscribe"):
            self.runtime.events.subscribe(self._on_progress_event)
        self._setup_readline()

    def _on_progress_event(self, event: Any) -> None:
        """Stream progress lines cleanly to terminal."""
        from avi.agent.formatting import OperationalEventFormatter

        line = OperationalEventFormatter.format_event(event)
        if line:
            self.out_stream.write(f"\n  {line}\n")
            self.out_stream.flush()

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

        if lower in ("tasks", "status"):
            table = self.runtime.task_registry.format_tasks_table()
            self.out_stream.write(f"\n{table}\n\n")
            self.out_stream.flush()
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

    def _read_confirmation(self, cmd_str: str, assessment: Any | None = None) -> bool:
        """Prompt user for explicit y/n confirmation. Default answer is NO."""
        if assessment is not None and hasattr(assessment, "format_confirmation_prompt"):
            prompt = assessment.format_confirmation_prompt()
        else:
            prompt = (
                f"\nCommand:\n{cmd_str}\n\nThis command can modify system state.\n\nExecute? [y/N] "
            )
        self.out_stream.write(prompt)
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
            if not self._read_confirmation(request.command_line, assessment=assessment):
                self.out_stream.write("Execution cancelled.\n")
                self.out_stream.flush()
                return

        result = self.router.execute_command(request)
        display = result.format_display()
        if display:
            self.out_stream.write(f"{display}\n")
            self.out_stream.flush()

    def _process_turn(self, query: str) -> None:
        """Dispatch a single conversation turn through the non-blocking AgentRuntime."""
        try:
            res = self.runtime.dispatch(query, context=self.context)
            if res.is_blocked:
                self.out_stream.write(f"\n[Blocked: {res.text}]\n")
                self.out_stream.flush()
                return

            if res.requires_confirmation and res.proposal is not None:
                if isinstance(res.proposal, CommandRequest):
                    self._handle_proposal(res.proposal)
                elif self._read_confirmation(res.text):
                    conf_res = self.runtime.dispatch(query, confirmed=True, context=self.context)
                    if conf_res.text:
                        self.out_stream.write(f"{conf_res.text.rstrip()}\n")
                        self.out_stream.flush()
                else:
                    self.out_stream.write("Execution cancelled.\n")
                    self.out_stream.flush()
                self._show_timing()
                return

            if res.text:
                self.out_stream.write(f"{res.text.rstrip()}\n")
                self.out_stream.flush()

            self.context = self.runtime.router.last_context
            self._show_timing()

        except KeyboardInterrupt:
            # Ctrl+C during execution cancels active turn
            self.out_stream.write("\n[Interrupted]\n")
            self.out_stream.flush()
        except (ProviderError, OllamaError) as err:
            self.err_stream.write(f"Error: {err}\n")
            self.err_stream.flush()

    def _show_timing(self) -> None:
        """Display elapsed timing if enabled."""
        if self.config.show_timing:
            metrics = self.router.last_metrics
            if metrics is not None and metrics.total_duration_ms is not None:
                self.err_stream.write(f"[Response: {metrics.total_duration_ms:.0f} ms]\n")
                self.err_stream.flush()

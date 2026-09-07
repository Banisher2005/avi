"""Command Line Interface entry point for AVI."""

import argparse
import re
import sys
from typing import Sequence

from avi import __version__
from avi.config import DEFAULT_MODEL, Config
from avi.core.router import Router
from avi.core.session import InteractiveSession
from avi.execution import CommandRequest
from avi.gateway import GatewayCore, JsonRpcDispatcher, StdioTransport, TcpTransport
from avi.hotkey import generate_systemd_user_service, get_hotkey_instructions
from avi.providers import OllamaError, ProviderError


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="avi",
        description="AVI — Fast, local-first AI desktop and terminal assistant for Linux.",
        add_help=True,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Shell Quoting Tip:
  When using natural language questions with shell metacharacters (?, *, &, ;, >),
  enclose your prompt in quotes so your shell does not attempt glob expansion:
    avi "how much space is left on my laptop?"
    avi "open google chrome"
    avi "set a timer for 5 seconds"

  Alternatively, launch the interactive REPL without arguments:
    avi
""",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt or instruction for AVI. If omitted, launches an interactive session.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"avi {__version__}",
    )
    parser.add_argument(
        "-p",
        "--provider",
        type=str,
        default=None,
        help="AI provider to use (default: ollama / local, options: local, ollama, antigravity)",
    )
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default=None,
        help=f"Model name to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Ollama host endpoint (default: http://127.0.0.1:11434)",
    )
    parser.add_argument(
        "-t",
        "--timing",
        action="store_true",
        default=None,
        help="Show response latency in milliseconds",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output and wait for complete response",
    )
    return parser


def _handle_cli_proposal(router: Router, request: CommandRequest) -> int:
    """Evaluate and execute command proposals in CLI mode."""
    assessment = router.evaluate_command(request)
    if assessment.is_blocked:
        sys.stdout.write(f"\nCommand:\n{request.command_line}\n\n[Blocked: {assessment.reason}]\n")
        sys.stdout.flush()
        return 1

    if assessment.requires_confirmation:
        sys.stdout.write(assessment.format_confirmation_prompt())
        sys.stdout.flush()
        try:
            line = sys.stdin.readline()
            ans = line.strip().lower() if line else ""
        except (EOFError, KeyboardInterrupt):
            ans = ""

        if ans != "y":
            sys.stdout.write("Execution cancelled.\n")
            sys.stdout.flush()
            return 0

    result = router.execute_command(request)
    display = result.format_display()
    if display:
        sys.stdout.write(f"{display}\n")
        sys.stdout.flush()
    return result.exit_code


def run_gateway(args: Sequence[str] | None = None) -> int:
    """Launch the Universal Protocol Gateway server for external AI clients (Claude, Cursor, MCP)."""
    parser = argparse.ArgumentParser(
        prog="avi gateway",
        description="Launch the AVI Universal Protocol Gateway for external AI clients.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "tcp"],
        default="stdio",
        help="Communication transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="TCP host to bind to (default: 127.0.0.1, local-only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="TCP port to listen on (default: 8765)",
    )
    parser.add_argument(
        "--auth-token",
        type=str,
        default=None,
        help="Optional bearer authentication token for clients",
    )
    parser.add_argument(
        "-p",
        "--provider",
        type=str,
        default=None,
        help="AI provider for gateway to use (default: local)",
    )
    opts = parser.parse_args(args)

    overrides = {}
    if opts.provider:
        overrides["provider"] = opts.provider

    config = Config.load(**overrides)
    router = Router(config)
    gateway = GatewayCore(router=router, config=config)
    dispatcher = JsonRpcDispatcher(gateway=gateway, auth_token=opts.auth_token)

    if opts.transport == "stdio":
        transport = StdioTransport(dispatcher)
        return transport.run()
    else:
        sys.stderr.write(f"AVI Gateway listening on tcp://{opts.host}:{opts.port}\n")
        sys.stderr.flush()
        transport = TcpTransport(dispatcher, host=opts.host, port=opts.port)
        return transport.start(block=True)


def run_hotkey(args: Sequence[str] | None = None) -> int:
    """Display Linux desktop environment status and hotkey setup instructions."""
    parser = argparse.ArgumentParser(
        prog="avi hotkey",
        description="Inspect desktop environment and setup Linux global hotkey for AVI.",
    )
    parser.add_argument(
        "--systemd",
        action="store_true",
        help="Print systemd user service unit definition for AVI Gateway",
    )
    opts = parser.parse_args(args)

    if opts.systemd:
        sys.stdout.write(generate_systemd_user_service())
        sys.stdout.flush()
        return 0

    info = get_hotkey_instructions()
    sys.stdout.write(f"Display Server: {info['display_server']}\n")
    sys.stdout.write(f"Desktop:        {info['desktop']}\n\n")
    sys.stdout.write(f"{info['instructions']}\n")
    sys.stdout.flush()
    return 0


def run_ui(args: Sequence[str] | None = None, is_activate: bool = False) -> int:
    """Launch the AVI GTK4 desktop popup window."""
    import os

    from avi.ui import AviApp

    if is_activate:
        has_display = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
        if has_display:
            sys.stdout.write("Activating AVI...\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(
                "Did you mean activate AVI? No display server detected (WAYLAND_DISPLAY or DISPLAY not set).\n"
            )
            sys.stdout.flush()
            return 1

    parser = argparse.ArgumentParser(
        prog="avi ui" if not is_activate else "avi activate",
        description="Launch or activate the AVI keyboard-first desktop popup window (GTK4).",
    )
    parser.add_argument(
        "-p",
        "--provider",
        type=str,
        default=None,
        help="AI provider to use (default: local)",
    )
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default=None,
        help="Model name to use",
    )
    parser.add_argument(
        "--use-system-python",
        action="store_true",
        help="Launch GTK4 UI via system Python interpreter if current environment lacks PyGObject",
    )
    parser.add_argument(
        "-b",
        "--background",
        "--daemon",
        action="store_true",
        dest="background",
        help="Start AVI overlay daemon resident in background with window hidden",
    )
    parser.add_argument(
        "--toggle",
        action="store_true",
        help="Toggle overlay visibility (hide if visible, show if hidden)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show and focus overlay window",
    )
    parser.add_argument(
        "--hide",
        action="store_true",
        help="Hide overlay window",
    )
    parser.add_argument(
        "--quit",
        action="store_true",
        help="Quit running background AVI overlay daemon",
    )
    opts = parser.parse_args(args)

    from avi.orchestrator import AssistantOrchestrator

    overrides = {}
    if opts.provider:
        overrides["provider"] = opts.provider
    if opts.model:
        overrides["model"] = opts.model

    config = Config.load(**overrides)
    router = Router(config)
    orchestrator = AssistantOrchestrator(config=config, router=router)
    allow_fallback = (
        opts.use_system_python
        or is_activate
        or opts.toggle
        or opts.background
        or opts.hide
        or opts.show
        or opts.quit
    )
    toggle_mode = opts.toggle or is_activate
    return AviApp(
        router,
        config,
        orchestrator=orchestrator,
        background=opts.background,
        toggle=toggle_mode,
        show=opts.show,
        hide=opts.hide,
    ).run(allow_system_fallback=allow_fallback)


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Internal CLI execution logic."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list:
        first = args_list[0].lower()
        if first in ("gateway", "serve"):
            return run_gateway(args_list[1:])
        elif first == "hotkey":
            return run_hotkey(args_list[1:])
        elif first in ("activate", "activaite", "activte", "actvate", "activat"):
            return run_ui(args_list[1:], is_activate=True)
        elif first == "ui":
            return run_ui(args_list[1:])

    parser = build_parser()
    args = parser.parse_args(args_list)

    # Build config from environment/defaults + CLI overrides
    overrides = {}
    if args.provider is not None:
        overrides["provider"] = args.provider
    if args.model is not None:
        overrides["model"] = args.model
    if args.host is not None:
        overrides["host"] = args.host
    if args.timing is not None and args.timing:
        overrides["show_timing"] = True
    if args.no_stream:
        overrides["stream"] = False

    config = Config.load(**overrides)
    router = Router(config)
    from avi.orchestrator import AssistantOrchestrator

    orchestrator = AssistantOrchestrator(config=config, router=router)

    raw_prompt = " ".join(args.prompt).strip() if args.prompt else ""
    # Normalize trailing standalone question mark from unquoted shell arguments
    raw_prompt = re.sub(r"\s+\?$", "?", raw_prompt)

    # Interactive mode when no prompt is supplied
    if not raw_prompt:
        session = InteractiveSession(router, config, orchestrator=orchestrator)
        return session.run()

    # 1. Check Assistant Orchestrator (intents, capabilities, actions, conversational read-only tools)
    if orchestrator.is_assistant_request(raw_prompt):
        res = orchestrator.handle(raw_prompt, auto_execute_actions=True)
        if res.is_blocked:
            sys.stdout.write(f"{res.text}\n")
            sys.stdout.flush()
            return 1
        if res.requires_confirmation and res.command_request is not None:
            _handle_cli_proposal(router, res.command_request)
        elif res.text:
            sys.stdout.write(res.text.rstrip("\n") + "\n")
            sys.stdout.flush()
        if config.show_timing:
            if res.metrics is not None and res.metrics.total_duration_ms is not None:
                sys.stderr.write(f"[Response: {res.metrics.total_duration_ms:.0f} ms]\n")
                sys.stderr.flush()
        return 0

    # Fast-path check
    fast_result = router.check_fast_path(raw_prompt)
    if isinstance(fast_result, str):
        sys.stdout.write(fast_result.rstrip("\n") + "\n")
        sys.stdout.flush()
        if config.show_timing:
            metrics = router.last_metrics
            if metrics is not None and metrics.total_duration_ms is not None:
                sys.stderr.write(f"[Response: {metrics.total_duration_ms:.0f} ms]\n")
                sys.stderr.flush()
        return 0

    # Single-shot execution mode
    if config.stream:
        buffered = ""
        stream_iter = iter(router.route(raw_prompt, stream=True))
        is_proposal = False

        for chunk in stream_iter:
            buffered += chunk
            clean_buf = buffered.strip().upper()
            if any(
                clean_buf.startswith(p) for p in ("COMMAND:", "PROPOSAL:", "```JSON", '{"', "{")
            ):
                is_proposal = True
                break
            if len(buffered.strip()) >= 12:
                break

        if is_proposal:
            full_text = buffered + "".join(stream_iter)
            proposal = router.parse_command_proposal(full_text)
            if isinstance(proposal, CommandRequest):
                _handle_cli_proposal(router, proposal)
            else:
                sys.stdout.write(full_text.rstrip("\n") + "\n")
                sys.stdout.flush()
        else:
            sys.stdout.write(buffered)
            sys.stdout.flush()
            last_char = buffered[-1] if buffered else ""
            for chunk in stream_iter:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                if chunk:
                    last_char = chunk[-1]
            if last_char and last_char != "\n":
                sys.stdout.write("\n")
                sys.stdout.flush()
    else:
        resp = router.route_full(raw_prompt)
        proposal = router.parse_command_proposal(resp.text)
        if isinstance(proposal, CommandRequest):
            _handle_cli_proposal(router, proposal)
        elif resp.text:
            sys.stdout.write(resp.text.rstrip("\n") + "\n")
            sys.stdout.flush()

    if config.show_timing:
        metrics = router.last_metrics
        if metrics is not None and metrics.total_duration_ms is not None:
            sys.stderr.write(f"[Response: {metrics.total_duration_ms:.0f} ms]\n")
            sys.stderr.flush()

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point with top-level error boundary."""
    try:
        return run_cli(argv)
    except (ProviderError, OllamaError) as err:
        sys.stderr.write(f"Error: {err}\n")
        sys.stderr.flush()
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nOperation cancelled. Aborted.\n")
        sys.stderr.flush()
        return 130
    except Exception as err:
        sys.stderr.write(f"Unexpected error: {err}\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    sys.exit(main())

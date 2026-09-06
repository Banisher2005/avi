"""Command Line Interface entry point for AVI."""

import argparse
import sys
from typing import Sequence

from avi import __version__
from avi.config import Config
from avi.core.router import Router
from avi.core.session import InteractiveSession
from avi.execution import CommandRequest
from avi.providers import OllamaError, ProviderError


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="avi",
        description="AVI — Fast, local-first AI terminal assistant for Linux.",
        add_help=True,
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
        help="Model name to use (default: qwen2.5:1.5b)",
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
        sys.stdout.write(
            f"\nCommand:\n{request.command_line}\n\nThis command can modify your filesystem.\n\nExecute? [y/N] "
        )
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


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Internal CLI execution logic."""
    parser = build_parser()
    args = parser.parse_args(argv)

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

    raw_prompt = " ".join(args.prompt).strip() if args.prompt else ""

    # Interactive mode when no prompt is supplied
    if not raw_prompt:
        session = InteractiveSession(router, config)
        return session.run()

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
            if any(clean_buf.startswith(p) for p in ("COMMAND:", "PROPOSAL:", "```JSON", '{"', "{")):
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
        sys.stderr.write("\nAborted.\n")
        sys.stderr.flush()
        return 130
    except Exception as err:
        sys.stderr.write(f"Unexpected error: {err}\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    sys.exit(main())

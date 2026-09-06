"""Command Line Interface entry point for AVI."""

import argparse
import sys
from typing import Sequence

from avi import __version__
from avi.config import Config
from avi.core.router import Router
from avi.providers.ollama import OllamaError


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
        help="Prompt or instruction for AVI (e.g. 'what command shows the current directory?')",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"avi {__version__}",
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


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI execution handler."""
    parser = build_parser()
    args = parser.parse_args(argv)

    raw_prompt = " ".join(args.prompt).strip() if args.prompt else ""
    if not raw_prompt:
        parser.print_help()
        return 0

    # Build config from environment/defaults + CLI overrides
    overrides = {}
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

    try:
        if config.stream:
            last_char = ""
            for chunk in router.route(raw_prompt, stream=True):
                sys.stdout.write(chunk)
                sys.stdout.flush()
                if chunk:
                    last_char = chunk[-1]
            if last_char and last_char != "\n":
                sys.stdout.write("\n")
                sys.stdout.flush()
        else:
            resp = router.route_full(raw_prompt)
            if resp.text:
                sys.stdout.write(resp.text.rstrip("\n") + "\n")
                sys.stdout.flush()

        if config.show_timing:
            metrics = router.last_metrics
            if metrics is not None and metrics.total_duration_ms is not None:
                sys.stderr.write(f"[Response: {metrics.total_duration_ms:.0f} ms]\n")
                sys.stderr.flush()

        return 0

    except OllamaError as err:
        sys.stderr.write(f"Error: {err}\n")
        sys.stderr.flush()
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        sys.stderr.flush()
        return 130


if __name__ == "__main__":
    sys.exit(main())

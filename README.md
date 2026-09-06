# AVI — Fast Local AI Terminal Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Ollama](https://img.shields.io/badge/backend-Ollama-purple.svg)](https://ollama.ai)

> **Press a hotkey or run `avi`, type naturally, and get a useful response almost instantly.**

AVI is a fast, local-first AI assistant for Linux terminals. It delivers instant shell command generation, system assistance, and local intelligence directly inside your workflow without relying on slow cloud APIs, heavy runtimes, or privacy-compromising telemetry.

---

## Why AVI?

* **Near-Instant Response (< 200 ms)**: Designed from the ground up for speed. Zero bloated dependencies, minimal prompt overhead, and direct HTTP communication with local LLM runtimes.
* **100% Local & Private**: All data stays on your machine. Powered by Ollama and lightweight local models like `qwen2.5:1.5b`.
* **Clean Command Output**: Shell commands are delivered directly without extraneous conversational fluff or annoying markdown fences when you just need the syntax.
* **Interactive Terminal REPL**: Full conversational session with readline support, command history, multi-turn memory, and signal handling.
* **Modular Provider Architecture**: Built with clear interfaces for local inference engines today, fast-path routing tomorrow, and complex agent delegation in the future.
* **Safe by Design**: Clear separation between generation and execution. AVI will never blindly execute dangerous commands without explicit safety pipelines and confirmation.

---

## Requirements

* **OS**: Linux (tested on modern Linux kernels with AMD/Intel/NVIDIA hardware)
* **Python**: Python 3.10 or higher
* **Ollama**: [Ollama](https://ollama.ai) installed and running locally
* **Default Model**: `qwen2.5:1.5b` (fast, lightweight, highly capable on 16GB RAM and integrated GPUs)

---

## Ollama Setup

1. **Install and start Ollama** (if not already running):
   ```bash
   ollama serve
   ```

2. **Pull the default model**:
   ```bash
   ollama pull qwen2.5:1.5b
   ```

3. **Verify model availability**:
   ```bash
   ollama list
   ```

---

## Installation & Development Setup

### Quick Install (Development / Editable)

Clone the repository and install using `uv` (recommended) or standard `pip`:

```bash
git clone https://github.com/Banisher2005/avi.git
cd avi

# Using uv (fastest)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Or using standard python3 venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify installation:

```bash
avi --version
```

---

## Usage

### 1. Interactive Session Mode

Run `avi` without arguments to launch the stateful interactive REPL:

```bash
avi
```

Example session:

```text
AVI Interactive Session (v0.1.0)
Type 'exit', 'quit', 'clear', or 'history'. Press Ctrl+C or Ctrl+D to exit.

AVI > what command shows my current directory?
pwd

AVI > my project is called AVI
It seems you're working on a project called "AVI". How can I assist you with this project?

AVI > what is my project called?
Your project is called "AVI".

AVI > history
     1  what command shows my current directory?
     2  my project is called AVI
     3  what is my project called?
     4  history

AVI > exit
```

#### Interactive Commands

| Command | Action |
| :--- | :--- |
| `exit` / `quit` | Cleanly exits the interactive session |
| `clear` | Clears the terminal screen |
| `history` | Displays command history for the session |

#### Interactive Signals

* **`Ctrl+C` while streaming**: Immediately cancels active model generation and returns to `AVI > ` without exiting.
* **`Ctrl+C` at prompt**: Exits cleanly without a traceback.
* **`Ctrl+D` at prompt**: Exits cleanly (standard EOF).
* **Command History**: Preserved across sessions in `~/.local/share/avi/history` via standard Python `readline`.

---

### 2. Single-Shot Mode

Pass a prompt directly on the command line for instant answers:

```bash
# Query a shell command
avi "what command shows the current directory?"
# Output: pwd

# Find large files
avi "find files larger than 500MB"
# Output: find . -type f -size +500M

# Inspect open ports
avi "how to check open ports listening on tcp"
# Output: ss -tulpn
```

### Response Latency & Timing

Track response duration in real-time with `-t` / `--timing`:

```bash
avi -t "what command shows the current directory?"
# Output:
# pwd
# [Response: 86 ms]
```

### Custom Model or Host

Override defaults dynamically via CLI flags or environment variables:

```bash
# CLI flag override
avi --model qwen2.5:1.5b "explain grep"
avi --host http://127.0.0.1:11434 "show disk usage"

# Non-streaming mode
avi --no-stream "list running processes sorted by memory"
```

### Environment Configuration

| Variable | Description | Default |
| :--- | :--- | :--- |
| `AVI_MODEL` | Default Ollama model | `qwen2.5:1.5b` |
| `AVI_OLLAMA_HOST` | Ollama HTTP endpoint | `http://127.0.0.1:11434` |
| `AVI_TIMEOUT` | Request timeout in seconds | `30.0` |
| `AVI_TEMPERATURE` | Generation temperature | `0.1` |
| `AVI_TIMING` | Always show response timing (`1` or `0`) | `0` |

---

## Architecture

AVI is designed around clean, decoupled components:

```text
                    AVI
                     │
              ┌──────┴──────┐
              │             │
        One-Shot CLI    Interactive REPL
              │             │
              └──────┬──────┘
                     ▼
                  Router
                     │
          ┌──────────┼──────────┐
          │          │          │
       Fast Path   Local LLM   Agent
          │          │          │
          │       Ollama    Antigravity
          │
          ▼
      Tool Layer
          │
  ┌───────┼───────┐
  │       │       │
Shell   Files   Git
```

For in-depth architectural design, provider abstractions, and future integration plans, see [docs/architecture.md](docs/architecture.md).

---

## Running Tests

Run the test suite with `pytest`:

```bash
# Run unit tests (mocked, no live Ollama required)
pytest tests/unit

# Run full test suite including live Ollama integration
pytest
```

---

## Project Roadmap

* [x] **Phase 0: Repository + Development Environment**
  * Modern Python packaging with `pyproject.toml`
  * Isolated virtual environment & CLI entry point
  * Git & GitHub repository setup
* [x] **Phase 1: Ollama Provider + Basic CLI**
  * Direct Ollama HTTP API communication with persistent keep-alive
  * Real-time streaming response engine
  * Output normalizer (code fence stripping, prompt sanitization)
  * Millisecond-accurate latency reporting
* [x] **Phase 2: Interactive CLI Session**
  * Persistent interactive REPL session (`avi`)
  * Native multi-turn conversation context retention via Ollama token arrays
  * Standard library `readline` command history and terminal navigation
  * Persistent history in `~/.local/share/avi/history`
  * Clean `Ctrl+C` stream cancellation and `Ctrl+D` handling
  * Local interactive commands (`exit`, `quit`, `clear`, `history`)
  * Top-level error boundary with clean diagnostics
* [ ] **Phase 3: Context Subsystem**
  * Minimal, explicit environment context (cwd, OS, shell, git branch)
  * Privacy controls preventing broad filesystem dumping
* [ ] **Phase 4: Tool Execution Subsystem**
  * Safe execution wrappers for shell, file inspection, and git status
* [ ] **Phase 5: Safety Subsystem & Risk Assessment**
  * Command classification (Safe, Confirmation Required, Blocked)
  * Safe execution verification pipeline
* [ ] **Phase 6: Fast-Path Routing**
  * Zero-latency deterministic resolution for common commands without invoking LLM
* [ ] **Phase 7: Antigravity Integration**
  * Intelligent handoff of complex refactor and development tasks to Antigravity CLI
* [ ] **Phase 8: Global Hotkey**
  * Linux system-wide hotkey trigger (`Ctrl+Space`)
* [ ] **Phase 9: Lightweight Desktop UI**
  * Minimal, keyboard-first desktop popup window
* [ ] **Phase 10: Packaging & Distribution**
  * Native Linux packages and PyPI distribution

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

# AVI — Fast Local AI Terminal Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Ollama](https://img.shields.io/badge/backend-Ollama-purple.svg)](https://ollama.ai)

> **Press a hotkey or run `avi`, type naturally, and get a useful response almost instantly.**

AVI is a fast, local-first AI assistant for Linux terminals. It delivers instant shell command generation, system assistance, and local intelligence directly inside your workflow without relying on slow cloud APIs, heavy runtimes, or privacy-compromising telemetry.

---

## Why AVI?

* **Near-Instant Response (< 100 ms)**: Designed from the ground up for speed. Zero bloated dependencies, minimal prompt overhead, and direct HTTP communication with local LLM runtimes.
* **Deterministic Fast-Path (0 ms)**: Direct environment queries (directory, git branch, shell, OS) resolve immediately without invoking the neural network.
* **Lazy Context Awareness**: Understands your current directory, shell, Git repository state, and previous command errors—only when relevant to your question.
* **100% Local & Private**: All data stays on your machine. Powered by Ollama and lightweight local models like `qwen2.5:1.5b`.
* **Clean Command Output**: Shell commands are delivered directly without extraneous conversational fluff or annoying markdown fences when you just need the syntax.
* **Interactive Terminal REPL**: Full conversational session with readline support, command history, multi-turn memory, and signal handling.
* **Modular Architecture**: Built with decoupled interfaces for local inference engines, fast-path routing, context ingestion, and future agent delegation.
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

### 1. Deterministic Fast-Path Queries (Instant 0 ms)

Common environment queries bypass the LLM and return instantly:

```bash
avi "what directory am I in?"
# Output: /home/abhinav/avi  [Response: 0 ms]

avi "what branch am I on?"
# Output: feature/context-awareness  [Response: 6 ms]

avi "what shell am I using?"
# Output: zsh  [Response: 0 ms]

avi "what OS is this?"
# Output: Linux  [Response: 0 ms]
```

---

### 2. Context-Aware Queries

AVI lazily detects when context is required:

```bash
# Git-aware queries
avi "recommend a command to clean up my git branch"
# Injects current branch and status into context

# Previous command error explanation
AVI_PREV_CMD="npm run dev" AVI_PREV_EXIT_CODE=1 AVI_PREV_OUTPUT="Error: address already in use" \
avi "why did my last command fail?"
# Explains port collision and suggests lsof / kill commands
```

#### Shell Integration for Previous Command (Optional)

To automatically record the last terminal command and exit code, add this hook to your `~/.zshrc` or `~/.bashrc`:

```bash
# In ~/.zshrc
precmd() {
  echo "{\"command\":\"$_ \",\"exit_code\":$?}" > ~/.local/share/avi/last_command.json 2>/dev/null
}
```

---

### 3. Interactive Session Mode

Run `avi` without arguments to launch the stateful interactive REPL:

```bash
avi
```

Example session:

```text
AVI Interactive Session (v0.1.0)
Type 'exit', 'quit', 'clear', or 'history'. Press Ctrl+C or Ctrl+D to exit.

AVI > what directory am I in?
/home/abhinav/avi

AVI > what branch am I on?
feature/context-awareness

AVI > what command shows my current directory?
pwd

AVI > history
     1  what directory am I in?
     2  what branch am I on?
     3  what command shows my current directory?
     4  history

AVI > exit
```

#### Interactive Commands

| Command | Action |
| :--- | :--- |
| `exit` / `quit` | Cleanly exits the interactive session |
| `clear` | Clears the terminal screen |
| `history` | Displays command history for the session |

---

### 4. Single-Shot Mode

Pass a prompt directly on the command line for instant answers:

```bash
# Query a shell command
avi "what command shows the current directory?"
# Output: pwd

# Find large files
avi "find files larger than 500MB"
# Output: find . -type f -size +500M

# Response latency tracking
avi -t "how to check open ports listening on tcp"
# Output:
# ss -tulpn
# [Response: 77 ms]
```

### Environment Configuration

| Variable | Description | Default |
| :--- | :--- | :--- |
| `AVI_MODEL` | Default Ollama model | `qwen2.5:1.5b` |
| `AVI_OLLAMA_HOST` | Ollama HTTP endpoint | `http://127.0.0.1:11434` |
| `AVI_TIMEOUT` | Request timeout in seconds | `30.0` |
| `AVI_TEMPERATURE` | Generation temperature | `0.1` |
| `AVI_TIMING` | Always show response timing (`1` or `0`) | `0` |
| `AVI_PREV_CMD` | Previous command text for context | `None` |
| `AVI_PREV_EXIT_CODE`| Previous command exit code | `None` |
| `AVI_PREV_OUTPUT` | Previous command output snippet | `None` |

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
* [x] **Phase 3: Context Subsystem**
  * Minimal, explicit environment context (cwd, OS, shell, git status)
  * Strict lazy context injection (zero overhead on generic questions)
  * Sub-millisecond deterministic fast-path for direct environment queries
  * Previous command inspection support
  * Strict privacy controls preventing broad filesystem or secret dumping
* [ ] **Phase 4: Tool Execution Subsystem**
  * Safe execution wrappers for shell, file inspection, and git status
* [ ] **Phase 5: Safety Subsystem & Risk Assessment**
  * Command classification (Safe, Confirmation Required, Blocked)
  * Safe execution verification pipeline
* [ ] **Phase 6: Fast-Path Routing**
  * Expanded zero-latency deterministic resolution for command templates
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

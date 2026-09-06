# AVI — Fast Local AI Terminal Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Ollama](https://img.shields.io/badge/backend-Ollama-purple.svg)](https://ollama.ai)

> **Press a hotkey or run `avi`, type naturally, and get a useful response almost instantly.**

AVI is a fast, local-first AI assistant for Linux terminals. It delivers instant shell command generation, read-only system inspection, and local intelligence directly inside your workflow without relying on slow cloud APIs, heavy runtimes, or privacy-compromising telemetry.

---

## Why AVI?

* **Near-Instant Response (< 100 ms)**: Designed from the ground up for speed. Zero bloated dependencies, minimal prompt overhead, and direct HTTP communication with local LLM runtimes.
* **Deterministic Fast-Path (< 20 ms)**: Read-only system inspection and environment queries resolve immediately via built-in tools without invoking the neural network.
* **Controlled Read-Only Tools**: Inspect filesystem contents, running processes, disk usage, system info, and Git repository status with strict security guarantees.
* **Lazy Context Awareness**: Understands your current directory, shell, Git repository state, and previous command errors—only when relevant to your question.
* **100% Local & Private**: All data stays on your machine. Powered by Ollama and lightweight local models like `qwen2.5:1.5b`.
* **Clean Command Output**: Shell commands are delivered directly without extraneous conversational fluff or annoying markdown fences when you just need the syntax.
* **Interactive Terminal REPL**: Full conversational session with readline support, command history, multi-turn memory, and signal handling.
* **Safe by Design**: Strict read-only tools. AVI will never execute arbitrary shell commands or modify your filesystem without explicit safety pipelines and confirmation.

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

### 1. Read-Only Tools & Fast-Path Queries (< 20 ms)

Inspect system, git, and filesystem information without invoking the LLM:

```bash
# Filesystem inspection
avi "what files are here?"
# Output:
# Contents of /home/abhinav/avi:
#   docs/                            [dir]
#   src/                             [dir]
#   tests/                           [dir]
#   README.md                        8.2 KB
#   pyproject.toml                   1.2 KB

# Process memory inspection
avi "what's using the most RAM?"
# Output:
# Top processes by memory:
#        PID  NAME                   %MEM    %CPU
#     209516  llama-server           7.6%   36.9%
#     201135  chrome                 3.0%    7.4%

# Filesystem disk usage
avi "how much disk space do I have?"
# Output:
# Disk Usage (/):
#   Total:     239.2 GB
#   Used:      73.3 GB (30.6%)
#   Available: 153.7 GB

# Git inspection
avi "what branch am I on?"
avi "recent commits"
avi "git status"
```

---

### 2. Available Read-Only Tools

| Tool Name | Domain | Description |
| :--- | :--- | :--- |
| `filesystem.list_directory` | Filesystem | Lists directory entries with types and sizes (non-recursive) |
| `filesystem.file_metadata` | Filesystem | Inspects file permissions, timestamps, and size |
| `system.processes` | System | Lists top running processes sorted by memory or CPU |
| `system.disk_usage` | System | Checks total, used, and available disk space |
| `system.system_info` | System | Reports OS, kernel version, architecture, and CPU count |
| `git.status` | Git | Checks repository root, branch, and clean/dirty status |
| `git.branch` | Git | Returns active branch name |
| `git.log` | Git | Displays recent commit history (non-diff) |

---

### 3. Context-Aware Inquiries

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

---

### 4. Interactive Session Mode

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
feature/read-only-tools

AVI > what files are here?
Contents of /home/abhinav/avi:
  src/                             [dir]
  tests/                           [dir]
  README.md                        8.2 KB

AVI > exit
```

---

### 5. Single-Shot Mode

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

---

### 6. AI Providers & Model Agnosticism

AVI is designed around a strict architectural invariant:

> **AVI Core is provider-independent. Providers are adapters around AVI, not dependencies inside AVI Core.**

The AI client provides intelligence; AVI provides execution, tools, safety, routing, context, and environment access.

```text
             ┌───────────────────────┐
             │      AI CLIENTS       │
             │                       │
             │ Claude / GPT / Gemini │
             │ Antigravity / Cursor  │
             │ Local / Custom Agent  │
             └───────────┬───────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │   PROVIDER ADAPTERS   │
             │ (Ollama / Antigravity │
             │  OpenAI / Anthropic)  │
             └───────────┬───────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │      AVI GATEWAY      │
             └───────────┬───────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │       AVI CORE        │
             │                       │
             │ Router                │
             │ FastPath              │
             │ Tool Registry         │
             │ Context Subsystem     │
             └───────────┬───────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │     SAFETY ENGINE     │
             │                       │
             │ SAFE / CONFIRM/BLOCK  │
             └───────────┬───────────┘
                         │
                         ▼
             ┌───────────────────────┐
             │ TOOLS / COMMANDS      │
             │                       │
             │ Read-Only Tools       │
             │ CommandExecutor       │
             └───────────────────────┘
```

#### Selecting Providers

Select the active provider via the CLI flag `-p / --provider` or the `AVI_PROVIDER` environment variable:

```bash
# Use local Ollama provider (default)
avi --provider local "how to check disk space"

# Use Antigravity adapter
avi --provider antigravity "explain git rebase"

# Or configure via environment variable
export AVI_PROVIDER=antigravity
avi "explain python context managers"
```

#### Strict Safety Invariant

No AI provider can execute shell commands directly or bypass AVI's `SafetyEngine`. All command proposals and tool requests from any provider must strictly pass through:

```text
AI Provider ──> ToolCall ──> Tool Registry ──> Safe Read-Only Execution ──> ToolResult
AI Provider ──> Command Proposal ──> SafetyEngine ──> SAFE / CONFIRM / BLOCK ──> CommandExecutor
```

---

## Running Tests

Run the test suite with `pytest`:

```bash
# Run all unit and integration tests (mocked, no live Ollama or Antigravity required)
pytest

# Run specifically provider tests
pytest tests/unit/test_providers.py
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
* [x] **Phase 4: Tool Execution Subsystem**
  * Modular, strictly read-only tool abstraction (`BaseTool`, `ToolResult`, `ToolRegistry`)
  * 8 core inspection tools (Filesystem, System, Git)
  * Sub-millisecond deterministic tool dispatch
  * Strict security: no `shell=True`, no file modification, no privilege escalation
* [x] **Phase 5: Safety Subsystem & Safe Command Execution**
  * Deterministic SAFE / CONFIRM / BLOCK risk classification engine
  * Fail-closed security policy preventing accidental command execution
  * Explicit interactive `[y/N]` confirmation with secure defaults
  * Isolated subprocess runner strictly using `subprocess.Popen(..., shell=False)` (0 `shell=True`)
  * Process group timeout termination (`os.killpg`) preventing zombie processes
  * 64 KiB memory and context output capping
* [x] **Phase 6: Fast-Path Routing**
  * Sub-millisecond deterministic intent resolution (`< 0.01 ms`, ~5.3 µs measured across 27 templates)
  * Pre-compiled templates for common terminal intents (directory, files, git, ports, memory, uptime, versions)
  * Parameterized templates (`find by size`, `find by language`, `find modified`, `find empty`, `git log -N`, `grep in files`)
  * Strict parameter sanitization (`is_safe_parameter`) rejecting shell metacharacters, control characters, expansions, and injections
  * Full routing through Phase 5 SafetyEngine before execution
  * Fail-closed fallback to LLM provider for ambiguous prompts
* [x] **Phase 7: Provider-Agnostic AI Integration Layer**
  * AIProvider / BaseProvider clean interface (`send`, `stream`, `capabilities`, `health_check`)
  * Normalized models (`AgentRequest`, `AgentResponse`, `ToolCall`, `ProviderCapabilities`, `ProviderHealth`, `ProviderError`)
  * Dynamic `ProviderRegistry` (`register_provider`, `get_provider`, `list_providers`, `remove_provider`)
  * Zero Antigravity or provider-specific coupling in AVI Core
  * Antigravity CLI adapter (`AntigravityProvider`)
  * Local Ollama provider abstraction (`LocalProvider` / `OllamaProvider`)
  * Universal SafetyEngine enforcement across all providers (zero command safety bypass)
  * Normalized tool calling architecture (`AI -> ToolCall -> Tool Registry -> Safety -> Execution`)
  * CLI `--provider` flag and `AVI_PROVIDER` configuration
* [ ] **Phase 8: Global Hotkey & Protocol Gateway**
  * Linux system-wide hotkey trigger (`Ctrl+Space`)
  * Standard agent/tool protocol gateway (MCP / JSON-RPC interface)
* [ ] **Phase 9: Lightweight Desktop UI**
  * Minimal, keyboard-first desktop popup window
* [ ] **Phase 10: Packaging & Distribution**
  * Native Linux packages and PyPI distribution

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

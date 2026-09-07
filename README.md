# AVI — Fast Local AI Terminal Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Ollama](https://img.shields.io/badge/backend-Ollama-purple.svg)](https://ollama.ai)

> **Press a hotkey or run `avi`, type naturally, and get a useful response almost instantly.**

AVI is a fast, local-first AI assistant for Linux terminals. It delivers instant shell command generation, read-only system inspection, and local intelligence directly inside your workflow without relying on slow cloud APIs, heavy runtimes, or privacy-compromising telemetry.

---

## Why AVI?

* **Desktop Assistant Runtime**: Beyond raw shell generation, AVI behaves like a genuine assistant: answering questions conversationally ("how much space is left on my laptop"), launching apps ("open brave", "open antigravity"), setting background timers, and opening URLs or local folders.
* **Near-Instant Response (< 100 ms)**: Designed from the ground up for speed. Zero bloated dependencies, minimal prompt overhead, and direct HTTP communication with local LLM runtimes.
* **Deterministic Fast-Path (< 20 ms)**: Read-only system inspection and environment queries resolve immediately via built-in tools without invoking the neural network.
* **Controlled Read-Only Tools**: Inspect filesystem contents, running processes, disk usage, system info, and Git repository status with strict security guarantees.
* **Lazy Context Awareness**: Understands your current directory, shell, Git repository state, and previous command errors—only when relevant to your question.
* **100% Local & Private**: All data stays on your machine. Powered by Ollama and lightweight local models like `qwen3:4b` (or `qwen2.5:1.5b`).
* **Clean Command Output**: Shell commands are delivered directly without extraneous conversational fluff or annoying markdown fences when you just need the syntax.
* **Granular Risk & Capability Taxonomy**: Distinguishes read-only inspection, benign desktop actions, outbound network calls, filesystem writes, destructive commands, and privileged operations with clear, contextual confirmation prompts.
* **Interactive Terminal REPL & GTK4 Popup**: Full conversational session with readline support, command history, and a keyboard-first GTK4 desktop popup window.
* **Safe by Design**: Strict read-only tools and zero `shell=True`. AVI will never execute arbitrary shell commands or modify your filesystem without explicit safety pipelines and confirmation.

---

## Requirements

* **OS**: Linux (tested on modern Linux kernels with AMD/Intel/NVIDIA hardware)
* **Python**: Python 3.10 or higher
* **Ollama**: [Ollama](https://ollama.ai) installed and running locally
* **Default Model**: `qwen3:4b` (recommended default, highly capable local reasoning; fallback models like `qwen2.5:1.5b` are fully supported)

---

## Ollama Setup

1. **Install and start Ollama** (if not already running):
   ```bash
   ollama serve
   ```

2. **Pull the default model**:
   ```bash
   ollama pull qwen3:4b
   ```

3. **Verify model availability**:
   ```bash
   ollama list
   ```

---

## Installation & Development Setup

### 1. One-Shot Linux Install (Recommended)

Install using the standalone script which automatically chooses the best isolated method (`uv tool`, `pipx`, or user local):

```bash
curl -fsSL https://raw.githubusercontent.com/Banisher2005/avi/main/install.sh | bash
```

Or install directly with `uv` or `pipx`:

```bash
# Using uv tool
uv tool install avi

# Using pipx
pipx install avi
```

### 2. Development / Editable Setup

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

### 1. Conversational Desktop Assistant & System Queries (< 20 ms)

AVI responds conversationally and executes native desktop actions without invoking slow neural networks:

```bash
# Natural-language disk space inspection
avi "how much space is left on my laptop"
# Output:
# You have about 153.7 GB free out of 239.2 GB on your main drive.

# Memory and RAM overview
avi "how much memory is free?"
# Output:
# You have about 10.2 GB of RAM available out of 15.3 GB total (33.3% in use).

# Top running processes
avi "what's using the most RAM?"
# Output:
# Highest memory usage: llama-server (7.6% RAM, 36.9% CPU), chrome (3.0% RAM, 7.4% CPU).

# Desktop application launching
avi "open brave"
# Output: Launched Brave Web Browser.

avi "open chrome"
# Output: Launched Google Chrome.

avi "open antigravity"
# Output: Launched Antigravity.

# Non-blocking desktop timers
avi "set a timer for 2 minutes"
# Output: Timer set for 2 minutes. I'll alert you when it's done.

# Open web URLs, files, and directories
avi "open https://github.com"
# Output: Opened https://github.com in your default browser.

avi "open ~/Documents"
# Output: Opened /home/abhinav/Documents in file manager.

# Desktop screenshots (LOCAL_ONLY, privacy-safe)
avi "take a screenshot"
# Output: Captured screenshot (1920x1080) to /home/abhinav/Pictures/Screenshots/screenshot_20260906_191000.png

# Multi-step workflows (piped execution)
avi "take a screenshot and open it"
# Output: Successfully captured screenshot and opened it in default viewer.

avi "find the newest PDF in ~/Downloads and open it"
# Output: Found /home/abhinav/Downloads/invoice.pdf and opened it.

# Audio volume & media playback controls
avi "mute system audio"
avi "set volume to 50%"
avi "pause music"

# Desktop notifications
avi "notify me meeting starts now"
```

> [!TIP]
> **Shell Wildcards & Globs**: Always quote arguments containing wildcards (e.g. `avi "find *.py"` rather than `avi find *.py`). If unquoted, your shell expands `*.py` before AVI receives the prompt!

---

### 2. Read-Only Tools & Fast-Path Queries (< 20 ms)

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

# Git inspection
avi "what branch am I on?"
avi "recent commits"
avi "git status"
```

---

### 3. Available Read-Only Tools

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

### 4. Context-Aware Inquiries

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

### 5. Interactive Session Mode

Run `avi` without arguments to launch the stateful interactive REPL:

```bash
avi
```

Example session:

```text
AVI Interactive Session (v0.3.0)
Type 'exit', 'quit', 'clear', or 'history'. Press Ctrl+C or Ctrl+D to exit.

AVI > what directory am I in?
/home/abhinav/avi

AVI > what branch am I on?
main

AVI > how much space is left on my laptop
You have about 153.7 GB free out of 239.2 GB on your main drive.

AVI > exit
```

---

### 6. Single-Shot Mode

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

### 7. AI Providers & Model Agnosticism

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

No AI provider or external protocol client can execute shell commands directly or bypass AVI's `SafetyEngine`. All command proposals and tool requests from any source must strictly pass through:

```text
AI Client / Provider ──> ToolCall ──> Tool Registry ──> Safe Read-Only Execution ──> ToolResult
AI Client / Provider ──> Command Proposal ──> SafetyEngine ──> SAFE / CONFIRM / BLOCK ──> CommandExecutor
```

---

## Universal Protocol Gateway & MCP (`avi gateway`)

AVI functions as an independent, protocol-agnostic AI tool and execution server for external AI clients such as **Claude Desktop**, **Cursor**, **VS Code**, and autonomous agents.

### Running the Gateway

```bash
# Launch standard I/O transport (for Claude Desktop / Cursor MCP)
avi gateway --transport stdio

# Or launch local TCP server on 127.0.0.1:8765
avi gateway --transport tcp --port 8765

# Secure with bearer authentication token
avi gateway --transport tcp --auth-token my-secret-token
```

### Claude Desktop & Cursor Integration

To use AVI tools directly inside Claude Desktop, add to `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "avi": {
      "command": "avi",
      "args": ["gateway", "--transport", "stdio"]
    }
  }
}
```

### Supported Protocols & Operations

* **Model Context Protocol (MCP)**: Conforms to specification 2024-11-05 (`initialize`, `ping`, `tools/list`, `tools/call`).
* **JSON-RPC 2.0**: Full standard support including batch requests and error codes.
* **Command Safety Evaluation**: Clients can probe command risk (`command/evaluate`) before execution.
* **Cryptographic Confirmation Tokens**: State-modifying commands require explicit confirmation tokens (`cf-...`) with a 5-minute TTL to prevent accidental remote modifications.

---

## Linux Global Hotkey & Desktop Integration (`avi hotkey`)

Inspect your Linux desktop environment and set up instant terminal activation:

```bash
# Detect display server (Wayland / X11) and view configuration steps
avi hotkey

# Output systemd user service unit definition
avi hotkey --systemd
```

### One-Button Instant Desktop Activation (`avi activate`)
Bind a global keybinding (such as `Super+Space`) to `avi activate`.
If the popup is already running, `avi activate` instantly brings the existing window to the front and focuses the input prompt using FreeDesktop DBus single-instance communication—without launching duplicate processes:

- **GNOME**: Settings -> Keyboard -> Custom Shortcuts -> Command: `avi activate`
- **KDE Plasma**: System Settings -> Shortcuts -> Custom Shortcuts -> Command: `avi activate`
- **Sway**: `bindsym $mod+space exec avi activate`
- **Hyprland**: `bind = $mainMod, SPACE, exec, avi activate`
- **Terminal Mode**: Use `avi activate --terminal` (runs `gnome-terminal -- avi`) if you prefer an instant terminal popup.

---

## Desktop UI (`avi ui`)

AVI includes a **keyboard-first GTK4 popup window** — a minimal, floating prompt that can be opened from a global hotkey and closed instantly with Escape.

### Requirements

- GTK4 and PyGObject installed on your system:
  ```bash
  sudo apt install python3-gi gir1.2-gtk-4.0   # Debian/Ubuntu
  sudo pacman -S python-gobject gtk4            # Arch
  sudo dnf install python3-gobject gtk4         # Fedora
  ```
- Wayland or X11 display session (auto-detected).

### Launching the Popup

```bash
# Open popup with default provider
avi ui

# Open popup with a specific provider
avi ui --provider ollama --model llama3.1

# If running inside a virtualenv with GTK4 installed system-wide:
avi ui --use-system-python
```

> [!NOTE]
> **Virtualenv & GTK4 ABI Compatibility**: If your system packages (e.g. `python3-gi`, `gir1.2-gtk-4.0`) are installed under your system Python (such as Python 3.14) while your local virtualenv runs a different version (such as Python 3.12), running `avi ui` will display a clear diagnosis. Use `avi ui --use-system-python` or create your venv with `--system-site-packages` to bridge system libraries.

### Global Hotkey Binding (GNOME/Wayland)

1. Open **Settings** → **Keyboard** → **Custom Shortcuts**
2. Add shortcut:
   - **Name**: `AVI Popup`
   - **Command**: `/path/to/avi/venv/bin/avi ui`
   - **Shortcut**: `Super+A` (or any preference)

### Popup Keyboard Controls

| Key | Action |
| :--- | :--- |
| `Enter` | Submit prompt to AVI |
| `Esc` | Close window immediately |
| `y` | Confirm proposed command execution |
| `n` | Reject proposed command execution |

### Popup Window Features

- **Real-time streaming** — response text appears as tokens arrive
- **Inline confirmation** — commands requiring approval show `[✓ Yes] [✗ No]` buttons inline
- **BLOCK enforcement** — dangerous commands are blocked before any execution prompt
- **Provider/model indicator** — shows active AI provider in the status bar
- **Headless-safe** — exits cleanly with code 1 + helpful message if no display is available

---

## Running Tests

Run the test suite with `pytest`:

```bash
# Run all 768 unit and integration tests (766 passing, 2 conditionally skipped)
pytest

# Run agent capabilities and screenshot tests
pytest tests/unit/test_capabilities.py tests/unit/test_screenshot.py

# Run agent planner and acceptance tests
pytest tests/unit/test_agent_planner.py tests/integration/test_agent_acceptance.py

# Run assistant orchestrator, actions, and app tests
pytest tests/unit/test_assistant.py tests/unit/test_actions.py tests/unit/test_apps.py

# Run packaging and distribution tests
pytest tests/unit/test_packaging.py

# Run UI tests
pytest tests/unit/test_ui.py

# Run gateway and MCP tests
pytest tests/unit/test_gateway.py

# Run provider tests
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
* [x] **Phase 8: Universal Protocol Gateway & Desktop Integration**
  * Standard agent/tool protocol gateway (JSON-RPC 2.0 & Model Context Protocol / MCP)
  * Multi-transport support: line-delimited `stdio` and local `tcp://127.0.0.1` socket server
  * Full tool discovery (`tools/list`), execution (`tools/call`), and parameter schemas
  * Cryptographic confirmation tokens (`cf-...`) with 5-minute TTL for state-modifying operations
  * External agent routing (`agent/send`) and environment context queries (`context/get`)
  * Linux desktop environment detector (Wayland / X11 / Headless) with native compositor hotkey guides
  * Systemd user service generator (`avi hotkey --systemd`)
* [x] **Phase 9: Lightweight Desktop UI**
  * Keyboard-first GTK4 popup window (`avi ui`)
  * Real-time streaming response display
  * Inline CONFIRM/BLOCK safety enforcement with `[y/N]` keyboard controls
  * Wayland-native + X11 via GTK4 (zero extra Python dependencies)
  * Thread-safe: AVI routing in background thread, GTK updates via `GLib.idle_add`
  * Headless-safe: graceful exit with descriptive error when no display available
* [x] **Phase 10: Packaging & Distribution**
  * One-shot Linux installer script (`install.sh`) supporting `uv tool`, `pipx`, and `pip --user`
  * Complete `pyproject.toml` distribution configuration (v0.3.0, build targets, URLs, classifiers)
  * Automated GitHub Actions CI workflow (Python 3.10, 3.11, 3.12, linting, security scans)
  * Automated GitHub Actions Release workflow with OIDC trusted PyPI publishing
  * Developer `Makefile` with targets for setup, testing, formatting, linting, building, and benchmarking
  * Keep-a-Changelog structured `CHANGELOG.md`
  * Packaging and distribution test suite with wheel verification (68 tests)
* [x] **Phase 11: Desktop Assistant Runtime & Orchestration Model**
  * Layered `AssistantOrchestrator` mediating intents, actions, tools, and LLM reasoning
  * Deterministic intent classification engine (< 1 ms latency)
  * FreeDesktop `.desktop` application resolver with PATH discovery and desktop aliasing
  * Native desktop actions: non-blocking timers, app launching, web URLs, file/dir opening
  * Natural-language conversational synthesizers for disk space, RAM, CPU, processes, and git status
  * Granular 6-tier risk & capability taxonomy (`READ_ONLY`, `LOW_RISK_ACTION`, `EXTERNAL_ACTION`, `FILESYSTEM_WRITE`, `DESTRUCTIVE`, `PRIVILEGED`)
  * Dynamic GTK4 multi-python ABI diagnosis and `--use-system-python` option
  * Provider abstraction refinement (`LLMProvider`) separating model reasoning from runtime execution
* [x] **Phase 12: AVI Agent Runtime + One-Button Desktop Assistant**
  * Unified capability system (`BaseCapability`, `CapabilityRegistry`, `CapabilityResult`)
  * Desktop capabilities: screenshot capture (Wayland/X11), notifications, volume/audio controls, media playback, application launcher
  * Filesystem capabilities: bounded recursive search, directory creation, safe copy, move, and delete
  * Bounded multi-step `AgentPlanner` and `AgentExecutor` with dataflow piping and partial failure recovery
  * Strict privacy boundaries: `LOCAL_ONLY` screenshot artifacts stored locally, never uploaded to remote providers
  * Honest vision negotiation: upfront capability inspection declaring when active model lacks image analysis
  * One-button desktop assistant: `avi activate` with single-instance GTK4 window handling and compositor hotkey integration (GNOME, KDE, Sway, Hyprland, X11)
  * 766 passed unit and integration tests (100% clean)

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

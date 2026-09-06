# AVI System Architecture

## 1. Overview & Architectural Philosophy

AVI is built around three core architectural tenets:
1. **Speed First**: Sub-100ms latency for simple terminal requests and 0ms (~5.3 µs) for deterministic queries. Zero unnecessary imports, minimal context overhead, persistent model warmup, and direct HTTP streaming.
2. **Modular Decoupling**: Strict boundary separation between User Interface (One-Shot CLI & Interactive REPL), Routing, Context Ingestion, Model Providers, Tool Execution, Safety Verification, and External Protocol Gateways.
3. **Safety & Privacy by Design**: AI models should suggest; execution must always be verified. Context gathering is strictly bounded, private, and never dumps environment variables or arbitrary files.

### 1.1 The Seven Architectural Invariants

1. **Provider Independence**: AVI Core is provider-independent. AI providers are adapters around AVI, not dependencies inside AVI Core.
2. **Universal Safety Enforcement**: No provider, user CLI invocation, or external protocol client may bypass `SafetyEngine` or execute commands without risk assessment.
3. **Strict Subprocess Execution**: Zero occurrences of `shell=True`, `os.system()`, `eval()`, or `exec()`. Every command runs as an array of arguments via `subprocess.Popen(..., shell=False)`.
4. **Cryptographic One-Time Confirmation Tokens**: State-modifying operations require time-bounded (TTL), cryptographically random confirmation tokens (`cf-...`) that expire and are invalidated immediately after a single use.
5. **Local-Only Networking**: Network transports default strictly to `127.0.0.1` (localhost) and stdio to avoid unauthorized remote exposure.
6. **Sandboxed Read-Only Inspection**: Built-in tools are strictly read-only and enforce JSON Schema argument validation before execution.
7. **Sub-Millisecond Protocol Overhead**: Protocol dispatch and deterministic intent resolution must maintain sub-millisecond execution times (< 0.05 ms).

```text
┌─────────────────────────────────────────────────────────────┐
│                       EXTERNAL CLIENTS                      │
│   Claude Desktop  │  Cursor  │  VS Code  │  Terminal REPL   │
└──────────────┬───────────────────────────────┬──────────────┘
               │ (stdio / MCP / JSON-RPC 2.0)   │ (direct CLI)
               ▼                               │
┌──────────────────────────────────────────────┴──────────────┐
│                   UNIVERSAL PROTOCOL GATEWAY                │
│    JSON-RPC 2.0 Dispatcher  │  MCP Protocol Conformance     │
│    StdioTransport           │  TcpTransport (127.0.0.1)     │
│    Confirmation Token TTL   │  GatewayCore Operations       │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                          AVI CORE                           │
│    FastPath Engine          │  Context Subsystem            │
│    Core Router              │  Tool Registry (8 tools)      │
│    ProviderRegistry         │  Model Adapters (Ollama, AGY) │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                        SAFETY ENGINE                        │
│    Deterministic Classifier │  SAFE / CONFIRM / BLOCK       │
│    Syntax & Injection Guard │  Fail-Closed Enforcement      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                      EXECUTION LAYER                        │
│    Read-Only Tool Dispatch  │  CommandExecutor              │
│    subprocess(shell=False)  │  Output Buffering & Timeout   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Core Components

### 2.1 CLI Layer (`avi.cli`)
The CLI provides the primary entry point:
* **One-Shot Mode**: Parses flags, routes the prompt, streams chunks to stdout, and exits.
* **Gateway Subcommand**: `avi gateway` / `avi serve` starts the Universal Protocol Gateway server over stdio or local TCP.
* **Hotkey Subcommand**: `avi hotkey` detects desktop environment (Wayland / X11 / Headless) and provides keybinding instructions or systemd unit files (`--systemd`).
* **Provider Flag**: Supports explicit provider selection via `-p / --provider` (e.g. `avi --provider local`, `avi --provider antigravity`).
* **Interactive Mode**: Instantiates `InteractiveSession` when invoked without prompt arguments.
* **Top-Level Error Boundary**: Catches domain exceptions (`ProviderError`, `OllamaError`), system interruptions (`KeyboardInterrupt`), and unexpected runtime errors with clean diagnostics.

### 2.2 Interactive Session (`avi.core.session`)
Manages the stateful REPL:
* **Readline Integration**: Uses Python's standard library `readline` module for arrow-key navigation, editing, and persistent command history in `~/.local/share/avi/history`.
* **Zero-Dependency Architecture**: No heavyweight terminal frameworks are imported, keeping session startup latency under 80 ms.
* **Local Commands**: Evaluates `exit`, `quit`, `clear`, and `history` locally before routing to the provider backend.
* **Granular Signal Handling**: `Ctrl+C` while streaming cancels only the active generation and returns to `AVI > `. `Ctrl+C` or `Ctrl+D` at the prompt exits cleanly.

### 2.3 Context Subsystem (`avi.context`)
A dedicated, isolated module providing environmental awareness:
* **`TerminalContext`**: Captures canonical current working directory (`cwd`), operating system (`platform.system()`), and shell name (`$SHELL`).
* **`GitContext`**: Checks repository boundaries via `git rev-parse`, reports repository root, active branch, and porcelain status (clean/dirty, modified count, untracked count) without reading diffs.
* **`PreviousCommandContext`**: Inspects `AVI_PREV_CMD` env variables or `~/.local/share/avi/last_command.json` for command string, exit code, and truncated output (max 500 chars).
* **Strict Privacy Guard**: Never collects environment variables, secrets, credentials, or arbitrary file content.
* **Lazy Context Selection**: `detect_needed_context()` analyzes incoming prompt keywords. Unrelated requests (`"what command lists files?"`) receive **zero context**, keeping prompt tokens under 85 and latency under 100 ms.

### 2.4 Core Router (`avi.core.router`)
The central coordinator:
* **Deterministic Fast-Path**: Intercepts direct environment queries (`what directory am I in?`, `what branch am I on?`, `what shell am I using?`, `what OS is this?`) and resolves them in **0 to 6 ms** without invoking an LLM.
* **Fast-Path Command Templates**: Resolves common Linux intent patterns in **~5.3 µs** directly to structured requests without provider calls.
* **Lazy Prompt Assembly**: Combines base system instructions with formatted context blocks only when context is required.
* **Provider Agnosticism**: Dispatches normalized requests (`AgentRequest`) to the configured `AIProvider`.
* **Tool Calling Resolution**: Resolves provider `ToolCall` requests through the verified `ToolRegistry`.

### 2.5 Provider Abstraction Layer (`avi.providers`)
Abstracts model backends behind a uniform interface:
* **`AIProvider` / `BaseProvider`**: Common interface supporting:
  * `send(request: AgentRequest) -> AgentResponse`: Normalized synchronous request execution.
  * `stream(request: AgentRequest) -> Iterator[str]`: Normalized streaming token chunks.
  * `capabilities() -> ProviderCapabilities`: Explicit capability reporting (streaming, tool calling, reasoning, local/remote).
  * `health_check() -> ProviderHealth`: Backend connectivity and status diagnostic.
  * `is_available() -> bool`: Quick reachability probe.
  * `warmup() -> bool`: Memory/cache warmup.
* **Normalized Models**:
  * `AgentRequest`: Request payload (prompt, system prompt, context, tools, temperature, stream flag).
  * `AgentResponse`: Response container (text, tool calls, metrics, context tokens, finish reason, raw data).
  * `ToolCall`: Structured tool invocation (`name`, `arguments`, `call_id`).
  * `ProviderCapabilities`: Flags (`streaming`, `tool_calling`, `structured_output`, `reasoning`, `context_size`, `local`, `remote`).
  * `ProviderHealth`: Health report (`healthy`, `message`, `latency_ms`, `details`).
  * `ProviderError`: Standard exception hierarchy (`ProviderConnectionError`, `ProviderTimeoutError`, `ProviderModelNotFoundError`, `ProviderAPIError`, `ProviderNotAvailableError`).
* **`ProviderRegistry`**: Dynamic registration and lookup factory (`register_provider`, `get_provider`, `list_providers`, `remove_provider`).
* **Built-in Adapters**:
  * `LocalProvider` / `OllamaProvider`: Local inference via Ollama HTTP API with zero token warmup and context reuse.
  * `AntigravityProvider`: Antigravity CLI adapter using secure subprocess execution (`shell=False`) and tool-calling extraction.

### 2.6 Tool Subsystem (`avi.tools`)
A modular, controlled subsystem for executing read-only machine inspections:
* **`BaseTool` & `ToolResult`**: Strict abstract base class requiring `safety_level = "read_only"`. Tools provide complete JSON Schema parameter definitions via `input_schema` and return strongly typed `ToolResult` objects.
* **`ToolRegistry`**: Centralized registry for tool registration, lookup, listing, and execution.
* **8 Default Read-Only Tools**:
  * `filesystem.list_directory`: Lists directory contents with sizes and modification times.
  * `filesystem.file_metadata`: Retrieves file metadata without reading arbitrary file contents.
  * `system.processes`: Inspects running processes sorted by RAM or CPU via `ps` with fixed argument array (`shell=False`).
  * `system.disk_usage`: Checks total, used, and free disk space via standard library `shutil.disk_usage`.
  * `system.system_info`: Inspects OS distribution, kernel version, architecture, CPU count, and hostname.
  * `git.status`: Runs `git status --porcelain=v1` within repository boundaries (`shell=False`).
  * `git.branch`: Returns active git branch via `git branch --show-current`.
  * `git.log`: Inspects recent commit log history via `git log -n ...` with format restrictions.
* **Tool Calling Flow**:
  ```text
  AI Provider ──> ToolCall ──> AVI Tool Registry ──> Tool Execution ──> ToolResult ──> AI Provider
  ```

### 2.7 Safety Engine Subsystem (`avi.safety`)
A deterministic, model-independent validation and risk assessment pipeline:
* **Safety Invariant**: All providers and gateway clients, without exception, must route command proposals through SafetyEngine:
  ```text
  Client / Provider ──> Normalized Proposal ──> SafetyEngine ──> SAFE / CONFIRM / BLOCK ──> CommandExecutor
  ```
* **Risk Levels**:
  * `SAFE`: Read-only, benign inspection commands (`pwd`, `ls`, `git status`, `df`, `du`, `ps`, `cat`, `grep`, etc.). Executed automatically without user interruption.
  * `CONFIRM`: Commands capable of modifying filesystem or system state (`rm`, `mv`, `cp`, `mkdir`, `chmod`, `git checkout`, etc.). Requires explicit interactive confirmation (`[y/N]`) or a cryptographic confirmation token.
  * `BLOCK`: High-confidence catastrophic or malicious patterns (`rm -rf /`, `mkfs`, `dd` to raw disk, fork bombs, root permission changes, privilege escalation). Refused immediately.
* **Shell Syntax Guard**: Blocks chained commands (`&&`, `;`, `|`), substitutions (`$(...)`), and redirections (`>`, `>>`) from silent execution.
* **Fail-Closed Policy**: Any unrecognized or ambiguous command defaults to `CONFIRM` rather than `SAFE`.

### 2.8 Command Execution Subsystem (`avi.execution`)
Strict, isolated command execution boundary:
* **`CommandRequest`**: Structured representation containing program name, structured arguments list, current working directory, timeout, and max output limit.
* **`ExecutionResult`**: Strongly typed execution summary returning `exit_code`, captured `stdout`, captured `stderr`, `duration_ms`, `timed_out`, and `output_truncated`.
* **Subprocess Sandboxing**: Exclusively invokes `subprocess.Popen(..., shell=False)`. Zero occurrences of `shell=True`.

### 2.9 Fast-Path Routing Subsystem (`avi.core.fastpath`)
Deterministic intent resolution layer for common terminal requests:
* **Zero-Latency In-Memory Matching**: Resolves unambiguous terminal intents in **~0.0053 ms (~5.3 µs)** without invoking any AI provider.
* **27 Default Intent Templates**: System inspections, disk space, git state, process monitors, and parameterized filesystem search.
* **Strict Parameter Sanitization**: Validates arguments against shell metacharacters and control characters.
* **Safety Pipeline Compliance**: Fast-path templates return structured `CommandRequest` instances that strictly route through `SafetyEngine`.

### 2.10 Universal Protocol Gateway (`avi.gateway`)
Enables external AI clients (Claude Desktop, Cursor, VS Code, Autonomous Agents) to interact safely with AVI over standard protocols:
* **`GatewayCore`**: Protocol-agnostic API layer:
  * `list_tools()`: Enumerates all 8 read-only tools with full JSON Schemas.
  * `call_tool(name, arguments)`: Executes a tool with argument validation.
  * `evaluate_command(command)`: Inspects command safety without executing.
  * `execute_command(command, confirmed, confirmation_token)`: Executes safe commands immediately or requests confirmation tokens for state-modifying operations.
  * `send_agent_request(prompt)`: Routes queries through AVI's AI provider layer.
  * `get_context(domains)`: Inspects system environment snapshot.
  * `get_health()` & `get_capabilities()`: Service status and protocol metadata.
* **`JsonRpcDispatcher`**:
  * Full JSON-RPC 2.0 compliance (single requests, batch requests, error codes -32700 through -32603, notifications).
  * Model Context Protocol (MCP) conformance (`initialize`, `ping`, `tools/list`, `tools/call`).
  * Optional bearer authentication token validation (`auth_token`).
* **Transports**:
  * `StdioTransport`: Line-delimited standard I/O for MCP clients (Claude Desktop, Cursor, VS Code).
  * `TcpTransport`: Local TCP server socket bound strictly to `127.0.0.1`.
* **Cryptographic Confirmation Tokens**:
  * When a modifying command is proposed, the gateway issues a `cf-<hex>` token with a 5-minute TTL.
  * The command can only execute when resubmitted with `confirmed=true` and the valid token.
  * The token is invalidated immediately upon use (single-use invariant).

### 2.11 Linux Desktop & Hotkey Subsystem (`avi.hotkey`)
Headless and Wayland-friendly desktop integration:
* **`detect_desktop_environment()`**: Inspects `WAYLAND_DISPLAY`, `DISPLAY`, and `XDG_SESSION_TYPE` to detect display server and desktop manager.
* **Wayland Security Compliance**: Wayland compositors (GNOME, Sway, Hyprland, KDE) intentionally block arbitrary background keyloggers. AVI guides native compositor custom shortcut setup (e.g. `gnome-terminal -- avi` bound to `Ctrl+Space`).
* **`generate_systemd_user_service()`**: Generates `systemd --user` unit definitions for running AVI Gateway continuously in the background.

---

## 3. Extending AVI with New AI Providers

Adding a new AI provider requires **zero modifications to AVI Core, Router, SafetyEngine, or CommandExecutor**:

```python
from avi.providers import (
    AIProvider,
    AgentRequest,
    AgentResponse,
    ProviderCapabilities,
    register_provider,
)


class CloudProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "claude-3-7-sonnet"):
        self.api_key = api_key
        self.model = model

    def get_model_name(self) -> str:
        return self.model

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            structured_output=True,
            context_size=200000,
            local=False,
            remote=True,
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def warmup(self) -> bool:
        return True

    def send(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(text="response text")

    def stream(self, request: AgentRequest):
        yield "response text"


# Register dynamically:
register_provider("cloud", lambda cfg, **kw: CloudProvider(api_key="...", model=cfg.model))
```

---

## 4. Performance & Benchmark Verification

| Operation | Tokens | Measured Latency | Explanation |
| :--- | :---: | :---: | :--- |
| **Gateway JSON-RPC Ping** | 0 | **~2.76 µs (0.0028 ms)** | Immediate in-memory JSON-RPC response dispatch |
| **Deterministic Fast-Path Matching** | 0 | **~5.32 µs (0.0053 ms)** | In-memory regex intent resolution and structured request building |
| **Gateway MCP tools/list** | 0 | **~15.20 µs (0.0152 ms)** | Complete MCP tool enumeration and schema serialization |
| **Gateway command/evaluate** | 0 | **~22.26 µs (0.0223 ms)** | Full SafetyEngine AST parsing and risk classification |
| **Deterministic Fast-Path (Context)** | 0 | **< 1 ms – 6 ms** | CWD, branch, shell, OS bypass AI provider entirely |
| **Read-Only Tool Execution** | 0 | **< 1 ms – 18 ms** | Direct execution of disk usage, file listing, process info |
| **Safety Assessment Overhead** | 0 | **< 0.05 ms** | In-memory tokenization and deterministic rule evaluation |
| **Safe Command Execution** | 0 | **~4 ms – 5 ms** | Subprocess execution of safe commands (e.g. `ls -la`) |
| **LLM Command Proposal (Local)** | ~85 | **~267 ms** | Local LLM generates structured command proposal |
| **Generic Question (No Context)** | ~83 | **~77 ms – 101 ms** | Fast prompt eval, zero context overhead |
| **Small Relevant Context** | ~105 | **~284 ms** | Injected terminal block (~22 extra tokens) |
| **Full Context Block** | ~150 | **~392 ms** | Injected terminal + git + previous command (~67 extra tokens) |
| **Session Startup** | N/A | **~80 ms** | Standard library cold process launch to prompt |
| **Executor Overhead** | N/A | **< 1 ms** | Direct standard library subprocess call |

---

## 5. Desktop UI Subsystem (Phase 9)

### 5.1 Overview

`src/avi/ui/` provides a keyboard-first GTK4 popup window that can be triggered from any Linux desktop environment via a global hotkey bound to `avi ui`.

```
src/avi/ui/
├── __init__.py      # Public: AviApp, check_display, is_ui_available
├── app.py           # AviApp — GTK4 Application wrapper (lazy GTK import)
└── window.py        # AviWindow — main popup widget, threading bridge, CSS
```

### 5.2 Design Principles

| Property | Decision |
| :--- | :--- |
| **Display servers** | Wayland-native + X11 via GTK4 (no xdotool, no wmctrl required) |
| **Python deps** | Zero extra: uses system `python3-gi` + `gir1.2-gtk-4.0` |
| **Threading** | AVI routing in `threading.Thread`; GTK updates via `GLib.idle_add` |
| **Headless safety** | `check_display()` + `_GTK_AVAILABLE` guard before any GTK import |
| **No-GTK fallback** | `AviApp.run()` returns exit code 1 + descriptive error if GTK absent |

### 5.3 Window Layout

```
┌────────────────────────────────────────────────┐
│ ⚡ AVI  [ Ask anything or type a command...  ] ⟳│
├────────────────────────────────────────────────┤
│                                                │
│  Streamed AVI response appears here in real    │
│  time (monospace, word-wrapped, scrollable)    │
│                                                │
├── confirm bar (hidden unless CONFIRM) ─────────┤
│ Execute: rm -rf tmp/build?  [✓ Yes] [✗ No]    │
├────────────────────────────────────────────────┤
│ Ready · Esc to close           local/qwen2.5   │
└────────────────────────────────────────────────┘
```

### 5.4 Keyboard Shortcuts

| Key | Action |
| :--- | :--- |
| `Enter` | Submit prompt |
| `Esc` | Close window |
| `y` | Confirm CONFIRM-level command execution |
| `n` | Reject CONFIRM-level command execution |

### 5.5 Invocation

```bash
# Direct invocation (popup appears on current display)
avi ui

# With specific provider
avi ui --provider ollama --model llama3.1

# Via global hotkey (configured in GNOME Settings → Keyboard → Custom Shortcuts):
# Command: /path/to/.venv/bin/avi ui
# Shortcut: Super+A  (or any preferred binding)

# Or via avi hotkey for setup instructions:
avi hotkey
```

### 5.6 Threading Model

```
Main Thread (GTK)             Background Thread (AVI)
─────────────────             ────────────────────────
  GTK event loop                Router.check_fast_path(prompt)
        │                             │ (no fast path)
        │                       Router.route(prompt, stream=True)
        │                             │ yields chunks
        │       GLib.idle_add ◀────── chunk received
  _append_response(chunk)            │
        │                       full text assembled
        │       GLib.idle_add ◀────── parse_command_proposal()
  _show_confirm_prompt()             │ (if CONFIRM)
        │                            └── thread exits
  User: [y] key
        │
  Background: _run_confirmed_command()
        │       GLib.idle_add ◀────── result
  _finish_stream()
```

---

## 6. Packaging & Distribution Pipeline (Phase 10)

### 6.1 Distribution Model

AVI is packaged for Python 3.10+ Linux environments using the standard Python packaging ecosystem (`hatchling` build backend, PEP 517/518/621 conformance):

```
┌─────────────────────────────────────────────────────────────┐
│                    DISTRIBUTION ARTIFACTS                   │
│      Source Dist (.tar.gz)   │   Built Wheel (.whl)         │
└──────────────┬───────────────────────────────┬──────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────────────┐ ┌─────────────────────────────┐
│       PyPI Repository        │ │      One-Shot Installer     │
│   pip install avi            │ │   install.sh (uv/pipx/pip)  │
│   pipx install avi           │ │   Isolated user-local bin   │
│   uv tool install avi        │ │   No root required          │
└──────────────────────────────┘ └─────────────────────────────┘
```

### 6.2 Installation Targets

| Method | Target Audience | Command |
| :--- | :--- | :--- |
| **Standalone Script** | General Linux Users | `curl -fsSL https://.../install.sh \| bash` |
| **uv tool** | Modern Python Devs | `uv tool install avi` |
| **pipx** | CLI Tool Users | `pipx install avi` |
| **Editable Dev** | Contributors | `git clone ... && make setup` |

### 6.3 Automation Pipelines

1. **Continuous Integration (`.github/workflows/ci.yml`)**:
   - Multi-version matrix testing across Python 3.10, 3.11, and 3.12
   - Formatting and lint verification with Ruff
   - AST-based security scan verifying zero occurrences of `shell=True`, `os.system()`, `eval()`, or `exec()`
   - Clean distribution builds producing reproducible wheels and source archives

2. **Automated Release (`.github/workflows/release.yml`)**:
   - Tag-triggered build (`v*.*.*`)
   - Auto-extracted release notes parsed directly from `CHANGELOG.md`
   - GitHub Release creation with attached wheel and sdist assets
   - Trusted publishing to PyPI via GitHub Actions OpenID Connect (OIDC) without long-lived tokens

---

## 7. Assistant Runtime & Orchestration Model

### 7.1 Architectural Shift: From Shell Generator to Desktop Assistant Runtime
Earlier versions of AVI operated primarily as an LLM-assisted shell command translator, translating natural language into single CLI invocations. While powerful for command discovery, this model produced poor user experience for natural desktop interactions (e.g. producing raw `df -h` tables for "how much space is left on my laptop", generating `sleep 2` for "set a timer for 2 seconds", or failing to locate GUI applications like Brave or Antigravity).

The Desktop Assistant Runtime re-architects AVI around a layered orchestration model:
1. **Deterministic Intent Classifier**: Fast sub-millisecond keyword and regex intent detection without LLM round-trips.
2. **Native Assistant Actions**: Zero-shell desktop operations (`TimerAction`, `OpenAppAction`, `OpenUrlAction`, `OpenFileAction`, `OpenDirAction`).
3. **Conversational Tool Synthesizers**: Intercepting read-only tool outputs (disk space, RAM, CPU, processes, Git status) and rendering concise, human-friendly responses.
4. **Application Discovery & Resolution**: FreeDesktop `.desktop` entry scanning, alias resolution, and background process spawning.
5. **Granular Risk & Capability Model**: Explicit action categorization (`READ_ONLY`, `LOW_RISK_ACTION`, `EXTERNAL_ACTION`, `FILESYSTEM_WRITE`, `DESTRUCTIVE`, `PRIVILEGED`).
6. **LLM Provider Reasoning Fallback**: Falling back to AI model providers when semantic inference or complex shell commands are genuinely needed.

```text
┌─────────────────────────────────────────────────────────────┐
│                      USER PROMPT INPUT                      │
│   CLI: avi "prompt"  │  REPL  │  avi ui  │  MCP Gateway     │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│                    ASSISTANT ORCHESTRATOR                   │
│                    (avi.orchestrator)                       │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ 1. Intent Recognition (avi.assistant.intents)       │   │
│   │    - Greeting / Help / Capabilities                 │   │
│   │    - System Metrics (Disk, Memory, CPU, Processes)  │   │
│   │    - Native Actions (Timer, App, URL, File, Dir)    │   │
│   └──────────────┬───────────────────────────────┬──────┘   │
│                  │ Matched Intent                │ No Match │
│                  ▼                               ▼          │
│   ┌───────────────────────────┐   ┌─────────────────────┐   │
│   │ 2. Native Action Dispatch │   │ 4. Router & LLM     │   │
│   │    TimerAction (daemon)   │   │    Provider Fallback│   │
│   │    OpenApp / OpenUrl      │   │    - FastPath Engine│   │
│   │    ApplicationResolver    │   │    - LLM Reasoning  │   │
│   └───────────────────────────┘   │    - Shell Proposals│   │
│                  │                └──────────┬──────────┘   │
│                  │ Needs Data                │              │
│                  ▼                           ▼              │
│   ┌───────────────────────────┐   ┌─────────────────────┐   │
│   │ 3. Tool Execution &       │   │ 5. Safety Engine    │   │
│   │    Conversational Synth   │   │    ActionCategory   │   │
│   │    - DiskSpaceSynthesizer │   │    Classification   │   │
│   │    - MemorySynthesizer    │   │    Fail-Closed      │   │
│   │    - ProcessSynthesizer   │   └─────────────────────┘   │
│   └───────────────────────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 The Assistant Orchestrator (`avi.orchestrator`)
The `AssistantOrchestrator` mediates all prompt processing. When a user prompt arrives:
- It runs the `IntentClassifier`.
- For `GREETING`, `CAPABILITIES`, `SMALL_TALK`: Returns friendly conversational responses immediately.
- For `DISK_SPACE`, `MEMORY`, `CPU_USAGE`, `PROCESSES`, `GIT_STATUS`: Executes the corresponding read-only tool (`DiskUsageTool`, `SystemInfoTool`, `ProcessesTool`, `GitStatusTool`) and routes raw output through `avi.assistant.synthesizer` to return concise natural language answers (e.g. `"You have about 154 GB free out of 240 GB on your main drive."`).
- For `TIMER`: Instantiates `TimerAction` running as a background Python daemon thread, completing immediately without blocking the CLI or shell.
- For `OPEN_APP`: Resolves application names via `ApplicationResolver` and spawns the app asynchronously via `OpenAppAction`.
- For `OPEN_URL`: Safely opens URLs in the default browser using `xdg-open`.
- For `OPEN_FILE` / `OPEN_DIR`: Safely opens paths in preferred editors or desktop file managers.
- For open-ended questions: Falls back to the standard `CoreRouter` and `LLMProvider` pipeline.

### 7.3 Application Resolver (`avi.apps`)
The `ApplicationResolver` provides Linux desktop application resolution:
- **FreeDesktop `.desktop` File Search**: Scans standard directories (`/usr/share/applications`, `~/.local/share/applications`, `/var/lib/snapd/desktop/applications`, Flatpak export paths) parsing `Name`, `Exec`, `Icon`, and `Terminal` fields. Strips field codes (`%u`, `%F`).
- **Binary PATH Resolution**: Checks `$PATH` for matching executable binaries.
- **Desktop Alias Mapping**: Normalizes common aliases:
  - `brave` -> `brave-origin-stable`, `brave-browser`
  - `chrome` / `google chrome` -> `google-chrome-stable`, `google-chrome`
  - `antigravity` -> `agy`
  - `code` / `vscode` -> `code`
  - `files` / `file manager` -> `nautilus`, `thunar`, `dolphin`, `pcmanfm`
- **Safe Process Spawning**: Spawns GUI processes with `subprocess.Popen(cmd_args, shell=False, start_new_session=True, stdout=DEVNULL, stderr=DEVNULL)` so the spawned application is detached from AVI's process tree and does not block.

### 7.4 Native Assistant Actions (`avi.actions`)
- **`TimerAction`**: Uses `threading.Timer` with daemon threads. Supports durations in seconds, minutes, and hours (e.g. "2 mins", "30s"). When finished, prints an alert message and triggers terminal audio bell (`\a`).
- **`OpenAppAction`**: Launches applications resolved by `ApplicationResolver`.
- **`OpenUrlAction`**: Launches default web browser via `xdg-open <url>` without shell interpretation.
- **`OpenFileAction` / `OpenDirAction`**: Opens target files in default handlers or directories in desktop file managers.

### 7.5 Conversational Synthesizers (`avi.assistant.synthesizer`)
Built-in deterministic synthesizers transform raw tool structures into natural language:
- `DiskSpaceSynthesizer`: Reads disk total, used, available, and percentage, highlighting main drive storage and any drives nearing capacity.
- `MemorySynthesizer`: Summarizes total, used, and available RAM with conversational percentage summaries.
- `CpuSynthesizer`: Summarizes CPU core counts and load averages.
- `ProcessSynthesizer`: Highlights top memory and CPU consuming processes.
- `GitStatusSynthesizer`: Summarizes branch state, clean/dirty working directory, and uncommitted file counts.

### 7.6 Granular Risk & Capability Model
The safety engine (`avi.safety`) replaces single-category filesystem warnings with a 6-tier capability taxonomy:
- `ActionCategory.READ_ONLY`: Zero state changes, safe for automatic execution.
- `ActionCategory.LOW_RISK_ACTION`: Non-destructive desktop actions (timers, app launch, URL launch).
- `ActionCategory.EXTERNAL_ACTION`: Outbound network access (curl, wget, ssh, git fetch/clone). Confirmation: `"This command accesses external network resources."`
- `ActionCategory.FILESYSTEM_WRITE`: Modifies files, directories, or builds (touch, mkdir, sed, tar). Confirmation: `"This command can modify your filesystem."`
- `ActionCategory.DESTRUCTIVE`: Permanent data deletion (rm, rmdir, shred, git reset --hard). Confirmation: `"WARNING: This command can permanently delete files or data."`
- `ActionCategory.PRIVILEGED`: Superuser operations requiring elevated permissions (sudo, chown, systemctl, apt). Confirmation: `"ELEVATED PRIVILEGES: This command requests superuser access."`

### 7.7 GTK4 Multi-Python Environment Diagnostics (`avi.ui.detector`)
Linux desktop installations frequently encounter Python interpreter ABI mismatches when running GUI toolkits from virtual environments. For example, system packages like `python3-gi` and `gir1.2-gtk-4.0` may reside in `/usr/lib/python3/dist-packages` under Python 3.14, while a development virtualenv uses Python 3.12.
- `diagnose_gtk_environment()` analyzes the active Python executable, virtualenv state, display servers (Wayland `$WAYLAND_DISPLAY`, X11 `$DISPLAY`), and PyGObject availability.
- It returns structured diagnosis strings explaining the exact discrepancy and recommending `avi ui --use-system-python` or recreating the venv with `--system-site-packages`.

---

## 8. Agent Runtime & Unified Capabilities (Phase 12)

### 8.1 Architectural Vision
Phase 12 evolves AVI into a full Linux computer assistant ("JARVIS-style" desktop agent) with bounded multi-step autonomous planning, typed desktop/filesystem capabilities, strict privacy boundaries, and one-button instant activation.

### 8.2 Unified Capability System (`avi.capabilities`)
Standardizes all operations under a typed capability model:
- `BaseCapability`: Declares name, description, `ActionCategory`, JSON schema, `DataClassification`, confirmation requirement, and `execute(params) -> CapabilityResult`.
- `DataClassification`:
  - `LOCAL_ONLY`: Artifacts confined strictly to local storage (e.g. screenshots). Never uploaded to cloud/remote providers without user consent.
  - `PROVIDER_ELIGIBLE`: Structured text and metrics safe for LLM context.
  - `USER_CONFIRMATION_REQUIRED`: Sensitive data requiring user approval.
- `ExecutionStatus`: `SUCCESS`, `PARTIAL_SUCCESS`, `FAILED`, `CANCELLED`, `CONFIRMATION_REQUIRED`.
- `CapabilityRegistry`: Central registry managing capability lookup, aliases, schemas, and execution gating.
- `ToolCapabilityAdapter` & `ActionCapabilityAdapter`: Backward-compatible bridging of Phase 3/4 tools and Phase 11 actions.

### 8.3 Desktop & Filesystem Capabilities
- **Desktop Capabilities (`avi.capabilities.desktop`)**:
  - `desktop.screenshot`: Wayland (`grim`, `gnome-screenshot`) and X11 (`scrot`, `maim`, `import`, `spectacle`) screen capture with local PNG dimensions parser and `LOCAL_ONLY` privacy classification.
  - `desktop.notification`: Desktop alerts via `notify-send` (`shell=False`).
  - `desktop.volume.get` / `desktop.volume.set`: Audio volume and mute control via `wpctl` or `amixer`.
  - `desktop.media.control`: Media playback (`play`, `pause`, `next`, `previous`, `stop`) via `playerctl`.
  - `desktop.app.launch`: Resolves and launches FreeDesktop applications asynchronously.
  - `desktop.url.open` / `desktop.file.open` / `desktop.directory.open`: Safe resource opening via `xdg-open`.
- **Filesystem Capabilities (`avi.capabilities.filesystem`)**:
  - `filesystem.search`: Safe bounded file search with glob pattern, extension filter, max depth, and recency sorting.
  - `filesystem.create_directory`, `filesystem.copy`, `filesystem.move`: Safe directory and file operations.
  - `filesystem.delete`: Destructive operation with safety checks (refuses root `/` and user home `~`) requiring explicit confirmation.

### 8.4 Multi-Step Agent Planner & Executor (`avi.agent`)
- **`AgentPlanner`**: Decomposes natural language requests into deterministic multi-step plans (`PlanStep` sequences):
  - Screenshot + Open: captures screen and immediately opens it in default viewer.
  - Screenshot + Notify: captures screen and displays a notification.
  - Screenshot + Move: captures screen and moves the file to specified target.
  - Search + Open: finds newest file matching criteria and opens it.
  - System controls + Notify: adjusts volume/playback and sends confirmation notification.
- **`AgentExecutor`**:
  - Piped data flow: passes output from previous step (e.g., captured screenshot path) into subsequent step inputs.
  - Bounded execution: enforces max steps (default 5) and execution timeout.
  - Partial failure handling: isolates non-critical step failures and continues execution when possible.
  - Confirmation gating: halts and requests user confirmation for destructive or privileged operations.

### 8.5 Privacy Invariant & Honest Vision Negotiation
- **Privacy Invariant**: All screenshots and visual artifacts are classified as `LOCAL_ONLY`. They are stored in `~/Pictures/Screenshots/` and are never silently uploaded to remote APIs.
- **Honest Vision Negotiation**: AVI inspects the active provider's `capabilities().vision`. If vision is not supported, AVI honestly states that screen analysis is unavailable on the current model rather than hallucinating or dropping the task silently.

### 8.6 One-Button Desktop Assistant (`avi activate`)
- FreeDesktop DBus single-instance application model (`io.github.banisher2005.avi`).
- Launching `avi activate` or pressing a global hotkey presents the existing window (`window.present()`) and immediately grabs keyboard focus (`prompt_entry.grab_focus()`) without creating duplicate processes.
- Native compositor configuration guides provided for GNOME, KDE, Sway, Hyprland, and X11.





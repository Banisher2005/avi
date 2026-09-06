# AVI System Architecture

## 1. Overview & Architectural Philosophy

AVI is built around three core architectural tenets:
1. **Speed First**: Sub-100ms latency for simple terminal requests and 0ms (~5.3 µs) for deterministic queries. Zero unnecessary imports, minimal context overhead, persistent model warmup, and direct HTTP streaming.
2. **Modular Decoupling**: Strict boundary separation between User Interface (One-Shot CLI & Interactive REPL), Routing, Context Ingestion, Model Providers, Tool Execution, and Safety Verification.
3. **Safety & Privacy by Design**: AI models should suggest; execution must always be verified. Context gathering is strictly bounded, private, and never dumps environment variables or arbitrary files.

### 1.1 Core Architectural Invariant

> **AVI Core is provider-independent. Providers are adapters around AVI, not dependencies inside AVI Core.**

The AI client provides intelligence. AVI provides execution, tools, safety, routing, context, and environment access.
The AI provider is replaceable without changing AVI Core.

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

---

## 2. Core Components

### 2.1 CLI Layer (`avi.cli`)
The CLI provides the primary entry point:
* **One-Shot Mode**: Parses flags, routes the prompt, streams chunks to stdout, and exits.
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
* **`BaseTool` & `ToolResult`**: Strict abstract base class requiring `safety_level = "read_only"`. Tools return strongly typed `ToolResult(success, output, error, data)` objects.
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
* **Safety Invariant**: All providers, without exception, must route command proposals through SafetyEngine:
  ```text
  AI Provider ──> Normalized Proposal ──> SafetyEngine ──> SAFE / CONFIRM / BLOCK ──> CommandExecutor
  ```
* **Risk Levels**:
  * `SAFE`: Read-only, benign inspection commands (`pwd`, `ls`, `git status`, `df`, `du`, `ps`, `cat`, `grep`, etc.). Executed automatically without user interruption.
  * `CONFIRM`: Commands capable of modifying filesystem or system state (`rm`, `mv`, `cp`, `mkdir`, `chmod`, `git checkout`, etc.). Requires explicit interactive confirmation (`[y/N]`). Default is NO.
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

---

## 3. Extending AVI with New AI Providers

Adding a new AI provider requires **zero modifications to AVI Core, Router, SafetyEngine, or CommandExecutor**:

```python
from avi.providers import AIProvider, AgentRequest, AgentResponse, ProviderCapabilities, register_provider

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
        # Call provider API securely...
        return AgentResponse(text="response text")

    def stream(self, request: AgentRequest):
        # Stream response chunks...
        yield "response text"

# Register dynamically:
register_provider("cloud", lambda cfg, **kw: CloudProvider(api_key="...", model=cfg.model))
```

---

## 4. Performance & Benchmark Verification

| Mode | Tokens | Measured Latency | Explanation |
| :--- | :---: | :---: | :--- |
| **Deterministic Fast-Path Matching** | 0 | **~0.0053 ms (5.3 µs)** | In-memory regex intent resolution and structured request building |
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

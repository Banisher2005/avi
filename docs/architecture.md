# AVI System Architecture

## 1. Overview & Architectural Philosophy

AVI is built around three core architectural tenets:
1. **Speed First**: Sub-100ms latency for simple terminal requests and 0ms for deterministic queries. Zero unnecessary imports, minimal context overhead, persistent model warmup, and direct HTTP streaming.
2. **Modular Decoupling**: Strict boundary separation between User Interface (One-Shot CLI & Interactive REPL), Routing, Context Ingestion, Model Providers, Tool Execution, and Safety Verification.
3. **Safety & Privacy by Design**: AI models should suggest; execution must always be verified. Context gathering must strictly bounded, private, and never dump environment variables or file systems.

```text
                       User Invocation
                              │
               ┌──────────────┴──────────────┐
               ▼                             ▼
          One-Shot CLI                Interactive REPL
          (Single turn)            (Multi-turn Session)
               │                             │
               └──────────────┬──────────────┘
                              ▼
                         Core Router
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
      Fast-Path Engine  Context Subsystem  Model Providers
     (0 ms Regex Match) (Terminal / Git /         │
                        Previous Command)  ┌──────┴──────┐
                              │            ▼             ▼
                              │      OllamaProvider  Antigravity
                              ▼            │         (Delegation)
                      Output Normalizer ◄──┘
                              │
                              ▼
                         Tool Layer
                  (Shell / Filesystem / Git)
                              │
                              ▼
                       Safety Pipeline
                 (Risk Filter / User Confirm)
```

---

## 2. Core Components

### 2.1 CLI Layer (`avi.cli`)
The CLI provides the primary entry point:
* **One-Shot Mode**: Parses flags, routes the prompt, streams chunks to stdout, and exits.
* **Interactive Mode**: Instantiates `InteractiveSession` when invoked without prompt arguments.
* **Top-Level Error Boundary**: Catches domain exceptions (`OllamaError`), system interruptions (`KeyboardInterrupt`), and unexpected runtime errors with clean diagnostics.

### 2.2 Interactive Session (`avi.core.session`)
Manages the stateful REPL:
* **Readline Integration**: Uses Python's standard library `readline` module for arrow-key navigation, editing, and persistent command history in `~/.local/share/avi/history`.
* **Zero-Dependency Architecture**: No heavyweight terminal frameworks are imported, keeping session startup latency under 80 ms.
* **Local Commands**: Evaluates `exit`, `quit`, `clear`, and `history` locally before routing to the LLM backend.
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
* **Lazy Prompt Assembly**: Combines base system instructions with formatted context blocks only when context is required.
* **Local Inference**: Dispatches requests and conversation context to `OllamaProvider`.
* **Agent Delegation (Phase 7)**: Detects complex coding tasks requiring Antigravity CLI handoff.

### 2.5 Provider Abstraction (`avi.providers`)
Abstracts the model backend:
* **`BaseProvider`**: Interface defining generation, availability, warmup, and context tracking.
* **`OllamaProvider`**: Direct HTTP implementation communicating with `/api/generate`:
  * Native conversation context token array propagation across multi-turn exchanges.
  * Zero-token model warmup via `done_reason: "load"`.
  * Nanosecond engine metric extraction.

### 2.6 Output Normalizer (`avi.core.normalizer`)
Strips extraneous markdown code fences (```` ```bash ````), backticks, and shell prompt prefixes ($) in real-time streaming and complete text responses.

### 2.7 Tool Subsystem (`avi.tools`)
A modular, controlled subsystem for executing read-only machine inspections:
* **`BaseTool` & `ToolResult`**: Strict abstract base class requiring `safety_level = "read_only"`. Tools return strongly typed `ToolResult(success, output, error, data)` objects.
* **`ToolRegistry`**: Centralized registry for tool registration, lookup, listing, and execution.
* **8 Default Read-Only Tools**:
  * `filesystem.list_directory`: Lists directory contents with sizes and modification times (max entries capped, path validated).
  * `filesystem.file_metadata`: Retrieves size, permissions, MIME/type, and timestamps of a file (strictly read-only metadata, does not read arbitrary file content).
  * `system.processes`: Inspects running processes sorted by RAM or CPU via `ps` with fixed argument array (`shell=False`).
  * `system.disk_usage`: Checks total, used, and free disk space via standard library `shutil.disk_usage`.
  * `system.system_info`: Inspects OS distribution, kernel version, architecture, CPU count, and hostname.
  * `git.status`: Runs `git status --porcelain=v1` within repository boundaries (`shell=False`).
  * `git.branch`: Returns active git branch via `git branch --show-current`.
  * `git.log`: Inspects recent commit log history via `git log -n ...` with format restrictions.
* **Security & Sandboxing Boundaries**:
  * Strictly NO `shell=True` subprocess calls.
  * Strictly NO file writing, modifying, deletion, or execution.
  * Standard library utilities preferred (`shutil.disk_usage`, `os.scandir`, `os.stat`, `platform`).
  * Outputs are hard-capped to prevent context explosion.
* **Router Integration**:
  * Regex-based deterministic tool queries (`what files are here?`, `what's using the most RAM?`, `how much disk space do I have?`) execute the corresponding tool directly in **< 20 ms** without calling the LLM.
  * Queries asking for command syntax (`what command lists files?`) bypass tool execution and prompt the LLM for shell commands.

---

## 3. Performance & Benchmark Verification

| Mode | Tokens | Measured Latency | Explanation |
| :--- | :---: | :---: | :--- |
| **Deterministic Fast-Path** | 0 | **< 1 ms – 6 ms** | CWD, branch, shell, OS bypass LLM entirely |
| **Read-Only Tool Execution** | 0 | **< 1 ms – 20 ms** | Direct execution of disk usage, file listing, process info |
| **Generic Question (No Context)** | ~83 | **~77 ms – 101 ms** | Fast prompt eval, zero context overhead |
| **Small Relevant Context** | ~105 | **~284 ms** | Injected terminal block (~22 extra tokens) |
| **Full Context Block** | ~150 | **~392 ms** | Injected terminal + git + previous command (~67 extra tokens) |
| **Multi-Turn Chat Turn 2** | ~190 | **~758 ms – 864 ms** | Accumulated conversational history |
| **Session Startup** | N/A | **~80 ms** | Standard library cold process launch to prompt |
| **Client Overhead** | N/A | **< 2 ms** | Internal Python processing |


# AVI System Architecture

## 1. Overview & Architectural Philosophy

AVI is built around three core architectural tenets:
1. **Speed First**: Sub-200ms latency for simple terminal requests. Zero unnecessary imports, minimal context overhead, persistent model warmup, and direct HTTP streaming.
2. **Modular Decoupling**: Strict boundary separation between User Interface (CLI/UI), Routing, Model Providers, Context Ingestion, Tool Execution, and Safety Verification.
3. **Safety by Design**: AI models should suggest; execution must always be verified and gated by an explicit risk assessment system.

```text
                                AVI CLI / UI
                                     │
                                     ▼
                              Core Router
                                     │
             ┌───────────────────────┼───────────────────────┐
             ▼                       ▼                       ▼
      Fast-Path Engine         Model Providers        Agent Delegation
     (Deterministic regex)           │               (Antigravity CLI)
                                     ├─ OllamaProvider
                                     └─ Future Providers
                                     │
                                     ▼
                             Output Normalizer
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
The CLI provides the primary user entry point. It handles command-line arguments (`argparse`), environment resolution, streaming output formatting, and terminal signal handling (`SIGINT`/`SIGTERM`).
* Positional query arguments: accepts unquoted or quoted queries (`avi what command shows current directory`).
* Flags: `--timing`, `--model`, `--host`, `--no-stream`, `--version`.
* Zero heavy dependencies to ensure immediate CLI execution without interpreter startup lag.

### 2.2 Configuration Subsystem (`avi.config`)
Centralized configuration loaded with strict precedence:
1. Command-line flags (highest)
2. Environment variables (`AVI_MODEL`, `AVI_OLLAMA_HOST`, etc.)
3. User configuration file (`~/.config/avi/config.json`)
4. System defaults (lowest)

Key settings include endpoint URLs, default model (`qwen2.5:1.5b`), inference temperature (default `0.1` for deterministic syntax), request timeout, and Ollama `keep_alive` parameter (`5m`).

### 2.3 Provider Abstraction (`avi.providers`)
The provider layer abstracts the underlying inference backend.
* **`BaseProvider`**: Abstract interface defining `generate()`, `generate_full()`, `is_available()`, and latency metrics capture.
* **`OllamaProvider`**: Direct implementation of the Ollama HTTP API:
  * Uses HTTP connection to `http://127.0.0.1:11434`.
  * Communicates directly with `/api/generate` and `/api/version`.
  * Emits streaming NDJSON chunks for real-time terminal output.
  * Preserves models in GPU memory via `keep_alive`.
  * Converts nanosecond engine metrics into millisecond benchmarks.
  * Formats clean error messages for offline services, missing models, or timeouts.

### 2.4 Core Router (`avi.core.router`)
The central coordinator that determines how a user prompt is fulfilled:
* **Fast-Path (Phase 6)**: Intercepts standard deterministic queries (e.g. `avi "show my current directory"`) and returns immediate shell syntax (`pwd`) in 0 ms without invoking neural inference.
* **Local Inference (Phase 1)**: Dispatches requests to the configured local model provider (`OllamaProvider`).
* **Agent Delegation (Phase 7)**: Detects requests that require multi-file code editing, test running, or complex reasoning, delegating them to Antigravity.

### 2.5 Output Normalizer (`avi.core.normalizer`)
Ensures output returned to the terminal is clean and directly executable:
* Strips extraneous markdown code block fences (```` ```bash ... ``` ````).
* Strips redundant inline backticks (`` `pwd` ``).
* Filters shell prompt prefixes (e.g. `$ `).
* Supports real-time stream normalization to prevent fence artifacts from flickering in the terminal.

---

## 3. Future Subsystems

### 3.1 Context Subsystem (`avi.context` — Phase 3)
Gathers system and repository context with strict minimization:
* Current working directory (`pwd`)
* Shell environment (`$SHELL`)
* Git branch and status (clean vs dirty, untracked files)
* Selective file inspection only when explicitly requested
* **Principle**: Never dump entire directory trees or repository source trees into the prompt. Keep prompt tokens below 100 for command tasks to preserve sub-200ms generation speeds.

### 3.2 Tool Execution Layer (`avi.tools` — Phase 4)
Provides sandboxed, verifiable execution primitives:
* `ShellTool`: Executes verified shell commands.
* `FileTool`: Reads specific file lines or previews diffs.
* `GitTool`: Inspects commits, branches, and diffs.

### 3.3 Safety Subsystem (`avi.safety` — Phase 5)
Every command destined for execution must traverse the safety pipeline:
1. **Parser**: Extracts binary, subcommands, and flags.
2. **Risk Classifier**:
   * **Safe (Green)**: Read-only operations (`ls`, `pwd`, `cat`, `git status`, `uptime`).
   * **Confirmation Required (Yellow)**: Potentially modifying operations (`git checkout`, `systemctl restart`, `chmod`).
   * **Blocked / High-Risk (Red)**: Destructive commands (`rm -rf /`, `mkfs`, raw block device writes, piping unverified scripts to sudo).
3. **Execution Gate**: Displays the command, risk level, and requires explicit user confirmation before executing.

### 3.4 Antigravity Integration (`avi.providers.antigravity` — Phase 7)
AVI is designed to complement Google Antigravity rather than duplicate it:
* AVI handles fast, instant terminal assistance, shell syntax, and lightweight queries locally (< 200 ms).
* When a task requires deep reasoning, multi-file code refactoring, running test suites, or autonomous multi-step operations, AVI can hand off the prompt to the Antigravity CLI (`agy -p` or `agy -i`).
* Communication will leverage the actual installed CLI flags (`--add-dir`, `--model`, `--effort`, etc.).

---

## 4. Latency Optimization Strategy

In terminal workflows, delay breaks focus. AVI achieves near-instant responsiveness through:
1. **No Cold-Start Overhead**: Native Python standard library implementation with zero external runtime package imports.
2. **Direct Socket / HTTP API**: Communicates directly over local TCP sockets to Ollama instead of spawning external CLI subprocesses (`ollama run ...`).
3. **Ollama Keep-Alive**: Passes `"keep_alive": "5m"` on every request to prevent the local model weights from unloading from GPU VRAM / system RAM.
4. **Prompt Token Minimization**: Concise, instruction-tuned system prompts that maximize Ollama's prompt eval cache hit rate.
5. **Streaming First**: Chunks are rendered to stdout the instant they are generated by the model.

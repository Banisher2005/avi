# AVI System Architecture

## 1. Overview & Architectural Philosophy

AVI is built around three core architectural tenets:
1. **Speed First**: Sub-200ms latency for simple terminal requests. Zero unnecessary imports, minimal context overhead, persistent model warmup, and direct HTTP streaming.
2. **Modular Decoupling**: Strict boundary separation between User Interface (One-Shot CLI & Interactive REPL), Routing, Model Providers, Context Ingestion, Tool Execution, and Safety Verification.
3. **Safety by Design**: AI models should suggest; execution must always be verified and gated by an explicit risk assessment system.

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
      Fast-Path Engine  Model Providers  Agent Delegation
     (Deterministic)          │         (Antigravity CLI)
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
The CLI provides the primary entry point:
* **One-Shot Mode**: When arguments are provided (e.g. `avi "find files larger than 500MB"`), it parses flags, queries the router, streams the response, and exits cleanly.
* **Interactive Mode**: When invoked without positional arguments (`avi`), it instantiates `InteractiveSession` and runs the REPL loop.
* **Top-Level Error Boundary**: Catches domain exceptions (`OllamaError`), system interruptions (`KeyboardInterrupt`), and unexpected runtime errors, formatting clean diagnostics without unhandled tracebacks.

### 2.2 Interactive Session (`avi.core.session`)
A dedicated component managing the stateful REPL:
* **Readline Integration**: Uses Python's standard library `readline` module for arrow-key navigation, editing, and persistent command history in `~/.local/share/avi/history`.
* **Zero-Dependency Architecture**: No heavyweight terminal frameworks (`prompt_toolkit`, `rich`, `textual`) are imported, keeping startup latency under 80 ms.
* **Local Interactive Commands**: Intercepts `exit`, `quit`, `clear`, and `history` locally before routing to the LLM backend.
* **Granular Signal Handling**:
  * `Ctrl+C` while the model is streaming cancels only the active generation and returns to the `AVI > ` prompt without crashing the session.
  * `Ctrl+C` or `Ctrl+D` at the prompt exits cleanly.
* **Error Resilience**: Ollama connectivity failures report a diagnostic message to `sys.stderr` and allow the user to continue the interactive session without restart.

### 2.3 Configuration Subsystem (`avi.config`)
Centralized configuration loaded with strict precedence:
1. Command-line flags (highest)
2. Environment variables (`AVI_MODEL`, `AVI_OLLAMA_HOST`, etc.)
3. User configuration file (`~/.config/avi/config.json`)
4. System defaults (lowest)

Key settings include endpoint URLs, default model (`qwen2.5:1.5b`), inference temperature (default `0.1`), request timeout, and Ollama `keep_alive` parameter (`5m`).

### 2.4 Provider Abstraction (`avi.providers`)
The provider layer abstracts the underlying inference backend:
* **`BaseProvider`**: Abstract interface defining:
  * `generate(prompt, system_prompt, context, stream)`
  * `generate_full(prompt, system_prompt, context)`
  * `is_available()`
  * `warmup()`
  * Properties `last_metrics` and `last_context`
* **`OllamaProvider`**: Direct implementation of the Ollama HTTP API:
  * Uses HTTP connection to `http://127.0.0.1:11434`.
  * Communicates directly with `/api/generate` and `/api/version`.
  * Passes `"keep_alive": "5m"` to keep model weights warm in GPU VRAM.
  * **Native Multi-Turn Context**: Captures Ollama's opaque `context` token array on completion and passes it into subsequent turns. This enables KV cache reuse in Ollama with zero re-encoding latency.
  * **Zero-Token Model Warmup**: Leverages Ollama's `done_reason: "load"` warmup mechanism on session startup (~12 ms warm check).

### 2.5 Core Router (`avi.core.router`)
The central coordinator that determines how a user prompt is fulfilled:
* **Fast-Path (Phase 6)**: Intercepts standard deterministic queries in 0 ms.
* **Local Inference (Phases 1 & 2)**: Dispatches requests and conversation context to `OllamaProvider`.
* **Agent Delegation (Phase 7)**: Detects requests that require multi-file code editing or test running, delegating them to Antigravity CLI.

### 2.6 Output Normalizer (`avi.core.normalizer`)
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
* **Principle**: Keep prompt tokens minimal to preserve sub-200ms generation speeds.

### 3.2 Tool Execution Layer (`avi.tools` — Phase 4)
Provides sandboxed, verifiable execution primitives for shell, files, and git.

### 3.3 Safety Subsystem (`avi.safety` — Phase 5)
Risk classification pipeline (Safe, Confirmation Required, Blocked) before executing commands.

### 3.4 Antigravity Integration (`avi.providers.antigravity` — Phase 7)
Handoff of complex refactor and development tasks to Antigravity CLI (`agy -p` or `agy -i`).

---

## 4. Latency & Performance Strategy

1. **Standard Library Only**: Zero third-party runtime dependencies eliminates interpreter startup lag.
2. **Direct HTTP Sockets**: No external subprocess spawning (`ollama run ...`).
3. **Ollama Keep-Alive & Native Context**: Retaining the model in GPU memory and passing native context token arrays avoids expensive prompt re-encoding.
4. **Benchmark Summary**:
   * Interactive session startup overhead: **~80 ms**
   * Single-shot warm command response: **~86 ms**
   * First interactive turn: **~400 ms**
   * Multi-turn conversational follow-up: **~860 ms**

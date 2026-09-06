# Changelog

All notable changes to AVI are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [0.3.0] — 2026-09-06

### Added

#### Desktop Assistant Orchestrator Architecture
- **`AssistantOrchestrator` (`src/avi/orchestrator/`)**: Central runtime orchestrating user intent recognition, native assistant actions, read-only system tools, safety classification, and LLM reasoning fallback.
- **Deterministic Intent Classifier (`src/avi/assistant/intents.py`)**: Sub-millisecond intent extraction covering greetings, capabilities, disk space, memory/RAM, CPU metrics, running processes, timers, web URLs, file/directory opening, and application launching.
- **Conversational Synthesizers (`src/avi/assistant/synthesizer.py`)**: Transforms raw structured tool output into concise, friendly natural-language responses (e.g., "You have about 154 GB free out of 240 GB on your main drive.") instead of raw command outputs.
- **Application Resolver (`src/avi/apps/`)**: Linux FreeDesktop `.desktop` file parser across system and user directories (`/usr/share/applications`, `~/.local/share/applications`, `/var/lib/snapd/desktop/applications`, Flatpak); PATH resolution; desktop alias mapping (`brave`, `chrome`, `antigravity`/`agy`, `code`); clean background daemon spawning (`shell=False`, `start_new_session=True`).
- **Native Assistant Actions (`src/avi/actions/`)**:
  - `TimerAction`: Asynchronous daemon timers with terminal alerts and terminal bell notifications.
  - `OpenAppAction`: Launches resolved GUI applications asynchronously.
  - `OpenUrlAction`: Launches default browser via `xdg-open` safely.
  - `OpenFileAction` / `OpenDirAction`: Opens files in editors and directories in file managers via `xdg-open`.
- **Granular Risk & Capability Model (`src/avi/safety/models.py`)**:
  - Replaced generic filesystem warning with `ActionCategory`: `READ_ONLY`, `LOW_RISK_ACTION`, `EXTERNAL_ACTION`, `FILESYSTEM_WRITE`, `DESTRUCTIVE`, `PRIVILEGED`.
  - Contextual confirmation prompts explaining specific risks (network access, privileged execution, file modification, destructive operations).
- **GTK4 Environment Diagnosis (`src/avi/ui/detector.py`)**:
  - Pinpoints Python virtualenv vs system ABI mismatch for PyGObject / GTK4 (e.g., Python 3.12 venv with system Python 3.14 GTK4).
  - Diagnostic output with actionable instructions and `--use-system-python` support.
- **Provider Abstraction Enhancements (`src/avi/providers/base.py`)**:
  - Clean `LLMProvider` interface defining model reasoning vs AVI runtime execution boundaries.
- **Shell Wildcard Guidance**:
  - CLI parser diagnostic detecting premature shell glob expansion (e.g., `avi find *.py`) advising quotes (`avi "find *.py"`).
- **Comprehensive Unit Test Suite**:
  - Added 88 new unit tests covering application resolution, native actions, conversational synthesizers, intent parsing, orchestrator routing, GTK diagnostics, risk categories, and provider abstraction (total 644 tests, 642 passing, 2 skipped).

### Changed
- CLI and interactive session REPL routed through `AssistantOrchestrator`.
- Version bumped to `0.3.0`.

---

## [0.2.0] — 2026-09-06

### Added

#### Phase 9 — Lightweight Desktop UI
- **`avi ui`** — keyboard-first GTK4 popup window (`src/avi/ui/`)
- `AviWindow`: borderless, floating 680×360 prompt with Catppuccin Mocha dark theme
- Real-time token streaming into a scrollable monospace response view
- Inline CONFIRM bar with `[✓ Yes] / [✗ No]` buttons and `y` / `n` keyboard shortcuts
- Spinner indicator + provider/model status bar
- Thread-safe: AVI routing in a daemon thread, GTK updates via `GLib.idle_add`
- Wayland-native + X11 support via GTK4 — zero extra Python package dependencies
- Headless-safe: returns exit code 1 with descriptive install instructions when GTK4 absent
- `is_ui_available()` composite probe (GTK4 present + display server running)
- `avi ui --provider <name> --model <name>` flags for provider selection
- 17 unit tests (15 passing, 2 conditionally skipped when GTK4 not in test venv)

#### Phase 10 — Packaging & Distribution
- **`install.sh`**: one-shot `curl | bash` installer with `uv tool` → `pipx` → `pip --user` fallback chain; `--dev` mode for contributors
- **`Makefile`**: `setup`, `test`, `lint`, `format`, `build`, `release`, `benchmark`, `run-ui`, `run-gateway` targets
- **`.github/workflows/ci.yml`**: multi-Python CI (3.10, 3.11, 3.12), lint job (ruff), build artifact upload, security invariant test job
- **`.github/workflows/release.yml`**: tag-triggered release: test → build → GitHub Release (auto-generated notes from CHANGELOG) → PyPI publish via OIDC trusted publishing
- **`pyproject.toml`** upgraded to `v0.2.0`:
  - `Development Status :: 4 - Beta`
  - `[project.urls]`: Homepage, Repository, Bug Tracker, Changelog
  - `[tool.hatch.build.targets.sdist]` include list (tests, docs, install.sh, Makefile)
  - `[tool.ruff]` and `[tool.mypy]` configuration blocks
  - `ui` optional-dependency group documented
- `CHANGELOG.md` (this file)

### Changed
- `__version__` bumped from `0.1.0` → `0.2.0`
- README: added Desktop UI section, updated test count to 488, Phase 9 checked off in roadmap
- `docs/architecture.md`: added Section 5 — Desktop UI Subsystem (layout, threading model, keyboard shortcuts)

---

## [0.1.0] — 2026-09-06

Initial release covering Phases 1–8.

### Added

#### Phase 1 — Core CLI & Local LLM Integration
- Single-shot `avi "<prompt>"` and interactive REPL mode (`avi --interactive`)
- Ollama HTTP streaming API integration
- Zero external Python dependencies for core functionality

#### Phase 2 — Context-Aware Intelligence
- Terminal context: CWD, recent commands, shell, OS, Python version
- Git context: branch, status, recent log, staged/unstaged files
- Bounded context collection (never dumps env vars or arbitrary file contents)

#### Phase 3 — Tool Registry & Read-Only Inspection
- 8 read-only built-in tools: `filesystem.list_directory`, `filesystem.file_metadata`, `system.processes`, `system.disk_usage`, `system.system_info`, `git.status`, `git.branch`, `git.log`
- JSON Schema `input_schema` on every tool for MCP/gateway compatibility
- `ToolRegistry` with prefix-namespaced tool names

#### Phase 4 — Safety Engine
- `SafetyEngine`: deterministic 3-level risk classification — `SAFE` / `CONFIRM` / `BLOCK`
- `shlex`-based tokenizer (no shell=True, no regex ambiguity)
- Fail-closed: unknown/parse errors default to `CONFIRM`
- No AI involvement in safety decisions — fully deterministic

#### Phase 5 — Safe Command Execution
- `CommandExecutor`: `subprocess.Popen(shell=False)` only
- Configurable `timeout` and `max_output_bytes` per request
- `ExecutionResult` with truncation markers and exit code reporting
- Zero `shell=True`, `os.system()`, `eval()`, `exec()` anywhere in `src/avi/`

#### Phase 6 — Deterministic Fast-Path Routing
- `FastPathRegistry`: 27 intent templates covering filesystem, git, system, process, and code-search queries
- Parameterized templates with strict regex path/pattern sanitization
- ~5.32 µs per resolution (no LLM, no subprocess)
- Router integration: fast-path checked before any provider call

#### Phase 7 — Provider-Agnostic AI Integration
- `AIProvider` ABC (`base.py`): `send()`, `stream()`, `is_available()`, `warmup()`
- Normalized `AgentRequest` / `AgentResponse` / `ToolCall` models
- `ProviderRegistry`: dynamic registration, `get_provider(name, config)` factory
- `OllamaProvider` (local, streaming HTTP)
- `AntigravityProvider` (subprocess adapter for AGY CLI — provider-independent)
- `--provider` CLI flag and `AVI_PROVIDER` environment variable

#### Phase 8 — Universal Protocol Gateway
- `GatewayCore`: protocol-agnostic bridge (`list_tools`, `call_tool`, `evaluate_command`, `execute_command`, `send_agent_request`, `get_context`, `get_health`, `get_capabilities`)
- `JsonRpcDispatcher`: full JSON-RPC 2.0 + MCP 2024-11-05 conformance (11 methods, batch requests, notifications)
- `StdioTransport`: line-delimited stdin/stdout for Claude Desktop / Cursor
- `TcpTransport`: strictly bound to `127.0.0.1` (local-only)
- Cryptographic one-time confirmation tokens (`cf-<uuid_hex>`, 5-minute TTL, consumed immediately on use)
- `avi gateway --transport stdio|tcp` and `avi serve` aliases
- `HotkeyDetector`: Wayland / X11 / Headless display-server detection
- `avi hotkey`: native compositor hotkey setup guide + `--systemd` service unit generator

---

## Links

- [GitHub Repository](https://github.com/Banisher2005/avi)
- [PyPI Package](https://pypi.org/project/avi/) _(pending first release)_
- [Architecture Documentation](docs/architecture.md)

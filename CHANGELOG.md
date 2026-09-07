# Changelog

All notable changes to AVI are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [0.4.0] — 2026-09-06

### Added

#### Phase 12 — AVI Agent Runtime + One-Button Desktop Assistant
- **Unified Capability Subsystem (`src/avi/capabilities/`)**:
  - `BaseCapability`, `CapabilityResult`, `CapabilityRegistry`, `DataClassification`, and `ExecutionStatus`.
  - Adapters: `ToolCapabilityAdapter` and `ActionCapabilityAdapter`.
- **Desktop Capabilities (`src/avi/capabilities/desktop/`)**:
  - `desktop.screenshot`: Wayland (`grim`, `gnome-screenshot`) and X11 (`scrot`, `maim`, `import`, `spectacle`) screen capture with local PNG IHDR dimension parsing and `LOCAL_ONLY` privacy boundary.
  - `desktop.notification`: Desktop notification alerts via `notify-send` (`shell=False`).
  - `desktop.volume.get` & `desktop.volume.set`: Volume level inspection, setting, and muting via `wpctl` or `amixer`.
  - `desktop.media.control`: Media playback controls (play, pause, next, previous, stop) via `playerctl`.
  - `desktop.app.launch`, `desktop.url.open`, `desktop.file.open`, `desktop.directory.open`: Safe desktop application and resource launching.
- **Filesystem Capabilities (`src/avi/capabilities/filesystem/`)**:
  - `filesystem.search`: Safe bounded search with glob pattern, extension filter, bounded depth, and recency sorting.
  - `filesystem.create_directory`, `filesystem.copy`, `filesystem.move`: Directory and file operations.
  - `filesystem.delete`: Destructive operation with safety checks (refusing `/` and `~`) requiring explicit confirmation.
- **Agent Planner & Execution Engine (`src/avi/agent/`)**:
  - `AssistantInput`, `Plan`, `PlanStep`, and `PlanExecutionResult` models.
  - `AgentPlanner`: Deterministic decomposition of single and multi-step plans (e.g., screenshot + open, search + open, volume/media control + notify).
  - `AgentExecutor`: Piped dataflow, bounded step execution (max 5 steps), step timeout, confirmation enforcement, and partial failure recovery.
- **Privacy Boundaries & Honest Vision Negotiation**:
  - `LOCAL_ONLY` data classification ensuring visual artifacts are never sent to remote providers without explicit consent.
  - Upfront `ProviderCapabilities.vision` capability check honestly stating when the active model cannot read screen contents.
- **One-Button Desktop Assistant (`avi activate`)**:
  - `avi activate` CLI command.
  - Single-instance GTK4 window handling (`present()` and `grab_focus()` on subsequent activations) without duplicate processes.
  - Desktop entry generator and compositor hotkey setup guides for GNOME, KDE, Sway, Hyprland, and X11.
- **Comprehensive Test Suite**:
  - Added unit, integration, and packaging tests covering capabilities, screenshots, agent planner, executor, and the Part 19 acceptance scenarios (total 766 passing tests).

#### Phase 12.1 — Native CLI Routing Integration
- Eliminated legacy shell-command fallback for desktop actions (`avi "increase volume"`).
- Direct capability execution for volume, screenshot, and application actions without sudo prompts or amixer generation.

#### Phase 12.2 — Desktop Routing Gaps, Typo Safety & Activation Fixes
- Added volume mute/unmute phrasing routing (`"mute volume"`, `"turn sound back on"`).
- Added typo tolerance with Levenshtein fuzzy distance matching for desktop intents.
- Enforced strict domain boundary protection preventing random shell queries from executing dangerous operations.
- Single-instance GTK4 window activation via PID lock and process signaling.

#### Phase 12.3 — GTK4 UI Stabilization & Assistant UX
- Fixed PyGObject / GTK4 crash from deprecated GTK3 `override_font` and `TextView` APIs.
- Multi-bubble assistant conversation cards (User bubble, Assistant bubble, Error card, Confirmation card).
- Non-blocking background worker thread with `GLib.idle_add` UI dispatch.
- Added direct interactive action buttons for screenshots and files.

#### Phase 13.0 — Native Search Capabilities & Web Navigation
- Added `web.youtube.search` capability.
- Differentiated navigation (`"open YouTube"`) from search (`"search YouTube for Linux tutorials"`).
- Safe parameter-encoded URL construction (`build_youtube_search_url`).

#### Phase 13.1 — Web Retrieval, Search Result Understanding & Provider Selection
- Built retrieval subsystem (`src/avi/retrieval/`): `BaseSearchProvider`, `SearchResult`, `SearchResults`, `SearchOptions`.
- YouTube InnerTube endpoint integration (`YouTubeSearchRetrievalProvider`) for direct structured metadata fetching without browser automation.
- Strict HTTPS YouTube URL validation (`validate_youtube_url`).
- Dynamic provider capability selection via `ProviderCapabilities.supports()` and `select_provider()`.
- Added `web.youtube.search_results` capability.
- Added `YOUTUBE_RECOMMEND` intent (e.g. *"find me a good YouTube video about building local AI agents"*) with AI ranking.
- Added `OPEN_SEARCH_RESULT` intent for multi-turn deictic follow-up (*"open it"*, *"open the second one"*).
- Added interactive result cards in GTK4 UI with one-click `[Open]` buttons.
- Fixed environment-sensitive test execution (`sys.argv` mocking) across Python 3.10, 3.11, and 3.12 (906 passing tests).

#### Phase 13.2 — JARVIS Interaction Hardening + Qwen3 4B Migration
- **Model Migration**: Migrated default Ollama model to `qwen3:4b` across `Config`, `DEFAULT_MODEL`, and `OllamaProvider`, while preserving full user configurability via `--model` and config files.
- **JARVIS System Prompt Tuning**: Tailored system prompt to prioritize native desktop capabilities, enforce fail-safe boundaries against arbitrary shell generation, and keep reasoning concise.
- **Cross-Process Session State Persistence (`src/avi/session/`)**:
  - Implemented persistent session state (`~/.local/state/avi/session_state.json` via standard XDG state directory) with 30-minute bounded TTL.
  - Enables cross-process deictic follow-ups (e.g. `avi "find videos about python"` followed in a separate CLI run by `avi "open it"` or `avi "play it"` or `avi "use the first one"`).
  - Explicitly rejects treating `"it"` as a local filesystem path if no recent result exists.
- **Bounded Typo Normalization & Fuzzy Matching**:
  - Fuzzy intent matching for native desktop actions (`activaite`, `activte`, `screeenshot`, `screnshot`, `volum up`, `incrase volume`, `louder`).
  - Strict domain boundary ensures typos never fall back to unintended destructive or privileged shell commands (e.g. `systemctl enable lightdm`).
- **Bare Application & Directory Dispatch**:
  - Direct launching for bare application names (`avi chrome`, `avi brave`, `avi firefox`, `avi spotify`, `avi antigravity`).
  - Clear user-facing reporting for uninstalled applications (*"I couldn't find <app> installed."*).
  - Direct opening for bare desktop folders (`avi downloads`, `avi downlods`).
- **Test Suite Expansion**: Added comprehensive integration test suite `test_jarvis_routing.py` (total 930 passing tests).

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

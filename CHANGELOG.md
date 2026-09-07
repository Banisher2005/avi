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

#### Phase 13.3 — JARVIS Routing, Fast Path & YouTube Reliability
- **Fast Deterministic YouTube Candidate Ranking**:
  - Eliminated the multi-minute CPU inference hang on local models (e.g. Qwen3 4B taking 243+ seconds).
  - Deterministic first-class candidate ranking selects top candidates in < 1 second using structured metadata relevance.
  - Optional LLM-based re-ranking strictly bounded with timeout (3.0s) and immediate fallback.
  - Structured debug logging (`youtube.intent.detected`, `youtube.provider.selected`, `youtube.request.started`, `youtube.request.completed`, `youtube.results.parsed`, `youtube.recommendation.ranked`, `youtube.session.saved`).
  - Bounded network and search timeouts with friendly messages (*"YouTube search timed out. Try again."*, *"I couldn't reach YouTube right now."*, *"I couldn't find any matching YouTube videos."*).
- **YouTube Intent Grammar & Search Resolution**:
  - `avi open youtube mkbhd` and `avi open mkbhd on youtube` correctly resolve to YouTube searches rather than non-existent applications (`Youtube Mkbhd`, `Mkbhd On Youtube`).
  - Differentiated pure `avi open youtube` (opens `https://www.youtube.com`) from queries with search arguments.
  - Handled infix, suffix, and prefix patterns (`youtube mkbhd`, `open youtube and search mkbdh`).
- **Natural Volume Grammar & Automatic Unmuting**:
  - Added relative adjustment by percentage: `increase volume by 10`, `decrease volume by 10`.
  - Added absolute adjustment to boundaries: `increase volume to max`, `set volume to max` (100%), `set volume to min` (0%).
  - Automatically unmutes audio sink (`wpctl set-mute @DEFAULT_AUDIO_SINK@ 0` or `amixer sset Master unmute`) when raising volume or setting volume > 0 so adjustments are immediately audible.
- **CLI Activation & Abort Handling**:
  - `avi activate` / `avi activaite` prints visible confirmation (`Activating AVI...\n`) on display servers or friendly guidance if headless.
  - Graceful `KeyboardInterrupt` (Ctrl-C) handling with `Operation cancelled. Aborted.` and exit code 130 without tracebacks.
- **Performance Invariants & Instrumentation**:
  - Verified native desktop commands (`chrome`, `screenshot`, `volume`, `mute`, `downloads`) never invoke LLM providers.
  - Extended `ResponseMetrics` with latency breakdown fields (`intent_duration_ms`, `capability_duration_ms`, `retrieval_duration_ms`, `time_to_first_token_ms`).
- **Test Suite Expansion**: Added comprehensive test suite `TestPhase133JarvisFastPathAndYouTubeReliability` (954 passing tests, 2 skipped).

#### Phase 13.4 — JARVIS GTK Integration & Native Conversation UX
- **CLI Syntax & Quote Stripping (`clean_natural_language_input`)**:
  - Automatically normalizes input by stripping CLI command prefixes (`avi `, `avi: `, `avi, `) and wrapping quotes whether submitted via CLI or GTK UI.
  - Ensures GTK chat bubble displays clean natural language (`find me the best YouTube video about building local AI agents`) instead of shell syntax.
  - Converged GTK and CLI on the same public assistant API (`orchestrator.handle`) before intent detection.
- **Unified YouTube Retrieval & Deterministic Ranking in GTK**:
  - GTK queries like `avi "find me the best YouTube video..."` or `open youtube mkbhd` route directly to native YouTube search/recommendation rather than triggering fallback clarification (*"What would you like me to search for on YouTube?"*).
- **Structured Interactive Result Cards**:
  - Highlights `🎯 Best match` featured card with Title, Channel, Duration, and a prominent `[Play]` action button.
  - Numbered secondary result cards with `[Open]` action buttons.
  - Direct execution using structured `result.url` without regex prose parsing.
- **Deictic `open it` Follow-Up Resolution**:
  - Resolves `open it` from session state to open the top recommended YouTube result without falling through to open `/home/<user>/it` or launch an application named `it`.
  - Also resolves `open it` when preceded by screenshot captures or file operations, and safely requests clarification when no prior referent exists.
- **Activation Recursion Elimination**:
  - Separated UI startup from conversation; UI never injects `activate` into prompt entry or chat history.
  - Fixed fallback to system Python forwarding `["ui"]` instead of re-executing `activate`, eliminating duplicate `Activating AVI...` prints.
- **Dynamic GTK Loading State & Double-Submission Guard**:
  - Status bar displays immediate fast action status (`Opening Google Chrome…`, `Capturing screenshot…`, `Increasing volume…`, `Muting audio…`, `Opening Downloads…`, `Searching YouTube…`) for deterministic operations without showing `Thinking…` or the LLM provider label.
  - Hides `ollama / qwen3:4b` provider label unless Qwen3 is actually being queried for complex reasoning.
  - Added synchronous double-submission guard disabling the Send button and blocking duplicate queries while an operation is in-flight.
- **Test Suite Expansion**: Added unit and integration tests for UI input normalization, double submission prevention, loading status behavior, and best match card rendering (total 962 passing tests, 2 skipped).

#### Phase 14 — AVI Command Overlay (JARVIS-Style Desktop UI Redesign)
- **Frameless Floating Command Overlay (`src/avi/ui/window.py`)**:
  - Transformed the assistant into a compact, floating desktop command overlay (~620px width, minimal ~90px initial height) inspired by Spotlight, Raycast, and PowerToys Run.
  - Frameless window (`set_decorated(False)`) with Catppuccin Mocha styling, rounded corners (`border-radius: 14px`), and custom header.
  - Custom header featuring `✦ AVI` brand badge and `×` dismiss button.
  - Full-width prompt entry (`Ask AVI anything...`).
  - Action/Controls row with activity spinner, status indicator (`Ready · Esc to close`), `🎙` microphone placeholder, and `⏎ Enter` execution button.
- **Result-First / Adaptive Layout**:
  - Dynamic result area remains hidden when idle, expanding downward when results or cards are displayed.
  - Simple native actions (`chrome`, `increase volume to max`, `screenshot`, `downloads`): display clean action feedback and automatically dismiss after a brief delay (~1.8s).
  - Rich / complex results (YouTube search/recommendations, multi-step confirmations, long answers): dynamically expand interactive `🎯 Best match` card with `[Play]`, secondary result cards with `[Open]`, or confirmation cards with `[y] Proceed` / `[n] Cancel` without auto-dismissing.
- **Instant Auto-Dismiss & Cancellation**:
  - Transient native operations auto-dismiss after ~1.8 seconds.
  - User typing, clicking, or pressing keys cancels pending auto-dismiss instantly.
  - Escape cancels pending confirmations or dismisses the overlay immediately.
- **Daemon / Background Mode & Single-Instance D-Bus Control (`src/avi/ui/app.py`, `src/avi/cli.py`)**:
  - Added `avi ui --background` (`-b` / `--daemon`) to start the overlay resident in RAM with window hidden (`app.hold()`), enabling instant <50ms summoning.
  - Single-instance command handling via `Gio.ApplicationFlags.HANDLES_COMMAND_LINE`:
    - `avi activate` or `avi ui --toggle` summons/focuses or toggles existing overlay without spawning duplicate processes.
    - `avi ui --show` and `avi ui --hide` provide explicit visibility controls.
    - `avi ui --quit` cleanly shuts down resident daemon over D-Bus.
  - Window `close-request` intercepted to hide rather than destroy window, preserving resident application state.
- **Cross-Process Session State Continuity**:
  - Session state in `~/.local/state/avi/session_state.json` is preserved across overlay dismissals, allowing follow-ups like `open it` to work seamlessly.
- **Desktop Entry & Hotkey Updates (`src/avi/hotkey/service.py`)**:
  - Updated desktop launcher to `AVI Command Overlay` with `avi activate`.
  - Updated global hotkey documentation for `<Super>Space` / `<Ctrl>Space`.
- **Test Suite Expansion**:
  - Added unit tests covering frameless overlay layout, header, controls, show/hide/toggle, auto-dismiss scheduling and cancellation, daemon flags, and session state preservation across dismissals (969 passing tests, 2 skipped).

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

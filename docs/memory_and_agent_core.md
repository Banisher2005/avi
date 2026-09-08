# AVI Phase 15: Memory & Agent Core Architecture

## Overview

Phase 15 transitions AVI from a fast command launcher into a general-purpose, Jarvis-style computer agent. It introduces persistent SQLite-backed storage, explicit memory management, natural language entity resolution, a modular skill system, compound multi-step planning with live `TaskState` tracking, and an Observe → Act → Verify execution loop.

Crucially, Phase 15 preserves all sub-100ms deterministic fast paths introduced in Phase 14.3. Simple commands, greetings, media controls, and direct actions never make unnecessary LLM calls.

---

## 1. Architecture Components

```
User Prompt
    │
    ▼
Assistant Intent Recognition
    │
    ├── Fast Path (Greetings, Volume, Media, Screenshot, System Info) ──> Native Execution (<100ms)
    ├── Memory Commands ("remember...", "recall...", "forget...") ──────> MemoryManager & SQLite Storage
    ├── Web Destinations ("open chatgpt on chrome", typo tolerance) ─────> Browser / Destination Resolver
    └── Compound / Multi-Step Requests ("open youtube, search...", etc.) ──> Agent Planner
                                                                                   │
                                                                                   ▼
                                                                           Agent Executor
                                                                        (Observe-Act-Verify)
                                                                                   │
                                                                           Modular Skills
```

---

## 2. Persistent Local Storage (`avi.storage`)

The local persistence layer is powered by SQLite with WAL mode (`PRAGMA journal_mode = WAL`) and automatic schema migrations:
- **Location**: `~/.local/share/avi/avi.db` (overrideable with `AVI_DB_PATH`). Directory permissions are locked down to `0700`.
- **Thread Safety**: Backed by recursive threading locks (`threading.RLock`) and per-thread connections.
- **Sensitive Credential Screening**: `screen_for_sensitive_data()` validates every write operation to prevent storing passwords, bearer tokens, or API secrets.

### Tables
* `preferences`: Key-value storage for user defaults (e.g., `preferred_browser`, custom folder mappings).
* `memories`: Explicit and learned facts with category, confidence rating, metadata, and timestamps.
* `aliases`: User-defined command shortcuts.
* `task_history`: Auditable record of multi-step task execution goals, plans, and final results.
* `action_history`: Granular invocation logs for individual actions with duration metrics and success/failure status.

---

## 3. Explicit Memory Model (`avi.memory`)

AVI memory is strictly user-consensual and transparent:
- **Remember**: `"remember that Chrome is my preferred browser"`
- **Recall**: `"what do you remember about browser?"`
- **Forget**: `"forget that Chrome is my preferred browser"` or `"forget everything"`
- **Inspection**: `"what do you remember?"` / `"list memories"`
- **Security Boundaries**: Attempts to persist sensitive tokens (e.g. `"remember that my password is..."`) are rejected with an explicit security policy violation.

---

## 4. Web Destination & Typo Resolution (`avi.apps.destinations`)

AVI distinguishes between local desktop Linux applications and popular web platforms:
- Queries like `"open chatgpt on chrome"`, `"open youtube"`, or `"open gmail"` resolve directly to their web URLs rather than looking for local desktop files.
- **Typo Tolerance**: Tolerates spelling mistakes such as `"open caht gpt on chrome"` and resolves them to ChatGPT.
- **Browser Targeting**: Explicit browsers (e.g., `"on chrome"`, `"on firefox"`) or stored user preferences are honored when launching URLs.

---

## 5. Modular Skill Architecture (`avi.skills`)

Skills provide a standardized abstraction over desktop capabilities:
- `BaseSkill`: Core abstract class defining actions, parameters, verification, and safety.
- `SkillRegistry`: Registration, discovery, and execution dispatcher.
- **Built-in Skills**:
  - `BrowserSkill`: URL opening and web search across system browsers.
  - `ApplicationSkill`: Desktop application launching and resolution.
  - `FilesystemSkill`: Safe file and folder creation, copying, moving, deletion, and search.
  - `SystemControlsSkill`: System audio volume, mute, and settings.
  - `ScreenshotSkill`: Screen capture with timestamped output.
  - `TerminalSkill`: Safe subprocess execution with strict `shell=False` tokenization.
  - `MediaSkill`: Playback controls via MPRIS/playerctl.

---

## 6. Agent Core: Planning & Execution (`avi.agent`)

### Compound Planning
`AgentPlanner` creates ordered execution plans for compound requests such as:
> *"open youtube, search for mkbhd, and play the latest video"*

Outputs are automatically piped between steps:
- `pipe_from_step`: Identifies dependency step ID.
- `pipe_arg_name`: Names the argument to populate (`path`, `url`).

### Observe → Act → Verify Loop
`AgentExecutor` executes plans step-by-step:
1. **Observe**: Evaluate intermediate state and intermediate outputs from previous steps.
2. **Act**: Execute capability under safety engine checks.
3. **Verify**: Check post-conditions (e.g., verifying screenshot file creation on disk).
4. **Bounded Recovery**: If an execution or verification check fails, bounded retry (max 1 retry) is attempted before marking the step as failed.
5. **TaskState Tracking**: Maintains live state with `current_step_index`, `completed_steps`, `failed_steps`, `pending_steps`, and timing metrics.

---

## 7. Performance & Latency Telemetry

Every request returns detailed duration telemetry in `ResponseMetrics`:
- `routing_duration_ms`: Intent routing latency (<10ms).
- `memory_duration_ms`: Memory query/update duration.
- `planning_duration_ms`: Plan generation duration.
- `action_duration_ms`: Total execution time for underlying actions.
- `verification_duration_ms`: Post-condition verification time.
- `total_duration_ms`: End-to-end response time.

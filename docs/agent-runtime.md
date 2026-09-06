# AVI Agent Runtime & Desktop Capabilities

This document details the **Phase 12 Agent Runtime** architecture, Unified Capability System, Bounded Multi-Step Planner, Privacy Boundaries, and One-Button Desktop Assistant Activation in AVI.

---

## 1. Overview & Architectural Vision

AVI is designed as a **genuine Linux desktop assistant** rather than a command generator. In this runtime model:

```text
User Request (Hotkey / CLI / UI / Voice / API)
                │
                ▼
      AssistantOrchestrator
                │
        ┌───────┴────────┐
        ▼                ▼
   AgentPlanner     Core Router / LLM
        │                │
        ▼                │
   AgentExecutor         │
        │                ▼
        ├── Dataflow ── SafetyEngine (ActionCategory)
        │
        ▼
   CapabilityRegistry
   ├── Desktop Capabilities (screenshot, notification, volume, media, apps)
   ├── Filesystem Capabilities (search, create, copy, move, delete)
   └── Adapted Read-Only Tools / Actions
```

### Key Principles

1. **Deterministic by Default**: Multi-step desktop requests (such as *"take a screenshot and open it"*, *"find the newest PDF in ~/Downloads and open it"*, or *"mute system audio and notify me"*) are planned and executed deterministically without LLM roundtrips or hallucination.
2. **Fail-Closed Security**: Zero `shell=True`, zero `os.system()`, zero `eval()`, and zero `exec()`. State-changing or destructive operations strictly enforce `CONFIRMATION_REQUIRED`.
3. **Strict Privacy Boundaries**: Sensitive desktop artifacts (such as screenshots) are classified as `LOCAL_ONLY`. They are stored in user-controlled local directories and are never uploaded or transmitted to external AI providers without explicit confirmation.
4. **Honest Vision Negotiation**: Provider capabilities are negotiated upfront. If the active AI provider lacks vision processing, AVI honestly informs the user and retains the local image without hallucinating or silently dropping data.
5. **Bounded Execution**: Execution plans are strictly bounded (default maximum 5 steps) with step timeouts, failure isolation, and partial success reporting.
6. **One-Button Desktop Activation**: Single-instance desktop window presented instantly upon hotkey press or `avi activate` invocation without spawning redundant processes.

---

## 2. Unified Capability Architecture

The capability subsystem (`src/avi/capabilities/`) standardizes all system interactions under a typed, metadata-rich interface:

### 2.1 Capability Models (`models.py`)

- **`DataClassification`**:
  - `LOCAL_ONLY`: Artifacts strictly confined to local storage (e.g., screenshots, authentication tokens, system memory dumps).
  - `PROVIDER_ELIGIBLE`: Structured text or metrics safe to include in AI provider prompts.
  - `USER_CONFIRMATION_REQUIRED`: Data requiring explicit user approval before access or transmission.
- **`ExecutionStatus`**:
  - `SUCCESS`: Step completed with expected output.
  - `PARTIAL_SUCCESS`: Non-fatal warning or partial result.
  - `FAILED`: Execution failed cleanly with structured error details.
  - `CANCELLED`: Execution aborted due to upstream failure or cancellation.
  - `CONFIRMATION_REQUIRED`: Action is gated pending explicit user confirmation.
- **`CapabilityResult`**:
  - Structured response container carrying `status`, `data` (dict), `summary` (human-readable string), `error` (optional message), and `classification` (`DataClassification`).
- **`BaseCapability`**:
  - Abstract base class declaring `name`, `description`, `category` (`ActionCategory`), `schema`, `classification`, `requires_confirmation`, and `execute(params) -> CapabilityResult`.

### 2.2 Adapters

- **`ToolCapabilityAdapter`**: Wraps any legacy read-only inspection tool (`BaseTool`) as a `BaseCapability` under category `READ_ONLY`.
- **`ActionCapabilityAdapter`**: Wraps legacy desktop actions (`BaseAction`) into the capability schema.

### 2.3 Capability Registry (`registry.py`)

The `CapabilityRegistry` maintains registered capabilities, schema exports, aliases, and safety gates:
- Dynamic lookup by canonical name or alias.
- Validation of input arguments against JSON schemas.
- Enforcement of safety gates: destructive (`ActionCategory.DESTRUCTIVE`) and privileged (`ActionCategory.PRIVILEGED`) capabilities require explicit `confirmed=True` parameter before execution.

---

## 3. Desktop Capabilities (`src/avi/capabilities/desktop/`)

### 3.1 Screenshot Capability (`desktop.screenshot`)

- **Compositor Detection**:
  - Wayland: Automatically selects `grim` (Sway/Hyprland/generic) or `gnome-screenshot` (GNOME).
  - X11: Prioritizes `scrot`, `maim`, `import` (ImageMagick), or `spectacle` (KDE).
- **Safe Execution**:
  - Invokes backend CLI tools using `subprocess.run(args, shell=False, check=True)`.
  - Default output destination: `~/Pictures/Screenshots/screenshot_YYYYMMDD_HHMMSS.png` (created safely if missing).
  - Safe extraction of PNG dimensions (width and height) via standard PNG IHDR chunk inspection without external dependencies.
- **Privacy Boundary**:
  - Result marked as `DataClassification.LOCAL_ONLY`.
  - Stored strictly on local storage; never transmitted over network without consent.

### 3.2 Notification Capability (`desktop.notification`)

- Dispatches desktop notifications via `notify-send` (`shell=False`).
- Configurable `title`, `message`, `urgency` (`low`, `normal`, `critical`), and `icon`.
- Safely reports fallback if `notify-send` is not installed.

### 3.3 System Audio & Media Controls (`system_controls.py`)

- **`desktop.volume.get`**: Queries current system volume level and mute status via `wpctl get-volume @DEFAULT_AUDIO_SINK@` or `amixer get Master`.
- **`desktop.volume.set`**: Adjusts system volume (e.g. `50%`, `+5%`, `-10%`) or toggles mute (`wpctl set-volume` / `amixer set Master`).
- **`desktop.media.control`**: Controls media playback (`play`, `pause`, `play-pause`, `next`, `previous`, `stop`) via `playerctl`.

### 3.4 Application & Resource Launching (`app_launcher.py`)

- **`desktop.app.launch`**: Resolves desktop apps via FreeDesktop `.desktop` specs and launches them detached (`start_new_session=True`).
- **`desktop.url.open`**: Safely opens URLs via `xdg-open` in the user's default browser.
- **`desktop.file.open`**: Opens specific files in user-preferred editors/viewers via `xdg-open`.
- **`desktop.directory.open`**: Opens target directory in the desktop file manager via `xdg-open`.

---

## 4. Filesystem Capabilities (`src/avi/capabilities/filesystem/`)

### 4.1 Safe Filesystem Search (`filesystem.search`)

- Recursively searches directories with safety boundaries:
  - Configurable `max_depth` (default 5, max 20) and `max_results` (default 50).
  - Filename glob pattern matching (`*.pdf`, `budget*`).
  - File extension filtering (`pdf`, `py`, `md`).
  - Recency sorting (`newest_first` / `oldest_first`).
  - Dual return schema: `results` and `matches` containing file metadata (path, size, modification timestamp).

### 4.2 Safe Directory & File Operations (`operations.py`)

- **`filesystem.create_directory`**: Creates directories safely with `os.makedirs(exist_ok=True)`. Category: `FILESYSTEM_WRITE`.
- **`filesystem.copy`**: Copies files using standard library `shutil.copy2()`. Category: `FILESYSTEM_WRITE`.
- **`filesystem.move`**: Moves or renames files using standard library `shutil.move()`. Category: `FILESYSTEM_WRITE`.
- **`filesystem.delete`**:
  - Category: `DESTRUCTIVE`.
  - Default `requires_confirmation = True`.
  - **Invariants**: Refuses to delete filesystem root `/` or the user's home directory `~` under all circumstances.

---

## 5. Agent Planner & Execution Engine (`src/avi/agent/`)

### 5.1 Models (`models.py`)

- **`AssistantInput`**: Represents an incoming request with source (`TEXT`, `VOICE`, `HOTKEY`, `API`, `UI`), raw prompt, and optional metadata (e.g. active window, display type).
- **`PlanStep`**: Represents an individual action within a plan, specifying step ID, target capability, input parameters, and output key mapping.
- **`Plan`**: Structured execution sequence with unique plan ID, list of `PlanStep`s, estimated risk, and bounded timeout.
- **`PlanExecutionResult`**: Aggregate result tracking overall status (`SUCCESS`, `PARTIAL_SUCCESS`, `FAILED`), completed step results, execution duration, and failure diagnostics.

### 5.2 Deterministic Decomposition (`planner.py`)

The `AgentPlanner` analyzes natural language requests and deterministically synthesizes single or multi-step execution plans:
- **Screenshot Workflows**:
  - *"take a screenshot and open it"* -> Step 1: `desktop.screenshot`, Step 2: `desktop.file.open` (piping `path`).
  - *"take a screenshot and alert me"* -> Step 1: `desktop.screenshot`, Step 2: `desktop.notification`.
  - *"take a screenshot and move it to ~/Documents"* -> Step 1: `desktop.screenshot`, Step 2: `filesystem.move`.
- **Filesystem Workflows**:
  - *"find the newest PDF in ~/Downloads and open it"* -> Step 1: `filesystem.search` (sorted by recency), Step 2: `desktop.file.open`.
- **Desktop Control Workflows**:
  - *"mute audio and notify me"* -> Step 1: `desktop.volume.set` (`mute`), Step 2: `desktop.notification`.
- **Fallback**:
  - Requests not matching multi-step or desktop capability patterns gracefully fall back to the standard `CoreRouter` and `SafetyEngine` pipeline.

### 5.3 Data Pipelining & Execution (`executor.py`)

The `AgentExecutor` executes planned steps in sequence:
- **Pipelined Data Flow**: If a step parameter is omitted, the executor pipes relevant outputs from previous steps (e.g., `path` or `source` generated by a screenshot or search step).
- **Bounded Step Execution**: Execution halts if the plan exceeds `max_steps` (default 5) or if total time exceeds `timeout_seconds`.
- **Partial Failure Recovery**: If a non-essential step fails, the executor records the partial failure and attempts to continue or reports a clean explanation without throwing unexpected exceptions.
- **Confirmation Gating**: If a step requires confirmation and `confirmed=False`, the executor immediately pauses and returns `CONFIRMATION_REQUIRED`.

---

## 6. Privacy Boundaries & Honest Vision Detection

### 6.1 Privacy Invariant for Screenshots

Desktop screenshots can capture sensitive user information (private messages, financial data, personal documents). Therefore:
1. Screenshots produced by `desktop.screenshot` are classified as `DataClassification.LOCAL_ONLY`.
2. AVI never automatically transmits screenshot files to cloud or remote AI providers.
3. Screenshots are stored locally in standard user directories (`~/Pictures/Screenshots/`).

### 6.2 Honest Vision Capability Negotiation

When a user prompt asks about the screen (e.g., *"what's on my screen?"*):
1. AVI checks `active_provider.capabilities().vision`.
2. **If vision is supported**: The provider may analyze the image if explicit permission is granted.
3. **If vision is NOT supported**: AVI honestly states:
   > *"I captured a screenshot to `<path>`, but the active AI provider (<provider_name>) does not support image analysis. I cannot read the contents of your screen directly."*
4. AVI **never fakes vision** or produces hallucinated descriptions of unanalyzable images.

---

## 7. One-Button Desktop Assistant & Single-Instance Flow

### 7.1 Single-Instance GTK4 Architecture (`src/avi/ui/app.py`, `window.py`)

To deliver an instant "JARVIS-like" experience with zero duplicate processes:
- `AviApp` registers with application ID `io.github.banisher2005.avi` via standard FreeDesktop DBus single-instance semantics.
- When `AviApp` is launched while already running (e.g. from hotkey press or `avi activate`):
  1. The primary instance receives the `activate` signal.
  2. The existing `AviWindow` is presented (`window.present()`).
  3. The prompt entry receives immediate keyboard focus (`prompt_entry.grab_focus()`).
  4. The secondary launch process exits cleanly without creating duplicate windows.

### 7.2 CLI Activation Command

AVI exposes the dedicated `avi activate` command:
```bash
# Activates running desktop popup or starts it if not running
avi activate

# Or run in terminal assistant mode
avi activate --terminal
```

### 7.3 Hotkey Integration Guide

Compositors and window managers can bind a single key (e.g., `Super+Space` or `F12`) to `avi activate`:

#### GNOME (Wayland / X11)
1. Settings -> Keyboard -> Keyboard Shortcuts -> Custom Shortcuts.
2. Name: `AVI Assistant`
3. Command: `avi activate`
4. Shortcut: `Super+Space`

#### KDE Plasma
1. System Settings -> Shortcuts -> Custom Shortcuts.
2. Edit -> New -> Global Shortcut -> Command/URL.
3. Trigger: `Meta+Space` | Action: `avi activate`

#### Sway (`~/.config/sway/config`)
```sway
bindsym $mod+space exec avi activate
```

#### Hyprland (`~/.config/hypr/hyprland.conf`)
```ini
bind = $mainMod, SPACE, exec, avi activate
```

#### X11 (Generic / `sxhkd`)
```sxhkdrc
super + space
    avi activate
```

---

## 8. Summary of Added Capabilities

| Capability ID | Category | Classification | Confirmation Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| `desktop.screenshot` | `LOW_RISK_ACTION` | `LOCAL_ONLY` | No | Captures desktop screenshot via Wayland/X11 tools |
| `desktop.notification` | `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Sends desktop notification via `notify-send` |
| `desktop.volume.get` | `READ_ONLY` | `PROVIDER_ELIGIBLE` | No | Reads current volume and mute status |
| `desktop.volume.set` | `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Sets or adjusts volume / mute |
| `desktop.media.control` | `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Controls playback via `playerctl` |
| `desktop.app.launch` | `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Launches desktop applications asynchronously |
| `desktop.url.open` | `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Opens URL in default browser via `xdg-open` |
| `desktop.file.open` | `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Opens file in default application |
| `desktop.directory.open`| `LOW_RISK_ACTION` | `PROVIDER_ELIGIBLE` | No | Opens directory in file manager |
| `filesystem.search` | `READ_ONLY` | `PROVIDER_ELIGIBLE` | No | Safe bounded file search |
| `filesystem.create_directory` | `FILESYSTEM_WRITE`| `PROVIDER_ELIGIBLE` | No | Creates directories safely |
| `filesystem.copy` | `FILESYSTEM_WRITE`| `PROVIDER_ELIGIBLE` | No | Copies files safely |
| `filesystem.move` | `FILESYSTEM_WRITE`| `PROVIDER_ELIGIBLE` | No | Moves or renames files |
| `filesystem.delete` | `DESTRUCTIVE` | `PROVIDER_ELIGIBLE` | **Yes** | Deletes files with safety protections |

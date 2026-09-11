# AVI Phase 8: Non-Blocking Interactive Agent Runtime & Streaming Progress

## Overview

Phase 8 elevates AVI from a synchronous task runner into a concurrent, interactive, Jarvis-style companion. Prior to Phase 8, when AVI accepted complex planning or multi-step agent requests, both the GTK4 desktop overlay and the CLI/REPL blocked waiting for execution to finish. The UI locked user inputs, surfaced vague spinner states, and in cases of empty plans could conclude with deceptive messages like `"Task completed with no executable plan."`

Phase 8 establishes a non-blocking execution architecture featuring:
1. **Interactive Agent Runtime (`AgentRuntime`)**: Decouples immediate fast-path responses from background multi-step task execution.
2. **Runtime Task Registry (`RuntimeTaskRegistry`)**: In-memory registry tracking running, paused, completed, and failed tasks with unique IDs and honest progress fractions.
3. **Resource Lock Manager (`ResourceLockManager`)**: Prevents conflicting concurrent mutations across filesystem directories, browser tabs, and desktop applications.
4. **Natural Reference Resolution**: Resolves pronouns (*"that"*, *"it"*, *"the task"*) and keywords (*"download"*, *"scan"*) to active tasks, with interactive clarification when ambiguous.
5. **Operational Event Formatter (`OperationalEventFormatter`)**: Strips chain-of-thought and LLM reasoning leaks, emitting clean operational progress glyphs (`◌`, `✓`, `→`, `↻`, `⏸`, `▶`, `■`, `✗`).
6. **Cancellation & Pause/Resume**: Step-boundary execution controls via cooperative cancellation and pause tokens.
7. **Empty-Plan Bug Fix**: Unplannable or empty plans cleanly transition to `TaskStatus.FAILED` and emit `TASK_FAILED`, never falsely reporting success.

---

## Architecture Diagram

```
                       User Prompt / Input
                               │
                               ▼
                    ┌──────────────────────┐
                    │     AgentRuntime     │
                    │      dispatch()      │
                    └──────────┬───────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
  [Runtime Commands]      [Fast Paths]      [Background Worker]
  - tasks / status        - Greetings       - Multi-step plans
  - pause / resume        - System info     - Autonomous actions
  - cancel / stop         - Quick intents   - Browser / OS work
  - what are you doing?   (Synchronous)               │
        │                                             ▼
        │                                  ┌────────────────────┐
        │                                  │ ResourceLockManager│
        │                                  └──────────┬─────────┘
        │                                             │
        ▼                                             ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    RuntimeTaskRegistry                      │
  │     - Active tasks         - Pause / Resume tokens          │
  │     - Natural reference    - Cooperative cancellation       │
  └─────────────────────────────┬───────────────────────────────┘
                                │
                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                  OperationalEventFormatter                  │
  │     - Formats ProgressEvent stream                          │
  │     - Strips CoT reasoning tokens                           │
  │     - Dispatches clean status glyphs to UI / CLI            │
  └─────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. `AgentRuntime` (`src/avi/agent/runtime.py`)
- Coordinates turn routing between deterministic fast-paths, assistant direct actions, runtime control commands, and background agent tasks.
- `dispatch(query, ...)` returns a `RuntimeResponse` immediately:
  - If query is a runtime control command (`tasks`, `cancel`, `pause`, `resume`), it operates on the task registry and returns instantly.
  - If query matches a fast path or immediate assistant intent, it executes synchronously in < 2ms.
  - If query requires multi-step agent planning and execution, it checks resource locks, registers a `RuntimeTask`, spawns a daemon background thread, and returns `is_background=True` with the task metadata attached.

### 2. `RuntimeTaskRegistry` & `ResourceLockManager` (`src/avi/agent/task_registry.py`)
- **`RuntimeTask`**: Tracks task ID, goal, start/end timestamps, step progress (`3/7 steps`), current action description, pause/cancellation threading events, and terminal status (`COMPLETED`, `FAILED`, `CANCELLED`).
- **`ResourceLockManager`**: Implements reader-writer locks for coarse-grained system resources:
  - `filesystem:<path>` (e.g., Downloads, Documents, Desktop)
  - `browser:active_tab`
  - `app:<name>` (e.g., terminal, text editor)
- **Natural Reference Resolution**:
  - Direct ID: `task-123` or `#1`
  - Pronouns: `"cancel that"`, `"pause it"`, `"stop"` resolve to the single active task.
  - Keywords: `"stop download"` matches the task whose goal contains `"download"`.
  - Ambiguity: If multiple active tasks match, returns `clarification_needed=True` and prompts the user to select.

### 3. `OperationalEventFormatter` (`src/avi/agent/formatting.py`)
- Translates raw agent execution events into clean, user-friendly status lines:
  - `◌ Understanding request...`
  - `◌ Planning...`
  - `✓ Plan ready (3 steps)`
  - `→ Selected: desktop.screenshot`
  - `→ [1] Capturing full screen`
  - `✓ Step completed`
  - `↻ Diagnosed: element not interactable`
  - `↻ Attempting recovery: scroll into view`
  - `✓ State change verified`
  - `⏸ Task paused.`
  - `▶ Task resumed.`
  - `■ Task cancelled.`
  - `✓ Task completed successfully.`
  - `✗ Task failed.`
- Sanitizes `<think>` blocks and internal thoughts to prevent chain-of-thought leakage into user interfaces.

### 4. Step-Boundary Cancellation & Pause/Resume
- `AgentExecutor.execute_plan` checks `cancellation_token` and `pause_token` at every step boundary.
- When cancelled, the executor immediately aborts remaining steps, marks the execution status as `CANCELLED`, and emits `TASK_CANCELLED`.
- When paused, the executor enters a wait loop until the pause token is cleared, allowing seamless resumption without losing execution context.

### 5. UI and CLI Integration
- **CLI/REPL (`src/avi/core/session.py`)**: Uses `AgentRuntime.dispatch()` to execute turns non-blockingly, listening to the event stream to display formatted progress lines while keeping the prompt interactive. Supports the `/tasks` command.
- **GTK4 Desktop Overlay (`src/avi/ui/window.py`)**: Removed input-blocking locks (`self._is_busy`). Uses `GLib.idle_add` for thread-safe UI updates, displays background task badges in the status bar, and keeps the text entry active for subsequent queries or control commands.

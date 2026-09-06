# AVI Desktop Assistant Acceptance & Runtime Specification

## 1. Product Direction: Desktop Assistant vs. Shell Command Generator

Historically, command-line AI tools acted as prompt-to-bash transpilers, translating questions like *"how much space is left on my laptop"* into raw shell pipelines (`df -h / | awk ...`) or executing unverified scripts.

**AVI is a genuine Linux desktop assistant runtime.** The shell is strictly an internal implementation detail, not the primary user interface.

```
                  User Query
                      │
                      ▼
           ┌─────────────────────┐
           │ Assistant Intent    │  Deterministic, < 0.05ms
           │ Recognition         │
           └──────────┬──────────┘
                      │
        ┌─────────────┼─────────────────────────────┐
        ▼             ▼                             ▼
┌──────────────┐ ┌──────────────┐            ┌──────────────┐
│ Conversational│ │ Native Tools │            │ Native Action│
│ Greetings,   │ │ (Disk, RAM,  │            │ (URL, Folder,│
│ Courtesies,  │ │  CPU, OS)    │            │  Apps, Timer)│
│ Clarification│ └──────┬───────┘            └──────┬───────┘
└──────┬───────┘        │                           │
       │                ▼                           │
       │         ┌──────────────┐                   │
       │         │ Natural Lang │                   │
       │         │ Synthesizer  │                   │
       │         └──────┬───────┘                   │
       │                │                           │
       └────────────────┼───────────────────────────┘
                        │
                        ▼
         ┌─────────────────────────────┐
         │ Bounded Conversation State  │
         │ (Turns, History, Context)   │
         └──────────────┬──────────────┘
                        │
                        ▼
            Natural-Language Response
```

### Core Tenets of the Desktop Assistant Runtime

1. **Intent Understanding First**: Native desktop capabilities (checking RAM/disk, launching apps, opening folders/URLs, setting timers, asking for clarification) are recognized deterministically in microseconds without LLM roundtrip latency.
2. **Native Tool Selection & Natural Synthesis**: When inspecting the system, AVI queries native, read-only tools and synthesizes concise, friendly natural-language answers rather than dumping raw terminal tables.
3. **Clarification Over Blind Guessing**: Ambiguous or deictic requests (*"open it"*, *"delete that"*, *"open the project"*, *"clean it up"*) prompt the user for clarification instead of guessing or executing arbitrary commands.
4. **State-Changing Operations Pass Through SafetyEngine**: Every action that alters system state passes through deterministic safety assessment. Dangerous operations are halted; modifying operations require explicit confirmation.
5. **Fail-Closed and Subprocess Isolation**: Zero `shell=True`, zero `os.system()`, zero `eval()`. Native actions invoke system binaries as argument arrays via standard desktop portals or `xdg-open`.

---

## 2. Acceptance Test Suite (Categories A through G)

All acceptance scenarios are formally verified in `tests/integration/test_assistant_acceptance.py` (55 test cases across 7 categories).

### Category A: Basic Conversation

| User Prompt | Expected Assistant Behavior | Verification Status |
| :--- | :--- | :--- |
| `"hi"`, `"hello"`, `"Good morning"`, `"howdy"` | Responds with friendly greeting (`"Hello! How can I help?"`) without invoking tools or shell. | **PASS** |
| `"what can you do?"`, `"help"`, `"what is avi?"` | Explains core capabilities (system information, applications, files, timers, AI reasoning). | **PASS** |
| `"what's up"`, `"how are you doing?"` | Responds with conversational small talk readiness (`"I'm ready! Ask me about your system..."`). | **PASS** |
| `"thanks"`, `"thank you"`, `"thx"`, `"cheers"` | Acknowledges courteously (`"You're welcome! Let me know if you need anything else."`). | **PASS** |

### Category B: System Questions (Native Read-Only Tools)

| User Prompt | Tool Invoked | Natural-Language Response Format | Verification Status |
| :--- | :--- | :--- | :--- |
| `"how much space is left on my laptop?"` | `system.disk_usage` | *"You have about 154 GB free out of 240 GB on your main drive."* | **PASS** |
| `"how much memory is free?"` | `/proc/meminfo` | *"You have about 9.1 GB of RAM available out of 14.5 GB total (37% in use)."* | **PASS** |
| `"what is using the most ram?"` | `system.processes` (sort=memory) | *"llama-server is currently using the most memory at about 8.2%, followed by firefox."* | **PASS** |
| `"what is using the most cpu?"` | `system.processes` (sort=cpu) | *"ffmpeg is currently using the most CPU at about 85.4%."* | **PASS** |
| `"what is running on my computer"` | `system.processes` | *"Active processes include systemd, pipewire, bash. systemd is currently highest..."* | **PASS** |
| `"what operating system am i on?"` | `system.system_info` | *"You are running Linux (6.11.0) on x86_64 with 16 CPU cores."* | **PASS** |

### Category C: Native Desktop Actions

| User Prompt | Action Dispatched | Execution Mechanism | Verification Status |
| :--- | :--- | :--- | :--- |
| `"open https://github.com"` | `OpenUrlAction` | `webbrowser.open()` / `xdg-open` | **PASS** |
| `"open youtube"` | `OpenUrlAction` | Resolves `youtube` to `https://www.youtube.com` | **PASS** |
| `"open my Documents folder"` | `OpenDirAction` | Resolves to `~/Documents` via desktop folder normalization | **PASS** |
| `"open downloads"` | `OpenDirAction` | Resolves to `~/Downloads` via folder alias | **PASS** |
| `"open google chrome"` | `OpenAppAction` | Desktop entry / executable resolution via `ApplicationResolver` | **PASS** |
| `"open photoshop"` | *(Uninstalled)* | Informs cleanly that application is not installed on system | **PASS** |
| `"set a timer for 5 seconds"` | `TimerAction` | Native non-blocking / sleep timer with start and finish alerts | **PASS** |

### Category D: Conversational Follow-up State

| Interaction Flow | Follow-up Handling | Verification Status |
| :--- | :--- | :--- |
| **Turn 1**: *"how much space is left?"*<br>**Turn 2**: *"what is taking up the most space?"* | Retains previous turn context; recognizes follow-up inquiry about disk storage breakdown. | **PASS** |
| **Turn 1**: *"how much memory do i have?"*<br>**Turn 2**: *"which app is using the most?"* | Routes to RAM usage process metrics based on preceding memory inquiry. | **PASS** |
| Bounded Capacity Verification | `ConversationHistory(max_turns=N)` preserves latest turns and discards oldest turn when bound is exceeded. | **PASS** |

### Category E: Clarification Requests

| Ambiguous Request | Assistant Clarification Question | Verification Status |
| :--- | :--- | :--- |
| `"open it"`, `"open this"`, `"open that"` | *"What would you like me to open? You can specify an application name, file path, directory, or website URL."* | **PASS** |
| `"delete that"`, `"delete it"` | *"Please specify the file or directory you would like to delete."* | **PASS** |
| `"open the project"` | *"Which project folder would you like to open? Please provide a path or project name."* | **PASS** |
| `"clean it up"` | *"What would you like to clean up? For example, temporary files, package caches, or a specific directory."* | **PASS** |
| `"send this"` | *"What would you like to send and where?"* | **PASS** |
| `"close it"` | *"Which application or window would you like to close?"* | **PASS** |

### Category F: Invalid Requests & Guidance

| Invalid Query | Assistant Guidance | Verification Status |
| :--- | :--- | :--- |
| `"set a timer for banana"` | *"Please specify a valid duration for the timer, such as 'set a timer for 5 minutes' or 'timer 30 seconds'."* | **PASS** |
| `"timer 0s"`, `"timer -5s"` | *"Timer duration must be greater than zero. For example: 'set a timer for 5 minutes'."* | **PASS** |
| `""`, `"   "` | Returns empty result gracefully without crashing or invoking models. | **PASS** |

### Category G: Safety Invariants & Execution Boundaries

| Invariant | Specification | Verification Status |
| :--- | :--- | :--- |
| **Read-Only Invariant** | Questions about disk, RAM, CPU, OS, processes perform zero shell executions. | **PASS** |
| **Catastrophic Defense** | Dangerous commands (`rm -rf /`, fork bombs) are halted with `RiskLevel.BLOCK`. | **PASS** |
| **Confirmation Boundary** | State-modifying command proposals require explicit user confirmation (`RiskLevel.CONFIRM`). | **PASS** |
| **Safe Subprocess Calls** | All actions execute using `subprocess.Popen(..., shell=False)`. | **PASS** |

---

## 3. Gap Analysis & Prioritized Backlog

Based on architectural inspection and test outcomes, the current implementation status is categorized across four priority tiers:

```
┌─────────────────────────────────────────────────────────────┐
│ P0: Core Assistant Runtime & Safety Foundation (COMPLETED)   │
│  - Intent Recognition & Conversational Synthesis            │
│  - Native Actions (URLs, Folders, Apps, Timers)             │
│  - Bounded ConversationTurn History                         │
│  - Clarification & Invalid Request Handling                 │
├─────────────────────────────────────────────────────────────┤
│ P1: Contextual Anaphora & Reference Resolution              │
│  - Automatic deictic resolution from Turn N-1 context       │
│  - Entity extraction from preceding tool results            │
├─────────────────────────────────────────────────────────────┤
│ P2: Native Linux Desktop Subsystems (D-Bus / MPRIS)         │
│  - Media player control (Spotify, VLC via MPRIS)            │
│  - Volume, display brightness, power profile management     │
│  - Battery and network Wi-Fi status                         │
├─────────────────────────────────────────────────────────────┤
│ P3: Multimodal GTK4 Desktop Integration                     │
│  - Streaming token rendering directly in GTK TextView       │
│  - Speech-to-Text (STT) and Text-to-Speech (TTS) pipeline   │
└─────────────────────────────────────────────────────────────┘
```

### P0: Core Assistant Runtime (Completed in Phase 11.5)
- [x] Natural language synthesis for disk, RAM, CPU, OS, and process inspection.
- [x] Clarification intent for ambiguous pronouns (`it`, `that`, `the project`).
- [x] Graceful courtesy intent for expressions of gratitude.
- [x] Robust timer duration parsing and guidance for invalid formats.
- [x] Desktop folder normalization (`my Documents folder` -> `~/Documents`).
- [x] `ConversationTurn` sequence tracking in `AssistantOrchestrator`.
- [x] 100% pass rate across 707 test cases, `make lint`, and `make format-check`.

### P1: Contextual Anaphora & Entity Resolution
- **Current State**: If a user asks *"how much space is left on my laptop?"* followed by *"open it"*, the assistant asks for clarification because *"it"* could refer to the file manager, terminal, or disk settings.
- **Target Experience**: If Turn N-1 produced a clear single entity (e.g. an application or unique file path), *"open it"* can resolve with high confidence to that entity, with a polite confirmation check if ambiguous: *"Opening your main drive in Files..."*.
- **Implementation Path**: Add an `EntityTracker` within `ConversationHistory` storing `last_referenced_path`, `last_referenced_app`, `last_referenced_url`.

### P2: Native Linux Desktop Subsystems (D-Bus Integration)
- **Current State**: System actions are limited to launching apps, directories, URLs, and local timers.
- **Target Experience**:
  - *"mute audio"* / *"set volume to 50%"* via PulseAudio / PipeWire D-Bus interface.
  - *"pause music"* / *"next track"* via MPRIS (`org.mpris.MediaPlayer2.Player`).
  - *"how much battery is left?"* via UPower (`org.freedesktop.UPower`).
- **Implementation Path**: Implement `avi.actions.dbus` leveraging native Python D-Bus bindings without external CLI dependencies.

### P3: Multimodal GTK4 Desktop Integration
- **Current State**: GTK4 popup (`avi ui`) provides floating query execution and confirmation dialogs.
- **Target Experience**:
  - Live streaming word-by-word token animation inside the popup.
  - Optional local Whisper STT / Piper TTS for hands-free voice assistance on Linux.

---

## 4. Phase 12 Design Proposal: Context-Aware Entity Resolution & Desktop Control

### 4.1 Architectural Extension: `EntityContextTracker`

To transition from asking clarification on every pronoun to intelligently resolving antecedents:

```python
@dataclass
class EntityContext:
    active_directory: Path | None = None
    last_mentioned_file: Path | None = None
    last_mentioned_app: str | None = None
    last_mentioned_url: str | None = None
    last_mentioned_pid: int | None = None
```

When an action or tool completes:
1. `system.disk_usage` registers `active_directory = Path("/")`.
2. `system.processes` registers `last_mentioned_pid` and `last_mentioned_app` of the top consumer.
3. `OpenAppAction` registers `last_mentioned_app`.
4. Subsequent deictic queries (*"close it"*, *"inspect that"*) evaluate `EntityContext` before prompting for clarification.

### 4.2 D-Bus Desktop Integration Architecture

```
                    AssistantOrchestrator
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
          Existing Actions           D-Bus Controller
         (Apps, URLs, Files)    (org.freedesktop.DBus)
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    ▼                      ▼                      ▼
                 MPRIS                   UPower              PipeWire
             (Media Player)          (Battery Status)       (Volume / Mute)
```

All D-Bus actions adhere to AVI's Seven Architectural Invariants: strictly non-blocking, sandboxed, and zero `shell=True`.

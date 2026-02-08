# Stage 1: Intercept — Implementation Document

**Date:** February 8, 2026
**Status:** Complete
**Version:** 0.1.0

---

## Overview

Stage 1 implements the **event interception layer** for Voice Copilot. It captures real-time events from Claude Code via its native hooks system, normalizes them into typed events, and exposes them on an async event bus for downstream consumers (Stage 2: Summarization, Stage 3: TTS).

### Distribution

```
pip install voice-copilot
voice-copilot start
```

The package installs as a Python CLI tool. When started, it:
1. Auto-installs hooks into `~/.claude/settings.json`
2. Starts a FastAPI server on `localhost:7865`
3. Watches Claude Code transcript files for assistant messages
4. Exposes all events on an async event bus + SSE debug stream

---

## Architecture

```
┌──────────────┐        ┌──────────────────────────────────┐
│ Claude Code  │        │  voice-copilot server (Python)    │
│              │        │                                    │
│  hooks fire ─┼──POST──▶  FastAPI server (:7865)           │
│              │        │    ├── POST /event (hook data)     │
│  transcript ─┼──watch─▶    ├── GET  /health               │
│  files       │        │    └── GET  /events (SSE stream)  │
│              │        │                                    │
└──────────────┘        │  Hook handler → Event bus          │
                        │  Transcript watcher → Event bus    │
                        │                                    │
                        │  Event bus → (Stage 2 consumes)    │
                        └──────────────────────────────────┘
```

### Data Flow

```
Claude Code hook fires
  → on_event.sh reads JSON from stdin, POSTs to localhost:7865/event
    → FastAPI route receives raw JSON
      → hook_handler.parse_hook_event() normalizes to VoiceCopilotEvent
        → EventBus.emit() fans out to all subscriber queues
          → SSE stream (GET /events) for debugging
          → Future: Stage 2 summarizer subscribes here
```

Additionally, the transcript watcher provides a complementary data source:
```
Claude Code writes to ~/.claude/projects/**/*.jsonl
  → watchdog detects file modification
    → Incremental read (byte offset tracking, only new lines)
      → Parse JSONL, filter for assistant text messages
        → Emit agent_message events to EventBus
```

---

## Event Types

Six normalized event types capture the full Claude Code lifecycle:

| Event Type | Source | Trigger | Key Fields |
|---|---|---|---|
| `tool_executed` | hook (PostToolUse) | Claude Code runs a tool (Bash, Edit, Read, etc.) | `tool_name`, `tool_input`, `tool_output` |
| `agent_blocked` | hook (Notification) | Permission prompt or idle prompt | `block_reason`, `message`, `options` |
| `agent_stopped` | hook (Stop) | Claude Code finishes responding | `stop_reason` |
| `agent_message` | transcript watcher | Assistant text appears in JSONL | `text` |
| `session_start` | hook (SessionStart) | New Claude Code session begins | `session_id` |
| `session_end` | hook (SessionEnd) | Session ends | `session_id` |

### Block Reasons

The `agent_blocked` event is the most critical — it solves the "silent blocking" problem (Pain Point #1 from the PRD):

| Block Reason | Meaning |
|---|---|
| `permission_prompt` | Agent needs permission to run a tool |
| `idle_prompt` | Agent is idle, waiting for user input |
| `question` | Agent asked a clarifying question |

---

## File Structure

```
voice-copilot/
├── pyproject.toml                          # Package config, dependencies, CLI entry point
├── docs/
│   └── stage1-intercept-implementation.md  # This document
├── voice_copilot/
│   ├── __init__.py                         # Package init, __version__
│   ├── __main__.py                         # python -m voice_copilot entry point
│   ├── cli.py                              # CLI: start/stop/status/install-hooks/uninstall
│   ├── config.py                           # Paths, ports, env var handling
│   ├── server/
│   │   ├── __init__.py
│   │   ├── app.py                          # FastAPI app with lifespan management
│   │   └── routes.py                       # POST /event, GET /health, GET /events (SSE)
│   ├── interceptors/
│   │   ├── __init__.py
│   │   ├── hook_handler.py                 # Parse Claude Code hook JSON → VoiceCopilotEvent
│   │   ├── hook_installer.py               # Auto-install/uninstall hooks in settings.json
│   │   └── transcript_watcher.py           # Watch JSONL transcripts for assistant messages
│   ├── events/
│   │   ├── __init__.py
│   │   ├── types.py                        # Pydantic models (VoiceCopilotEvent, EventType, BlockReason)
│   │   └── event_bus.py                    # Async fan-out event bus (asyncio.Queue)
│   └── hooks/
│       ├── __init__.py
│       └── on_event.sh                     # Shell script installed as Claude Code hook
└── tests/
    ├── __init__.py
    ├── conftest.py                         # Shared fixtures (event_bus, sample_event, app, async_client)
    ├── test_event_types.py                 # 25 tests — Pydantic models and enums
    ├── test_event_bus.py                   # 14 tests — subscribe, emit, unsubscribe, fan-out
    ├── test_hook_handler.py                # 21 tests — parsing each hook event type + edge cases
    ├── test_hook_installer.py              # 13 tests — install, uninstall, merge, idempotency
    ├── test_transcript_watcher.py          # 21 tests — JSONL parsing, incremental reading, graceful errors
    └── test_server.py                      # 12 tests — POST /event, GET /health, SSE stream
```

**Total: 17 source files, 8 test files, 110 tests**

---

## Component Details

### 1. Event Types (`voice_copilot/events/types.py`)

Pydantic v2 models for the normalized event format:

- `EventType` — 6-value string enum
- `BlockReason` — 3-value string enum
- `VoiceCopilotEvent` — Pydantic `BaseModel` with:
  - Required: `type`, `session_id`, `source` (literal "hook" | "transcript")
  - Auto-populated: `timestamp` (defaults to `time.time()`)
  - Optional per event type: `tool_name`, `tool_input`, `tool_output`, `block_reason`, `message`, `options`, `text`, `stop_reason`

### 2. Event Bus (`voice_copilot/events/event_bus.py`)

Async fan-out event bus using `asyncio.Queue`:

- `subscribe()` — returns a new queue (each subscriber gets their own)
- `emit(event)` — pushes to all subscriber queues
- `unsubscribe(queue)` — removes a subscriber
- Thread-safe via `asyncio.Lock`
- Full queues drop events with a warning log (never blocks the producer)
- Default queue size: 256

### 3. Hook Handler (`voice_copilot/interceptors/hook_handler.py`)

Parses raw Claude Code hook JSON into typed `VoiceCopilotEvent` instances:

- Routes by `hook_event_name` field to per-event parsers
- Block reason inference: checks explicit `type` field first, then falls back to substring matching in message body
- Returns `None` for unrecognized events (logged at WARNING)
- All field extraction uses `.get()` with defaults — never raises `KeyError`
- Entire dispatch wrapped in `try/except` — malformed payloads never crash the caller

### 4. Hook Installer (`voice_copilot/interceptors/hook_installer.py`)

Manages Voice Copilot hooks in `~/.claude/settings.json`:

- `install_hooks()` — merges hooks (preserving existing user hooks), copies `on_event.sh` to `~/.voice-copilot/hooks/`
- `uninstall_hooks()` — removes only Voice Copilot hooks (identified by command path), preserves all user hooks
- `are_hooks_installed()` — checks current state
- Creates backup (`settings.json.bak`) before any modification
- Idempotent — running install twice doesn't duplicate hooks
- Creates directories as needed, handles missing/empty settings files

### 5. Hook Shell Script (`voice_copilot/hooks/on_event.sh`)

Bridge script that Claude Code's hook system executes:

- Reads JSON from stdin, POSTs to `localhost:$VOICE_COPILOT_PORT/event`
- Fails silently if server is not running (`|| true`, `exit 0`)
- `--max-time 5` prevents hanging
- No backgrounding — Claude Code's async flag handles timing at its level

### 6. FastAPI Server (`voice_copilot/server/`)

**app.py:**
- Factory function `create_app()` with async lifespan
- Creates `EventBus` and `TranscriptWatcher` singletons
- Starts/stops transcript watcher in lifespan context

**routes.py:**
- `POST /event` — receives hook JSON, parses via `hook_handler`, emits to bus
- `GET /health` — returns status, version, subscriber count
- `GET /events` — SSE stream of all events (uses `sse-starlette`), with keep-alive pings every 15s

### 7. Transcript Watcher (`voice_copilot/interceptors/transcript_watcher.py`)

Watches `~/.claude/projects/` recursively for `*.jsonl` files:

- Uses `watchdog` library with a custom `FileSystemEventHandler`
- Tracks byte offset per file — only reads new lines on modification
- Parses Claude Code's JSONL format: filters for `type: "assistant"` entries with `role: "assistant"` in message
- Extracts text from `content[].type == "text"` blocks
- Cross-source deduplication via session_id + timestamp hashing (100ms window)
- Bridges watchdog's background thread to async event bus via `loop.call_soon_threadsafe()`
- Handles: missing directory, file deletion, file truncation, permission errors, malformed JSONL

### 8. CLI (`voice_copilot/cli.py`)

Click-based CLI with 5 commands:

| Command | Description |
|---|---|
| `voice-copilot start [--port] [--daemon] [--skip-hooks]` | Install hooks + start server |
| `voice-copilot stop` | Stop background server (SIGTERM → SIGKILL fallback) |
| `voice-copilot status` | Check if running + health endpoint |
| `voice-copilot install-hooks` | Manually install hooks only |
| `voice-copilot uninstall` | Stop server + remove all hooks |

- Daemon mode via `os.fork()` + `os.setsid()` with PID file at `~/.voice-copilot/server.pid`
- Logs to `~/.voice-copilot/server.log` in daemon mode
- Port validation (1024-65535), already-running detection, stale PID cleanup

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Notification hook sync | Synchronous (no `async: true`) | Gives time to alert the developer before Claude Code continues |
| Hook merge strategy | Append, never overwrite | Preserves existing user hooks in `settings.json` |
| Event bus pattern | Fan-out with per-subscriber queues | Each consumer gets independent delivery; full queues drop (never block producer) |
| Transcript watcher | Complementary to hooks | Hooks give structured tool events; transcripts give natural language text |
| Field extraction | `.get()` with defaults everywhere | Never crash on unexpected/missing hook payload fields |
| Shell script error handling | `|| true` + `exit 0` | Never block Claude Code with a hook error |

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| HTTP Server | FastAPI + uvicorn |
| Data Models | Pydantic v2 |
| File Watching | watchdog |
| CLI | click |
| SSE Streaming | sse-starlette |
| Testing | pytest + pytest-asyncio |
| Build System | hatchling (PEP 621) |

---

## Test Coverage

**110 tests, all passing (0.36s)**

| Test File | Tests | Coverage |
|---|---|---|
| `test_event_types.py` | 25 | EventType enum, BlockReason enum, VoiceCopilotEvent model creation/serialization |
| `test_event_bus.py` | 14 | Subscribe, emit fan-out, unsubscribe, queue-full drop behavior |
| `test_hook_handler.py` | 21 | Each hook event type, block reason inference, edge cases (missing fields, unknown events) |
| `test_hook_installer.py` | 13 | Install (create/merge/idempotent), uninstall (selective removal), backup creation |
| `test_transcript_watcher.py` | 21 | JSONL parsing, session ID extraction, incremental reading, error handling |
| `test_server.py` | 12 | POST /event, GET /health, SSE stream, malformed input handling |

All file system tests use `tmp_path` + `monkeypatch` — never touch real user files.

---

## What's Next

### Stage 2: Filter & Summarize
- Subscribe to the event bus built in Stage 1
- Use a local LLM (Ollama/transformers) to summarize events into concise narration text
- Example: `tool_executed(Bash, "npm test")` → "Running tests now."
- Example: `agent_blocked(permission_prompt)` → "The agent needs permission to edit auth.ts."

### Stage 3: TTS
- Subscribe to Stage 2's summarized output
- Convert text to speech via ElevenLabs
- Use LiveKit for real-time audio streaming
- Differentiate alert tone for `agent_blocked` events

### Stage 4: Question Detection & Alert
- Distinct audio alert when agent is blocked
- Read out question + available options

### Stage 5: STT & Voice Response
- Listen for developer's spoken response
- Convert speech to text, map to option, feed back to Claude Code

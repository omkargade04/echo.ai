# Voice Copilot — Stage 1: Intercept — Implementation Plan

## Decisions Made
- **MVP Platform:** Claude Code only
- **Language:** Python (FastAPI)
- **Distribution:** Python package (`pip install voice-copilot`) + CLI
- **VS Code Extension:** Not in MVP (future thin wrapper)
- **Summarization (Stage 2):** Local LLM (Ollama/transformers)
- **Hook Installation:** Auto-install into `~/.claude/settings.json`

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│  Distribution: pip install voice-copilot                  │
│                                                           │
│  Entry points:                                            │
│  1. CLI: voice-copilot start (terminal users)             │
│  2. Module: python -m voice_copilot (programmatic)        │
│  3. Library: from voice_copilot import EventBus (devs)    │
└──────────────────────────────────────────────────────────┘

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

### User Experience

**Terminal user runs Claude Code in one tab, Voice Copilot in another:**
```bash
# Tab 1: Start Voice Copilot (one-time)
pip install voice-copilot
voice-copilot start
# > Hooks installed in ~/.claude/settings.json
# > Server running on localhost:7865
# > Listening for Claude Code events...

# Tab 2: Use Claude Code normally
claude
# > (Voice Copilot captures all events in the background)
```

---

## What Claude Code Hooks Give Us

| Hook Event | Captures | Maps to |
|---|---|---|
| `PostToolUse` | Tool name, input, response after each tool call | `tool_executed` — "Edited auth.ts", "Ran npm test" |
| `Notification` (matcher: `permission_prompt`) | Agent waiting for permission | `agent_blocked` — **solves silent blocking** |
| `Notification` (matcher: `idle_prompt`) | Agent idle, needs input | `agent_blocked` |
| `Stop` | Agent finished responding | `agent_stopped` |
| `SessionStart` | Session began | `session_start` |
| `SessionEnd` | Session ended | `session_end` |

Each hook receives structured JSON on stdin including `session_id`, `transcript_path`, `tool_name`, `tool_input`, `tool_response`, `message`, etc.

---

## Normalized Event Format

```python
from pydantic import BaseModel
from typing import Literal, Optional
from enum import Enum

class EventType(str, Enum):
    TOOL_EXECUTED = "tool_executed"
    AGENT_BLOCKED = "agent_blocked"
    AGENT_STOPPED = "agent_stopped"
    AGENT_MESSAGE = "agent_message"
    SESSION_START = "session_start"
    SESSION_END = "session_end"

class BlockReason(str, Enum):
    PERMISSION_PROMPT = "permission_prompt"
    IDLE_PROMPT = "idle_prompt"
    QUESTION = "question"

class VoiceCopilotEvent(BaseModel):
    type: EventType
    timestamp: float
    session_id: str
    source: Literal["hook", "transcript"]

    # tool_executed
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[dict] = None

    # agent_blocked
    block_reason: Optional[BlockReason] = None
    message: Optional[str] = None
    options: Optional[list[str]] = None

    # agent_message (from transcript watcher)
    text: Optional[str] = None

    # agent_stopped
    stop_reason: Optional[str] = None
```

---

## Package Structure

```
voice-copilot/
├── pyproject.toml                # Package config
├── README.md
├── voice_copilot/
│   ├── __init__.py
│   ├── __main__.py               # python -m voice_copilot
│   ├── cli.py                    # CLI: voice-copilot start/stop/status
│   ├── config.py                 # Port, paths, settings
│   ├── server/
│   │   ├── __init__.py
│   │   ├── app.py                # FastAPI app setup
│   │   └── routes.py             # POST /event, GET /health, GET /events
│   ├── interceptors/
│   │   ├── __init__.py
│   │   ├── hook_handler.py       # Parse hook JSON → VoiceCopilotEvent
│   │   ├── hook_installer.py     # Auto-install/uninstall hooks in ~/.claude/settings.json
│   │   └── transcript_watcher.py # Watch JSONL files for assistant messages
│   ├── events/
│   │   ├── __init__.py
│   │   ├── types.py              # Pydantic models (VoiceCopilotEvent, etc.)
│   │   └── event_bus.py          # Async event bus (asyncio.Queue based)
│   └── hooks/
│       └── on_event.sh           # Shell script installed as Claude Code hook
├── tests/
│   ├── test_hook_handler.py
│   ├── test_hook_installer.py
│   ├── test_transcript_watcher.py
│   └── test_server.py
└── vscode-extension/             # Future (not in MVP)
```

**Tech stack:**
- Python 3.10+
- FastAPI + uvicorn (HTTP server)
- Pydantic (data models)
- watchdog (file watching — Python equivalent of chokidar)
- click or typer (CLI framework)
- pytest (testing)

---

## Implementation Steps

### Step 1: Project scaffold
- `pyproject.toml` with dependencies, CLI entry point, metadata
- Directory structure as above
- `voice_copilot/cli.py` with `start`, `stop`, `status` commands

### Step 2: Event types + event bus
- `voice_copilot/events/types.py` — Pydantic models (VoiceCopilotEvent, etc.)
- `voice_copilot/events/event_bus.py` — async event bus using `asyncio.Queue`
  - `emit(event)` — push event
  - `subscribe(callback)` — register listener
  - Subscribers get called for every event (Stage 2 will subscribe here)

### Step 3: Hook handler
- `voice_copilot/interceptors/hook_handler.py`
- `parse_hook_event(raw_json: dict) -> VoiceCopilotEvent`
- Maps `hook_event_name` to event type:
  - `PostToolUse` → `tool_executed`
  - `Notification` (permission_prompt) → `agent_blocked`
  - `Notification` (idle_prompt) → `agent_blocked`
  - `Stop` → `agent_stopped`
  - `SessionStart` → `session_start`
  - `SessionEnd` → `session_end`

### Step 4: Hook auto-installer
- `voice_copilot/interceptors/hook_installer.py`
- `install_hooks()`:
  - Reads `~/.claude/settings.json`
  - Merges Voice Copilot hooks (preserving existing user hooks)
  - Writes back
  - Copies `on_event.sh` to `~/.voice-copilot/hooks/`
- `uninstall_hooks()`:
  - Removes only Voice Copilot hooks from settings
  - Cleans up `~/.voice-copilot/hooks/`

### Step 5: Hook shell script
- `voice_copilot/hooks/on_event.sh`
- Reads JSON from stdin, POSTs to `localhost:7865/event`
- Single script for all hook types (JSON contains `hook_event_name`)

**Hooks config installed into `~/.claude/settings.json`:**
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [{
          "type": "command",
          "command": "~/.voice-copilot/hooks/on_event.sh",
          "async": true
        }]
      }
    ],
    "Notification": [
      {
        "matcher": "permission_prompt|idle_prompt",
        "hooks": [{
          "type": "command",
          "command": "~/.voice-copilot/hooks/on_event.sh"
        }]
      }
    ],
    "Stop": [
      {
        "hooks": [{
          "type": "command",
          "command": "~/.voice-copilot/hooks/on_event.sh",
          "async": true
        }]
      }
    ],
    "SessionStart": [
      {
        "hooks": [{
          "type": "command",
          "command": "~/.voice-copilot/hooks/on_event.sh",
          "async": true
        }]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [{
          "type": "command",
          "command": "~/.voice-copilot/hooks/on_event.sh",
          "async": true
        }]
      }
    ]
  }
}
```

Note: `Notification` hook is **synchronous** (not async) to give time for alerting the developer before Claude continues.

### Step 6: FastAPI server
- `voice_copilot/server/app.py` — FastAPI app with lifespan (start/stop transcript watcher)
- `voice_copilot/server/routes.py`:
  - `POST /event` — receives hook JSON, parses via hook_handler, emits to event bus
  - `GET /health` — returns 200 + server status
  - `GET /events` — SSE stream of all events (for debugging / future UI)

### Step 7: Transcript file watcher
- `voice_copilot/interceptors/transcript_watcher.py`
- Uses `watchdog` library to watch `~/.claude/projects/` recursively for `*.jsonl`
- Tracks byte offset per file (only reads new lines)
- Parses JSONL entries, filters for assistant text messages
- Emits `agent_message` events to event bus
- Deduplicates with hook events using session_id + timestamp proximity (100ms window)

### Step 8: CLI
- `voice_copilot/cli.py` using `click` or `typer`:
  - `voice-copilot start [--port 7865]` — install hooks + start server (foreground)
  - `voice-copilot start --daemon` — start as background process
  - `voice-copilot stop` — stop background server
  - `voice-copilot status` — show if running, active sessions
  - `voice-copilot install-hooks` — manually install hooks only
  - `voice-copilot uninstall` — remove hooks + clean up

### Step 9: Tests
- `test_hook_handler.py` — parse each hook event type correctly
- `test_hook_installer.py` — install/uninstall preserves existing hooks
- `test_transcript_watcher.py` — incremental file parsing
- `test_server.py` — POST /event → event bus emits correct event

---

## Verification Plan

1. **Unit tests:** `pytest tests/`
2. **Integration test:**
   - Start server: `voice-copilot start`
   - Simulate hook: `curl -X POST localhost:7865/event -H "Content-Type: application/json" -d '{"hook_event_name": "PostToolUse", "session_id": "test", "tool_name": "Bash", "tool_input": {"command": "npm test"}, "tool_response": {"exit_code": 0}}'`
   - Verify event on SSE stream: `curl localhost:7865/events`
3. **Manual E2E test:**
   - Run `voice-copilot start`
   - Verify hooks in `~/.claude/settings.json`
   - Open Claude Code in another terminal, give it a task
   - Observe events arriving (via SSE stream or server logs)
   - Verify `agent_blocked` fires when Claude asks for permission
   - Verify `tool_executed` fires for each tool call
   - Verify `agent_stopped` fires when Claude finishes

---

## Key References

- Claude Code hooks reference: https://code.claude.com/docs/en/hooks
- Claude Code hooks guide: https://code.claude.com/docs/en/hooks-guide
- agent-tts (file watching reference): https://github.com/kiliman/agent-tts
- FastAPI docs: https://fastapi.tiangolo.com/
- watchdog (file watcher): https://python-watchdog.readthedocs.io/
- PRD: voice-copilot-prd.md (in project root)

---

## Files to Create

| File | Purpose |
|---|---|
| `pyproject.toml` | Package config, dependencies, CLI entry point |
| `voice_copilot/__init__.py` | Package init |
| `voice_copilot/__main__.py` | `python -m voice_copilot` entry |
| `voice_copilot/cli.py` | CLI commands (start/stop/status) |
| `voice_copilot/config.py` | Configuration (port, paths) |
| `voice_copilot/server/app.py` | FastAPI app setup |
| `voice_copilot/server/routes.py` | HTTP endpoints |
| `voice_copilot/interceptors/hook_handler.py` | Parse hook JSON → VoiceCopilotEvent |
| `voice_copilot/interceptors/hook_installer.py` | Auto-install/uninstall Claude Code hooks |
| `voice_copilot/interceptors/transcript_watcher.py` | Watch JSONL transcripts |
| `voice_copilot/events/types.py` | Pydantic event models |
| `voice_copilot/events/event_bus.py` | Async event bus |
| `voice_copilot/hooks/on_event.sh` | Shell script for Claude Code hooks |
| `tests/test_hook_handler.py` | Hook handler tests |
| `tests/test_hook_installer.py` | Hook installer tests |
| `tests/test_transcript_watcher.py` | Transcript watcher tests |
| `tests/test_server.py` | Server integration tests |

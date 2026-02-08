# Stage 2: Filter & Summarize — Implementation Document

**Date:** February 8, 2026
**Status:** Complete
**Version:** 0.1.0
**Depends on:** Stage 1 (Intercept) — Complete

---

## Overview

Stage 2 implements the **summarization layer** for Voice Copilot. It subscribes to the Stage 1 event bus, converts raw `VoiceCopilotEvent` objects into concise narration text optimized for text-to-speech, and publishes `NarrationEvent` objects on a dedicated narration bus for downstream consumers (Stage 3: TTS).

### Key Capability

```
VoiceCopilotEvent (raw)                  NarrationEvent (TTS-ready)
─────────────────────                    ─────────────────────────
tool_executed(Bash, "npm test")    →     "Ran command: npm test"
agent_blocked(permission_prompt)   →     "The agent needs permission. Allow edit of auth.ts?"
3x Edit in 500ms                   →     "Edited 3 files."
agent_message(long assistant text) →     "Refactored auth module and added tests." (LLM summary)
```

### Dual-Mode Strategy

- **Template engine** (5 of 6 event types): Deterministic string templates, instant, zero external deps
- **LLM summarizer** (agent_message only): Ollama local LLM with truncation fallback when unavailable

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Stage 1 (existing)                                          │
│                                                               │
│  Claude Code hooks ──▶ EventBus ──▶ VoiceCopilotEvent        │
│  Transcript watcher ──▶            (6 event types)           │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 2 (new)                                               │
│                                                               │
│  Summarizer (subscribes to EventBus)                         │
│    │                                                          │
│    ├── TemplateEngine (5 event types → instant narration)     │
│    │     tool_executed  → "Ran npm test."                     │
│    │     agent_blocked  → "Agent needs permission to edit..." │
│    │     agent_stopped  → "Agent finished."                   │
│    │     session_start  → "New coding session started."       │
│    │     session_end    → "Session ended."                    │
│    │                                                          │
│    ├── EventBatcher (collapses rapid tool events)             │
│    │     3x Edit in 500ms → "Edited 3 files."                │
│    │                                                          │
│    └── LLMSummarizer (agent_message only)                    │
│          Long text → Ollama qwen2.5:0.5b → concise summary   │
│          Fallback: truncate to ~150 chars                     │
│                                                               │
│    ──▶ NarrationBus ──▶ NarrationEvent                       │
│                          (text, priority, source_event_type)  │
│                                                               │
│  New endpoints:                                               │
│    GET /narrations  — SSE stream of narration events          │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 3 (future): TTS subscribes to NarrationBus            │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

```
VoiceCopilotEvent arrives on EventBus
  → Summarizer._consume_loop() pulls from its subscriber queue
    → _process_event() routes by event type:
      ├── tool_executed → EventBatcher.add()
      │     └── batch window (500ms) expires or max size (10) hit:
      │           → TemplateEngine.render_batch(events)
      │             → NarrationBus.emit(NarrationEvent)
      ├── agent_blocked → flush batcher [IMMEDIATE, CRITICAL priority]
      │     → TemplateEngine.render()
      │       → NarrationBus.emit(NarrationEvent)
      ├── agent_message → flush batcher
      │     → LLMSummarizer.summarize()
      │       ├── Ollama available → LLM summary (SummarizationMethod.LLM)
      │       └── Ollama unavailable → truncation (SummarizationMethod.TRUNCATION)
      │         → NarrationBus.emit(NarrationEvent)
      └── agent_stopped / session_start / session_end → flush batcher
            → TemplateEngine.render()
              → NarrationBus.emit(NarrationEvent)
```

---

## Narration Types

### NarrationPriority

| Priority | Value | Meaning | Event Types |
|---|---|---|---|
| `CRITICAL` | `"critical"` | Must interrupt current TTS playback | `agent_blocked` |
| `NORMAL` | `"normal"` | Spoken in order when TTS queue is free | `tool_executed`, `agent_message`, `agent_stopped` |
| `LOW` | `"low"` | May be dropped if queue is congested | `session_start`, `session_end` |

### SummarizationMethod

| Method | Value | Meaning |
|---|---|---|
| `TEMPLATE` | `"template"` | Deterministic template string rendering |
| `LLM` | `"llm"` | Ollama-based language model summarization |
| `TRUNCATION` | `"truncation"` | Text truncation fallback (LLM unavailable) |

### NarrationEvent Model

```python
class NarrationEvent(BaseModel):
    text: str                                    # Narration text ready for TTS
    priority: NarrationPriority                  # TTS scheduling urgency
    source_event_type: EventType                 # Which event type produced this
    summarization_method: SummarizationMethod     # How the text was generated
    session_id: str                              # Carried from source event
    timestamp: float = Field(default_factory=time.time)
    source_event_id: str | None = None           # UUID linking back to source VoiceCopilotEvent
```

---

## File Structure

```
voice-copilot/
├── voice_copilot/
│   ├── summarizer/                          # NEW — Stage 2 module
│   │   ├── __init__.py                     # Re-exports: Summarizer, NarrationEvent, etc.
│   │   ├── types.py                        # NarrationEvent, NarrationPriority, SummarizationMethod
│   │   ├── summarizer.py                   # Core orchestrator (subscribe → route → emit)
│   │   ├── template_engine.py              # Deterministic event-to-text templates
│   │   ├── llm_summarizer.py              # Ollama client + truncation fallback
│   │   └── event_batcher.py               # Time-windowed batching for rapid tool events
│   ├── events/
│   │   ├── types.py                        # MODIFIED — added event_id field (uuid4)
│   │   └── event_bus.py                    # MODIFIED — made generic (EventBus[T])
│   ├── server/
│   │   ├── app.py                          # MODIFIED — wires Summarizer + NarrationBus
│   │   └── routes.py                       # MODIFIED — GET /narrations SSE, updated /health
│   └── config.py                           # MODIFIED — Ollama configuration constants
├── tests/
│   ├── test_narration_types.py             # 22 tests — Pydantic models and enums
│   ├── test_template_engine.py             # 46 tests — every template, batching, priorities
│   ├── test_llm_summarizer.py              # 29 tests — Ollama calls, fallback, health check
│   ├── test_event_batcher.py               # 25 tests — batch window, timer, flush triggers
│   ├── test_summarizer.py                  # 26 tests — routing, lifecycle, end-to-end
│   └── test_server_narrations.py           # 13 tests — SSE stream, health fields, regression
└── docs/
    └── stage2-summarize-implementation.md  # This document
```

**Stage 2 totals: 6 new source files, 5 modified files, 6 test files, 161 new tests**

---

## Component Details

### 1. NarrationEvent Types (`voice_copilot/summarizer/types.py`)

Pydantic v2 models for the narration output format, following the same style as Stage 1's `events/types.py`:

- `NarrationPriority` — 3-value string enum (critical, normal, low)
- `SummarizationMethod` — 3-value string enum (template, llm, truncation)
- `NarrationEvent` — Pydantic `BaseModel` with required fields (`text`, `priority`, `source_event_type`, `summarization_method`, `session_id`) and auto-populated `timestamp`

### 2. Template Engine (`voice_copilot/summarizer/template_engine.py`)

Deterministic event-to-narration-text mapper with two public methods:

**`render(event) -> NarrationEvent`** — Converts a single event using templates:

| Event Type | Tool/Reason | Template | Example |
|---|---|---|---|
| `tool_executed` | `Bash` | "Ran command: {cmd}" (60 char max) | "Ran command: npm test" |
| `tool_executed` | `Read` | "Read {basename}" | "Read auth.ts" |
| `tool_executed` | `Edit` | "Edited {basename}" | "Edited auth.ts" |
| `tool_executed` | `Write` | "Created {basename}" | "Created jwt.ts" |
| `tool_executed` | `Glob` | "Searched for files matching {pattern}" | "Searched for files matching *.ts" |
| `tool_executed` | `Grep` | "Searched code for {pattern}" | "Searched code for TODO" |
| `tool_executed` | `Task` | "Launched a sub-agent" | "Launched a sub-agent" |
| `tool_executed` | `WebFetch` | "Fetched a web page" | "Fetched a web page" |
| `tool_executed` | `WebSearch` | "Searched the web for {query}" | "Searched the web for React hooks" |
| `tool_executed` | (other) | "Used {tool_name} tool" | "Used NotebookEdit tool" |
| `agent_blocked` | `permission_prompt` | "The agent needs permission. {message}" | "The agent needs permission. Allow edit of auth.ts?" |
| `agent_blocked` | `idle_prompt` | "The agent is waiting for your input." | — |
| `agent_blocked` | `question` | "The agent has a question. {message}" | "The agent has a question. Which DB?" |
| `agent_blocked` | (none) | "The agent is blocked and needs attention." | — |
| `agent_stopped` | — | "Agent finished." / "Agent stopped: {reason}." | "Agent finished." |
| `session_start` | — | "New coding session started." | — |
| `session_end` | — | "Session ended." | — |

Options rendering: When `agent_blocked` events have options, appends natural-language options — "Options are: A and B." (2 items), "Options are: A, B, or C." (3+ with Oxford comma).

File path handling: Full paths are reduced to basenames via `Path(path).name` for TTS readability.

**`render_batch(events) -> NarrationEvent`** — Combines multiple `tool_executed` events:
- Same tool: "Edited 3 files." / "Ran 2 commands."
- Mixed tools: "Edited 2 files and ran a command."

Defensive coding: All input fields use `.get()` with defaults, `tool_input` guarded against `None`, top-level try/except ensures no exceptions propagate.

### 3. Event Batcher (`voice_copilot/summarizer/event_batcher.py`)

Time-windowed batcher that collapses rapid consecutive `tool_executed` events:

- **Batch window:** 500ms (`BATCH_WINDOW_SEC = 0.5`)
- **Max batch size:** 10 events (`MAX_BATCH_SIZE = 10`)
- **Timer management:** `asyncio.Task` with `asyncio.sleep` for deferred flush
- **Flush triggers:** Timer expiry, max size reached, non-tool event arrives, explicit `flush()` call

Key design:
- `add(event)` → Returns `NarrationEvent` only if max size hit (immediate flush); otherwise returns `None` and accumulates
- `flush()` → Force-flush with timer cancellation; returns `NarrationEvent` or `None` if empty
- `set_flush_callback()` → Injects the async callback for timer-based flushes (wired to `Summarizer._emit_narration`)
- `render_batch` callable injected via constructor (decoupled from TemplateEngine)
- `CancelledError` handled gracefully in timer tasks
- Never raises exceptions — all errors caught and logged at debug level

### 4. LLM Summarizer (`voice_copilot/summarizer/llm_summarizer.py`)

Ollama-based summarizer for `agent_message` events with truncation fallback:

**Ollama integration:**
- Connects via `httpx.AsyncClient` to `http://localhost:11434` (configurable)
- Default model: `qwen2.5:0.5b` (smallest/fastest, <100ms on CPU)
- Calls `POST /api/generate` with `stream: false`, `num_predict: 50`, `temperature: 0.3`
- Prompt: "Summarize this AI coding assistant message in one short sentence (under 20 words) suitable for text-to-speech narration."

**Health check system:**
- On startup, pings `GET /api/tags` to check Ollama availability
- If unavailable, sets `_ollama_available = False` and logs a warning
- Periodically re-checks every 60s (`OLLAMA_HEALTH_CHECK_INTERVAL`) in case Ollama starts later
- Re-check only happens when Ollama is currently unavailable (no unnecessary pings)

**Truncation fallback:**
- Text <= 150 chars: returned as-is
- Text > 150 chars: first 140 chars + "..."
- Always produces a `NarrationEvent` — never errors out

**Configuration** (all env var overridable):

| Constant | Env Var | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `VOICE_COPILOT_LLM_MODEL` | `qwen2.5:0.5b` |
| `OLLAMA_TIMEOUT` | `VOICE_COPILOT_LLM_TIMEOUT` | `5.0` seconds |
| `OLLAMA_HEALTH_CHECK_INTERVAL` | — | `60.0` seconds |

### 5. Summarizer (`voice_copilot/summarizer/summarizer.py`)

Core async orchestrator that ties all Stage 2 components together:

**Lifecycle:**
```
Summarizer(event_bus, narration_bus)
  → start()
    → Wire batcher's timer-flush callback to _emit_narration
    → Start LLM summarizer (httpx client + health check)
    → Subscribe to EventBus (gets its own asyncio.Queue)
    → Launch _consume_loop as asyncio.Task
  → ... (processes events continuously) ...
  → stop()
    → Cancel consume task (await CancelledError)
    → Flush pending batcher events
    → Unsubscribe from EventBus
    → Stop LLM summarizer (close httpx client)
```

**Event routing:**

| Event Type | Handler | Flushes Batcher First? |
|---|---|---|
| `tool_executed` | `EventBatcher.add()` | No (accumulates in batch) |
| `agent_message` | `LLMSummarizer.summarize()` | Yes |
| `agent_blocked` | `TemplateEngine.render()` | Yes (CRITICAL — immediate) |
| `agent_stopped` | `TemplateEngine.render()` | Yes |
| `session_start` | `TemplateEngine.render()` | Yes |
| `session_end` | `TemplateEngine.render()` | Yes |

**Error handling:** Processing errors on individual events are logged and skipped — the consume loop never crashes. The only way it stops is via `CancelledError` from `stop()`.

### 6. Generic EventBus (`voice_copilot/events/event_bus.py`)

Refactored from Stage 1 to support `Generic[T]`:

```python
class EventBus(Generic[T]):
    async def emit(self, event: T) -> None: ...
    async def subscribe(self) -> asyncio.Queue[T]: ...
    async def unsubscribe(self, queue: asyncio.Queue[T]) -> None: ...
```

**Usage in production:**
```python
event_bus: EventBus[VoiceCopilotEvent] = EventBus()    # Stage 1 events
narration_bus: EventBus[NarrationEvent] = EventBus()    # Stage 2 narrations
```

Backward-compatible — `EventBus()` without type parameter still works. Log messages use `getattr(event, "type", type(event).__name__)` for safe display across event types.

### 7. Server Integration

**app.py changes:**
- Creates `narration_bus` and `summarizer` as module-level singletons alongside existing `event_bus` and `transcript_watcher`
- Lifespan: starts summarizer after transcript watcher, stops summarizer before transcript watcher
- Attaches `narration_bus` and `summarizer` to `app.state`

**routes.py changes:**
- `GET /narrations` — SSE stream of NarrationEvents (mirrors `/events` pattern with 15s keep-alive pings)
- `GET /health` — Now includes `narration_subscribers` (int) and `ollama_available` (bool)

**New health response:**
```json
{
  "status": "ok",
  "version": "0.1.0",
  "subscribers": 1,
  "narration_subscribers": 0,
  "ollama_available": false
}
```

### 8. VoiceCopilotEvent Enhancement

Added `event_id` field for traceability:
```python
event_id: str = Field(default_factory=lambda: str(uuid4()))
```

Every event now gets a unique UUID. `NarrationEvent.source_event_id` references this for debugging and tracing through the pipeline.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Template vs LLM | Template-first, LLM only for agent_message | 5/6 event types are structured — templates are instant, deterministic, and require no external deps |
| Ollama model | qwen2.5:0.5b | Smallest/fastest model. Summarization is a simple task — no need for larger models. <100ms on CPU |
| Ollama fallback | Truncation (not error) | Pipeline must never block waiting for LLM. Degraded narration > no narration |
| Event batching | 500ms window, max 10 events | Prevents "edited file, edited file, edited file" narration spam. 500ms is fast enough for real-time feel |
| agent_blocked priority | Critical (immediate flush) | PRD Pain Point #1 — this is the most important event. Must never be delayed by batching |
| Generic EventBus | Make EventBus `Generic[T]` | Avoids duplicating the fan-out bus code. NarrationBus = EventBus[NarrationEvent] |
| Narration text style | Short, imperative, present tense | Optimized for TTS: "Edited auth.ts. Running tests." not "The agent has edited the file auth.ts and is now running tests." |
| No streaming from Ollama | `stream: false` | Summary is <20 words — streaming adds complexity for negligible latency gain |
| event_id on VoiceCopilotEvent | uuid4 auto-generated | Enables NarrationEvent to reference its source event for debugging/tracing |
| Batcher render injection | Constructor injection (`render_batch` callable) | Decouples batcher from TemplateEngine; enables easy testing with mocks |
| File path basenames | `Path(path).name` | Full paths are too verbose for TTS; "auth.ts" is clearer than "/Users/dev/project/src/auth.ts" |

---

## Tech Stack

| Component | Technology | Notes |
|---|---|---|
| Narration models | Pydantic v2 | Same pattern as Stage 1 event models |
| LLM backend | Ollama (HTTP API) | No Python SDK needed — httpx calls `/api/generate` |
| LLM model | qwen2.5:0.5b | User can override via `VOICE_COPILOT_LLM_MODEL` env var |
| HTTP client | httpx.AsyncClient | Already a project dependency from Stage 1 |
| Event bus | asyncio.Queue (Generic) | Reuses Stage 1 EventBus, now generic |
| SSE streaming | sse-starlette | Same as Stage 1 `/events` endpoint |
| New Python deps | **None** | All dependencies already present from Stage 1 |

---

## Test Coverage

**271 total tests (110 Stage 1 + 161 Stage 2), all passing in 3.19s**

| Test File | Tests | Lines | Coverage |
|---|---|---|---|
| `test_narration_types.py` | 22 | 242 | NarrationPriority enum, SummarizationMethod enum, NarrationEvent creation/serialization/validation |
| `test_template_engine.py` | 46 | 441 | All 10 tool templates, Bash truncation, basename extraction, agent_blocked with each reason, options formatting (1/2/3/4+ items), agent_stopped, session events, render_batch (same/mixed tools), priority mapping, summarization method |
| `test_llm_summarizer.py` | 29 | 521 | Health check success/failure/timeout, summarize with LLM, fallback to truncation, HTTP errors, truncation boundaries (150/151 chars), periodic re-check timing, config values wiring, start/stop lifecycle |
| `test_event_batcher.py` | 25 | 388 | First add returns None, accumulation, max batch flush, empty flush, timer fires, timer callback, explicit flush cancels timer, double flush safety, lifecycle (flush+re-add), defensive error handling |
| `test_summarizer.py` | 26 | 849 | Start/stop lifecycle, routing for all 6 event types, batcher flush on non-tool events, narration emission to bus, end-to-end (tool→batch→flush, blocked→CRITICAL), error handling (doesn't crash loop), stop flushes pending batch, llm_available property |
| `test_server_narrations.py` | 13 | 301 | Health includes narration_subscribers/ollama_available, narration SSE route exists, subscriber receives events, correct structure, keep-alive ping, end-to-end (POST→narration), agent_blocked→CRITICAL, regression tests for existing endpoints |

All file system tests use mocks — no real Ollama connections, no real file I/O. LLM summarizer tests mock `httpx.AsyncClient`. Batcher timer tests use `BATCH_WINDOW_SEC = 0.05` (50ms) for fast, non-flaky execution.

---

## Modifications to Stage 1

Stage 2 made minimal, backward-compatible changes to Stage 1:

| File | Change | Impact |
|---|---|---|
| `events/event_bus.py` | Made generic (`EventBus[T]`) | Zero API changes — all Stage 1 code works unchanged |
| `events/types.py` | Added `event_id: str` (uuid4 default) | Additive — existing code ignores this field |
| `config.py` | Added 4 Ollama constants at end of file | Additive — no existing code affected |
| `server/app.py` | Added narration_bus + summarizer singletons, lifespan hooks | Additive — existing event_bus and transcript_watcher unchanged |
| `server/routes.py` | Added GET /narrations, extended GET /health | Additive — existing endpoints unchanged |

**All 110 Stage 1 tests pass with zero modifications.**

---

## What's Next

### Stage 3: TTS
- Subscribe to the NarrationBus built in Stage 2
- Convert narration text to speech via ElevenLabs
- Use LiveKit for real-time audio streaming
- Use `NarrationPriority` for TTS scheduling:
  - `CRITICAL`: Interrupt current speech, play alert tone, then narrate
  - `NORMAL`: Queue for next narration slot
  - `LOW`: Skip if backlogged

### Stage 4: Question Detection & Alert
- Distinct audio alert when agent is blocked
- Read out question + available options

### Stage 5: STT & Voice Response
- Listen for developer's spoken response
- Convert speech to text, map to option, feed back to Claude Code

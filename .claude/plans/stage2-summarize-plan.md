# Echo — Stage 2: Filter & Summarize — Implementation Plan

**Date:** February 8, 2026
**Status:** Planning
**Depends on:** Stage 1 (Intercept) — Complete

---

## Decisions Made

- **Dual-mode summarization:** Template engine for structured events + Ollama LLM for free-text summarization
- **Template-first:** 5 of 6 event types use deterministic templates (zero latency, no external deps)
- **LLM only for `agent_message`:** Long assistant text is the only event type requiring intelligent summarization
- **Default Ollama model:** `qwen2.5:0.5b` — fastest small model, <100ms inference on CPU
- **Graceful degradation:** When Ollama is unavailable, fall back to text truncation (never block the pipeline)
- **Event batching:** Rapid consecutive `tool_executed` events are batched into a single narration (e.g., "Edited 3 files")
- **Priority levels:** `agent_blocked` = critical (interrupts), others = normal/low
- **Output bus:** Reuse the `EventBus` pattern for a `NarrationBus` that Stage 3 will subscribe to
- **No new Python dependencies:** `httpx` (already installed) is sufficient for Ollama HTTP API

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Stage 1 (existing)                                          │
│                                                               │
│  Claude Code hooks ──▶ EventBus ──▶ EchoEvent        │
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
│    │     session_start  → "New session started."              │
│    │     session_end    → "Session ended."                    │
│    │                                                          │
│    ├── EventBatcher (collapses rapid tool events)             │
│    │     3x Edit in 500ms → "Edited 3 files."                │
│    │                                                          │
│    └── LLMSummarizer (agent_message only)                    │
│          Long text → Ollama qwen2.5:0.5b → concise summary   │
│          Fallback: truncate to ~100 chars                     │
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
EchoEvent arrives on EventBus
  → Summarizer.consume() pulls from its subscriber queue
    → Route by event type:
      ├── tool_executed → EventBatcher.add()
      │     └── if batch window expires (500ms) or different tool type:
      │           → TemplateEngine.render(batched_events)
      │             → NarrationBus.emit(NarrationEvent)
      ├── agent_blocked → TemplateEngine.render() [IMMEDIATE, high priority]
      │     → NarrationBus.emit(NarrationEvent)
      ├── agent_stopped → TemplateEngine.render()
      │     → NarrationBus.emit(NarrationEvent)
      ├── agent_message → LLMSummarizer.summarize(text)
      │     └── Ollama available? → LLM summary
      │     └── Ollama unavailable? → truncation fallback
      │     → NarrationBus.emit(NarrationEvent)
      ├── session_start → TemplateEngine.render()
      │     → NarrationBus.emit(NarrationEvent)
      └── session_end → TemplateEngine.render()
            → NarrationBus.emit(NarrationEvent)
```

---

## NarrationEvent Model

```python
class NarrationPriority(str, Enum):
    CRITICAL = "critical"     # agent_blocked — must interrupt current TTS
    NORMAL = "normal"         # tool_executed, agent_message, agent_stopped
    LOW = "low"               # session_start, session_end

class SummarizationMethod(str, Enum):
    TEMPLATE = "template"     # Deterministic template rendering
    LLM = "llm"              # Ollama-based summarization
    TRUNCATION = "truncation" # Fallback when LLM unavailable

class NarrationEvent(BaseModel):
    text: str                                    # The narration text for TTS
    priority: NarrationPriority
    source_event_type: EventType                 # Which event type produced this
    summarization_method: SummarizationMethod     # How the text was generated
    session_id: str                              # Carried from source event
    timestamp: float = Field(default_factory=time.time)
    source_event_id: str | None = None           # For traceability
```

---

## Template Engine — Event-to-Text Mapping

### tool_executed Templates

| Tool Name | Template | Example Output |
|---|---|---|
| `Bash` | "Ran command: {first 60 chars of command}" | "Ran command: npm test" |
| `Read` | "Read {file_path}" | "Read src/auth.ts" |
| `Edit` | "Edited {file_path}" | "Edited src/auth.ts" |
| `Write` | "Created {file_path}" | "Created src/utils/jwt.ts" |
| `Glob` | "Searched for files matching {pattern}" | "Searched for files matching *.ts" |
| `Grep` | "Searched code for {pattern}" | "Searched code for TODO" |
| `Task` | "Launched a sub-agent" | "Launched a sub-agent" |
| `WebFetch` | "Fetched web page" | "Fetched web page" |
| `WebSearch` | "Searched the web for {query}" | "Searched the web for React hooks" |
| (other) | "Used {tool_name} tool" | "Used NotebookEdit tool" |

**Batching:** When multiple tool_executed events arrive within 500ms:
- Same tool: "Edited 3 files" / "Read 5 files"
- Mixed tools: "Edited 2 files and ran a command"

### agent_blocked Templates

| Block Reason | Template | Example Output |
|---|---|---|
| `permission_prompt` | "The agent needs permission. {message}" | "The agent needs permission. Allow edit of auth.ts?" |
| `idle_prompt` | "The agent is waiting for your input." | "The agent is waiting for your input." |
| `question` | "The agent has a question: {message}" | "The agent has a question: Which database should I use?" |
| (no reason) | "The agent is blocked and needs attention." | "The agent is blocked and needs attention." |

If `options` are present, append: "Options are: {option1}, {option2}, or {option3}."

### Other Templates

| Event Type | Template |
|---|---|
| `agent_stopped` | "Agent finished." (or "Agent stopped: {stop_reason}" if reason present) |
| `session_start` | "New coding session started." |
| `session_end` | "Session ended." |

### agent_message — LLM Summarization

**Prompt template for Ollama:**
```
Summarize this AI coding assistant message in one short sentence (under 20 words) suitable for text-to-speech narration. Focus on what was done or decided, not how.

Message:
{text}

Summary:
```

**Truncation fallback** (when Ollama unavailable):
- If text <= 150 chars: use as-is
- If text > 150 chars: take first 140 chars + "..."

---

## Ollama Integration

### Connection

```python
OLLAMA_BASE_URL = "http://localhost:11434"  # Default Ollama endpoint
OLLAMA_MODEL = "qwen2.5:0.5b"              # Default model
OLLAMA_TIMEOUT = 5.0                        # Max seconds per request
```

All configurable via environment variables:
- `OLLAMA_BASE_URL`
- `ECHO_LLM_MODEL`
- `ECHO_LLM_TIMEOUT`

### Health Check

On startup, the Summarizer pings `GET /api/tags` to check Ollama availability. If unavailable:
- Log a warning: "Ollama not available — using truncation fallback for agent_message events"
- Set `_ollama_available = False`
- Periodically re-check (every 60s) in case Ollama starts later

### API Call

```
POST /api/generate
{
  "model": "qwen2.5:0.5b",
  "prompt": "<summarization prompt>",
  "stream": false,
  "options": {"num_predict": 50, "temperature": 0.3}
}
```

Uses `httpx.AsyncClient` (already a project dependency) with timeout.

---

## Event Batcher

The batcher collapses rapid consecutive events of the same type into a single narration:

```python
class EventBatcher:
    BATCH_WINDOW_MS = 500   # Collapse events within this window
    MAX_BATCH_SIZE = 10     # Force flush after this many events

    async def add(self, event: EchoEvent) -> NarrationEvent | None:
        """Add event to batch. Returns NarrationEvent if batch should flush."""

    async def flush(self) -> NarrationEvent | None:
        """Force-flush current batch. Called by timer or on different event type."""
```

**Batching rules:**
1. First `tool_executed` starts a new batch + a 500ms timer
2. Subsequent `tool_executed` events within the window extend the batch
3. Batch flushes when: timer expires, a non-`tool_executed` event arrives, or batch hits 10 events
4. Any `agent_blocked` event immediately flushes any pending batch (critical priority preempts)

---

## File Structure

```
echo/
├── summarizer/                          # NEW — Stage 2 module
│   ├── __init__.py                     # Re-exports Summarizer, NarrationEvent, etc.
│   ├── types.py                        # NarrationEvent, NarrationPriority, SummarizationMethod
│   ├── summarizer.py                   # Core Summarizer class (subscribe → process → emit)
│   ├── template_engine.py              # Deterministic event-to-text templates
│   ├── llm_summarizer.py              # Ollama integration for agent_message
│   └── event_batcher.py               # Collapses rapid tool_executed events
├── events/
│   ├── types.py                        # EXISTING — add event_id field (uuid4)
│   └── event_bus.py                    # EXISTING — reused as NarrationBus (generic enough)
├── server/
│   ├── app.py                          # MODIFY — wire Summarizer + NarrationBus into lifespan
│   └── routes.py                       # MODIFY — add GET /narrations SSE endpoint
├── config.py                           # MODIFY — add Ollama config constants
└── cli.py                              # MODIFY — add --no-llm flag to start command
```

### New Files (7)

| File | Purpose | Est. Lines |
|---|---|---|
| `echo/summarizer/__init__.py` | Re-exports public API | ~15 |
| `echo/summarizer/types.py` | NarrationEvent, NarrationPriority, SummarizationMethod | ~45 |
| `echo/summarizer/summarizer.py` | Core async summarizer — subscribe, route, emit | ~120 |
| `echo/summarizer/template_engine.py` | All template-based event-to-text mappings | ~130 |
| `echo/summarizer/llm_summarizer.py` | Ollama client, health check, fallback | ~110 |
| `echo/summarizer/event_batcher.py` | Time-windowed batching for tool events | ~100 |
| `tests/test_template_engine.py` | Template rendering tests | ~150 |

### Modified Files (4)

| File | Changes |
|---|---|
| `echo/events/types.py` | Add `event_id: str` field (uuid4 default) for traceability |
| `echo/server/app.py` | Create NarrationBus + Summarizer in lifespan, start/stop |
| `echo/server/routes.py` | Add `GET /narrations` SSE endpoint |
| `echo/config.py` | Add `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT` constants |

### Test Files (6)

| File | Tests | Coverage |
|---|---|---|
| `tests/test_narration_types.py` | ~15 | NarrationEvent model, enums, serialization |
| `tests/test_template_engine.py` | ~30 | Every tool template, agent_blocked variants, batched output |
| `tests/test_llm_summarizer.py` | ~20 | Ollama call, timeout, fallback, health check, re-check |
| `tests/test_event_batcher.py` | ~20 | Batch window, flush on timer, flush on different type, max batch |
| `tests/test_summarizer.py` | ~25 | End-to-end: event in → narration out, routing, priority, lifecycle |
| `tests/test_server_narrations.py` | ~10 | GET /narrations SSE, health endpoint updated |

**Estimated total: ~120 new tests**

---

## Integration Points with Stage 1

### Subscribing to EventBus

The Summarizer subscribes to the existing `EventBus` (same as the SSE `/events` endpoint):

```python
class Summarizer:
    def __init__(self, event_bus: EventBus, narration_bus: EventBus):
        self._event_bus = event_bus
        self._narration_bus = narration_bus
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._queue = await self._event_bus.subscribe()
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._queue:
            await self._event_bus.unsubscribe(self._queue)
```

### Reusing EventBus for NarrationBus

The existing `EventBus` class is generic enough — it accepts `EchoEvent` but we can create a typed alias or make it generic. The simplest approach: create a second `EventBus` instance specifically for narrations and store `NarrationEvent` objects (they share the same queue-based fan-out pattern).

**Decision:** Make `EventBus` generic over event type rather than creating a separate class. This avoids code duplication:

```python
# event_bus.py — make generic
class EventBus(Generic[T]):
    def __init__(self, maxsize: int = 256) -> None: ...
    async def emit(self, event: T) -> None: ...
    async def subscribe(self) -> asyncio.Queue[T]: ...

# Usage:
event_bus: EventBus[EchoEvent] = EventBus()        # Stage 1
narration_bus: EventBus[NarrationEvent] = EventBus()        # Stage 2
```

### Server Wiring (app.py)

```python
# In lifespan():
narration_bus = EventBus()
summarizer = Summarizer(event_bus=event_bus, narration_bus=narration_bus)
await summarizer.start()
app.state.narration_bus = narration_bus
# ... yield ...
await summarizer.stop()
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Template vs LLM | Template-first, LLM only for agent_message | 5/6 event types are structured — templates are instant, deterministic, and require no external deps |
| Ollama model | qwen2.5:0.5b | Smallest/fastest model. Summarization is a simple task — no need for larger models. <100ms on CPU. |
| Ollama fallback | Truncation (not error) | Pipeline must never block waiting for LLM. Degraded narration > no narration. |
| Event batching | 500ms window, max 10 events | Prevents "edited file, edited file, edited file" narration spam. 500ms is fast enough for real-time feel. |
| agent_blocked priority | Critical (immediate flush) | PRD Pain Point #1 — this is the most important event. Must never be delayed by batching. |
| Generic EventBus | Make EventBus generic (Generic[T]) | Avoids duplicating the fan-out bus code. NarrationBus = EventBus[NarrationEvent]. |
| Narration text style | Short, imperative, present tense | Optimized for TTS output: "Edited auth.ts. Running tests." not "The agent has edited the file auth.ts and is now running tests." |
| No streaming from Ollama | `stream: false` | Summary is <20 words — streaming adds complexity for negligible latency gain |
| event_id on EchoEvent | Add uuid4 | Enables NarrationEvent to reference its source event for debugging/tracing |

---

## Deliverables

1. **NarrationEvent Pydantic model** — typed output of the summarization pipeline
2. **NarrationPriority enum** — critical/normal/low priority levels
3. **TemplateEngine** — deterministic event-to-text mapper for 5 event types
4. **EventBatcher** — time-windowed collapse of rapid tool_executed events
5. **LLMSummarizer** — Ollama client with health check, summarization, and truncation fallback
6. **Summarizer** — core async orchestrator that subscribes to EventBus, routes events, emits NarrationEvents
7. **Generic EventBus** — refactor existing EventBus to be generic over event type
8. **GET /narrations endpoint** — SSE stream of NarrationEvents for debugging + Stage 3 consumption
9. **Updated config.py** — Ollama configuration constants with env var overrides
10. **Updated app.py lifespan** — wire Summarizer + NarrationBus startup/shutdown
11. **Updated health endpoint** — include narration subscriber count + Ollama status
12. **~120 new tests** across 6 test files
13. **Updated docs** — Stage 2 implementation documentation

---

## Task Breakdown

### Task 1: NarrationEvent types (no dependencies)
**Files:** `echo/summarizer/__init__.py`, `echo/summarizer/types.py`
- NarrationEvent, NarrationPriority, SummarizationMethod Pydantic models
- Tests: `tests/test_narration_types.py`

### Task 2: Make EventBus generic (no dependencies)
**Files:** `echo/events/event_bus.py`, `echo/events/types.py`
- Refactor EventBus to `EventBus(Generic[T])`
- Add `event_id: str = Field(default_factory=lambda: str(uuid4()))` to EchoEvent
- Ensure all existing Stage 1 tests still pass
- Tests: verify existing `test_event_bus.py` still passes

### Task 3: Template engine (depends on Task 1)
**Files:** `echo/summarizer/template_engine.py`
- All tool-specific templates (Bash, Read, Edit, Write, Glob, Grep, etc.)
- agent_blocked templates (with options rendering)
- agent_stopped, session_start, session_end templates
- Batched event rendering ("Edited 3 files")
- Tests: `tests/test_template_engine.py`

### Task 4: Event batcher (depends on Task 1)
**Files:** `echo/summarizer/event_batcher.py`
- Time-windowed batching with 500ms window
- Max batch size enforcement
- Immediate flush on priority event
- Flush on event type change
- Tests: `tests/test_event_batcher.py`

### Task 5: LLM summarizer (depends on Task 1)
**Files:** `echo/summarizer/llm_summarizer.py`, update `echo/config.py`
- Ollama HTTP client using httpx.AsyncClient
- Health check on startup + periodic re-check
- Summarization prompt template
- Truncation fallback
- Config constants for Ollama URL, model, timeout
- Tests: `tests/test_llm_summarizer.py`

### Task 6: Core Summarizer (depends on Tasks 1-5)
**Files:** `echo/summarizer/summarizer.py`
- Subscribe to EventBus, consume loop
- Route events to TemplateEngine / EventBatcher / LLMSummarizer
- Emit NarrationEvents to NarrationBus
- Graceful start/stop lifecycle
- Tests: `tests/test_summarizer.py`

### Task 7: Server integration (depends on Tasks 2, 6)
**Files:** update `echo/server/app.py`, update `echo/server/routes.py`
- Wire NarrationBus + Summarizer into app lifespan
- Add `GET /narrations` SSE endpoint (mirrors `/events` pattern)
- Update `GET /health` to include narration_subscribers + ollama_status
- Tests: `tests/test_server_narrations.py`

### Task 8: Run all tests + integration verification
- Run full test suite (Stage 1 + Stage 2)
- Verify zero regressions in Stage 1 tests
- Manual integration test: POST sample events → verify narration output on SSE

### Parallelization Strategy

```
Task 1 ──┬──▶ Task 3 ──┐
          ├──▶ Task 4 ──┼──▶ Task 6 ──▶ Task 7 ──▶ Task 8
          └──▶ Task 5 ──┘
Task 2 ────────────────────────────────▶ Task 7
```

- **Wave 1 (parallel):** Tasks 1 + 2
- **Wave 2 (parallel):** Tasks 3 + 4 + 5 (all depend on Task 1 types only)
- **Wave 3:** Task 6 (needs all of 3, 4, 5)
- **Wave 4:** Task 7 (needs 2 + 6)
- **Wave 5:** Task 8 (final verification)

---

## Verification Plan

1. **Unit tests:** `pytest tests/` — all ~230 tests (110 Stage 1 + ~120 Stage 2)
2. **Integration test:**
   ```bash
   echo-copilot start
   # In another terminal:
   curl -X POST localhost:7865/event \
     -H "Content-Type: application/json" \
     -d '{"hook_event_name":"PostToolUse","session_id":"test","tool_name":"Bash","tool_input":{"command":"npm test"},"tool_response":{"exit_code":0}}'
   # Watch narration stream:
   curl localhost:7865/narrations
   # Should see: {"text": "Ran command: npm test", "priority": "normal", ...}
   ```
3. **Ollama test** (when Ollama is running):
   ```bash
   # Send agent_message event
   curl -X POST localhost:7865/event \
     -H "Content-Type: application/json" \
     -d '{"hook_event_name":"agent_message","session_id":"test","text":"I have analyzed the codebase and found three issues with the authentication module. First, the JWT tokens are not being validated properly..."}'
   # Narration should be a concise ~20 word summary
   ```
4. **Fallback test** (stop Ollama, send agent_message):
   - Should produce truncated text, not error

---

## Tech Stack Additions

| Component | Technology | Notes |
|---|---|---|
| LLM Backend | Ollama (HTTP API) | No Python SDK needed — httpx calls `/api/generate` |
| LLM Model | qwen2.5:0.5b | User can override via `ECHO_LLM_MODEL` env var |
| New Python deps | None | httpx already in dependencies |

---

## What Stage 3 Expects from Stage 2

Stage 3 (TTS) will:
1. Subscribe to the `NarrationBus` (same pattern as EventBus subscription)
2. Receive `NarrationEvent` objects with `.text` ready for TTS
3. Use `.priority` to decide interruption behavior:
   - `CRITICAL`: Interrupt current speech, play alert tone, then narrate
   - `NORMAL`: Queue for next narration slot
   - `LOW`: Queue with lower priority, skip if backlogged
4. Use `.session_id` to track which Claude Code session the narration belongs to

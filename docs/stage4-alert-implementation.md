# Stage 4: Question Detection & Alert — Implementation Document

**Date:** February 9, 2026
**Status:** Complete
**Version:** 0.1.0
**Depends on:** Stage 3 (TTS) — Complete (442 tests)

---

## Overview

Stage 4 implements **differentiated alert tones and repeat alerts** for Echo. When Claude Code is blocked (permission request, question, or idle), the pipeline now plays a distinct audio tone per block reason so the developer can identify the type of interruption by ear alone. If the developer does not respond, the system repeats the alert at configurable intervals until resolution or a maximum repeat count is reached. An AlertManager component tracks active alerts and resolves them automatically when the agent resumes work.

### Key Capability

```
agent_blocked (permission_prompt)  →  Urgent double-beep (880Hz→1320Hz x2)
                                       + "The agent needs your permission..."
                                       + repeat every 30s if no response

agent_blocked (question)           →  Rising two-tone (660Hz→880Hz)
                                       + "The agent has a question..."
                                       + repeat every 30s if no response

agent_blocked (idle_prompt)        →  Gentle low tone (440Hz→550Hz)
                                       + "The agent is idle..."
                                       + repeat every 30s if no response

tool_executed (same session)       →  Alert resolved, repeat timer cancelled
```

### What Changed from Stage 3

- One generic alert tone replaced by four block-reason-specific tones
- New AlertManager tracks active alerts with per-session repeat timers
- Template engine produces richer narration with numbered options for TTS
- Hook handler now extracts `options` from Claude Code notification JSON
- NarrationEvent carries `block_reason` through the pipeline to the TTS layer
- Health endpoint reports `alert_active` status

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Stages 1-3 (existing, modified)                             │
│                                                               │
│  EchoEvent (agent_blocked, with options + block_reason)      │
│    → EventBus.emit() (fan-out to all subscribers)            │
│                                                               │
│  ┌─ Subscriber 1: Summarizer                                │
│  │    → TemplateEngine.render() → NarrationEvent             │
│  │      (now includes block_reason + numbered options text)   │
│  │    → NarrationBus.emit()                                  │
│  │                                                           │
│  └─ Subscriber 2: AlertManager._consume_loop()              │
│       → Watches for non-blocked events → clears alerts       │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 4 (new + modified)                                    │
│                                                               │
│  TTSEngine._handle_critical()                                │
│    │                                                          │
│    ├── AudioPlayer.play_alert(block_reason)                  │
│    │     Select cached tone by BlockReason                    │
│    │     PERMISSION → urgent double-beep (~600ms)             │
│    │     QUESTION   → rising two-tone (~350ms)                │
│    │     IDLE       → gentle low tone (~400ms)                │
│    │     None       → standard alert (~350ms)                 │
│    │                                                          │
│    ├── ElevenLabsClient.synthesize(text) → PCM bytes         │
│    ├── AudioPlayer.play_immediate(pcm) → speakers            │
│    ├── LiveKitPublisher.publish(pcm) → remote room           │
│    │                                                          │
│    └── AlertManager.activate(session_id, block_reason, text) │
│          Start repeat timer (30s default)                     │
│          After interval: callback → interrupt + alert + play  │
│          Max repeats: 5 (configurable)                        │
│          Resolution: any non-blocked event for same session   │
│                                                               │
│  Modified endpoints:                                          │
│    GET /health — now includes alert_active field              │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

```
EchoEvent(agent_blocked, block_reason=PERMISSION_PROMPT, options=["yes","no"])
  → EventBus.emit() (fan-out)
    ├── Subscriber 1: Summarizer._handle_agent_blocked()
    │     → TemplateEngine.render()
    │         → "The agent needs your permission and is waiting for your answer.
    │            It's asking: Allow running rm -rf /tmp/test?
    │            Option one: yes. Option two: no."
    │     → NarrationEvent(priority=CRITICAL, block_reason=PERMISSION_PROMPT)
    │     → NarrationBus.emit()
    │         → TTSEngine._handle_critical()
    │             1. AudioPlayer.interrupt() — stop current playback
    │             2. AudioPlayer.play_alert(PERMISSION_PROMPT) — urgent double-beep
    │             3. ElevenLabsClient.synthesize(text) → PCM bytes
    │             4. AudioPlayer.play_immediate(pcm) → speakers
    │             5. LiveKitPublisher.publish(pcm) (if connected)
    │             6. AlertManager.activate("session-1", PERMISSION_PROMPT, text)
    │                 → Start _repeat_loop task (30s interval)
    │
    └── Subscriber 2: AlertManager._consume_loop()
          → Sees agent_blocked: no-op (activation handled by TTSEngine)
          → After 30s: _repeat_loop fires
            → _repeat_callback(PERMISSION_PROMPT, text)
              → TTSEngine._handle_repeat_alert()
                → interrupt + play_alert + synthesize + play_immediate + publish

... Later: tool_executed event for same session ...
  → AlertManager._handle_event()
    → Clear active alert, cancel repeat timer
```

### Alert Resolution

Any non-`agent_blocked` event for the same session ID clears the alert:

```
tool_executed / agent_message / session_end for session X
  → AlertManager._handle_event()
    → _clear_alert(session_id)
      → Pop from _active_alerts dict
      → Cancel repeat_task (await CancelledError)
```

---

## Alert Tones

Each `BlockReason` maps to a distinct audio signature built from sine wave segments:

| BlockReason | Frequencies | Pattern | Duration | Character |
|---|---|---|---|---|
| `PERMISSION_PROMPT` | 880Hz, 1320Hz | Two pairs: 880→1320, 880→1320 (with 40ms silences) | ~600ms | Urgent double-beep |
| `QUESTION` | 660Hz, 880Hz | Single rise: 660→880 (50ms silence) | ~350ms | Softer notification |
| `IDLE_PROMPT` | 440Hz, 550Hz | Gentle: 440→550 (50ms silence) | ~400ms | Low gentle reminder |
| `None` (default) | 880Hz, 1320Hz | Standard: 880→1320 (50ms silence) | ~350ms | Original Stage 3 alert |

All tones use:
- 5ms linear fade-in/fade-out on each sine segment to prevent clicks
- float32 output in [-1.0, 1.0] range
- 16kHz sample rate (configurable)
- Generated once at `AudioPlayer.start()`, cached as PCM16 bytes per reason

### Tone Specifications

```python
# Permission: urgent double-beep
[(880, 0.12), (0, 0.04), (1320, 0.12), (0, 0.04),
 (880, 0.12), (0, 0.04), (1320, 0.12)]

# Question: rising two-tone
[(660, 0.15), (0, 0.05), (880, 0.15)]

# Idle: gentle low tone
[(440, 0.20), (0, 0.05), (550, 0.15)]

# Default: standard alert (matches original Stage 3)
[(880, 0.15), (0, 0.05), (1320, 0.15)]
```

---

## File Structure

```
echo-copilot/
├── echo/
│   ├── interceptors/
│   │   └── hook_handler.py              # MODIFIED — parse options from notification JSON
│   ├── summarizer/
│   │   ├── types.py                     # MODIFIED — block_reason field on NarrationEvent
│   │   └── template_engine.py           # MODIFIED — richer blocked templates, numbered options
│   ├── tts/
│   │   ├── __init__.py                  # MODIFIED — re-exports AlertManager
│   │   ├── alert_tone.py               # MODIFIED — renamed _generate_sine/_apply_fade to public
│   │   ├── alert_tones.py              # NEW — per-block-reason tone generation
│   │   ├── alert_manager.py            # NEW — alert state tracking + repeat timer
│   │   ├── audio_player.py            # MODIFIED — block_reason tone caching + selection
│   │   └── tts_engine.py             # MODIFIED — AlertManager wiring, tone routing, repeat callback
│   ├── server/
│   │   ├── app.py                      # MODIFIED — pass event_bus to TTSEngine
│   │   └── routes.py                   # MODIFIED — alert_active in /health
│   └── config.py                       # MODIFIED — ALERT_REPEAT_INTERVAL, ALERT_MAX_REPEATS
├── tests/
│   ├── test_alert_tones.py            # NEW — 15 tests
│   ├── test_alert_manager.py          # NEW — 33 tests
│   ├── test_hook_handler.py           # MODIFIED — +6 tests (options parsing)
│   ├── test_narration_types.py        # MODIFIED — +3 tests (block_reason field)
│   ├── test_template_engine.py        # MODIFIED — +10 tests (enhanced templates, numbered options)
│   ├── test_audio_player.py           # MODIFIED — +5 tests (block_reason tone selection)
│   ├── test_tts_engine.py            # MODIFIED — +9 tests (AlertManager wiring, tone routing)
│   ├── test_server_tts.py            # MODIFIED — +2 tests (alert_active in health)
│   └── test_tts_config.py            # MODIFIED — +5 tests (new config vars)
└── docs/
    └── stage4-alert-implementation.md  # This document
```

**Stage 4 totals: 2 new source files, 9 modified source files, 2 new test files, 7 modified test files, 111 new tests (553 total)**

---

## Component Details

### 1. Alert Tones (`echo/tts/alert_tones.py`)

New module that generates per-block-reason alert tones using the sine wave primitives from `alert_tone.py`:

**Public API:**
- `generate_alert_for_reason(block_reason: BlockReason | None, sample_rate: int = 16000) -> np.ndarray`: Returns float32 array in [-1.0, 1.0]
- `generate_alert_for_reason_pcm16(block_reason: BlockReason | None, sample_rate: int = 16000) -> bytes`: Returns int16 PCM bytes

**Tone map:**
- Internal `_TONE_MAP` dict maps `BlockReason | None` to a list of `(frequency_hz, duration_sec)` tuples
- `frequency=0` means silence (gap between tones)
- Each non-silence segment gets fade-in/fade-out via `apply_fade()`

**Dependency on `alert_tone.py`:**
- Imports `generate_sine()` and `apply_fade()` (renamed from private `_generate_sine`/`_apply_fade` in Stage 4)
- The original `generate_alert_tone()` in `alert_tone.py` continues to work unchanged

### 2. AlertManager (`echo/tts/alert_manager.py`)

Core new component that tracks active blocked alerts and manages repeat timers:

**Data model — `ActiveAlert`:**
- `session_id: str` — the blocked session
- `block_reason: BlockReason | None` — why the agent is blocked
- `narration_text: str` — text to re-speak on repeat
- `created_at: float` — `time.monotonic()` timestamp
- `repeat_count: int` — number of repeats fired so far (starts at 0)
- `repeat_task: asyncio.Task | None` — the background repeat timer task

**Lifecycle:**
```
AlertManager(event_bus)
  → start()
    → Subscribe to EventBus (gets its own asyncio.Queue)
    → Launch _consume_loop as asyncio.Task
  → ... (watches events, manages alerts) ...
  → stop()
    → Cancel all active repeat tasks
    → Clear _active_alerts dict
    → Cancel consume task (await CancelledError)
    → Unsubscribe from EventBus
```

**Key methods:**
- `set_repeat_callback(callback)` — TTSEngine provides `async callback(block_reason, text)` for re-playing alerts
- `activate(session_id, block_reason, text)` — Register an alert, replace existing if any, start repeat timer
- `has_active_alert(session_id) -> bool` — Check if a session has an active alert
- `get_active_alert(session_id) -> ActiveAlert | None` — Get the active alert object
- `active_alert_count -> int` — Number of currently active alerts

**Consume loop:**
- Pulls `EchoEvent` from EventBus subscriber queue with 1s timeout
- For any non-`agent_blocked` event: checks if that session has an active alert, clears it if so
- `agent_blocked` events are ignored (activation handled by TTSEngine after playback)
- All exceptions caught and logged — never crashes the loop

**Repeat loop:**
- `asyncio.Task` per session, started on `activate()`
- Sleeps for `ALERT_REPEAT_INTERVAL` seconds (default 30)
- Fires `_repeat_callback(block_reason, narration_text)` up to `ALERT_MAX_REPEATS` times (default 5)
- Cancels cleanly when alert is cleared or manager is stopped
- Callback exceptions are caught and logged — never crashes the timer

### 3. Alert Tone Refactor (`echo/tts/alert_tone.py`)

The private functions `_generate_sine()` and `_apply_fade()` were renamed to public `generate_sine()` and `apply_fade()` so that `alert_tones.py` can import and reuse them. The existing `generate_alert_tone()` and `generate_alert_tone_pcm16()` functions continue to work unchanged.

### 4. AudioPlayer Modifications (`echo/tts/audio_player.py`)

**Changed `play_alert()` signature:**
- Old: `play_alert() -> None` — played the single cached alert tone
- New: `play_alert(block_reason: BlockReason | None = None) -> None` — plays the tone for the given reason

**Tone caching at startup:**
- `start()` now generates and caches PCM16 bytes for all four block reasons: `None`, `PERMISSION_PROMPT`, `QUESTION`, `IDLE_PROMPT`
- Stored in `_alert_tones: dict[BlockReason | None, bytes]`
- `play_alert()` looks up the cached tone by `block_reason`, falls back to `None` (default) if reason not found

**Import change:**
- Now imports `generate_alert_for_reason` from `echo.tts.alert_tones` (plural) instead of using the old single-tone generator

### 5. Template Engine Enhancements (`echo/summarizer/template_engine.py`)

**Richer `agent_blocked` templates per block reason:**

| BlockReason | Template |
|---|---|
| `PERMISSION_PROMPT` | "The agent needs your permission and is waiting for your answer. It's asking: {message}" |
| `QUESTION` | "The agent has a question and is waiting for your answer. It's asking: {message}" |
| `IDLE_PROMPT` | "The agent is idle and waiting for your input." |
| `None` (unknown) | "The agent is blocked and needs your attention. {message}" |

**New `_format_options_numbered()` static method:**
- Formats options with spoken ordinals: "Option one: RS256. Option two: HS256."
- Supports up to 10 named ordinals ("one" through "ten"), falls back to digits
- Period-separated for TTS pause/clarity
- Appended to the blocked narration text when `event.options` is present

**`block_reason` pass-through:**
- `render()` now passes `block_reason=event.block_reason` to the `NarrationEvent` constructor
- This carries the block reason through the pipeline so TTSEngine can select the right tone

**Existing `_format_options()` preserved:**
- The original Oxford-comma formatter ("Options are: foo, bar, or baz.") remains for backward compatibility
- New code uses `_format_options_numbered()` instead for TTS readability

### 6. Hook Handler Fix (`echo/interceptors/hook_handler.py`)

**Options parsing added to `_parse_notification()`:**
- New line: `options: list[str] | None = raw.get("options")`
- Passed through to `EchoEvent` constructor: `options=options`
- Previously, `options` was present in the Claude Code JSON but silently dropped

### 7. NarrationEvent Extension (`echo/summarizer/types.py`)

**New optional field:**
- `block_reason: BlockReason | None = None`
- Backward compatible — defaults to `None` for all non-blocked events
- Allows the TTS layer to select the correct alert tone without subscribing to the EventBus directly

### 8. TTSEngine Modifications (`echo/tts/tts_engine.py`)

**Constructor change:**
- New keyword param: `event_bus: EventBus | None = None` (backward compatible)
- When `event_bus` is provided, creates an `AlertManager(event_bus)` instance
- When `None`, AlertManager is not started (pure Stage 3 behavior)

**Start/stop lifecycle:**
- `start()`: After starting sub-components and subscribe loop, calls `alert_manager.set_repeat_callback()` and `alert_manager.start()`
- `stop()`: Stops `alert_manager` first (before consume task cancellation)

**`_handle_critical()` changes:**
- Passes `narration.block_reason` to `AudioPlayer.play_alert(block_reason=...)`
- After playback, calls `alert_manager.activate(session_id, block_reason, text)` to register the alert and start the repeat timer

**New `_handle_repeat_alert()` method:**
- Callback provided to AlertManager for repeat alerts
- Signature: `async def _handle_repeat_alert(block_reason: BlockReason | None, text: str) -> None`
- Same flow as `_handle_critical()`: interrupt, play_alert, synthesize, play_immediate, publish

**New property:**
- `alert_active -> bool`: Returns `True` if AlertManager has any active alerts

### 9. Server Integration

**app.py changes:**
- TTSEngine constructor now receives `event_bus=event_bus` keyword argument
- Before: `tts_engine = TTSEngine(narration_bus=narration_bus)`
- After: `tts_engine = TTSEngine(narration_bus=narration_bus, event_bus=event_bus)`

**routes.py changes:**
- `GET /health` response now includes `alert_active` field:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "subscribers": 2,
  "narration_subscribers": 1,
  "ollama_available": false,
  "tts_state": "disabled",
  "tts_available": false,
  "audio_available": false,
  "livekit_connected": false,
  "alert_active": false
}
```

Note: `subscribers` is now 2 when AlertManager is active (Summarizer + AlertManager both subscribe to EventBus).

---

## Priority & Interruption

Stage 4 builds on the Stage 3 interrupt mechanism, adding block-reason-specific tone selection:

```
IDLE ──→ PLAYING (normal/low) ──→ IDLE
              │
              │ CRITICAL arrives (agent_blocked)
              ▼
         INTERRUPTING
              │ 1. sd.stop() immediately
              │ 2. Drain non-critical queue items (keep critical)
              │ 3. Select alert tone by block_reason:
              │    ├── PERMISSION_PROMPT → urgent double-beep (~600ms)
              │    ├── QUESTION          → rising two-tone (~350ms)
              │    ├── IDLE_PROMPT       → gentle low tone (~400ms)
              │    └── None              → standard alert (~350ms)
              │ 4. Play selected alert tone
              │ 5. Synthesize critical text via ElevenLabs
              │ 6. Play critical narration immediately
              │ 7. AlertManager.activate() → start repeat timer
              ▼
         PLAYING (critical) ──→ IDLE ──→ (wait ALERT_REPEAT_INTERVAL)
                                              │
                                              ▼
                                         REPEAT ALERT
                                              │ 1. interrupt + play_alert
                                              │ 2. synthesize + play_immediate
                                              │ 3. repeat_count++
                                              │ 4. If repeat_count < MAX → sleep → repeat
                                              ▼
                                         IDLE (or MAX reached → stop repeating)
```

**Guarantees:**
1. CRITICAL never delayed by normal queue items (unchanged from Stage 3)
2. Alert tone matches the block reason — developer can identify the type by ear
3. Repeat alerts fire at configurable intervals until resolution or max repeats
4. Any non-blocked event for the same session cancels the repeat timer
5. Multiple sessions can have independent active alerts simultaneously
6. Second `agent_blocked` for same session replaces the previous alert (cancels old timer)

---

## Repeat Alert Flow

```
1. agent_blocked event arrives
     → CRITICAL pipeline: interrupt → alert tone → narration
     → AlertManager.activate(session_id, block_reason, text)
       → Store ActiveAlert in _active_alerts[session_id]
       → Start _repeat_loop(session_id) as asyncio.Task

2. After ALERT_REPEAT_INTERVAL (30s default):
     → _repeat_loop wakes up
     → Check: alert still active? Yes.
     → Check: repeat_count < ALERT_MAX_REPEATS (5)? Yes.
     → repeat_count++ (now 1)
     → Call _repeat_callback(block_reason, narration_text)
       → TTSEngine._handle_repeat_alert()
         → interrupt → play_alert → synthesize → play_immediate → publish
     → Sleep ALERT_REPEAT_INTERVAL again

3. Repeat up to ALERT_MAX_REPEATS times:
     → After 5 repeats: log "Max alert repeats reached", break loop

4. Resolution (at any point):
     → non-agent_blocked event for same session arrives on EventBus
     → AlertManager._handle_event() detects resolution
     → _clear_alert(session_id)
       → Pop from _active_alerts
       → Cancel repeat_task
       → Log "Alert resolved for session X"
```

**Configuration:**

| Variable | Default | Description |
|---|---|---|
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts (0 = no repeats) |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Maximum number of repeat alerts before stopping |

**Edge cases:**
- `ECHO_ALERT_REPEAT_INTERVAL=0`: Repeat timer is not started, single alert only
- AlertManager stopped before alert resolves: All repeat tasks cancelled in `stop()`
- Repeat callback raises an exception: Caught, logged, loop continues to next repeat
- Two `agent_blocked` events for same session: Second replaces first (old timer cancelled)

---

## Configuration

All new configuration is via environment variables in `echo/config.py`:

| Variable | Default | Description |
|---|---|---|
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts. Set to 0 to disable repeats. |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Maximum number of repeat alerts before the system stops repeating. |

These are additive — all Stage 3 configuration variables remain unchanged.

---

## Health Endpoint

`GET /health` now includes the `alert_active` field:

| Field | Type | Source | Description |
|---|---|---|---|
| `alert_active` | `bool` | `TTSEngine.alert_active` | `True` if any session has an unresolved blocked alert |

This allows the CLI `status` command and external monitoring to detect when the developer's attention is needed.

---

## Graceful Degradation

Stage 4 follows Echo's core design principle of never blocking the pipeline. Each new component degrades independently:

| Condition | Effect | Developer Experience |
|---|---|---|
| TTS disabled (no API key) | Alert tones still cached, AlertManager still tracks | No audio, but `/health` shows `alert_active` |
| No audio output device | Playback disabled, AlertManager still tracks | Remote monitoring via `/health` and SSE |
| AlertManager event_bus is None | AlertManager not created | Stage 3 behavior — single alert, no repeats |
| Repeat callback fails | Exception logged, next repeat attempted | Some repeat alerts may be silent, loop continues |
| Max repeats reached | Repeat timer stops, alert stays "active" | Developer can check `/health` for status |
| Alert cleared during repeat sleep | `_clear_alert()` cancels the task | Clean cancellation, no orphaned tasks |

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Carry block_reason through pipeline | Add nullable field to NarrationEvent | Clean, backward compatible. Avoids TTS subscribing to EventBus directly for block_reason info. |
| AlertManager subscribes to EventBus | Independent subscriber (own queue) | Follows EventBus fan-out pattern. Each subscriber gets its own queue, independent delivery. |
| TTSEngine event_bus param | Optional keyword arg (default None) | Backward compatible. AlertManager simply not created when None. All Stage 3 tests pass without changes. |
| Alert resolution trigger | Any non-agent_blocked event for same session | Simple, conservative. Developer taking any action means the block is resolved. |
| Repeat mechanism | asyncio.Task per session with sleep loop | Clean cancellation via task.cancel(). Max repeats cap prevents infinite loops. |
| Alert tone generation | Separate `alert_tones.py` module | Keeps the original `alert_tone.py` intact. New module composes tones from the same primitives. |
| Refactor `_generate_sine`/`_apply_fade` to public | Rename in `alert_tone.py` | Enables code reuse between `alert_tone.py` and `alert_tones.py` without duplication. |
| Cache all tones at startup | Dict of `BlockReason -> bytes` in AudioPlayer | Avoids generating tones on every alert. All four tones generated once during `start()`. |
| Numbered options for TTS | "Option one: X. Option two: Y." | Spoken ordinals are clearer than "1, 2, 3" for audio. Period-separated for natural TTS pauses. |

---

## Modifications to Stages 1-3

Stage 4 made targeted, backward-compatible changes to existing stages:

| File | Change | Impact |
|---|---|---|
| `echo/interceptors/hook_handler.py` | Added `options` extraction in `_parse_notification()` | Additive — `EchoEvent.options` was already an optional field |
| `echo/summarizer/types.py` | Added `block_reason: BlockReason \| None = None` to `NarrationEvent` | Additive — nullable field, defaults to None |
| `echo/summarizer/template_engine.py` | Richer blocked templates, `_format_options_numbered()`, pass `block_reason` | Enhanced — existing templates improved, new helper added |
| `echo/tts/alert_tone.py` | Renamed `_generate_sine`/`_apply_fade` to public | Non-breaking — all callers within the module already used them |
| `echo/tts/audio_player.py` | `play_alert(block_reason)`, multi-tone caching | Backward compatible — `block_reason` defaults to `None` |
| `echo/tts/tts_engine.py` | `event_bus` kwarg, AlertManager integration, repeat callback | Backward compatible — `event_bus` defaults to `None` |
| `echo/tts/__init__.py` | Added `AlertManager` to re-exports | Additive |
| `echo/config.py` | Added `ALERT_REPEAT_INTERVAL`, `ALERT_MAX_REPEATS` | Additive |
| `echo/server/app.py` | Pass `event_bus=event_bus` to TTSEngine constructor | Additive — enables AlertManager |
| `echo/server/routes.py` | Added `alert_active` to `/health` response | Additive — existing fields unchanged |

**All 442 Stage 1-3 tests continue to pass with zero breakage.**

---

## Test Coverage

**553 total tests (110 Stage 1 + 161 Stage 2 + 171 Stage 3 + 111 Stage 4), all passing**

### New Test Files

| Test File | Tests | Coverage |
|---|---|---|
| `test_alert_tones.py` | 15 | Float32 output for all block reasons, amplitude range, 1D shape, non-empty, permission longer than question, different reasons produce different lengths, custom sample rate, silence gaps in permission tone, None matches default length, PCM16 output, byte length even, consistency with float version |
| `test_alert_manager.py` | 33 | ActiveAlert field storage and defaults, start/stop lifecycle (subscribe/unsubscribe), stop cancels consume task, stop without start is safe, start-stop-restart cycle, activate creates alert, has/get active alert, active alert count, activate replaces existing, multiple sessions independent, tool_executed/agent_message/session_end clears alert, agent_blocked does not clear, event for different session does not clear, clear nonexistent is noop, repeat fires after interval, repeat callback args, max repeats, repeat cancelled on clear, repeat disabled when interval zero, callback exception does not crash, repeat increments count, consume loop exception recovery, stop cancels all repeats, stop clears all alerts, set callback before start, no callback set is noop |

### Modified Test Files

| Test File | New Tests | Coverage |
|---|---|---|
| `test_hook_handler.py` | +6 | Options parsing: notification with options array, notification without options, options passed through to EchoEvent, empty options list, non-list options ignored, options with various block reasons |
| `test_narration_types.py` | +3 | block_reason field: defaults to None, accepts BlockReason value, serialization includes block_reason |
| `test_template_engine.py` | +10 | Enhanced blocked templates: permission with message, question with message, idle template, unknown reason, options with numbered format, options appended to permission, options appended to question, empty options list, single option formatting, many options fallback to digits |
| `test_audio_player.py` | +5 | Block-reason tone selection: play_alert with permission, play_alert with question, play_alert with idle, play_alert with None, unknown reason falls back to default |
| `test_tts_engine.py` | +9 | AlertManager integration: event_bus wiring, alert_active property, _handle_critical passes block_reason to play_alert, _handle_critical activates alert, repeat callback handler, alert_active reflects AlertManager state, no AlertManager when event_bus is None, start/stop includes AlertManager |
| `test_server_tts.py` | +2 | Health endpoint: alert_active field present, alert_active reflects engine state |
| `test_tts_config.py` | +5 | Config: ALERT_REPEAT_INTERVAL default, ALERT_MAX_REPEATS default, ALERT_REPEAT_INTERVAL type, ALERT_MAX_REPEATS type, env var overrides |

All tests mock external dependencies — no real ElevenLabs API calls, no real audio device access, no real LiveKit connections. AlertManager tests use a real `EventBus` with controlled event emission.

---

## What's Next

### Stage 5: STT & Voice Response
- Listen for developer's spoken response via microphone
- Convert speech to text (STT)
- Map spoken response to option, feed back to Claude Code
- Bidirectional audio via LiveKit (infrastructure already in place from Stage 3)
- Integration with AlertManager for alert resolution via voice

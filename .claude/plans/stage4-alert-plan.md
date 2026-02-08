# Echo — Stage 4: Question Detection & Alert — Implementation Plan

**Date:** February 9, 2026
**Status:** Planning
**Depends on:** Stage 3 (TTS) — Complete (442 tests)

---

## Context

Stages 1-3 are complete (442 tests). The CRITICAL priority pipeline already works end-to-end: `agent_blocked` events flush the batcher, produce CRITICAL narration, interrupt playback, play an alert tone, synthesize speech, and play immediately. However, several gaps remain that prevent a complete "alert" experience per the PRD:

1. **Hook handler drops `options`** — `_parse_notification()` reads `type` and `message` but never extracts `options` from Claude Code hook JSON
2. **One alert tone for everything** — no differentiation by block reason (permission vs question vs idle)
3. **No repeat alerts** — if the developer doesn't respond, the system stays silent forever
4. **No alert state tracking** — no way to know if an alert is active or resolved
5. **NarrationEvent lacks `block_reason`** — TTS engine can't select the right tone because that metadata is lost in the pipeline

**What's NOT in scope (deferred):**
- Desktop notifications (separate concern, not in PRD Stage 4)
- Transcript watcher block detection (hooks cover it)
- Voice response to unblock agent (Stage 5)

---

## Architecture

```
EchoEvent (agent_blocked, with options + block_reason)
    │
    ▼
EventBus.emit()
    │
    ├── [Subscriber 1] Summarizer._handle_agent_blocked()
    │     → TemplateEngine.render() → NarrationEvent (now includes block_reason)
    │     → NarrationBus.emit()
    │         → TTSEngine._handle_critical()
    │             → Select alert tone by block_reason
    │             → AudioPlayer.play_alert(block_reason)
    │             → ElevenLabs synthesize + play + publish
    │             → AlertManager.activate(session_id, block_reason, text)
    │
    └── [Subscriber 2] AlertManager._consume_loop()
          → Sees agent_blocked: (no-op, activation handled by TTSEngine)
          → After ALERT_REPEAT_INTERVAL: re-play alert + narration
          → Sees non-blocked event for same session: clear alert
```

### Alert Resolution

Any non-`agent_blocked` event for the same session clears the alert:
```
tool_executed / agent_message / session_end for session X
  → AlertManager._handle_event()
    → Clear active alert, cancel repeat timer
```

---

## Component Design

### 1. Hook Handler Fix — Parse `options`

**File:** `echo/interceptors/hook_handler.py`

Add one line to `_parse_notification()` to extract `options` and pass it to `EchoEvent`:
```python
options: list[str] | None = raw.get("options")
# ... pass options=options to EchoEvent constructor
```

### 2. NarrationEvent Extension — Add `block_reason`

**File:** `echo/summarizer/types.py`

Add optional field (backward compatible, defaults to `None`):
```python
block_reason: BlockReason | None = None
```

### 3. Template Engine Enhancements

**File:** `echo/summarizer/template_engine.py`

- **Pass `block_reason` through** in `render()` → `NarrationEvent(..., block_reason=event.block_reason)`
- **Richer narration templates** per PRD example:
  - PERMISSION: "The agent needs your permission and is waiting for your answer. It's asking: {message}"
  - QUESTION: "The agent has a question and is waiting for your answer. It's asking: {message}"
  - IDLE: "The agent is idle and waiting for your input."
- **New `_format_options_numbered()` helper** for TTS-friendly output:
  - "Option one: RS256. Option two: HS256." (spoken ordinals, period-separated)
- Keep existing `_format_options()` for backward compatibility

### 4. Alert Tone Variants (NEW)

**File:** `echo/tts/alert_tones.py` (new)

Per-block-reason tone generation using the existing `alert_tone.py` sine wave pattern:

| BlockReason | Pattern | Duration | Character |
|---|---|---|---|
| PERMISSION_PROMPT | 880Hz→1320Hz repeated twice | ~600ms | Urgent double-beep |
| QUESTION | 660Hz→880Hz single rise | ~350ms | Softer notification |
| IDLE_PROMPT | 440Hz→550Hz gentle | ~400ms | Gentle reminder |
| None (default) | 880Hz→1320Hz (existing) | ~350ms | Standard alert |

Refactor: rename `_generate_sine`/`_apply_fade` in `alert_tone.py` to public (remove underscore) so `alert_tones.py` can reuse them.

### 5. AlertManager (NEW)

**File:** `echo/tts/alert_manager.py` (new)

Core new component — tracks active alerts and manages repeat timers:

- **`ActiveAlert`** dataclass: `session_id`, `block_reason`, `narration_text`, `created_at`, `repeat_count`, `repeat_task`
- **`AlertManager(event_bus)`**: subscribes to EventBus to detect alert resolution
  - `activate(session_id, block_reason, text)` — called by TTSEngine after initial alert
  - `_consume_loop()` — watches for non-blocked events → clears alerts
  - `_repeat_loop(session_id)` — fires repeat after `ALERT_REPEAT_INTERVAL` seconds, up to `ALERT_MAX_REPEATS`
  - `set_repeat_callback(callback)` — TTSEngine provides `async callback(block_reason, text)` for re-play
  - Properties: `active_alert_count`, `has_active_alert(session_id)`, `get_active_alert(session_id)`
- Second `activate()` for same session replaces previous alert (cancels old repeat timer)

### 6. AudioPlayer Modifications

**File:** `echo/tts/audio_player.py`

- Modify `play_alert()` signature: `play_alert(block_reason: BlockReason | None = None)`
- Cache alert tones for all block reasons at startup (one `generate_alert_for_reason()` call each)
- Select correct cached tone based on `block_reason` parameter

### 7. TTSEngine Modifications

**File:** `echo/tts/tts_engine.py`

- Constructor: add `event_bus: EventBus | None = None` keyword param (backward compatible)
- Create `AlertManager(event_bus)` when event_bus provided
- `_handle_critical()`: pass `narration.block_reason` to `play_alert()`, call `alert_manager.activate()` after playback
- New `_handle_repeat_alert(block_reason, text)` callback for AlertManager repeats (interrupt → alert → synthesize → play)
- New property: `alert_active -> bool`
- Start/stop: include AlertManager lifecycle

### 8. Server Integration

**Files:** `echo/server/app.py`, `echo/server/routes.py`

- `app.py`: pass `event_bus=event_bus` to TTSEngine constructor
- `routes.py`: add `"alert_active": tts_engine.alert_active` to `/health` response

---

## Configuration (new env vars)

| Variable | Default | Description |
|---|---|---|
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts (0 = disabled) |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Max repeat alerts before stopping |

---

## File Summary

### New Files (3 source + 3 test)

| File | Purpose |
|---|---|
| `echo/tts/alert_tones.py` | Per-block-reason alert tone generation |
| `echo/tts/alert_manager.py` | Alert state tracking + repeat timer |
| `tests/test_alert_tones.py` | ~15 tests |
| `tests/test_alert_manager.py` | ~30 tests |
| `tests/test_stage4_integration.py` | ~12 tests |

### Modified Files (9 source + 7 test)

| File | Changes |
|---|---|
| `echo/interceptors/hook_handler.py` | Parse `options` from notification JSON |
| `echo/summarizer/types.py` | Add `block_reason` to NarrationEvent |
| `echo/summarizer/template_engine.py` | Pass block_reason, enhance templates, add `_format_options_numbered()` |
| `echo/tts/alert_tone.py` | Rename `_generate_sine`/`_apply_fade` to public |
| `echo/tts/audio_player.py` | `play_alert(block_reason)`, cache multiple tones |
| `echo/tts/tts_engine.py` | Accept event_bus, wire AlertManager, tone selection, repeat callback |
| `echo/tts/__init__.py` | Re-export AlertManager |
| `echo/config.py` | Add ALERT_REPEAT_INTERVAL, ALERT_MAX_REPEATS |
| `echo/server/app.py` | Pass event_bus to TTSEngine |
| `echo/server/routes.py` | Add alert_active to /health |
| `tests/test_hook_handler.py` | +6 tests (options parsing) |
| `tests/test_narration_types.py` | +3 tests (block_reason field) |
| `tests/test_template_engine.py` | +10 tests (enhanced templates, numbered options) |
| `tests/test_audio_player.py` | +5 tests (block_reason tone selection) |
| `tests/test_tts_engine.py` | +10 tests (AlertManager wiring, tone routing) |
| `tests/test_server_tts.py` | +2 tests (alert_active in health) |
| `tests/test_tts_config.py` | +4 tests (new config vars) |

**Estimated: ~97 new tests → ~539 total**

---

## Task Breakdown & Waves

### Wave 1 (parallel — no dependencies)

| Task | Files | Tests |
|---|---|---|
| **T1: Config** | `echo/config.py` | `test_tts_config.py` (+4) |
| **T2: Hook handler fix** | `echo/interceptors/hook_handler.py` | `test_hook_handler.py` (+6) |
| **T3: NarrationEvent extension** | `echo/summarizer/types.py` | `test_narration_types.py` (+3) |

### Wave 2 (parallel — depends on Wave 1)

| Task | Files | Tests |
|---|---|---|
| **T4: Alert tone variants** | `echo/tts/alert_tone.py` (refactor), `echo/tts/alert_tones.py` (new) | `test_alert_tones.py` (~15) |
| **T5: Template enhancements** | `echo/summarizer/template_engine.py` | `test_template_engine.py` (+10) |

### Wave 3 (parallel — depends on Waves 1-2)

| Task | Files | Tests |
|---|---|---|
| **T6: AudioPlayer mods** | `echo/tts/audio_player.py` | `test_audio_player.py` (+5) |
| **T7: AlertManager** | `echo/tts/alert_manager.py` (new) | `test_alert_manager.py` (~30) |

### Wave 4 (depends on Waves 1-3)

| Task | Files | Tests |
|---|---|---|
| **T8: TTSEngine mods** | `echo/tts/tts_engine.py`, `echo/tts/__init__.py` | `test_tts_engine.py` (+10) |

### Wave 5 (depends on Wave 4)

| Task | Files | Tests |
|---|---|---|
| **T9: Server integration** | `echo/server/app.py`, `echo/server/routes.py` | `test_server_tts.py` (+2) |

### Wave 6 (depends on Wave 5)

| Task | Files | Tests |
|---|---|---|
| **T10: Integration tests** | `tests/test_stage4_integration.py` (new) | ~12 tests |

### Wave 7 (depends on Wave 6)

| Task | Files | Tests |
|---|---|---|
| **T11: Full test run + docs** | `docs/stage4-alert-implementation.md`, update `CLAUDE.md` | pytest: ~539 pass |

```
Wave 1 (parallel):  T1  |  T2  |  T3
Wave 2 (parallel):  T4  |  T5
Wave 3 (parallel):  T6  |  T7
Wave 4:             T8
Wave 5:             T9
Wave 6:             T10
Wave 7:             T11
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Carry block_reason through pipeline | Add to NarrationEvent | Clean, backward compatible (nullable). Avoids TTS subscribing to EventBus directly. |
| AlertManager subscribes to EventBus | Independent subscriber | Follows EventBus fan-out pattern. Each subscriber gets its own queue. |
| TTSEngine event_bus param | Optional keyword arg (default None) | Backward compatible. AlertManager simply not started when None. |
| Alert resolution trigger | Any non-agent_blocked event for same session | Simple, conservative. Can add event-type filter later if needed. |
| Repeat mechanism | asyncio.Task per session with sleep loop | Clean cancellation via task.cancel(). Max repeats cap prevents infinite loops. |
| Desktop notifications | Deferred | Not in PRD Stage 4. Can add independently without touching pipeline. |

---

## Verification

1. **Unit tests**: `pytest` — all ~539 tests pass (442 existing + ~97 new)
2. **Regression**: Zero failures on existing 442 tests
3. **Manual test (with API key)**:
   ```bash
   export ECHO_ELEVENLABS_API_KEY="sk-..."
   echo-copilot start

   # Permission prompt with options
   curl -X POST localhost:7865/event \
     -H "Content-Type: application/json" \
     -d '{"hook_event_name":"Notification","session_id":"test-1","type":"permission_prompt","message":"Allow running: rm -rf /tmp/test?","options":["yes","no"]}'
   # Expect: urgent double-beep + "The agent needs your permission..."

   # Wait 30s — expect repeat alert

   # Resolve by sending tool_executed for same session
   curl -X POST localhost:7865/event \
     -H "Content-Type: application/json" \
     -d '{"hook_event_name":"PostToolUse","session_id":"test-1","tool_name":"Bash","tool_input":{"command":"ls"}}'
   # Expect: alert cleared, no more repeats
   ```
4. **Health check**: `curl localhost:7865/health` → `"alert_active": true/false`
5. **Different tones**: Send question vs permission vs idle → hear distinct tones
6. **Degradation**: No API key → alerts tracked but no audio, no crash
7. **Repeat disabled**: `ECHO_ALERT_REPEAT_INTERVAL=0` → no repeats

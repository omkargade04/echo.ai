# Echo — Stage 3: TTS — Implementation Plan

**Date:** February 8, 2026
**Status:** Planning
**Depends on:** Stage 2 (Filter & Summarize) — Complete (161 tests)

---

## Context

Stages 1 (Intercept) and 2 (Filter & Summarize) are complete with 271 passing tests. The NarrationBus emits `NarrationEvent` objects with `.text` ready for TTS, `.priority` for playback scheduling, and `.session_id` for tracking. Stage 3 subscribes to this bus and converts narration text to audible speech so the developer can monitor their agent without watching the screen.

**User decisions:**
- **LiveKit from the start** — full real-time audio infrastructure, using LiveKit Cloud
- **Direct system audio** — plays through system speakers via sounddevice (not browser-based)
- **Skip narration** when ElevenLabs is unavailable (no local TTS fallback)
- **numpy** acceptable as a dependency

---

## Architecture Overview

```
NarrationBus (existing EventBus[NarrationEvent])
         │
         ▼
┌─────────────────────────────────────────────────┐
│  TTSEngine (orchestrator)                       │
│  Subscribes to NarrationBus, routes by priority │
│                                                  │
│  ┌──────────────────┐  ┌────────────────────┐   │
│  │ ElevenLabsClient │  │   AudioPlayer      │   │
│  │ (speech synth)   │  │ (local playback)   │   │
│  │                  │  │                    │   │
│  │ - httpx POST     │  │ - PriorityQueue   │   │
│  │ - health check   │  │ - interrupt logic │   │
│  │ - PCM output     │  │ - alert tone      │   │
│  └──────────────────┘  │ - sounddevice     │   │
│                         └────────────────────┘   │
│  ┌──────────────────────────────────────────┐   │
│  │ LiveKitPublisher (optional, parallel)    │   │
│  │                                           │   │
│  │ - Connects to LiveKit Cloud room          │   │
│  │ - Publishes audio tracks                  │   │
│  │ - Ready for Stage 5 bidirectional audio   │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### Data Flow

```
NarrationEvent arrives on NarrationBus
  → TTSEngine._consume_loop() pulls from subscriber queue
    → Route by priority:
      │
      ├── CRITICAL:
      │     1. AudioPlayer.interrupt() — stop current playback, clear queue
      │     2. AudioPlayer.play_alert() — play two-tone alert (~350ms)
      │     3. ElevenLabsClient.synthesize(text) → PCM bytes
      │     4. AudioPlayer.play_immediate(pcm_bytes) → speakers
      │     5. LiveKitPublisher.publish(pcm_bytes) (if connected)
      │
      ├── NORMAL:
      │     1. ElevenLabsClient.synthesize(text) → PCM bytes
      │     2. AudioPlayer.enqueue(pcm_bytes, priority=NORMAL)
      │     3. LiveKitPublisher.publish(pcm_bytes) (if connected)
      │
      └── LOW:
            1. Check AudioPlayer.queue_depth > BACKLOG_THRESHOLD?
               → Yes: log + skip
               → No: synthesize + enqueue(priority=LOW)
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| TTS provider | ElevenLabs via httpx (not official SDK) | Matches LLMSummarizer pattern. Official SDK is heavy. httpx already a dependency. |
| ElevenLabs API | Standard (non-streaming) | Narration texts are <30 words. Streaming adds complexity for no gain at this length. |
| Audio output format | PCM 16kHz 16-bit mono | Request directly from ElevenLabs (`output_format=pcm_16000`). No decoding library needed. |
| Local playback | sounddevice (PortAudio) | Cross-platform, async-compatible, plays raw PCM directly. Zero network overhead. |
| LiveKit | LiveKit Cloud + `livekit` SDK | Publish audio tracks to a room. Ready for Stage 5 bidirectional audio. Optional — disabled if unconfigured. |
| Alert tone | Generated programmatically (numpy) | Two-tone sine wave (880Hz + 1320Hz, ~350ms). Generated once at startup, cached. No bundled assets. |
| ElevenLabs model | `eleven_turbo_v2_5` | Lowest latency model. Optimized for real-time. |
| ElevenLabs voice | Rachel (`21m00Tcm4TlvDq8ikWAM`) | Clear, professional. Configurable via env var. |
| Fallback when unavailable | Skip narration, log warning | Developer still has SSE `/narrations` for text. No local TTS fallback. |
| Backlog handling | Drop LOW when queue > 3 items | Prevents accumulation during rapid events. |

---

## Component Design

### 1. `ElevenLabsClient` — Speech Synthesis

Follows the **exact same pattern** as `echo/summarizer/llm_summarizer.py`:

- `start()`: Create `httpx.AsyncClient` with API key header, health check via `GET /v1/user`
- `stop()`: Close httpx client
- `synthesize(text) → bytes | None`: `POST /v1/text-to-speech/{voice_id}` with `output_format=pcm_16000`, returns raw PCM bytes or `None` on failure
- `_check_health()` + `_maybe_recheck_health()`: Periodic re-check (60s interval)
- No API key → `is_available = False` at startup, TTS disabled

### 2. `AudioPlayer` — Local Playback with Priority Queue

- `asyncio.PriorityQueue` with items `(priority_int, sequence_counter, pcm_bytes)`
- Single playback worker task pulls from queue, plays via `sounddevice.play()`
- **Interrupt mechanism**: `asyncio.Event` flag; on interrupt, calls `sd.stop()` and drains non-critical queue items
- **Alert tone**: Pre-generated numpy array (880Hz 150ms + 50ms gap + 1320Hz 150ms), cached at startup
- **No audio device**: `sd.query_devices(kind="output")` check at startup; gracefully disabled
- Backlog threshold: 3 items

### 3. `LiveKitPublisher` — Room Audio Publishing

- `start()`: Connect to LiveKit Cloud room as a participant using `livekit` SDK, create audio track
- `stop()`: Disconnect from room, clean up
- `publish(pcm_bytes)`: Publish audio frame to the room's audio track
- **Optional**: If `LIVEKIT_URL` or credentials not configured, disabled with a log message
- Periodic reconnect on disconnect

### 4. `TTSEngine` — Core Orchestrator

Follows the **exact same lifecycle pattern** as `echo/summarizer/summarizer.py`:

- `__init__(narration_bus)`: Creates ElevenLabsClient, AudioPlayer, LiveKitPublisher
- `start()`: Start all three sub-components, subscribe to narration_bus, launch `_consume_loop` task
- `stop()`: Cancel task, unsubscribe, stop sub-components (reverse order)
- `_consume_loop()`: `while True: event = await queue.get()` with try/except (never crash)
- `_process_narration()`: Routes by priority (CRITICAL/NORMAL/LOW)
- Properties: `tts_available`, `audio_available`, `livekit_connected`, `state` (TTSState enum)

---

## Configuration (new env vars in `echo/config.py`)

| Variable | Default | Description |
|---|---|---|
| `ECHO_ELEVENLABS_API_KEY` | `""` (empty = TTS disabled) | ElevenLabs API key |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID (Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model |
| `ECHO_TTS_TIMEOUT` | `10.0` | Synthesis request timeout (sec) |
| `LIVEKIT_URL` | `""` (empty = disabled) | LiveKit Cloud server URL |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |

---

## New Dependencies (`pyproject.toml`)

```toml
"sounddevice>=0.4.6",    # Cross-platform audio playback via PortAudio
"numpy>=1.24",           # Alert tone generation + PCM array handling
"livekit>=0.11",         # LiveKit real-time audio SDK
```

**Not adding**: `elevenlabs` SDK (too heavy), `pydub`/`miniaudio` (PCM direct from API), `pyttsx3` (no local fallback).

---

## File Structure

### New Files (9 source + 7 test)

```
echo/tts/                          # NEW — Stage 3 module
├── __init__.py                   # Re-exports TTSEngine, TTSState
├── types.py                      # TTSState enum
├── elevenlabs_client.py          # ElevenLabs HTTP client
├── audio_player.py               # Priority queue playback + interrupt
├── alert_tone.py                 # generate_alert_tone() pure function
├── livekit_publisher.py          # LiveKit room audio publishing
└── tts_engine.py                 # Core orchestrator

tests/
├── test_tts_types.py             # ~8 tests
├── test_elevenlabs_client.py     # ~25 tests
├── test_audio_player.py          # ~30 tests
├── test_alert_tone.py            # ~8 tests
├── test_livekit_publisher.py     # ~15 tests
├── test_tts_engine.py            # ~35 tests
└── test_server_tts.py            # ~12 tests
```

### Modified Files (4)

| File | Changes |
|---|---|
| `echo/config.py` | Add ElevenLabs + LiveKit + audio config constants |
| `echo/server/app.py` | Create TTSEngine singleton, wire into lifespan start/stop |
| `echo/server/routes.py` | Update `/health` with `tts_state`, `tts_available`, `audio_available`, `livekit_connected` |
| `echo/cli.py` | Add `--no-tts` flag to `start` command |

**Estimated: ~133 new tests** across 7 test files

---

## Server Integration

### `app.py` — Lifespan wiring

- New singleton: `tts_engine = TTSEngine(narration_bus=narration_bus)`
- Start order: `transcript_watcher → summarizer → tts_engine`
- Stop order (reverse): `tts_engine → summarizer → transcript_watcher`
- Attach to `app.state.tts_engine`

### `routes.py` — Health endpoint additions

```python
"tts_state": tts_engine.state.value,           # "active" / "degraded" / "disabled"
"tts_available": tts_engine.tts_available,      # ElevenLabs reachable
"audio_available": tts_engine.audio_available,   # audio device present
"livekit_connected": tts_engine.livekit_connected  # LiveKit room joined
```

### `cli.py` — `--no-tts` flag

Sets `ECHO_ELEVENLABS_API_KEY=""` env var to force TTS disabled mode.

---

## Priority/Interruption Algorithm

```
IDLE ──→ PLAYING (normal/low) ──→ IDLE
              │
              │ CRITICAL arrives
              ▼
         INTERRUPTING
              │ 1. sd.stop() immediately
              │ 2. Drain non-critical queue items
              │ 3. Play alert tone (~350ms)
              │ 4. Synthesize critical text
              │ 5. Play critical narration
              ▼
         PLAYING (critical) ──→ IDLE
```

**Guarantees:**
1. CRITICAL never delayed by normal queue items
2. Current playback interrupts within one audio buffer (~10ms)
3. Alert tone always plays before critical narration text
4. LOW dropped when queue > 3 items
5. NORMAL plays FIFO within priority level

---

## Task Breakdown

### Task 1: Types + config (no deps)
**Files:** `echo/tts/__init__.py`, `echo/tts/types.py`, modify `echo/config.py`
**Tests:** `tests/test_tts_types.py` (~8 tests)

### Task 2: Alert tone generator (no deps)
**Files:** `echo/tts/alert_tone.py`
**Tests:** `tests/test_alert_tone.py` (~8 tests)

### Task 3: ElevenLabs client (depends on Task 1)
**Files:** `echo/tts/elevenlabs_client.py`
**Tests:** `tests/test_elevenlabs_client.py` (~25 tests)

### Task 4: Audio player (depends on Tasks 1, 2)
**Files:** `echo/tts/audio_player.py`
**Tests:** `tests/test_audio_player.py` (~30 tests)

### Task 5: LiveKit publisher (depends on Task 1)
**Files:** `echo/tts/livekit_publisher.py`
**Tests:** `tests/test_livekit_publisher.py` (~15 tests)

### Task 6: TTS engine (depends on Tasks 3, 4, 5)
**Files:** `echo/tts/tts_engine.py`
**Tests:** `tests/test_tts_engine.py` (~35 tests)

### Task 7: Server integration (depends on Task 6)
**Files:** modify `echo/server/app.py`, `echo/server/routes.py`, `echo/cli.py`
**Tests:** `tests/test_server_tts.py` (~12 tests)

### Task 8: Dependencies + full test run
**Files:** modify `pyproject.toml`
Run full suite: 271 existing + ~133 new = ~404 tests, zero regressions

### Task 9: Documentation
**Files:** `docs/stage3-tts-implementation.md`, update `CLAUDE.md`, update `README.md`

### Parallelization

```
Wave 1 (parallel): Tasks 1, 2
Wave 2 (parallel): Tasks 3, 4, 5
Wave 3:            Task 6
Wave 4:            Task 7
Wave 5:            Task 8
Wave 6:            Task 9
```

---

## Verification

1. **Unit tests**: `pytest` — all ~404 tests pass
2. **Integration** (manual, with API key):
   ```bash
   export ECHO_ELEVENLABS_API_KEY="sk-..."
   echo-copilot start
   curl -X POST localhost:7865/event \
     -H "Content-Type: application/json" \
     -d '{"hook_event_name":"PostToolUse","session_id":"test","tool_name":"Bash","tool_input":{"command":"npm test"}}'
   # Should hear "Ran command: npm test" through speakers
   ```
3. **CRITICAL test**: Send agent_blocked event → hear alert tone then narration
4. **Degradation test**: Unset API key → TTS disabled, pipeline continues
5. **LiveKit test**: Configure LiveKit Cloud credentials → audio published to room

# Stage 3: TTS — Implementation Document

**Date:** February 8, 2026
**Status:** Complete
**Version:** 0.1.0
**Depends on:** Stage 2 (Filter & Summarize) — Complete (161 tests)

---

## Overview

Stage 3 implements the **text-to-speech layer** for Echo. It subscribes to the Stage 2 NarrationBus, converts narration text into speech via ElevenLabs, plays the audio through the system speakers via sounddevice, and optionally publishes to a LiveKit Cloud room for remote listeners. When ElevenLabs is unavailable, TTS is silently disabled — the pipeline continues and narrations remain available via the SSE `/narrations` endpoint.

### Key Capability

```
NarrationEvent (text)                  Audio Output
─────────────────────                  ─────────────────────────
"Ran command: npm test"            →   Spoken through speakers (+ LiveKit room)
"The agent needs permission."      →   Alert tone → spoken immediately (interrupt)
"New coding session started."      →   Spoken if no backlog (or skipped)
```

### Three Output Modes

- **Local playback** (sounddevice): PCM audio through system speakers via a priority queue with interrupt support
- **Remote streaming** (LiveKit): Parallel audio publishing to a LiveKit Cloud room for remote listeners
- **Disabled**: No API key or no audio device — narrations logged and available via SSE only

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Stage 2 (existing)                                          │
│                                                               │
│  Summarizer → NarrationBus → NarrationEvent                  │
│                  (text, priority, source_event_type)          │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Stage 3 (new)                                               │
│                                                               │
│  TTSEngine (subscribes to NarrationBus)                      │
│    │                                                          │
│    ├── Routes by NarrationPriority:                           │
│    │     CRITICAL → interrupt + alert + immediate playback    │
│    │     NORMAL   → synthesize + enqueue                      │
│    │     LOW      → check backlog, synthesize + enqueue       │
│    │                                                          │
│    ├── ElevenLabsClient (speech synthesis)                    │
│    │     POST /v1/text-to-speech/{voice_id}                   │
│    │     PCM 16kHz 16-bit mono output                         │
│    │     Health check via GET /v1/user                        │
│    │                                                          │
│    ├── AudioPlayer (local playback)                           │
│    │     asyncio.PriorityQueue → sounddevice                  │
│    │     Interrupt mechanism for CRITICAL events              │
│    │     Pre-generated alert tone (880Hz + 1320Hz)            │
│    │                                                          │
│    └── LiveKitPublisher (remote streaming, optional)          │
│          Connect to LiveKit Cloud room as participant          │
│          Publish AudioFrames via audio track                   │
│                                                               │
│  Modified endpoints:                                          │
│    GET /health — now includes tts_state, tts_available,       │
│                  audio_available, livekit_connected            │
│  Modified CLI:                                                │
│    echo-copilot start --no-tts                                │
└──────────────────────────────────────────────────────────────┘
```

### Data Flow

```
NarrationEvent arrives on NarrationBus
  → TTSEngine._consume_loop() pulls from its subscriber queue
    → _process_narration() routes by priority:
      ├── CRITICAL:
      │     1. AudioPlayer.interrupt() — stop current playback, drain non-critical queue
      │     2. AudioPlayer.play_alert() — two-tone alert (~350ms)
      │     3. ElevenLabsClient.synthesize(text) → PCM bytes
      │     4. AudioPlayer.play_immediate(pcm) → speakers
      │     5. LiveKitPublisher.publish(pcm) (if connected)
      │
      ├── NORMAL:
      │     1. ElevenLabsClient.synthesize(text) → PCM bytes
      │     2. AudioPlayer.enqueue(pcm, priority=1)
      │     3. LiveKitPublisher.publish(pcm) (if connected)
      │
      └── LOW:
            1. Check AudioPlayer.queue_depth > BACKLOG_THRESHOLD (3)?
               → Yes: log warning + skip entirely (no synthesis call)
               → No:
                 2. ElevenLabsClient.synthesize(text) → PCM bytes
                 3. AudioPlayer.enqueue(pcm, priority=2)
                 4. LiveKitPublisher.publish(pcm) (if connected)
```

---

## Priority System

Stage 3 implements the priority scheduling defined by Stage 2's `NarrationPriority` enum:

| Priority | Int Value | Behavior | Event Types |
|---|---|---|---|
| `CRITICAL` | 0 | Interrupt current playback, play alert tone, synthesize, play immediately | `agent_blocked` |
| `NORMAL` | 1 | Synthesize, enqueue for ordered playback | `tool_executed`, `agent_message`, `agent_stopped` |
| `LOW` | 2 | Drop if backlog > 3 items, otherwise synthesize + enqueue | `session_start`, `session_end` |

### Interrupt Algorithm

```
IDLE ──→ PLAYING (normal/low) ──→ IDLE
              │
              │ CRITICAL arrives
              ▼
         INTERRUPTING
              │ 1. sd.stop() immediately
              │ 2. Drain non-critical queue items (keep critical)
              │ 3. Play alert tone (~350ms)
              │ 4. Synthesize critical text via ElevenLabs
              │ 5. Play critical narration immediately
              ▼
         PLAYING (critical) ──→ IDLE
```

**Guarantees:**
1. CRITICAL never delayed by normal queue items
2. Current playback interrupts within one audio buffer (~10ms)
3. Alert tone always plays before critical narration text
4. LOW dropped when queue > 3 items (before synthesis, saving API calls)
5. NORMAL plays FIFO within priority level (via monotonic sequence counter)

---

## File Structure

```
echo-copilot/
├── echo/
│   ├── tts/                              # NEW — Stage 3 module
│   │   ├── __init__.py                   # Re-exports: TTSEngine, TTSState, AudioPlayer
│   │   ├── types.py                      # TTSState enum (active/degraded/disabled)
│   │   ├── elevenlabs_client.py          # ElevenLabs HTTP client for speech synthesis
│   │   ├── audio_player.py              # Priority-queued local audio playback via sounddevice
│   │   ├── alert_tone.py                # Programmatic two-tone alert generation (numpy)
│   │   ├── livekit_publisher.py         # LiveKit Cloud room audio publishing
│   │   └── tts_engine.py               # Core orchestrator: subscribes to NarrationBus
│   ├── server/
│   │   ├── app.py                        # MODIFIED — wires TTSEngine into lifespan
│   │   └── routes.py                     # MODIFIED — /health includes TTS fields
│   ├── cli.py                            # MODIFIED — added --no-tts flag
│   └── config.py                         # MODIFIED — ElevenLabs + LiveKit + audio constants
├── tests/
│   ├── conftest.py                       # MODIFIED — added tts_engine fixture, updated app fixture
│   ├── test_tts_types.py                # 9 tests — TTSState enum
│   ├── test_tts_config.py              # 24 tests — TTS config defaults, types, env overrides
│   ├── test_alert_tone.py              # 10 tests — alert tone generation, PCM16 conversion
│   ├── test_elevenlabs_client.py       # 28 tests — startup, synthesis, health check, config
│   ├── test_audio_player.py            # 30 tests — startup, enqueue, interrupt, playback, edge cases
│   ├── test_livekit_publisher.py       # 17 tests — config, startup, publishing, SDK unavailable
│   ├── test_tts_engine.py             # 39 tests — lifecycle, state, priority routing, consume loop
│   └── test_server_tts.py            # 14 tests — health TTS fields, CLI --no-tts, app integration
└── docs/
    └── stage3-tts-implementation.md    # This document
```

**Stage 3 totals: 7 new source files, 5 modified files, 8 test files, 171 new tests**

---

## Component Details

### 1. TTSState Enum (`echo/tts/types.py`)

Operational state of the TTS subsystem:

| State | Value | Meaning |
|---|---|---|
| `ACTIVE` | `"active"` | ElevenLabs available AND audio device present |
| `DEGRADED` | `"degraded"` | One of ElevenLabs or audio device available, but not both |
| `DISABLED` | `"disabled"` | Neither ElevenLabs nor audio device available |

### 2. ElevenLabs Client (`echo/tts/elevenlabs_client.py`)

httpx-based HTTP client for ElevenLabs text-to-speech API, following the exact same lifecycle pattern as `LLMSummarizer`:

**Lifecycle:**
- `start()`: Create `httpx.AsyncClient` with API key header (`xi-api-key`), run initial health check via `GET /v1/user`
- `stop()`: Close httpx client
- No API key at startup: `is_available = False`, client not created, TTS silently disabled

**Synthesis:**
- `synthesize(text) -> bytes | None`: `POST /v1/text-to-speech/{voice_id}` with `output_format=pcm_16000`
- Request body: `{"text": text, "model_id": TTS_MODEL}`
- Returns raw PCM 16kHz 16-bit mono bytes on success, `None` on any failure
- All HTTP errors caught and logged — never raises

**Health checking:**
- On startup, pings `GET /v1/user` to validate the API key
- If unavailable, sets `_available = False` and logs a warning
- Periodically re-checks every 60s (`TTS_HEALTH_CHECK_INTERVAL`) when currently unavailable
- Re-check only happens when currently unavailable (no unnecessary pings when healthy)

**Configuration:**

| Constant | Env Var | Default |
|---|---|---|
| `ELEVENLABS_API_KEY` | `ECHO_ELEVENLABS_API_KEY` | `""` (empty = TTS disabled) |
| `ELEVENLABS_BASE_URL` | `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` |
| `TTS_VOICE_ID` | `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` (Rachel) |
| `TTS_MODEL` | `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` |
| `TTS_TIMEOUT` | `ECHO_TTS_TIMEOUT` | `10.0` seconds |
| `TTS_HEALTH_CHECK_INTERVAL` | `ECHO_TTS_HEALTH_CHECK_INTERVAL` | `60.0` seconds |

### 3. Audio Player (`echo/tts/audio_player.py`)

Priority-queued local audio playback via sounddevice with interrupt support:

**Queue design:**
- `asyncio.PriorityQueue` with items `(priority_int, sequence_counter, pcm_bytes)`
- Priority mapping: 0 = CRITICAL, 1 = NORMAL, 2 = LOW
- Within same priority, FIFO via monotonically increasing sequence counter
- Single background worker task pulls from queue, plays via `sounddevice.play()`

**Interrupt mechanism:**
- `interrupt()` sets an `asyncio.Event`, calls `sd.stop()`, drains non-critical items from queue (re-enqueues critical items)
- Worker checks interrupt flag before playing non-critical items, discards them during interrupt
- `play_immediate(pcm)` bypasses the queue entirely for CRITICAL playback

**Alert tone:**
- Pre-generated at startup via `generate_alert_tone()`, cached as numpy array
- `play_alert()` converts to int16 PCM, plays via `asyncio.to_thread`

**Playback:**
- `_play_sync(pcm_bytes)` runs in a thread via `asyncio.to_thread`
- Converts int16 PCM to float32 (divide by 32768), plays via `sd.play()` + `sd.wait()`

**Backlog shedding:**
- `enqueue()` drops LOW-priority items (priority 2) when `queue_depth > AUDIO_BACKLOG_THRESHOLD` (default 3)
- CRITICAL items (priority 0) are always enqueued regardless of backlog

**Graceful degradation:**
- `start()` probes for an output device via `sd.query_devices(kind="output")`
- No device: `is_available = False`, all playback methods become no-ops (no errors)

### 4. Alert Tone Generator (`echo/tts/alert_tone.py`)

Pure-function alert tone generation using numpy:

- `generate_alert_tone(sample_rate=16000) -> np.ndarray`: Returns float32 array in [-1.0, 1.0]
- Structure: 880 Hz for 150ms, 50ms silence, 1320 Hz for 150ms (total ~350ms)
- Linear fade-in/fade-out (5ms) on each tone to prevent clicks
- `generate_alert_tone_pcm16(sample_rate=16000) -> bytes`: Convenience wrapper returning int16 PCM bytes
- Generated once at `AudioPlayer.start()`, cached for reuse

### 5. LiveKit Publisher (`echo/tts/livekit_publisher.py`)

Optional LiveKit Cloud room audio publisher for remote listeners:

**Lifecycle:**
- `start()`: Generate JWT access token, connect to LiveKit room as `echo-server` participant, create and publish audio track
- `stop()`: Disconnect from room, release resources
- Not configured (missing URL or credentials or SDK): silently disabled with a log message

**Publishing:**
- `publish(pcm_bytes)`: Converts PCM16 bytes to a `rtc.AudioFrame`, pushes via `AudioSource.capture_frame()`
- All errors caught and logged — never raises

**SDK handling:**
- LiveKit SDK imported with `try/except ImportError`
- `LIVEKIT_SDK_AVAILABLE` flag controls whether the publisher can be configured
- All four conditions required for `is_configured`: URL, API key, API secret, SDK available

**Configuration:**

| Constant | Env Var | Default |
|---|---|---|
| `LIVEKIT_URL` | `LIVEKIT_URL` | `""` (empty = disabled) |
| `LIVEKIT_API_KEY` | `LIVEKIT_API_KEY` | `""` |
| `LIVEKIT_API_SECRET` | `LIVEKIT_API_SECRET` | `""` |

### 6. TTS Engine (`echo/tts/tts_engine.py`)

Core async orchestrator that ties all Stage 3 components together, following the exact same lifecycle pattern as `Summarizer`:

**Lifecycle:**
```
TTSEngine(narration_bus)
  → start()
    → ElevenLabsClient.start() (create httpx client, health check)
    → AudioPlayer.start() (probe device, cache alert tone, start worker)
    → LiveKitPublisher.start() (connect to room if configured)
    → Subscribe to NarrationBus (gets its own asyncio.Queue)
    → Launch _consume_loop as asyncio.Task
  → ... (processes narration events continuously) ...
  → stop()
    → Cancel consume task (await CancelledError)
    → Unsubscribe from NarrationBus
    → LiveKitPublisher.stop() (disconnect from room)
    → AudioPlayer.stop() (cancel worker, drain queue)
    → ElevenLabsClient.stop() (close httpx client)
```

**Event routing:**

| Priority | Handler | Actions |
|---|---|---|
| `CRITICAL` | `_handle_critical()` | interrupt + alert + synthesize + play_immediate + publish |
| `NORMAL` | `_handle_normal()` | synthesize + enqueue(priority=1) + publish |
| `LOW` | `_handle_low()` | check backlog → skip or synthesize + enqueue(priority=2) + publish |

**State properties:**

| Property | Returns | Source |
|---|---|---|
| `state` | `TTSState` | Computed from ElevenLabs + AudioPlayer availability |
| `tts_available` | `bool` | `ElevenLabsClient.is_available` |
| `audio_available` | `bool` | `AudioPlayer.is_available` |
| `livekit_connected` | `bool` | `LiveKitPublisher.is_connected` |

**Error handling:** Processing errors on individual narrations are logged and skipped — the consume loop never crashes. When synthesis returns `None` (ElevenLabs unavailable), playback and LiveKit publishing are skipped but the loop continues.

### 7. Server Integration

**app.py changes:**
- New singleton: `tts_engine = TTSEngine(narration_bus=narration_bus)`
- Start order: `transcript_watcher -> summarizer -> tts_engine`
- Stop order (reverse): `tts_engine -> summarizer -> transcript_watcher`
- Attached to `app.state.tts_engine`

**routes.py changes:**
- `GET /health` now includes four TTS fields:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "subscribers": 1,
  "narration_subscribers": 1,
  "ollama_available": false,
  "tts_state": "disabled",
  "tts_available": false,
  "audio_available": false,
  "livekit_connected": false
}
```

**cli.py changes:**
- New `--no-tts` flag on `start` command
- Sets `ECHO_ELEVENLABS_API_KEY=""` env var to force TTS disabled mode

---

## Graceful Degradation

Stage 3 follows Echo's core design principle of never blocking the pipeline. Each component degrades independently:

| Condition | Effect | Developer Experience |
|---|---|---|
| No ElevenLabs API key | TTS disabled at startup | Narrations available via SSE `/narrations` only |
| ElevenLabs unreachable | TTS disabled, periodic re-check | Same as above; auto-recovers when API returns |
| No audio output device | Local playback disabled | LiveKit publishing may still work if configured |
| No LiveKit credentials | LiveKit disabled | Local playback still works |
| LiveKit SDK not installed | LiveKit disabled | Local playback still works |
| Synthesis fails for one event | That narration skipped | Next narration processed normally |
| Audio backlog (queue > 3) | LOW events skipped | CRITICAL and NORMAL still processed |
| `--no-tts` CLI flag | TTS fully disabled | Server runs for SSE-only monitoring |

The `TTSState` enum reflects the combined operational state:
- `ACTIVE` — both ElevenLabs and audio device available
- `DEGRADED` — one of the two available (e.g., LiveKit only, or audio without ElevenLabs)
- `DISABLED` — neither available

---

## Configuration

All new configuration is via environment variables in `echo/config.py`:

| Variable | Default | Description |
|---|---|---|
| `ECHO_ELEVENLABS_API_KEY` | `""` (empty = TTS disabled) | ElevenLabs API key |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID (Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model (lowest latency) |
| `ECHO_TTS_TIMEOUT` | `10.0` | Synthesis request timeout (sec) |
| `ECHO_TTS_HEALTH_CHECK_INTERVAL` | `60.0` | ElevenLabs re-check interval (sec) |
| `LIVEKIT_URL` | `""` (empty = disabled) | LiveKit Cloud server URL |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |
| `ECHO_AUDIO_SAMPLE_RATE` | `16000` | Audio sample rate (Hz) |
| `ECHO_AUDIO_BACKLOG_THRESHOLD` | `3` | Queue depth above which LOW events are dropped |

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| TTS provider | ElevenLabs via httpx (not official SDK) | Matches LLMSummarizer pattern. Official SDK is heavy. httpx already a dependency. |
| ElevenLabs API | Standard (non-streaming) | Narration texts are <30 words. Streaming adds complexity for no gain at this length. |
| Audio output format | PCM 16kHz 16-bit mono | Request directly from ElevenLabs (`output_format=pcm_16000`). No decoding library needed. |
| Local playback | sounddevice (PortAudio) | Cross-platform, async-compatible, plays raw PCM directly. Zero network overhead. |
| LiveKit | LiveKit Cloud + `livekit` SDK | Publish audio tracks to a room. Ready for Stage 5 bidirectional audio. Optional — disabled if unconfigured. |
| Alert tone | Generated programmatically (numpy) | Two-tone sine wave (880Hz + 1320Hz, ~350ms). Generated once at startup, cached. No bundled audio assets. |
| ElevenLabs model | `eleven_turbo_v2_5` | Lowest latency model. Optimized for real-time narration. |
| ElevenLabs voice | Rachel (`21m00Tcm4TlvDq8ikWAM`) | Clear, professional default. Configurable via env var. |
| Fallback when unavailable | Skip narration, log warning | Developer still has SSE `/narrations` for text. No local TTS fallback per user decision. |
| Backlog handling | Drop LOW when queue > 3 items | Prevents accumulation during rapid events. Saves API calls by skipping synthesis. |
| LiveKit SDK import | `try/except ImportError` | SDK is a production dependency but may not always be available in dev/CI environments. |
| Thread playback | `asyncio.to_thread(_play_sync)` | sounddevice's `sd.play()` + `sd.wait()` blocks; running in a thread keeps the event loop responsive. |

---

## Tech Stack

| Component | Technology | Notes |
|---|---|---|
| TTS synthesis | ElevenLabs (HTTP API) | No official SDK — httpx calls `/v1/text-to-speech` |
| TTS model | `eleven_turbo_v2_5` | User can override via `ECHO_TTS_MODEL` env var |
| Local audio | sounddevice (PortAudio) | Cross-platform, plays raw PCM via numpy arrays |
| Alert tones | numpy | Float32 sine wave generation, int16 PCM conversion |
| Remote audio | LiveKit Cloud (`livekit` SDK) | Room-based audio publishing for remote listeners |
| HTTP client | httpx.AsyncClient | Same pattern as Stage 2 Ollama integration |
| Priority queue | asyncio.PriorityQueue | Tuple-based priority ordering with sequence counter |
| Build system | hatchling (PEP 621) | Three new deps added to pyproject.toml |

**New dependencies (3):**

| Package | Version | Purpose |
|---|---|---|
| `sounddevice` | `>=0.4.6` | Cross-platform audio playback via PortAudio |
| `numpy` | `>=1.24` | Alert tone generation + PCM array handling |
| `livekit` | `>=0.11` | LiveKit real-time audio SDK |

---

## Test Coverage

**442 total tests (110 Stage 1 + 161 Stage 2 + 171 Stage 3), all passing**

| Test File | Tests | Coverage |
|---|---|---|
| `test_tts_types.py` | 9 | TTSState enum values, membership, string comparison, dict/set usage, imports |
| `test_tts_config.py` | 24 | Default values for all 11 TTS config constants, type checks, env var overrides |
| `test_alert_tone.py` | 10 | Float32 output, correct duration, amplitude range, silence gap, custom sample rate, PCM16 conversion |
| `test_elevenlabs_client.py` | 28 | Startup with/without API key, health check success/failure/timeout, synthesis success/error/timeout, URL/body/params correctness, periodic re-check, config wiring |
| `test_audio_player.py` | 30 | Device detection, queue ordering, enqueue by priority, backlog shedding, interrupt drains non-critical, play_immediate, play_alert, worker processing, edge cases (double start/stop, enqueue after stop) |
| `test_livekit_publisher.py` | 17 | Configuration checks (URL/key/secret/SDK), connect/disconnect, publish success/error, AudioFrame format, SDK unavailable handling |
| `test_tts_engine.py` | 39 | Start/stop lifecycle, state computation (active/degraded/disabled), CRITICAL routing (interrupt + alert + synthesize + play_immediate + publish), NORMAL routing (synthesize + enqueue), LOW routing (backlog skip, under-threshold enqueue), consume loop error handling, timeout continuation |
| `test_server_tts.py` | 14 | Health endpoint TTS fields (tts_state, tts_available, audio_available, livekit_connected), CLI --no-tts flag existence/behavior, app integration (tts_engine on app.state), regression for existing fields |

All tests mock external dependencies — no real ElevenLabs API calls, no real audio device access, no real LiveKit connections. sounddevice is patched with no-ops. LiveKit SDK is replaced with a fake module via `sys.modules` patching.

---

## Modifications to Stage 1 & Stage 2

Stage 3 made minimal, backward-compatible changes to existing stages:

| File | Change | Impact |
|---|---|---|
| `config.py` | Added ElevenLabs, LiveKit, and audio config constants | Additive — no existing code affected |
| `server/app.py` | Added `tts_engine` singleton, lifespan start/stop hooks | Additive — existing singletons and ordering preserved |
| `server/routes.py` | Added 4 TTS fields to `GET /health` response | Additive — existing fields unchanged |
| `cli.py` | Added `--no-tts` flag to `start` command | Additive — existing flags unchanged |
| `tests/conftest.py` | Added `tts_engine` fixture, updated `app` fixture | Additive — existing fixtures still work |

**All 271 Stage 1 + Stage 2 tests pass with zero modifications.**

---

## What's Next

### Stage 4: Question Detection & Alert
- Distinct audio alert patterns for different block reasons
- Read out question text + available options
- Enhanced interrupt behavior for multi-part alerts

### Stage 5: STT & Voice Response
- Listen for developer's spoken response via microphone
- Convert speech to text
- Map spoken response to option, feed back to Claude Code
- Bidirectional audio via LiveKit (infrastructure already in place from Stage 3)

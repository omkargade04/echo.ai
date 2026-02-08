# Stage 5: Voice Response (STT) — Implementation Document

**Date:** February 9, 2026
**Status:** Complete
**Version:** 0.1.0
**Depends on:** Stage 4 (Alert) — Complete (553 tests)

---

## Overview

Stage 5 closes the feedback loop: the developer speaks a response, Echo converts it to text via speech-to-text (STT), maps it to the correct option, confirms the selection audibly, and injects keystrokes into the Claude Code terminal. This eliminates the need to physically return to the screen to type a response when the agent is blocked.

### Key Capability

```
agent_blocked (with options: ["RS256", "HS256"])
  → Alert tone + narration: "Option one: RS256. Option two: HS256."
  → Microphone starts listening
  → Developer speaks: "Option one"
  → STT transcribes: "option one"
  → ResponseMatcher: ordinal match → RS256 (confidence: 0.95)
  → Confirmation narration: "Sending: RS256"
  → ResponseDispatcher: tmux send-keys "RS256" Enter
  → Alert resolved
```

### What Changed from Stage 4

- New `echo/stt/` package with 7 modules (types, microphone, stt_client, response_matcher, response_dispatcher, stt_engine, __init__)
- Options (`list[str] | None`) now flow through the entire pipeline: EchoEvent → NarrationEvent → ActiveAlert → STTEngine
- New ResponseBus (`EventBus[ResponseEvent]`) carries matched responses
- New POST /respond endpoint for manual text response override
- New GET /responses SSE stream for debugging
- /health includes STT state fields
- --no-stt CLI flag

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Stages 1-4 (existing, enhanced)                             │
│                                                               │
│  EchoEvent (agent_blocked, with options + block_reason)      │
│    → EventBus.emit() (fan-out to all subscribers)            │
│      → Summarizer → TemplateEngine.render()                  │
│        → NarrationEvent (with options)                        │
│          → NarrationBus → TTSEngine                           │
│            → AlertManager.activate(options=...)               │
│            → Alert tone + narration playback                  │
│                                                               │
│      → STTEngine._consume_loop() [NEW]                       │
│        → _handle_blocked_event()                              │
│          → _listen_and_respond()                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  Stage 5: Voice Response Pipeline [NEW]                       │
│                                                               │
│  MicrophoneCapture.capture_until_silence()                   │
│    → PCM int16 audio bytes                                    │
│                                                               │
│  STTClient.transcribe(audio_bytes)                           │
│    → transcript string ("option one")                         │
│                                                               │
│  ResponseMatcher.match(transcript, options, block_reason)    │
│    → MatchResult(text="RS256", confidence=0.95, method=ORDINAL)│
│                                                               │
│  Confidence check (>= 0.6 threshold)                         │
│                                                               │
│  ResponseBus.emit(ResponseEvent)                             │
│    → GET /responses SSE stream                                │
│                                                               │
│  TTSEngine confirmation: "Sending: RS256"                    │
│                                                               │
│  ResponseDispatcher.dispatch("RS256")                        │
│    → tmux send-keys "RS256" Enter                             │
└──────────────────────────────────────────────────────────────┘
```

### Listening Lifecycle

```
Alert activated (agent blocked with options)
  → STTEngine starts microphone capture
  → VAD monitors for speech (energy-based RMS threshold)
  → Speech detected → record until silence (1.5s)
  → Transcribe → match → confirm → dispatch
  → Stop listening

Alert resolved (non-blocked event arrives)
  → STTEngine stops microphone capture (if still listening)

Timeout (30s with no speech)
  → STTEngine stops listening
  → Alert repeat will re-trigger listening
```

---

## New Components

### STT Types (`echo/stt/types.py`)

| Type | Description |
|------|-------------|
| `STTState` | Enum: ACTIVE, DEGRADED, DISABLED, LISTENING |
| `MatchMethod` | Enum: ORDINAL, DIRECT, YES_NO, FUZZY, VERBATIM |
| `MatchResult` | Pydantic model: matched_text, confidence, method |
| `ResponseEvent` | Pydantic model: text, transcript, session_id, match_method, confidence, timestamp, options |

### MicrophoneCapture (`echo/stt/microphone.py`)

Captures audio from the default input device using `sounddevice.InputStream`. Energy-based VAD (RMS amplitude threshold) detects speech start and end.

- `start()` — probes for input device, graceful degradation if none
- `stop()` — release resources
- `capture_until_silence()` → `bytes | None` — records until silence or max duration
- Uses `asyncio.to_thread()` for blocking sounddevice calls (same pattern as AudioPlayer)

### STTClient (`echo/stt/stt_client.py`)

OpenAI Whisper API HTTP client. Mirrors the ElevenLabsClient pattern exactly: httpx.AsyncClient, health check, graceful degradation, periodic re-check.

- `start()` — initialize client, health check via GET /v1/models
- `stop()` — close client
- `transcribe(audio_bytes)` → `str | None` — wraps PCM in WAV, POSTs to /v1/audio/transcriptions
- `_wrap_wav()` — stdlib `wave` module, zero external deps

### ResponseMatcher (`echo/stt/response_matcher.py`)

Pure function (no I/O) that maps a transcript to an option. Priority chain:

1. **Ordinal** (0.95 confidence) — "option one", "first", "1" → options[0]. Lookup table covers 1-10.
2. **Yes/No** (0.9 confidence) — Only for 2-option permission prompts. "yes"/"yeah"/"allow" → options[0], "no"/"deny" → options[1].
3. **Direct** (0.85 confidence) — Case-insensitive substring match. Longest match wins.
4. **Fuzzy** (variable confidence) — `difflib.SequenceMatcher`. Only if ratio >= threshold (0.6).
5. **Verbatim** (1.0 confidence) — No options available or no match found. Returns transcript as-is.

### ResponseDispatcher (`echo/stt/response_dispatcher.py`)

Platform-specific keystroke injection via async subprocess:

1. **tmux** (priority 1) — `tmux send-keys <text> Enter`. Check `TMUX` env var.
2. **AppleScript** (priority 2, macOS) — `osascript` with System Events keystroke.
3. **xdotool** (priority 3, Linux) — `xdotool type` + `xdotool key Return`.

Auto-detection at start, or force via `ECHO_DISPATCH_METHOD` env var.

### STTEngine (`echo/stt/stt_engine.py`)

Core orchestrator that ties all components together. Follows TTSEngine pattern:

- Subscribes to EventBus (not NarrationBus) for original EchoEvent.options
- `agent_blocked` events → start listen task
- Non-blocked events for same session → cancel listening
- Full pipeline: capture → transcribe → match → confidence check → emit ResponseEvent → confirm → dispatch
- `handle_manual_response()` for POST /respond bypass

---

## Modified Components

### NarrationEvent (`echo/summarizer/types.py`)
Added: `options: list[str] | None = None`

### ActiveAlert (`echo/tts/alert_manager.py`)
Added: `options: list[str] | None = None` parameter to `__init__` and `AlertManager.activate()`

### TemplateEngine (`echo/summarizer/template_engine.py`)
Added: `options=event.options` in `render()` NarrationEvent constructor

### TTSEngine (`echo/tts/tts_engine.py`)
Added: `options=narration.options` in `_handle_critical()` activate() call

### Server App (`echo/server/app.py`)
Added: `response_bus`, `stt_engine` singletons and lifespan management

### Server Routes (`echo/server/routes.py`)
Added: `POST /respond`, `GET /responses` (SSE), STT fields in `GET /health`

### CLI (`echo/cli.py`)
Added: `--no-stt` flag that clears `ECHO_STT_API_KEY`

### Test Fixtures (`tests/conftest.py`)
Added: `response_bus`, `stt_engine` fixtures; updated `app` fixture

---

## Configuration (New Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `ECHO_STT_API_KEY` | `""` (disabled) | OpenAI Whisper API key |
| `ECHO_STT_BASE_URL` | `https://api.openai.com` | Whisper API base URL |
| `ECHO_STT_MODEL` | `whisper-1` | Whisper model name |
| `ECHO_STT_TIMEOUT` | `10.0` | Whisper request timeout (sec) |
| `ECHO_STT_LISTEN_TIMEOUT` | `30.0` | Max wait for speech after alert |
| `ECHO_STT_SILENCE_THRESHOLD` | `0.01` | RMS amplitude below which audio is silence |
| `ECHO_STT_SILENCE_DURATION` | `1.5` | Seconds of silence to end recording |
| `ECHO_STT_MAX_RECORD_DURATION` | `15.0` | Max recording duration per utterance |
| `ECHO_STT_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence to auto-dispatch |
| `ECHO_STT_HEALTH_CHECK_INTERVAL` | `60.0` | Re-check STT availability interval |
| `ECHO_DISPATCH_METHOD` | `""` (auto-detect) | Force: `applescript`, `xdotool`, `tmux` |

---

## API Endpoints

### New Endpoints

**POST /respond** — Manual text response override (bypass STT)
```json
// Request
{"session_id": "test-1", "text": "RS256"}

// Response (success)
{"status": "ok", "text": "RS256", "session_id": "test-1"}

// Response (dispatch failed)
{"status": "dispatch_failed", "text": "RS256", "session_id": "test-1"}
```

**GET /responses** — SSE stream of ResponseEvents
```
event: response
data: {"text": "RS256", "transcript": "option one", "session_id": "test-1", "match_method": "ordinal", "confidence": 0.95, ...}
```

### Updated Endpoints

**GET /health** — Now includes STT fields:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "subscribers": 3,
  "narration_subscribers": 1,
  "ollama_available": false,
  "tts_state": "disabled",
  "tts_available": false,
  "audio_available": false,
  "livekit_connected": false,
  "alert_active": false,
  "stt_state": "disabled",
  "stt_available": false,
  "mic_available": false,
  "dispatch_available": true,
  "stt_listening": false
}
```

---

## Error Handling & Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| No STT API key | STT disabled. Alerts still work, no voice response. POST /respond works. |
| No microphone | `mic_available: false`. POST /respond still works. |
| STT transcription fails | Log warning, no dispatch. Alert repeat re-triggers listening. |
| No dispatch method available | Log warning, narrate matched response. User types manually. |
| Dispatch fails | Log warning, return false from dispatch(). |
| Low confidence match (< 0.6) | Do not dispatch. Log info. |
| Timeout (no speech in 30s) | Stop listening. Alert repeat will re-trigger. |
| Alert resolved while listening | Cancel active capture immediately. |

---

## File Summary

### New Files (8 source + 8 test)

| File | Purpose |
|------|---------|
| `echo/stt/__init__.py` | Package init, re-exports |
| `echo/stt/types.py` | STTState, MatchMethod, MatchResult, ResponseEvent |
| `echo/stt/microphone.py` | Microphone capture with energy-based VAD |
| `echo/stt/stt_client.py` | OpenAI Whisper API HTTP client |
| `echo/stt/response_matcher.py` | Transcript-to-option matching logic |
| `echo/stt/response_dispatcher.py` | Platform-specific keystroke injection |
| `echo/stt/stt_engine.py` | Core STT orchestrator |
| `tests/test_stt_types.py` | STT type model tests |
| `tests/test_microphone.py` | Microphone capture tests |
| `tests/test_stt_client.py` | STT client tests |
| `tests/test_response_matcher.py` | Response matching tests |
| `tests/test_response_dispatcher.py` | Response dispatcher tests |
| `tests/test_stt_engine.py` | STT engine orchestrator tests |
| `tests/test_server_stt.py` | Server STT integration tests |
| `tests/test_stage5_integration.py` | End-to-end integration tests |

### Modified Files (8 source + 6 test)

| File | Changes |
|------|---------|
| `echo/config.py` | +11 STT config vars, +1 dispatch config var |
| `echo/summarizer/types.py` | +options field on NarrationEvent |
| `echo/summarizer/template_engine.py` | +options passthrough in render() |
| `echo/tts/alert_manager.py` | +options param on ActiveAlert and activate() |
| `echo/tts/tts_engine.py` | +options passthrough to activate() |
| `echo/server/app.py` | +response_bus, +stt_engine, +lifespan wiring |
| `echo/server/routes.py` | +POST /respond, +GET /responses, +STT health fields |
| `echo/cli.py` | +--no-stt flag |
| `tests/conftest.py` | +response_bus, +stt_engine fixtures, updated app fixture |
| `tests/test_tts_config.py` | +24 STT config tests |
| `tests/test_narration_types.py` | +3 options tests |
| `tests/test_alert_manager.py` | +5 options tests |
| `tests/test_template_engine.py` | +3 options passthrough tests |
| `tests/test_tts_engine.py` | +2 options passthrough tests |

---

## Test Summary

| Category | Tests |
|----------|-------|
| Stages 1-4 (existing, some modified) | 553 + ~17 modified |
| STT types | 25 |
| STT config | 24 |
| Microphone capture | 15 |
| STT client | 33 |
| Response matcher | 32 |
| Response dispatcher | 18 |
| STT engine | 31 |
| Server STT | 18 |
| Stage 5 integration | 13 |
| **Total** | **775** |

---

## Dependencies

**Zero new pip dependencies.** All functionality uses existing deps:

| Dependency | Already Installed | Stage 5 Use |
|------------|------------------|-------------|
| `sounddevice` | Yes (Stage 3) | Microphone InputStream |
| `httpx` | Yes (Stage 1) | STT HTTP client |
| `numpy` | Yes (Stage 3) | Audio buffer, RMS calculation |
| `wave` | stdlib | WAV header wrapping |
| `difflib` | stdlib | SequenceMatcher for fuzzy matching |
| `shutil` | stdlib | which() for tool detection |
| `asyncio` | stdlib | create_subprocess_exec for dispatch |

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| STT provider | OpenAI Whisper API | Same httpx pattern as ElevenLabs. ECHO_STT_BASE_URL allows local whisper.cpp. |
| VAD approach | Energy-based (RMS) | Zero new deps. Can upgrade to webrtcvad/silero later. |
| Match priority | Ordinal > yes/no > direct > fuzzy > verbatim | Ordered by specificity. "Option one" is most natural. |
| Fuzzy matching | difflib.SequenceMatcher | No external dependency. Sufficient for short strings. |
| Keystroke injection | tmux > AppleScript > xdotool | tmux most reliable (headless). AppleScript for macOS. xdotool for Linux X11. |
| STTEngine subscribes to | EventBus (not NarrationBus) | Needs original EchoEvent.options. |
| Confirmation | "Sending: X" via TTS | Prevents accidental wrong answers. |
| Manual override | POST /respond | Fallback when STT unavailable. Remote control via HTTP. |

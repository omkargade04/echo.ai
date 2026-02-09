# CLAUDE.md — Echo

Instructions for AI assistants working on this codebase.

## Project Overview

Echo is a real-time audio bridge between developers and AI coding agents (Claude Code for MVP). It captures events from the agent, summarizes them into concise narration text, converts that to speech, and allows the developer to respond verbally — all without watching the screen.

**Current state:** All 5 stages complete (865 tests). The full pipeline is operational: intercept → summarize → speak → alert → voice response.

## Architecture

Five-stage pipeline, all stages implemented:

```
Stage 1: Intercept     → Claude Code hooks + transcript watcher → EventBus
Stage 2: Summarize     → EventBus → Summarizer → NarrationBus
Stage 3: TTS           → NarrationBus → TTSProvider (ElevenLabs/Inworld) + sounddevice + LiveKit
Stage 4: Alert         → Differentiated tones per block reason + repeat alerts
Stage 5: Voice Response → Microphone → Whisper STT → ResponseMatcher → keystroke dispatch
```

The server is a FastAPI app running on `localhost:7865`. Events flow through three async buses:
- `EventBus[EchoEvent]` — raw events from Claude Code
- `EventBus[NarrationEvent]` — summarized narration text for TTS
- `EventBus[ResponseEvent]` — matched voice responses for dispatch (Stage 5)

## Key Conventions

### Code Style
- Python 3.10+ with type hints throughout
- Pydantic v2 for all data models (`BaseModel`, `Field`, string enums)
- Async-first: `asyncio` for all I/O, `asyncio.Queue` for event passing
- `httpx.AsyncClient` for HTTP calls (not `requests`)
- Click for CLI commands
- No docstring/comment additions unless the logic is non-obvious

### Error Handling
- Never crash the pipeline — all event processing is wrapped in try/except
- Field extraction uses `.get()` with defaults — never raises `KeyError`
- Hook shell script uses `|| true` and `exit 0` — never blocks Claude Code
- LLM unavailable? Fall back to truncation. Never block waiting for Ollama.
- Full queues drop events with a warning log — never block the producer

### Testing
- pytest + pytest-asyncio with `asyncio_mode = "auto"`
- All file system tests use `tmp_path` + `monkeypatch` — never touch real user files
- Mock external dependencies (Ollama HTTP calls, file system, watchdog)
- Test files mirror source: `echo/summarizer/template_engine.py` → `tests/test_template_engine.py`
- Run tests: `pytest` (or `pytest -v` for verbose)

### Naming
- Event types: snake_case strings (`tool_executed`, `agent_blocked`, `session_start`)
- Pydantic models: PascalCase (`EchoEvent`, `NarrationEvent`)
- Enums: PascalCase class, UPPER_CASE values in code, lowercase `.value` for serialization
- Private methods: `_underscore_prefix`
- Constants: `UPPER_SNAKE_CASE`

## Module Map

### `echo/events/`
- `types.py` — `EchoEvent`, `EventType` (6 values), `BlockReason` (3 values)
- `event_bus.py` — `EventBus[T]` generic async fan-out bus (asyncio.Queue per subscriber)

### `echo/interceptors/`
- `hook_handler.py` — Parses Claude Code hook JSON → `EchoEvent`
- `hook_installer.py` — Install/uninstall hooks in `~/.claude/settings.json`
- `transcript_watcher.py` — Watches `~/.claude/projects/**/*.jsonl` for assistant messages

### `echo/summarizer/`
- `types.py` — `NarrationEvent`, `NarrationPriority` (critical/normal/low), `SummarizationMethod`
- `summarizer.py` — Core orchestrator: subscribes to EventBus, routes events, emits to NarrationBus
- `template_engine.py` — Deterministic templates for 5 of 6 event types (all except `agent_message`)
- `event_batcher.py` — 500ms time-windowed batching for rapid `tool_executed` events
- `llm_summarizer.py` — Ollama HTTP client for `agent_message` summarization, truncation fallback

### `echo/tts/`
- `types.py` — `TTSState` enum (active/degraded/disabled)
- `provider.py` — `TTSProvider` abstract base class (start, stop, synthesize, is_available, provider_name)
- `provider_factory.py` — `create_tts_provider()` factory, selects provider via `ECHO_TTS_PROVIDER` env var
- `elevenlabs_client.py` — ElevenLabs HTTP client for speech synthesis (implements `TTSProvider`)
- `inworld_client.py` — Inworld HTTP client for speech synthesis (implements `TTSProvider`)
- `audio_player.py` — Priority-queued local audio playback via sounddevice, block-reason tone caching
- `alert_tone.py` — Programmatic two-tone alert generation (numpy), shared sine/fade primitives
- `alert_tones.py` — Per-block-reason alert tone generation (permission, question, idle, default)
- `alert_manager.py` — Alert state tracking, repeat timers, EventBus subscription for resolution (options passthrough)
- `livekit_publisher.py` — LiveKit Cloud room audio publishing
- `tts_engine.py` — Core orchestrator: subscribes to NarrationBus, routes by priority, uses TTSProvider via factory

### `echo/stt/`
- `types.py` — `STTState` (4 values), `MatchMethod` (5 values), `MatchResult`, `ResponseEvent`
- `microphone.py` — Microphone capture with energy-based VAD (RMS threshold), `sounddevice.InputStream`
- `stt_client.py` — OpenAI Whisper API HTTP client (httpx, health check, graceful degradation)
- `response_matcher.py` — Transcript-to-option matching: ordinal > yes/no > direct > fuzzy > verbatim
- `response_dispatcher.py` — Platform-specific keystroke injection: tmux > AppleScript > xdotool
- `stt_engine.py` — Core STT orchestrator: subscribes to EventBus, coordinates capture → transcribe → match → confirm → dispatch

### `echo/server/`
- `app.py` — FastAPI app factory with async lifespan (creates buses, summarizer, transcript watcher, TTS engine, STT engine)
- `routes.py` — `POST /event`, `POST /respond`, `GET /health` (includes TTS + STT fields), `GET /events` (SSE), `GET /narrations` (SSE), `GET /responses` (SSE)

### `echo/hooks/`
- `on_event.sh` — Shell script that Claude Code executes; reads JSON from stdin, POSTs to server

### Root
- `cli.py` — Click CLI: `start` (with `--no-tts`, `--no-stt` flags), `stop`, `status`, `install-hooks`, `uninstall`
- `config.py` — Paths, ports, Ollama, TTS provider selection, ElevenLabs, Inworld, LiveKit, audio, STT configuration (all env-var overridable)

## Event Flow

```
Claude Code hook fires
  → on_event.sh POSTs JSON to localhost:7865/event
    → hook_handler.parse_hook_event() → EchoEvent (with options for notifications)
      → EventBus.emit() (fan-out to all subscribers)
        → [Subscriber 1] Summarizer._consume_loop() pulls from queue
          → Routes by event type:
            tool_executed  → EventBatcher → TemplateEngine.render_batch()
            agent_message  → LLMSummarizer.summarize() (or truncation)
            agent_blocked  → TemplateEngine.render() [CRITICAL, block_reason, numbered options]
            others         → TemplateEngine.render()
          → NarrationBus.emit(NarrationEvent with block_reason)
            → GET /narrations SSE stream
            → TTSEngine._consume_loop() pulls NarrationEvent
              → Route by priority:
                CRITICAL → interrupt + select tone by block_reason + synthesize
                         + play_immediate + AlertManager.activate()
                NORMAL  → synthesize + enqueue
                LOW     → check backlog, synthesize + enqueue (or skip)
              → TTSProvider.synthesize() → PCM bytes
              → AudioPlayer (speakers) + LiveKitPublisher (room)
        → [Subscriber 2] AlertManager._consume_loop()
          → Non-blocked event for active session → clear alert, cancel repeat timer
          → Repeat timer → re-play alert tone + narration every 30s (up to 5x)
        → [Subscriber 3] STTEngine._consume_loop()
          → agent_blocked with options → start listen task
            → MicrophoneCapture.capture_until_silence() → PCM bytes
            → STTClient.transcribe(audio) → "option one"
            → ResponseMatcher.match("option one", options) → MatchResult(RS256, 0.95, ordinal)
            → Confidence check (>= 0.6)
            → ResponseBus.emit(ResponseEvent)
            → TTS confirmation: "Sending: RS256"
            → ResponseDispatcher.dispatch("RS256") → tmux send-keys
          → Non-blocked event for active session → cancel listening
```

## Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `ECHO_PORT` | `7865` | Server port |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `ECHO_LLM_MODEL` | `qwen2.5:0.5b` | Ollama model |
| `ECHO_LLM_TIMEOUT` | `5.0` | Ollama request timeout (sec) |
| `ECHO_TTS_PROVIDER` | `elevenlabs` | TTS provider: `elevenlabs` or `inworld` |
| `ECHO_ELEVENLABS_API_KEY` | `""` (empty = TTS disabled) | ElevenLabs API key |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID (Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model |
| `ECHO_TTS_TIMEOUT` | `10.0` | ElevenLabs request timeout (sec) |
| `ECHO_INWORLD_API_KEY` | `""` (empty = disabled) | Inworld API key |
| `ECHO_INWORLD_BASE_URL` | `https://api.inworld.ai` | Inworld API base URL |
| `ECHO_INWORLD_VOICE_ID` | `Ashley` | Inworld voice name |
| `ECHO_INWORLD_MODEL` | `inworld-tts-1.5-max` | Inworld model ID |
| `ECHO_INWORLD_TIMEOUT` | `10.0` | Inworld request timeout (sec) |
| `ECHO_INWORLD_TEMPERATURE` | `1.1` | Voice expressiveness (0.6-1.1) |
| `ECHO_INWORLD_SPEAKING_RATE` | `1.0` | Speed multiplier (0.5-1.5) |
| `LIVEKIT_URL` | `""` (empty = disabled) | LiveKit Cloud server URL |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts (0 = disabled) |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Max repeat alerts before stopping |
| `ECHO_STT_API_KEY` | `""` (empty = STT disabled) | OpenAI Whisper API key |
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

## File Paths

| Path | Purpose |
|---|---|
| `~/.claude/settings.json` | Claude Code hooks are installed here |
| `~/.claude/projects/**/*.jsonl` | Transcript files the watcher monitors |
| `~/.echo-copilot/hooks/on_event.sh` | Installed hook script |
| `~/.echo-copilot/server.pid` | PID file for daemon mode |
| `~/.echo-copilot/server.log` | Log file for daemon mode |

## Dependencies

Production: `fastapi`, `uvicorn[standard]`, `pydantic>=2.0`, `watchdog`, `click`, `sse-starlette`, `httpx`, `sounddevice>=0.4.6`, `numpy>=1.24`, `livekit>=0.11`

Dev: `pytest`, `pytest-asyncio`, `httpx`

## Common Tasks

```bash
# Run all tests (865 tests)
pytest

# Run tests for a specific stage
pytest tests/test_event_types.py tests/test_event_bus.py tests/test_hook_handler.py tests/test_hook_installer.py tests/test_transcript_watcher.py tests/test_server.py  # Stage 1
pytest tests/test_narration_types.py tests/test_template_engine.py tests/test_event_batcher.py tests/test_llm_summarizer.py tests/test_summarizer.py tests/test_server_narrations.py  # Stage 2
pytest tests/test_tts_types.py tests/test_tts_config.py tests/test_alert_tone.py tests/test_elevenlabs_client.py tests/test_audio_player.py tests/test_livekit_publisher.py tests/test_tts_engine.py tests/test_server_tts.py  # Stage 3
pytest tests/test_alert_tones.py tests/test_alert_manager.py tests/test_hook_handler.py tests/test_narration_types.py tests/test_template_engine.py tests/test_audio_player.py tests/test_tts_engine.py tests/test_server_tts.py tests/test_tts_config.py  # Stage 4
pytest tests/test_stt_types.py tests/test_microphone.py tests/test_stt_client.py tests/test_response_matcher.py tests/test_response_dispatcher.py tests/test_stt_engine.py tests/test_server_stt.py tests/test_stage5_integration.py  # Stage 5

# Start server in foreground
echo-copilot start

# Start without TTS audio output
echo-copilot start --no-tts

# Start without STT voice response
echo-copilot start --no-stt

# Install the package in dev mode
pip install -e ".[dev]"
```

## Plans & Docs

- PRD: `.claude/plans/echo-copilot-prd.md`
- Stage 1 plan: `.claude/plans/stage1-intercept-plan.md`
- Stage 2 plan: `.claude/plans/stage2-summarize-plan.md`
- Stage 3 plan: `.claude/plans/stage3-tts-plan.md`
- Stage 4 plan: `.claude/plans/stage4-alert-plan.md`
- Stage 5 plan: `.claude/plans/stage5-voice-response-plan.md`
- Stage 1 implementation doc: `docs/stage1-intercept-implementation.md`
- Stage 2 implementation doc: `docs/stage2-summarize-implementation.md`
- Stage 3 implementation doc: `docs/stage3-tts-implementation.md`
- Stage 4 implementation doc: `docs/stage4-alert-implementation.md`
- Stage 5 implementation doc: `docs/stage5-voice-response-implementation.md`

Always copy new plans to `.claude/plans/` directory.

## Design Principles

1. **Never block the pipeline** — errors are logged and skipped, not raised
2. **Template-first** — use deterministic templates when possible, LLM only when necessary
3. **Graceful degradation** — Ollama down? Truncate. Queue full? Drop. Hook fails? `exit 0`.
4. **EventBus fan-out** — each subscriber gets its own queue, independent delivery
5. **Async everywhere** — `asyncio.Queue`, `httpx.AsyncClient`, `asyncio.Task` for timers
6. **agent_blocked is CRITICAL** — always flush pending batches, emit immediately, never delay. Differentiated tones per block reason (permission, question, idle). Repeat alerts until resolved or max repeats reached.

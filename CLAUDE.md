# CLAUDE.md — Echo

Instructions for AI assistants working on this codebase.

## Project Overview

Echo is a real-time audio bridge between developers and AI coding agents (Claude Code for MVP). It captures events from the agent, summarizes them into concise narration text, and (in future stages) converts that to speech so developers can monitor their agent without watching the screen.

**Current state:** Stages 1 (Intercept), 2 (Filter & Summarize), 3 (TTS), and 4 (Alert) are complete. Stage 5 (voice response) is planned but not yet implemented.

## Architecture

Five-stage pipeline, four stages implemented:

```
Stage 1: Intercept     → Claude Code hooks + transcript watcher → EventBus
Stage 2: Summarize     → EventBus → Summarizer → NarrationBus
Stage 3: TTS           → NarrationBus → ElevenLabs + sounddevice + LiveKit (implemented)
Stage 4: Alert         → Differentiated tones per block reason + repeat alerts (implemented)
Stage 5: Voice Response → STT → feed response back to agent (planned)
```

The server is a FastAPI app running on `localhost:7865`. Events flow through two async buses:
- `EventBus[EchoEvent]` — raw events from Claude Code
- `EventBus[NarrationEvent]` — summarized narration text for TTS

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
- `elevenlabs_client.py` — ElevenLabs HTTP client for speech synthesis
- `audio_player.py` — Priority-queued local audio playback via sounddevice, block-reason tone caching
- `alert_tone.py` — Programmatic two-tone alert generation (numpy), shared sine/fade primitives
- `alert_tones.py` — Per-block-reason alert tone generation (permission, question, idle, default)
- `alert_manager.py` — Alert state tracking, repeat timers, EventBus subscription for resolution
- `livekit_publisher.py` — LiveKit Cloud room audio publishing
- `tts_engine.py` — Core orchestrator: subscribes to NarrationBus, routes by priority, AlertManager integration

### `echo/server/`
- `app.py` — FastAPI app factory with async lifespan (creates buses, summarizer, transcript watcher, TTS engine)
- `routes.py` — `POST /event`, `GET /health` (includes TTS fields), `GET /events` (SSE), `GET /narrations` (SSE)

### `echo/hooks/`
- `on_event.sh` — Shell script that Claude Code executes; reads JSON from stdin, POSTs to server

### Root
- `cli.py` — Click CLI: `start` (with `--no-tts` flag), `stop`, `status`, `install-hooks`, `uninstall`
- `config.py` — Paths, ports, Ollama, ElevenLabs, LiveKit, audio configuration (all env-var overridable)

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
              → ElevenLabsClient.synthesize() → PCM bytes
              → AudioPlayer (speakers) + LiveKitPublisher (room)
        → [Subscriber 2] AlertManager._consume_loop()
          → Non-blocked event for active session → clear alert, cancel repeat timer
          → Repeat timer → re-play alert tone + narration every 30s (up to 5x)
```

## Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `ECHO_PORT` | `7865` | Server port |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `ECHO_LLM_MODEL` | `qwen2.5:0.5b` | Ollama model |
| `ECHO_LLM_TIMEOUT` | `5.0` | Ollama request timeout (sec) |
| `ECHO_ELEVENLABS_API_KEY` | `""` (empty = TTS disabled) | ElevenLabs API key |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID (Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model |
| `ECHO_TTS_TIMEOUT` | `10.0` | ElevenLabs request timeout (sec) |
| `LIVEKIT_URL` | `""` (empty = disabled) | LiveKit Cloud server URL |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts (0 = disabled) |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Max repeat alerts before stopping |

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
# Run all tests (553 tests)
pytest

# Run tests for a specific stage
pytest tests/test_event_types.py tests/test_event_bus.py tests/test_hook_handler.py tests/test_hook_installer.py tests/test_transcript_watcher.py tests/test_server.py  # Stage 1
pytest tests/test_narration_types.py tests/test_template_engine.py tests/test_event_batcher.py tests/test_llm_summarizer.py tests/test_summarizer.py tests/test_server_narrations.py  # Stage 2
pytest tests/test_tts_types.py tests/test_tts_config.py tests/test_alert_tone.py tests/test_elevenlabs_client.py tests/test_audio_player.py tests/test_livekit_publisher.py tests/test_tts_engine.py tests/test_server_tts.py  # Stage 3
pytest tests/test_alert_tones.py tests/test_alert_manager.py tests/test_hook_handler.py tests/test_narration_types.py tests/test_template_engine.py tests/test_audio_player.py tests/test_tts_engine.py tests/test_server_tts.py tests/test_tts_config.py  # Stage 4

# Start server in foreground
echo-copilot start

# Start without TTS audio output
echo-copilot start --no-tts

# Install the package in dev mode
pip install -e ".[dev]"
```

## Plans & Docs

- PRD: `.claude/plans/echo-copilot-prd.md`
- Stage 1 plan: `.claude/plans/stage1-intercept-plan.md`
- Stage 2 plan: `.claude/plans/stage2-summarize-plan.md`
- Stage 3 plan: `.claude/plans/stage3-tts-plan.md`
- Stage 4 plan: `.claude/plans/stage4-alert-plan.md`
- Stage 1 implementation doc: `docs/stage1-intercept-implementation.md`
- Stage 2 implementation doc: `docs/stage2-summarize-implementation.md`
- Stage 3 implementation doc: `docs/stage3-tts-implementation.md`
- Stage 4 implementation doc: `docs/stage4-alert-implementation.md`

Always copy new plans to `.claude/plans/` directory.

## Design Principles

1. **Never block the pipeline** — errors are logged and skipped, not raised
2. **Template-first** — use deterministic templates when possible, LLM only when necessary
3. **Graceful degradation** — Ollama down? Truncate. Queue full? Drop. Hook fails? `exit 0`.
4. **EventBus fan-out** — each subscriber gets its own queue, independent delivery
5. **Async everywhere** — `asyncio.Queue`, `httpx.AsyncClient`, `asyncio.Task` for timers
6. **agent_blocked is CRITICAL** — always flush pending batches, emit immediately, never delay. Differentiated tones per block reason (permission, question, idle). Repeat alerts until resolved or max repeats reached.

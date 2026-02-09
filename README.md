# Echo

Real-time audio bridge between developers and AI coding agents.

Echo monitors your AI coding agent in the background — narrating what it's doing, alerting you when it's blocked, and accepting voice responses so you never have to return to your screen.

## The Problem

When AI coding agents (Claude Code, Cursor, Copilot) are working on a task, developers have no way to know what's happening without staring at the IDE. If the agent gets stuck waiting for permission or has a question, it sits there silently — sometimes for hours — until the developer notices.

Echo solves this with a five-stage pipeline:

| Stage | Name | Status | Description |
|-------|------|--------|-------------|
| 1 | **Intercept** | Complete | Capture events from Claude Code via hooks + transcript watching |
| 2 | **Filter & Summarize** | Complete | Convert raw events into concise TTS-ready narration text |
| 3 | **TTS** | Complete | Convert narration to speech via ElevenLabs or Inworld + sounddevice + LiveKit |
| 4 | **Alert** | Complete | Distinct alert tones per block reason + repeat alerts until resolved |
| 5 | **Voice Response** | Complete | Respond to agent prompts by voice — STT + option matching + keystroke dispatch |

**865 tests. Zero new dependencies for Stages 4-5.**

## Quick Start

### Prerequisites

- Python 3.10+
- macOS or Linux
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- (Optional) [ElevenLabs](https://elevenlabs.io) or [Inworld](https://inworld.ai) API key for text-to-speech
- (Optional) [OpenAI](https://platform.openai.com) API key for speech-to-text (Whisper)
- (Optional) [Ollama](https://ollama.ai) for LLM-powered summarization
- (Optional) [LiveKit Cloud](https://livekit.io) account for remote audio streaming

### Install

```bash
pip install -e ".[dev]"
```

> Requires `portaudio` for audio I/O: `brew install portaudio` (macOS) or `apt install libportaudio2` (Linux).

### Run

```bash
# Start in foreground (installs Claude Code hooks automatically)
echo-copilot start

# Start as background daemon
echo-copilot start --daemon

# Start without audio output
echo-copilot start --no-tts

# Start without voice response
echo-copilot start --no-stt

# Check status
echo-copilot status

# Stop
echo-copilot stop
```

On startup, Echo automatically installs hooks into Claude Code's `~/.claude/settings.json`. When Claude Code runs tools, encounters blocks, or produces output, events flow through the pipeline in real time.

### Full Setup (TTS + Voice Response)

```bash
# Option A: ElevenLabs TTS (default)
export ECHO_ELEVENLABS_API_KEY="your-elevenlabs-key"

# Option B: Inworld TTS
export ECHO_TTS_PROVIDER="inworld"
export ECHO_INWORLD_API_KEY="your-inworld-key"

# STT (for voice response)
export ECHO_STT_API_KEY="your-openai-key"

# Recommended: run inside tmux for keystroke dispatch
tmux new -s dev
echo-copilot start
```

Verify everything is operational:

```bash
curl -s localhost:7865/health | python3 -m json.tool
```

Key fields: `tts_provider: "elevenlabs"` (or `"inworld"`), `tts_available: true`, `stt_available: true`, `mic_available: true`, `dispatch_available: true`.

### Watch Events (Debug)

```bash
# Raw events from Claude Code
curl -N localhost:7865/events

# Summarized narration events
curl -N localhost:7865/narrations

# Voice response events
curl -N localhost:7865/responses

# Health check
curl localhost:7865/health
```

## How It Works

```
Claude Code                          Echo Server
+--------------+                     +----------------------------------------------+
|              |   hooks fire        |  Stage 1: Intercept                          |
|  Tool use   -+--POST /event------>|    Hook handler -> EventBus                  |
|  Blocked    -|                     |    Transcript watcher -> EventBus            |
|  Stopped    -|                     |                                              |
|              |   transcript files  |  Stage 2: Summarize                          |
|  Messages   -+--file watch------->|    EventBus -> Summarizer -> NarrationBus    |
|              |                     |                                              |
+--------------+                     |  Stage 3: TTS                                |
                                     |    NarrationBus -> TTS Provider -> speakers   |
Developer                            |                                              |
+--------------+                     |  Stage 4: Alert                              |
|              |                     |    agent_blocked -> alert tone + narration   |
|  Hears alert |<--- speakers ------+|    Repeat every 30s until resolved           |
|              |                     |                                              |
|  Speaks:     |                     |  Stage 5: Voice Response                     |
|  "Option one"|--- microphone ---->|    Mic -> Whisper STT -> ResponseMatcher     |
|              |                     |    -> confirm "Sending: RS256"               |
|              |                     |    -> tmux send-keys "RS256" Enter           |
+--------------+                     +----------------------------------------------+
```

### The Voice Response Loop

When the agent is blocked with options:

```
Agent blocked: "Should I use RS256 or HS256?"
  -> Alert tone plays (distinct per block reason)
  -> TTS narrates: "Option one: RS256. Option two: HS256."
  -> Microphone starts listening
  -> Developer speaks: "Option one"
  -> Whisper transcribes: "option one"
  -> ResponseMatcher: ordinal match -> RS256 (confidence: 0.95)
  -> TTS confirms: "Sending: RS256"
  -> Dispatcher types "RS256" + Enter into Claude Code terminal
  -> Alert resolved
```

### Event Types

| Event Type | Source | Priority | Description |
|---|---|---|---|
| `agent_blocked` | Hook | CRITICAL | Agent needs permission or has a question |
| `tool_executed` | Hook | NORMAL | Claude Code ran a tool (Bash, Edit, Read, etc.) |
| `agent_message` | Transcript | NORMAL | Assistant produced text output |
| `agent_stopped` | Hook | NORMAL | Agent finished responding |
| `session_start` | Hook | LOW | New Claude Code session began |
| `session_end` | Hook | LOW | Session ended |

### Narration Examples

| Input Event | Narration Output |
|---|---|
| `tool_executed(Bash, "npm test")` | "Ran command: npm test" |
| `tool_executed(Edit, "src/auth.ts")` | "Edited auth.ts" |
| 3x Edit events in 500ms | "Edited 3 files." |
| `agent_blocked(permission_prompt, options)` | "The agent needs permission... Option one: Allow. Option two: Deny." |
| `agent_blocked(question, options)` | "The agent has a question... Option one: RS256. Option two: HS256." |
| `session_start` | "New coding session started." |

### Response Matching

When you speak in response to an alert, the ResponseMatcher maps your words to the correct option:

| You Say | Match Method | Confidence | Result |
|---------|-------------|------------|--------|
| "Option one" | Ordinal | 0.95 | First option |
| "Yes" / "Allow" | Yes/No | 0.90 | First option (2-option prompts) |
| "PostgreSQL" | Direct | 0.85 | Exact match |
| "post gres" | Fuzzy | variable | Best fuzzy match |
| (anything, no options) | Verbatim | 1.0 | Raw transcript sent |

## CLI Commands

| Command | Description |
|---|---|
| `echo-copilot start [--port PORT] [--daemon] [--skip-hooks] [--no-tts] [--no-stt]` | Install hooks + start server |
| `echo-copilot stop` | Stop background server |
| `echo-copilot status [--port PORT]` | Check server health |
| `echo-copilot install-hooks` | Manually install Claude Code hooks |
| `echo-copilot uninstall` | Stop server + remove all hooks |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/event` | Receives hook payloads from Claude Code |
| `POST` | `/respond` | Manual text response override (bypass STT) |
| `GET` | `/health` | Server status, all component states |
| `GET` | `/events` | SSE stream of raw `EchoEvent` objects |
| `GET` | `/narrations` | SSE stream of `NarrationEvent` objects |
| `GET` | `/responses` | SSE stream of `ResponseEvent` objects |

### Manual Response Override

When STT is unavailable or you prefer typing:

```bash
curl -X POST localhost:7865/respond \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-1", "text": "RS256"}'
```

## Configuration

All configuration is via environment variables:

### Core

| Variable | Default | Description |
|---|---|---|
| `ECHO_PORT` | `7865` | Server port |

### LLM Summarization (Optional)

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ECHO_LLM_MODEL` | `qwen2.5:0.5b` | Ollama model for summarization |
| `ECHO_LLM_TIMEOUT` | `5.0` | Ollama request timeout (seconds) |

### Text-to-Speech (Optional)

Echo supports multiple TTS providers via a pluggable provider abstraction. Set `ECHO_TTS_PROVIDER` to choose which provider to use.

| Variable | Default | Description |
|---|---|---|
| `ECHO_TTS_PROVIDER` | `elevenlabs` | TTS provider: `elevenlabs` or `inworld` |

#### ElevenLabs (default)

| Variable | Default | Description |
|---|---|---|
| `ECHO_ELEVENLABS_API_KEY` | `""` | ElevenLabs API key (empty = TTS disabled) |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID (Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model |
| `ECHO_TTS_TIMEOUT` | `10.0` | ElevenLabs request timeout (seconds) |

#### Inworld

| Variable | Default | Description |
|---|---|---|
| `ECHO_INWORLD_API_KEY` | `""` | Inworld API key (empty = TTS disabled) |
| `ECHO_INWORLD_BASE_URL` | `https://api.inworld.ai` | Inworld API base URL |
| `ECHO_INWORLD_VOICE_ID` | `Ashley` | Inworld voice name |
| `ECHO_INWORLD_MODEL` | `inworld-tts-1.5-max` | Inworld model ID |
| `ECHO_INWORLD_TIMEOUT` | `10.0` | Inworld request timeout (seconds) |
| `ECHO_INWORLD_TEMPERATURE` | `1.1` | Voice expressiveness (0.6-1.1) |
| `ECHO_INWORLD_SPEAKING_RATE` | `1.0` | Speed multiplier (0.5-1.5) |

### LiveKit Remote Audio (Optional)

| Variable | Default | Description |
|---|---|---|
| `LIVEKIT_URL` | `""` | LiveKit Cloud server URL (empty = disabled) |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |

### Alert Behavior

| Variable | Default | Description |
|---|---|---|
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts (0 = disabled) |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Max repeat alerts before stopping |

### Speech-to-Text (Optional)

| Variable | Default | Description |
|---|---|---|
| `ECHO_STT_API_KEY` | `""` | OpenAI API key for Whisper (empty = STT disabled) |
| `ECHO_STT_BASE_URL` | `https://api.openai.com` | Whisper API base URL |
| `ECHO_STT_MODEL` | `whisper-1` | Whisper model name |
| `ECHO_STT_TIMEOUT` | `10.0` | Whisper request timeout (seconds) |
| `ECHO_STT_LISTEN_TIMEOUT` | `30.0` | Max seconds to wait for speech after alert |
| `ECHO_STT_SILENCE_THRESHOLD` | `0.01` | RMS amplitude below which audio is silence |
| `ECHO_STT_SILENCE_DURATION` | `1.5` | Seconds of silence to end recording |
| `ECHO_STT_MAX_RECORD_DURATION` | `15.0` | Max recording duration per utterance |
| `ECHO_STT_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence to auto-dispatch |

### Keystroke Dispatch

| Variable | Default | Description |
|---|---|---|
| `ECHO_DISPATCH_METHOD` | `""` (auto-detect) | Force: `tmux`, `applescript`, or `xdotool` |

Auto-detection priority: tmux (check `TMUX` env var) > AppleScript (macOS) > xdotool (Linux X11).

### Ollama Setup (Optional)

Echo uses Ollama to summarize long `agent_message` text into concise narration. Without Ollama, it falls back to text truncation — the pipeline never blocks.

```bash
brew install ollama          # macOS
ollama pull qwen2.5:0.5b     # Pull default model
ollama serve                  # Start (localhost:11434)
```

## Graceful Degradation

Echo never crashes the pipeline. Every component degrades gracefully:

| Missing Component | Behavior |
|---|---|
| No TTS API key | TTS disabled — narrations available via SSE only |
| No OpenAI key | STT disabled — use `POST /respond` for manual responses |
| No Ollama | `agent_message` summarized via truncation |
| No microphone | Voice capture disabled — `POST /respond` still works |
| No dispatch method | Matched response logged, not typed — user types manually |
| No speakers | Audio playback disabled — LiveKit streaming still works |

## Project Structure

```
echo-copilot/
+-- pyproject.toml                    # Package config, deps, CLI entry point
+-- README.md                         # This file
+-- CLAUDE.md                         # AI assistant instructions
+-- docs/
|   +-- stage1-intercept-implementation.md
|   +-- stage2-summarize-implementation.md
|   +-- stage3-tts-implementation.md
|   +-- stage4-alert-implementation.md
|   +-- stage5-voice-response-implementation.md
|   +-- setup-and-testing-guide.md
+-- echo/
|   +-- __init__.py                   # Package version
|   +-- __main__.py                   # python -m echo
|   +-- cli.py                        # CLI commands (click)
|   +-- config.py                     # Paths, ports, env vars
|   +-- events/
|   |   +-- types.py                  # EchoEvent, EventType, BlockReason
|   |   +-- event_bus.py              # Generic async fan-out bus
|   +-- interceptors/
|   |   +-- hook_handler.py           # Parse Claude Code hooks -> events
|   |   +-- hook_installer.py         # Install/uninstall hooks in settings.json
|   |   +-- transcript_watcher.py     # Watch JSONL transcripts for messages
|   +-- hooks/
|   |   +-- on_event.sh               # Shell script Claude Code executes
|   +-- summarizer/
|   |   +-- types.py                  # NarrationEvent, NarrationPriority
|   |   +-- summarizer.py             # Core orchestrator
|   |   +-- template_engine.py        # Deterministic event-to-text templates
|   |   +-- event_batcher.py          # Time-windowed tool event batching
|   |   +-- llm_summarizer.py         # Ollama LLM with truncation fallback
|   +-- tts/
|   |   +-- types.py                  # TTSState enum
|   |   +-- provider.py               # TTSProvider abstract base class
|   |   +-- provider_factory.py       # Factory: create_tts_provider()
|   |   +-- elevenlabs_client.py      # ElevenLabs speech synthesis
|   |   +-- inworld_client.py         # Inworld speech synthesis
|   |   +-- audio_player.py           # Priority-queued audio playback
|   |   +-- alert_tone.py             # Shared sine/fade primitives
|   |   +-- alert_tones.py            # Per-block-reason alert generation
|   |   +-- alert_manager.py          # Alert state tracking + repeat timers
|   |   +-- livekit_publisher.py      # LiveKit room audio publishing
|   |   +-- tts_engine.py             # TTS orchestrator
|   +-- stt/
|   |   +-- types.py                  # STTState, MatchMethod, ResponseEvent
|   |   +-- microphone.py             # Microphone capture with energy-based VAD
|   |   +-- stt_client.py             # OpenAI Whisper API client
|   |   +-- response_matcher.py       # Transcript-to-option matching
|   |   +-- response_dispatcher.py    # Platform keystroke injection
|   |   +-- stt_engine.py             # STT orchestrator
|   +-- server/
|       +-- app.py                    # FastAPI app with lifespan
|       +-- routes.py                 # HTTP + SSE endpoints
+-- tests/                            # 865 tests (pytest + pytest-asyncio)
    +-- conftest.py
    +-- test_event_types.py
    +-- test_event_bus.py
    +-- test_hook_handler.py
    +-- test_hook_installer.py
    +-- test_transcript_watcher.py
    +-- test_server.py
    +-- test_narration_types.py
    +-- test_template_engine.py
    +-- test_event_batcher.py
    +-- test_llm_summarizer.py
    +-- test_summarizer.py
    +-- test_server_narrations.py
    +-- test_tts_types.py
    +-- test_tts_config.py
    +-- test_tts_provider.py
    +-- test_provider_factory.py
    +-- test_alert_tone.py
    +-- test_alert_tones.py
    +-- test_alert_manager.py
    +-- test_elevenlabs_client.py
    +-- test_inworld_client.py
    +-- test_audio_player.py
    +-- test_livekit_publisher.py
    +-- test_tts_engine.py
    +-- test_server_tts.py
    +-- test_stt_types.py
    +-- test_microphone.py
    +-- test_stt_client.py
    +-- test_response_matcher.py
    +-- test_response_dispatcher.py
    +-- test_stt_engine.py
    +-- test_server_stt.py
    +-- test_stage5_integration.py
```

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| HTTP Server | FastAPI + uvicorn |
| Data Models | Pydantic v2 |
| File Watching | watchdog |
| CLI | click |
| SSE Streaming | sse-starlette |
| HTTP Client | httpx |
| LLM Backend | Ollama (optional) |
| TTS | ElevenLabs or Inworld (optional, pluggable) |
| STT | OpenAI Whisper (optional) |
| Audio I/O | sounddevice + numpy |
| Remote Audio | LiveKit Cloud (optional) |
| Testing | pytest + pytest-asyncio |
| Build System | hatchling (PEP 621) |

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all 865 tests
pytest

# Run with verbose output
pytest -v

# Run by stage
pytest tests/test_event_types.py tests/test_event_bus.py tests/test_hook_handler.py tests/test_hook_installer.py tests/test_transcript_watcher.py tests/test_server.py  # Stage 1
pytest tests/test_narration_types.py tests/test_template_engine.py tests/test_event_batcher.py tests/test_llm_summarizer.py tests/test_summarizer.py tests/test_server_narrations.py  # Stage 2
pytest tests/test_tts_types.py tests/test_tts_config.py tests/test_alert_tone.py tests/test_elevenlabs_client.py tests/test_audio_player.py tests/test_livekit_publisher.py tests/test_tts_engine.py tests/test_server_tts.py  # Stage 3
pytest tests/test_alert_tones.py tests/test_alert_manager.py tests/test_audio_player.py tests/test_tts_engine.py tests/test_tts_config.py  # Stage 4
pytest tests/test_stt_types.py tests/test_microphone.py tests/test_stt_client.py tests/test_response_matcher.py tests/test_response_dispatcher.py tests/test_stt_engine.py tests/test_server_stt.py tests/test_stage5_integration.py  # Stage 5
```

All tests use mocked I/O — no API keys, microphone, or speakers needed.

## Documentation

- [Setup & Testing Guide](docs/setup-and-testing-guide.md) — End-to-end setup, testing walkthrough, troubleshooting
- [Stage 1: Intercept](docs/stage1-intercept-implementation.md)
- [Stage 2: Summarize](docs/stage2-summarize-implementation.md)
- [Stage 3: TTS](docs/stage3-tts-implementation.md)
- [Stage 4: Alert](docs/stage4-alert-implementation.md)
- [Stage 5: Voice Response](docs/stage5-voice-response-implementation.md)

## License

MIT

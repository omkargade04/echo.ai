# Echo

Real-time audio bridge between developers and AI coding agents.

Echo provides ambient audio awareness of what your AI coding agent is doing — narrating actions, alerting when the agent is blocked, and (coming soon) accepting voice responses to keep the workflow moving, all without returning to your screen.

## The Problem

When AI coding agents (Claude Code, Cursor, Copilot) are working on a task, developers have no way to know what's happening without staring at the IDE. If the agent gets stuck waiting for permission or has a question, it sits there silently — sometimes for hours — until the developer notices.

Echo solves this with a five-stage pipeline:

| Stage | Name | Status | Description |
|-------|------|--------|-------------|
| 1 | **Intercept** | Complete | Capture events from Claude Code via hooks + transcript watching |
| 2 | **Filter & Summarize** | Complete | Convert raw events into concise TTS-ready narration text |
| 3 | **TTS** | Complete | Convert narration to speech via ElevenLabs + sounddevice + LiveKit |
| 4 | **Question Detection & Alert** | Planned | Distinct audio alerts when the agent is blocked |
| 5 | **STT & Voice Response** | Planned | Respond to agent questions by voice |

## Quick Start

### Prerequisites

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- (Optional) [Ollama](https://ollama.ai) for LLM-powered summarization
- (Optional) [ElevenLabs](https://elevenlabs.io) API key for text-to-speech
- (Optional) [LiveKit Cloud](https://livekit.io) account for remote audio streaming

### Install

```bash
pip install -e .
```

### Run

```bash
# Start in foreground
echo-copilot start

# Start as background daemon
echo-copilot start --daemon

# Check status
echo-copilot status

# Stop
echo-copilot stop
```

On startup, Echo automatically installs hooks into Claude Code's `~/.claude/settings.json`. When Claude Code runs tools, encounters blocks, or produces output, events flow through the pipeline in real time.

### Watch Events (Debug)

```bash
# Raw events from Claude Code
curl -N http://localhost:7865/events

# Summarized narration events (TTS-ready)
curl -N http://localhost:7865/narrations

# Health check
curl http://localhost:7865/health
```

## How It Works

```
Claude Code                          Echo Server
┌─────────────┐                     ┌──────────────────────────────────┐
│             │   hooks fire        │  Stage 1: Intercept              │
│  Tool use  ─┼──POST /event──────▶│    Hook handler → EventBus       │
│  Blocked   ─┤                     │    Transcript watcher → EventBus │
│  Stopped   ─┤                     │                                  │
│             │   transcript files  │  Stage 2: Summarize              │
│  Messages  ─┼──file watch───────▶│    EventBus → Summarizer         │
│             │                     │      ├── TemplateEngine (5 types)│
└─────────────┘                     │      ├── EventBatcher (collapse) │
                                    │      └── LLMSummarizer (Ollama)  │
                                    │    Summarizer → NarrationBus     │
                                    │                                  │
                                    │  Stage 3: TTS                    │
                                    │    NarrationBus → TTSEngine      │
                                    │      ├── ElevenLabsClient (synth)│
                                    │      ├── AudioPlayer (speakers)  │
                                    │      └── LiveKitPublisher (room) │
                                    │                                  │
                                    │  Endpoints                       │
                                    │    GET /events (SSE)             │
                                    │    GET /narrations (SSE)         │
                                    │    GET /health                   │
                                    └──────────────────────────────────┘
```

### Event Types

Six normalized event types capture the full Claude Code lifecycle:

| Event Type | Source | Description |
|---|---|---|
| `tool_executed` | Hook | Claude Code ran a tool (Bash, Edit, Read, etc.) |
| `agent_blocked` | Hook | Agent needs permission or has a question |
| `agent_stopped` | Hook | Agent finished responding |
| `agent_message` | Transcript | Assistant produced text output |
| `session_start` | Hook | New Claude Code session began |
| `session_end` | Hook | Session ended |

### Narration Examples

| Input Event | Narration Output |
|---|---|
| `tool_executed(Bash, "npm test")` | "Ran command: npm test" |
| `tool_executed(Edit, "src/auth.ts")` | "Edited auth.ts" |
| 3x Edit events in 500ms | "Edited 3 files." |
| `agent_blocked(permission_prompt)` | "The agent needs permission. Allow edit of auth.ts?" |
| `agent_message(long text...)` | "Refactored auth module and added JWT support." (LLM) |
| `session_start` | "New coding session started." |

## CLI Commands

| Command | Description |
|---|---|
| `echo-copilot start [--port PORT] [--daemon] [--skip-hooks] [--no-tts]` | Install hooks + start server |
| `echo-copilot stop` | Stop background server |
| `echo-copilot status [--port PORT]` | Check server health |
| `echo-copilot install-hooks` | Manually install Claude Code hooks |
| `echo-copilot uninstall` | Stop server + remove all hooks |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/event` | Receives hook payloads from Claude Code |
| `GET` | `/health` | Server status, version, subscriber counts, Ollama status, TTS state |
| `GET` | `/events` | SSE stream of raw `EchoEvent` objects |
| `GET` | `/narrations` | SSE stream of summarized `NarrationEvent` objects |

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ECHO_PORT` | `7865` | Server port |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ECHO_LLM_MODEL` | `qwen2.5:0.5b` | Ollama model for summarization |
| `ECHO_LLM_TIMEOUT` | `5.0` | Ollama request timeout (seconds) |
| `ECHO_ELEVENLABS_API_KEY` | `""` | ElevenLabs API key (empty = TTS disabled) |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | ElevenLabs voice ID (Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model |
| `ECHO_TTS_TIMEOUT` | `10.0` | ElevenLabs request timeout (seconds) |
| `LIVEKIT_URL` | `""` | LiveKit Cloud server URL (empty = disabled) |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |

### Ollama Setup (Optional)

Echo uses Ollama to summarize long `agent_message` text into concise narration. Without Ollama, it falls back to text truncation — the pipeline never blocks.

```bash
# Install Ollama (macOS)
brew install ollama

# Pull the default model
ollama pull qwen2.5:0.5b

# Start Ollama (runs on localhost:11434)
ollama serve
```

### ElevenLabs TTS Setup (Optional)

Echo uses ElevenLabs to convert narration text to speech. Without an API key, TTS is disabled and narrations are available only via the SSE `/narrations` endpoint.

```bash
# Set your ElevenLabs API key
export ECHO_ELEVENLABS_API_KEY="sk-..."

# Start with TTS enabled
echo-copilot start

# Or start without TTS
echo-copilot start --no-tts
```

## Project Structure

```
echo-copilot/
├── pyproject.toml                    # Package config, deps, CLI entry point
├── README.md                         # This file
├── CLAUDE.md                         # AI assistant instructions
├── docs/
│   ├── stage1-intercept-implementation.md
│   ├── stage2-summarize-implementation.md
│   └── stage3-tts-implementation.md
├── echo/
│   ├── __init__.py                   # Package version
│   ├── __main__.py                   # python -m echo
│   ├── cli.py                        # CLI commands (click)
│   ├── config.py                     # Paths, ports, env vars
│   ├── events/
│   │   ├── types.py                  # EchoEvent, EventType, BlockReason
│   │   └── event_bus.py              # Generic async fan-out bus
│   ├── interceptors/
│   │   ├── hook_handler.py           # Parse Claude Code hooks → events
│   │   ├── hook_installer.py         # Install/uninstall hooks in settings.json
│   │   └── transcript_watcher.py     # Watch JSONL transcripts for messages
│   ├── hooks/
│   │   └── on_event.sh               # Shell script Claude Code executes
│   ├── summarizer/
│   │   ├── types.py                  # NarrationEvent, NarrationPriority
│   │   ├── summarizer.py             # Core orchestrator
│   │   ├── template_engine.py        # Deterministic event-to-text templates
│   │   ├── event_batcher.py          # Time-windowed tool event batching
│   │   └── llm_summarizer.py         # Ollama LLM with truncation fallback
│   ├── tts/
│   │   ├── types.py                  # TTSState enum
│   │   ├── elevenlabs_client.py      # ElevenLabs speech synthesis
│   │   ├── audio_player.py           # Priority-queued audio playback
│   │   ├── alert_tone.py             # Two-tone alert generation
│   │   ├── livekit_publisher.py      # LiveKit room audio publishing
│   │   └── tts_engine.py             # TTS orchestrator
│   └── server/
│       ├── app.py                    # FastAPI app with lifespan
│       └── routes.py                 # HTTP + SSE endpoints
└── tests/                            # 442 tests (pytest + pytest-asyncio)
    ├── conftest.py
    ├── test_event_types.py
    ├── test_event_bus.py
    ├── test_hook_handler.py
    ├── test_hook_installer.py
    ├── test_transcript_watcher.py
    ├── test_server.py
    ├── test_narration_types.py
    ├── test_template_engine.py
    ├── test_event_batcher.py
    ├── test_llm_summarizer.py
    ├── test_summarizer.py
    ├── test_server_narrations.py
    ├── test_tts_types.py
    ├── test_tts_config.py
    ├── test_alert_tone.py
    ├── test_elevenlabs_client.py
    ├── test_audio_player.py
    ├── test_livekit_publisher.py
    ├── test_tts_engine.py
    └── test_server_tts.py
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
| TTS | ElevenLabs (optional) |
| Audio Playback | sounddevice + numpy |
| Remote Audio | LiveKit Cloud (optional) |
| Testing | pytest + pytest-asyncio |
| Build System | hatchling (PEP 621) |

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_template_engine.py
```

**442 tests** covering event types, event bus, hook handling, hook installation, transcript watching, server endpoints, narration types, template engine, event batching, LLM summarizer, core summarizer, narration streaming, TTS types, TTS configuration, alert tone generation, ElevenLabs client, audio player, LiveKit publisher, TTS engine, and server TTS integration.

## License

MIT

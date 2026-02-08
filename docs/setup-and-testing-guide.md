# Echo — Setup & End-to-End Testing Guide

**Date:** February 9, 2026
**Version:** 0.1.0

---

## Table of Contents

1. [Installation](#1-installation)
2. [Configuration Reference](#2-configuration-reference)
3. [Quick Start (Minimal Setup)](#3-quick-start-minimal-setup)
4. [Full Setup (All Features)](#4-full-setup-all-features)
5. [Testing Stage by Stage](#5-testing-stage-by-stage)
6. [End-to-End Test Walkthrough](#6-end-to-end-test-walkthrough)
7. [CLI Reference](#7-cli-reference)
8. [API Reference](#8-api-reference)
9. [Degraded Mode Testing](#9-degraded-mode-testing)
10. [Tuning Parameters](#10-tuning-parameters)
11. [Troubleshooting](#11-troubleshooting)
12. [Running Automated Tests](#12-running-automated-tests)

---

## 1. Installation

### Prerequisites

- **Python 3.10+**
- **macOS or Linux** (Windows is not supported for dispatch)
- **Claude Code** installed (`~/.claude/` directory exists)

### Install from Source

```bash
cd /path/to/ai-voice-copilot

# Install in dev mode (includes test dependencies)
pip install -e ".[dev]"

# Verify the CLI is available
echo-copilot --help
```

### System Dependencies

| Dependency | Required For | How to Install |
|-----------|-------------|----------------|
| `portaudio` | Microphone + speaker audio (sounddevice) | `brew install portaudio` (macOS) / `apt install libportaudio2` (Linux) |
| `tmux` | Keystroke dispatch (recommended) | `brew install tmux` (macOS) / `apt install tmux` (Linux) |

---

## 2. Configuration Reference

Echo is configured entirely via environment variables. All are optional — the app runs with zero config in a degraded mode.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `ECHO_PORT` | `7865` | HTTP server port |

### Stage 2: LLM Summarization (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `ECHO_LLM_MODEL` | `qwen2.5:0.5b` | Ollama model for `agent_message` summarization |
| `ECHO_LLM_TIMEOUT` | `5.0` | Ollama request timeout (seconds) |

> If Ollama is not running, the summarizer falls back to truncating long messages. This is fine for testing.

### Stage 3: Text-to-Speech

| Variable | Default | Description |
|----------|---------|-------------|
| `ECHO_ELEVENLABS_API_KEY` | `""` (disabled) | ElevenLabs API key. Empty = TTS disabled. |
| `ECHO_ELEVENLABS_BASE_URL` | `https://api.elevenlabs.io` | ElevenLabs API base URL |
| `ECHO_TTS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | Voice ID (default: Rachel) |
| `ECHO_TTS_MODEL` | `eleven_turbo_v2_5` | ElevenLabs model |
| `ECHO_TTS_TIMEOUT` | `10.0` | ElevenLabs request timeout (seconds) |

### Stage 3: LiveKit (Optional Remote Audio)

| Variable | Default | Description |
|----------|---------|-------------|
| `LIVEKIT_URL` | `""` (disabled) | LiveKit Cloud server URL |
| `LIVEKIT_API_KEY` | `""` | LiveKit API key |
| `LIVEKIT_API_SECRET` | `""` | LiveKit API secret |

> LiveKit is for streaming audio to remote listeners. Not needed for local testing.

### Stage 4: Alerts

| Variable | Default | Description |
|----------|---------|-------------|
| `ECHO_ALERT_REPEAT_INTERVAL` | `30.0` | Seconds between repeat alerts (0 = disabled) |
| `ECHO_ALERT_MAX_REPEATS` | `5` | Max repeat alerts before stopping |

### Stage 5: Speech-to-Text

| Variable | Default | Description |
|----------|---------|-------------|
| `ECHO_STT_API_KEY` | `""` (disabled) | OpenAI API key for Whisper STT |
| `ECHO_STT_BASE_URL` | `https://api.openai.com` | Whisper API base URL |
| `ECHO_STT_MODEL` | `whisper-1` | Whisper model name |
| `ECHO_STT_TIMEOUT` | `10.0` | Whisper request timeout (seconds) |
| `ECHO_STT_LISTEN_TIMEOUT` | `30.0` | Max seconds to wait for speech after alert |
| `ECHO_STT_SILENCE_THRESHOLD` | `0.01` | RMS amplitude below which audio is silence |
| `ECHO_STT_SILENCE_DURATION` | `1.5` | Seconds of silence to end recording |
| `ECHO_STT_MAX_RECORD_DURATION` | `15.0` | Max recording duration per utterance |
| `ECHO_STT_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence to auto-dispatch |
| `ECHO_STT_HEALTH_CHECK_INTERVAL` | `60.0` | Re-check STT availability interval (seconds) |

### Stage 5: Keystroke Dispatch

| Variable | Default | Description |
|----------|---------|-------------|
| `ECHO_DISPATCH_METHOD` | `""` (auto-detect) | Force dispatch method: `tmux`, `applescript`, or `xdotool` |

Auto-detection priority: tmux > AppleScript (macOS) > xdotool (Linux X11).

---

## 3. Quick Start (Minimal Setup)

The simplest way to test Stages 1-2 requires **zero API keys** — just the server and curl.

```bash
# Terminal 1: Start Echo (no TTS, no STT)
echo-copilot start --no-tts --no-stt

# Terminal 2: Check health
curl -s localhost:7865/health | python3 -m json.tool

# Terminal 2: Send a test event
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "PostToolUse",
    "session_id": "test-1",
    "tool_name": "Write",
    "tool_input": {"file_path": "/tmp/hello.py", "content": "print(1)"}
  }'

# Terminal 2: Watch the narration SSE stream (in another terminal)
curl -N localhost:7865/narrations
```

You should see narration events like:
```
event: narration
data: {"text":"Created hello.py","priority":"normal",...}
```

---

## 4. Full Setup (All Features)

For the complete pipeline including audio output, voice response, and keystroke dispatch:

### Step 1: Get API Keys

1. **ElevenLabs** (TTS): Sign up at https://elevenlabs.io → Profile → API Keys
2. **OpenAI** (STT): Sign up at https://platform.openai.com → API Keys

### Step 2: Set Environment Variables

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
export ECHO_ELEVENLABS_API_KEY="your-elevenlabs-key-here"
export ECHO_STT_API_KEY="your-openai-key-here"
```

Then reload: `source ~/.zshrc`

### Step 3: Start in tmux (Recommended for Dispatch)

```bash
# Start a tmux session
tmux new -s echo-test

# Start Echo (this pane)
echo-copilot start

# Split pane (Ctrl-B %) for testing
# In the new pane, send test events with curl
```

### Step 4: Verify Everything

```bash
curl -s localhost:7865/health | python3 -m json.tool
```

A fully operational setup looks like:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "subscribers": 3,
  "narration_subscribers": 1,
  "ollama_available": false,
  "tts_state": "active",
  "tts_available": true,
  "audio_available": true,
  "livekit_connected": false,
  "alert_active": false,
  "stt_state": "active",
  "stt_available": true,
  "mic_available": true,
  "dispatch_available": true,
  "stt_listening": false
}
```

**Checklist:**

| Field | Expected | Meaning |
|-------|----------|---------|
| `tts_available` | `true` | ElevenLabs key is valid |
| `audio_available` | `true` | Speaker output device detected |
| `stt_available` | `true` | Whisper API reachable |
| `mic_available` | `true` | Microphone input device detected |
| `dispatch_available` | `true` | tmux / AppleScript / xdotool available |
| `stt_listening` | `false` | Not actively listening (no alert) |

---

## 5. Testing Stage by Stage

### Stage 1: Event Interception

Test that Claude Code hooks deliver events to the server.

**A) Simulate a hook event manually:**

```bash
# Tool execution (PostToolUse)
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "PostToolUse",
    "session_id": "test-1",
    "tool_name": "Bash",
    "tool_input": {"command": "ls -la"}
  }'

# Session start
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "SessionStart",
    "session_id": "test-1"
  }'

# Session end
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "SessionEnd",
    "session_id": "test-1"
  }'

# Agent stopped
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Stop",
    "session_id": "test-1",
    "stop_reason": "end_turn"
  }'
```

**B) Monitor the raw event SSE stream:**

```bash
curl -N localhost:7865/events
```

**C) Test real Claude Code hooks:**

```bash
# Install hooks into Claude Code settings
echo-copilot install-hooks

# Start Claude Code in another terminal — every tool use and notification
# will fire events to the Echo server
```

The hook installer adds entries to `~/.claude/settings.json` for these hook events:
- `PostToolUse` (async) — fires after every tool call
- `Notification` (sync) — fires on permission prompts, idle prompts
- `Stop` (async) — fires when the agent stops
- `SessionStart` / `SessionEnd` (async)

### Stage 2: Summarization & Narration

**Monitor narrations in real time:**

```bash
curl -N localhost:7865/narrations
```

**Test different event types and their narration output:**

```bash
# Tool use → "Ran command: ls -la"
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","session_id":"s1","tool_name":"Bash","tool_input":{"command":"ls -la"}}'

# File edit → "Edited app.py"
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","session_id":"s1","tool_name":"Edit","tool_input":{"file_path":"/src/app.py"}}'

# Agent blocked (question) → CRITICAL priority narration with numbered options
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"Notification","session_id":"s1","type":"question","message":"Which algorithm?","options":["RS256","HS256"]}'

# Session start → "New coding session started." (LOW priority)
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"SessionStart","session_id":"s1"}'
```

**Narration priority levels:**
- **CRITICAL**: `agent_blocked` — always interrupts, never delayed
- **NORMAL**: `tool_executed`, `agent_message`, `agent_stopped`
- **LOW**: `session_start`, `session_end` — skipped if audio backlog

### Stage 3: Text-to-Speech

Requires: `ECHO_ELEVENLABS_API_KEY` set and speakers/headphones connected.

```bash
# Start with TTS enabled
echo-copilot start

# Send an event — you should HEAR the narration
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","session_id":"s1","tool_name":"Bash","tool_input":{"command":"npm install"}}'
# Expected audio: "Ran command: npm install"
```

If you don't hear audio, check:
- `tts_available: true` in `/health`
- `audio_available: true` in `/health`
- System volume is not muted
- Correct audio output device selected in System Settings

### Stage 4: Alert Tones & Repeat Alerts

Requires: TTS enabled (Stage 3).

**Test different block reasons (each has a distinct alert tone):**

```bash
# Permission prompt — urgent two-tone alert
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "alert-1",
    "type": "permission_prompt",
    "message": "Allow tool: Write to /etc/hosts?",
    "options": ["Allow", "Deny"]
  }'

# Question — medium urgency tone
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "alert-2",
    "type": "question",
    "message": "Which database should I use?",
    "options": ["PostgreSQL", "MySQL", "SQLite"]
  }'

# Idle prompt — gentle tone
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "alert-3",
    "type": "idle_prompt",
    "message": "Agent is idle and waiting for input"
  }'
```

**Expected:** Each plays a distinct alert tone followed by the narration.

**Test alert repeat:** Wait 30 seconds without resolving — you should hear the alert again.

**Test alert resolution:** Send a non-blocked event for the same session:

```bash
# This resolves the alert for alert-1
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","session_id":"alert-1","tool_name":"Write","tool_input":{"file_path":"/tmp/x"}}'
```

Check `alert_active: false` in `/health` after resolution.

### Stage 5: Voice Response

Requires: `ECHO_STT_API_KEY` set, microphone connected, dispatch method available.

**Test 1: Full voice loop**

```bash
# Send a blocked event with options
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "voice-1",
    "type": "question",
    "message": "Should I use RS256 or HS256?",
    "options": ["RS256", "HS256"]
  }'
```

**Expected flow:**
1. Alert tone + narration: *"...Option one: RS256. Option two: HS256."*
2. Microphone starts listening (`stt_listening: true` in `/health`)
3. **Speak: "Option one"**
4. Whisper transcribes → `"option one"`
5. ResponseMatcher: ordinal match → RS256 (confidence: 0.95)
6. TTS confirms: *"Sending: RS256"*
7. Dispatcher types `RS256` + Enter into active terminal pane

**Test 2: Yes/No matching**

```bash
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "voice-2",
    "type": "permission_prompt",
    "message": "Allow Write to /tmp/test.py?",
    "options": ["Allow", "Deny"]
  }'
```

**Speak: "Yes"** → maps to "Allow". Also accepts: "yeah", "yep", "sure", "allow", "go ahead".
**Speak: "No"** → maps to "Deny". Also accepts: "nah", "deny", "reject".

**Test 3: Direct name match**

```bash
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "voice-3",
    "type": "question",
    "message": "Which database?",
    "options": ["PostgreSQL", "MySQL", "SQLite"]
  }'
```

**Speak: "PostgreSQL"** → direct match (confidence: 0.85).

**Test 4: Manual override (no mic needed)**

```bash
curl -X POST localhost:7865/respond \
  -H "Content-Type: application/json" \
  -d '{"session_id": "voice-4", "text": "RS256"}'
```

Returns: `{"status": "ok", "text": "RS256", "session_id": "voice-4"}`

**Test 5: Monitor response stream**

```bash
curl -N localhost:7865/responses
```

Output:
```
event: response
data: {"text":"RS256","transcript":"option one","session_id":"voice-1","match_method":"ordinal","confidence":0.95,...}
```

---

## 6. End-to-End Test Walkthrough

This walkthrough tests the complete pipeline from Claude Code hook to keystroke dispatch.

### Setup

```bash
# 1. Set all API keys
export ECHO_ELEVENLABS_API_KEY="your-key"
export ECHO_STT_API_KEY="your-key"

# 2. Start tmux
tmux new -s e2e-test

# 3. Split into 3 panes (Ctrl-B % twice)
# Pane 0: Echo server
# Pane 1: curl commands
# Pane 2: Target pane (where keystrokes will appear)
```

### Test Script

Run these in Pane 1 (curl commands), watch Pane 2 for dispatched keystrokes:

```bash
# --- Step 1: Start Echo (Pane 0) ---
echo-copilot start

# --- Step 2: Verify health (Pane 1) ---
curl -s localhost:7865/health | python3 -m json.tool
# Confirm: stt_state=active, dispatch_available=true

# --- Step 3: Test Stage 1+2 (event → narration) ---
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","session_id":"e2e","tool_name":"Bash","tool_input":{"command":"git status"}}'
# Listen: "Ran command: git status"

# --- Step 4: Test Stage 3+4 (alert tone) ---
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "e2e",
    "type": "question",
    "message": "Use TypeScript or JavaScript?",
    "options": ["TypeScript", "JavaScript"]
  }'
# Listen: Alert tone + "...Option one: TypeScript. Option two: JavaScript."

# --- Step 5: Test Stage 5 (voice response) ---
# Speak into your microphone: "Option one"
# Listen: "Sending: TypeScript"
# Watch Pane 2: "TypeScript" should appear + Enter

# --- Step 6: Test alert resolution ---
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{"hook_event_name":"PostToolUse","session_id":"e2e","tool_name":"Write","tool_input":{"file_path":"/tmp/app.ts"}}'
# Listen: "Created app.ts"
# Confirm: alert_active=false in /health

# --- Step 7: Test manual override ---
curl -X POST localhost:7865/event \
  -H "Content-Type: application/json" \
  -d '{
    "hook_event_name": "Notification",
    "session_id": "e2e-2",
    "type": "permission_prompt",
    "message": "Allow Bash: rm -rf /tmp/cache?",
    "options": ["Allow", "Deny"]
  }'
# Instead of speaking, use manual override:
curl -X POST localhost:7865/respond \
  -H "Content-Type: application/json" \
  -d '{"session_id": "e2e-2", "text": "Allow"}'
# Watch Pane 2: "Allow" should appear + Enter
```

### Real Claude Code Test

```bash
# 1. Install hooks (in any terminal)
echo-copilot install-hooks

# 2. Start Echo in tmux Pane 0
echo-copilot start

# 3. Start Claude Code in tmux Pane 2
claude

# 4. Give Claude a task that requires permission
# e.g., "Create a file called /tmp/echo-test.txt with 'hello world'"
# Claude will hit a permission prompt → you hear the alert → speak "yes"
# → Echo types "Allow" into the Claude Code terminal
```

---

## 7. CLI Reference

```bash
# Start the server (foreground)
echo-copilot start

# Start as background daemon
echo-copilot start --daemon

# Start on custom port
echo-copilot start --port 8080

# Start without TTS (silent mode, events still flow)
echo-copilot start --no-tts

# Start without STT (no voice response, POST /respond still works)
echo-copilot start --no-stt

# Start without hooks (don't modify Claude Code settings)
echo-copilot start --skip-hooks

# Check server status
echo-copilot status

# Stop the server
echo-copilot stop

# Install hooks into Claude Code settings
echo-copilot install-hooks

# Uninstall hooks and clean up
echo-copilot uninstall
```

### Files Created by the CLI

| Path | Created By | Purpose |
|------|-----------|---------|
| `~/.echo-copilot/server.pid` | `start` | Server process ID |
| `~/.echo-copilot/server.log` | `start --daemon` | Daemon log output |
| `~/.echo-copilot/hooks/on_event.sh` | `start` / `install-hooks` | Hook script |
| `~/.claude/settings.json` | `install-hooks` | Modified to add hooks |
| `~/.claude/settings.json.bak` | `install-hooks` | Backup before modification |

---

## 8. API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/event` | Receive hook event JSON, emit on EventBus |
| `POST` | `/respond` | Manual text response (bypass STT) |
| `GET` | `/health` | Server health + all component states |
| `GET` | `/events` | SSE stream of all raw events |
| `GET` | `/narrations` | SSE stream of narration events |
| `GET` | `/responses` | SSE stream of matched voice responses |

### POST /event

Accepts the same JSON format that Claude Code sends to hooks:

```json
{
  "hook_event_name": "PostToolUse | Notification | Stop | SessionStart | SessionEnd",
  "session_id": "string",
  "tool_name": "string (PostToolUse only)",
  "tool_input": {},
  "type": "string (Notification only: permission_prompt | idle_prompt | question)",
  "message": "string (Notification only)",
  "options": ["string"] ,
  "stop_reason": "string (Stop only)"
}
```

### POST /respond

```json
{"session_id": "string", "text": "string"}
```

Returns:
```json
{"status": "ok | dispatch_failed | error", "text": "...", "session_id": "..."}
```

---

## 9. Degraded Mode Testing

Echo is designed to gracefully degrade when components are unavailable.

| Mode | Command | What Works | What Doesn't |
|------|---------|-----------|--------------|
| No TTS | `--no-tts` | Events, narrations, STT, dispatch | No audio output |
| No STT | `--no-stt` | Events, narrations, TTS, alerts | No voice response (POST /respond still works) |
| No TTS + No STT | `--no-tts --no-stt` | Events, narrations, SSE streams, POST /respond | No audio, no voice |
| No Ollama | (default) | Everything except LLM summarization | `agent_message` uses truncation fallback |
| No mic | (auto-detected) | TTS, alerts, POST /respond | No voice capture |
| No dispatch | (auto-detected) | TTS, alerts, STT matching | Matched response logged but not typed |

---

## 10. Tuning Parameters

| Situation | Variable to Adjust | Direction |
|-----------|--------------------|-----------|
| Speech not detected (quiet voice) | `ECHO_STT_SILENCE_THRESHOLD` | Lower (e.g., `0.005`) |
| Background noise triggers capture | `ECHO_STT_SILENCE_THRESHOLD` | Raise (e.g., `0.03`) |
| Recording cuts off mid-sentence | `ECHO_STT_SILENCE_DURATION` | Raise (e.g., `2.5`) |
| Need more time to start speaking | `ECHO_STT_LISTEN_TIMEOUT` | Raise (e.g., `60.0`) |
| Long spoken responses | `ECHO_STT_MAX_RECORD_DURATION` | Raise (e.g., `30.0`) |
| Fuzzy matches dispatching wrong option | `ECHO_STT_CONFIDENCE_THRESHOLD` | Raise (e.g., `0.8`) |
| Alerts too frequent | `ECHO_ALERT_REPEAT_INTERVAL` | Raise (e.g., `60.0`) |
| Alerts stop too soon | `ECHO_ALERT_MAX_REPEATS` | Raise (e.g., `10`) |
| Want to use local Whisper server | `ECHO_STT_BASE_URL` | Set to `http://localhost:8080` |

---

## 11. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `echo-copilot: command not found` | Package not installed | `pip install -e ".[dev]"` |
| `Port 7865 is already in use` | Server already running | `echo-copilot stop` then retry |
| `tts_state: "disabled"` | No ElevenLabs key | Set `ECHO_ELEVENLABS_API_KEY` |
| `tts_available: false` | Bad ElevenLabs key or network issue | Verify key at elevenlabs.io |
| `audio_available: false` | No audio output device | Connect speakers/headphones, check `portaudio` |
| `stt_state: "disabled"` | No OpenAI key | Set `ECHO_STT_API_KEY` |
| `stt_available: false` | Bad OpenAI key or network issue | Verify key at platform.openai.com |
| `mic_available: false` | No microphone connected | Connect mic, check System Settings > Sound > Input |
| `dispatch_available: false` | Not in tmux + no Accessibility permission | Run inside `tmux new`, or grant Terminal Accessibility |
| No sound from speakers | Volume muted or wrong output device | Check macOS Sound settings |
| Alert doesn't repeat | Alert was resolved by a new event | Check `alert_active` in `/health` |
| Whisper returns wrong text | Background noise | Move to quieter environment |
| Wrong option dispatched | Ambiguous speech | Say "option one"/"option two" for best accuracy |
| POST /respond returns error | Missing `session_id` or `text` | Include both fields in JSON body |
| Hooks not firing | Hooks not installed | `echo-copilot install-hooks` |
| AppleScript dispatch fails | No Accessibility permission | System Settings > Privacy > Accessibility > add Terminal |

---

## 12. Running Automated Tests

All tests use mocked I/O — no API keys, microphone, or speakers needed.

```bash
# Run all 775 tests
pytest

# Verbose output
pytest -v

# Run tests by stage
pytest tests/test_event_types.py tests/test_event_bus.py tests/test_hook_handler.py tests/test_hook_installer.py tests/test_transcript_watcher.py tests/test_server.py  # Stage 1 (110 tests)
pytest tests/test_narration_types.py tests/test_template_engine.py tests/test_event_batcher.py tests/test_llm_summarizer.py tests/test_summarizer.py tests/test_server_narrations.py  # Stage 2 (161 tests)
pytest tests/test_tts_types.py tests/test_tts_config.py tests/test_alert_tone.py tests/test_elevenlabs_client.py tests/test_audio_player.py tests/test_livekit_publisher.py tests/test_tts_engine.py tests/test_server_tts.py  # Stage 3 (171 tests)
pytest tests/test_alert_tones.py tests/test_alert_manager.py tests/test_hook_handler.py tests/test_narration_types.py tests/test_template_engine.py tests/test_audio_player.py tests/test_tts_engine.py tests/test_server_tts.py tests/test_tts_config.py  # Stage 4 (111 tests)
pytest tests/test_stt_types.py tests/test_microphone.py tests/test_stt_client.py tests/test_response_matcher.py tests/test_response_dispatcher.py tests/test_stt_engine.py tests/test_server_stt.py tests/test_stage5_integration.py  # Stage 5 (205 tests)

# Run a specific test file
pytest tests/test_response_matcher.py -v

# Run tests matching a pattern
pytest -k "ordinal" -v
```

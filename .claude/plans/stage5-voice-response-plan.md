# Echo — Stage 5: Voice Response (STT) — Implementation Plan

**Date:** February 9, 2026
**Status:** Planning
**Depends on:** Stage 4 (Alert) — Complete (553 tests)

---

## 1. Context

Stages 1-4 are complete (553 tests). The pipeline currently flows one way: Claude Code events are captured, summarized, spoken aloud, and repeated via alert tones when the agent is blocked. However, there is no way for the developer to respond verbally. When the agent is blocked (permission prompt, question, idle), it waits at stdin in the terminal for typed input. The developer must physically return to the screen and type a response.

Stage 5 closes the loop: the developer speaks a response, Echo converts it to text, maps it to the correct option, confirms the selection audibly, and injects keystrokes into the Claude Code terminal.

**Gaps in current implementation that Stage 5 must address:**

1. **ActiveAlert lacks `options`** — `ActiveAlert` stores `session_id`, `block_reason`, `narration_text` but NOT the `options: list[str]` from the original `EchoEvent`. The STT engine needs options to map spoken responses. The options also need to carry through `NarrationEvent`.
2. **No microphone capture** — `sounddevice` is used for output only. Need input stream.
3. **No STT client** — No speech-to-text integration exists.
4. **No response dispatch** — No mechanism to inject text into Claude Code's terminal.
5. **No response matching** — No logic to map "option one" to `options[0]`.
6. **One-way hook** — The hook script (`on_event.sh`) only sends data TO Echo. There is no return channel.

---

## 2. Architecture

```
AlertManager.activate() (from TTSEngine on CRITICAL)
    │
    ▼
STTEngine._on_alert_activated(session_id, options)
    │
    ▼
MicrophoneCapture.start_listening()
    │
    ▼
[Developer speaks: "Option one"]
    │
    ▼
VAD detects speech end
    │
    ▼
STTClient.transcribe(audio_bytes) → "option one"
    │
    ▼
ResponseMatcher.match("option one", options=["RS256", "HS256"])
    → MatchResult(matched_text="RS256", confidence=0.95, method="ordinal")
    │
    ▼
TTSEngine (confirmation): "Sending: RS256"
    │
    ▼
ResponseDispatcher.send_response("RS256", session_id)
    → Platform-specific keystroke injection
    │
    ▼
AlertManager._clear_alert(session_id) (triggered by subsequent non-blocked event)
```

### Listening Lifecycle

```
Alert activated (agent blocked with options)
  → STTEngine starts microphone capture
  → VAD monitors for speech
  → Speech detected → record until silence
  → Transcribe → match → confirm → dispatch
  → Stop listening

Alert resolved (non-blocked event arrives)
  → STTEngine stops microphone capture (if still listening)

Timeout (ECHO_STT_LISTEN_TIMEOUT seconds with no speech)
  → STTEngine stops listening
  → Narrate: "No response detected. Still waiting."
```

### New Bus: ResponseBus

A new `EventBus[ResponseEvent]` carries matched responses from STTEngine to the dispatcher and provides an SSE stream for debugging. This follows the existing pattern of EventBus and NarrationBus.

---

## 3. Component Design

### 3.1. STT Types (NEW)

**File:** `echo/stt/types.py`

```python
class STTState(str, Enum):
    """Operational state of the STT subsystem."""
    ACTIVE = "active"       # STT client available, mic available
    DEGRADED = "degraded"   # One of STT/mic available
    DISABLED = "disabled"   # Neither available
    LISTENING = "listening" # Actively capturing microphone audio

class MatchMethod(str, Enum):
    ORDINAL = "ordinal"       # "option one" → options[0]
    DIRECT = "direct"         # "RS256" → option containing "RS256"
    YES_NO = "yes_no"         # "yes"/"no" for 2-option prompts
    FUZZY = "fuzzy"           # Fuzzy string match
    VERBATIM = "verbatim"     # No options, send transcript as-is

class MatchResult(BaseModel):
    """Result of matching a transcript to available options."""
    matched_text: str         # Text to send
    confidence: float         # 0.0-1.0
    method: MatchMethod

class ResponseEvent(BaseModel):
    """A matched response ready for dispatch to the agent."""
    text: str                           # The final text to send (e.g., "RS256")
    transcript: str                     # Raw STT transcript (e.g., "option one")
    session_id: str
    match_method: MatchMethod           # How the match was made
    confidence: float                   # 0.0-1.0
    timestamp: float = Field(default_factory=time.time)
    options: list[str] | None = None    # Original options for traceability
```

### 3.2. Microphone Capture (NEW)

**File:** `echo/stt/microphone.py`

Captures audio from the default input device using `sounddevice.InputStream`. Follows the same pattern as `AudioPlayer` (probe for device at start, graceful degradation if no mic).

```python
class MicrophoneCapture:
    def __init__(self) -> None: ...

    async def start(self) -> None:
        """Probe for input device. No-op if unavailable."""

    async def stop(self) -> None:
        """Release resources."""

    @property
    def is_available(self) -> bool: ...

    async def capture_until_silence(
        self,
        *,
        max_duration: float = 15.0,
        silence_threshold: float = 0.01,
        silence_duration: float = 1.5,
        sample_rate: int = 16000,
    ) -> bytes | None:
        """Record audio until silence detected or max_duration reached.

        Returns PCM 16-bit bytes, or None if no speech detected.
        Uses VAD (energy-based) to detect speech start and end.
        Runs the blocking InputStream in a thread via asyncio.to_thread.
        """
```

Internal logic:
```
1. Open InputStream(samplerate=16000, channels=1, dtype='int16')
2. Wait for speech onset (RMS > silence_threshold) — up to listen_timeout
3. If no speech: return None
4. Record audio frames into buffer
5. Monitor for silence (RMS < silence_threshold for silence_duration seconds)
6. Stop recording
7. Return concatenated PCM bytes
```

Key design decisions:
- **Energy-based VAD** (RMS amplitude threshold) for simplicity. Zero new dependencies. Can upgrade to webrtcvad/silero later.
- **`asyncio.to_thread`** for the blocking `sounddevice` InputStream, matching AudioPlayer's pattern.
- **Configurable silence threshold** via `ECHO_STT_SILENCE_THRESHOLD` env var.

### 3.3. STT Client (NEW)

**File:** `echo/stt/stt_client.py`

HTTP client for OpenAI Whisper API (or compatible endpoint). Follows the exact same pattern as `ElevenLabsClient`: httpx.AsyncClient, health check, graceful degradation, periodic re-check.

```python
class STTClient:
    def __init__(self) -> None: ...

    async def start(self) -> None:
        """Initialize httpx.AsyncClient, run health check."""

    async def stop(self) -> None:
        """Close httpx.AsyncClient."""

    @property
    def is_available(self) -> bool: ...

    async def transcribe(self, audio_bytes: bytes) -> str | None:
        """Send PCM audio to Whisper API, return transcript text.

        Returns None on any failure (network, auth, timeout).
        Audio is sent as WAV file (PCM bytes wrapped with WAV header).
        """

    async def _check_health(self) -> None:
        """Validate API key via GET /v1/models."""

    async def _maybe_recheck_health(self) -> None:
        """Re-check if enough time has passed since last failure."""
```

WAV wrapping (uses only stdlib `wave` — no new deps):
```python
import io, wave
buf = io.BytesIO()
with wave.open(buf, 'wb') as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)  # 16-bit
    wf.setframerate(16000)
    wf.writeframes(audio_bytes)
buf.seek(0)
# Send as multipart: files={"file": ("audio.wav", buf, "audio/wav")}
```

Why OpenAI Whisper API:
- Same httpx.AsyncClient pattern as ElevenLabs (developer familiarity)
- High accuracy, widely available
- `ECHO_STT_BASE_URL` can be pointed to a local Whisper server (e.g., `whisper.cpp` with API server) for offline use

### 3.4. Response Matcher (NEW)

**File:** `echo/stt/response_matcher.py`

Pure function (no state, no I/O) that maps a transcript string to an option.

```python
class ResponseMatcher:
    """Maps spoken text to the appropriate option from a list."""

    def match(
        self,
        transcript: str,
        options: list[str] | None,
        block_reason: BlockReason | None = None,
    ) -> MatchResult:
        """Match transcript to best option. Returns MatchResult.

        Matching priority (first match wins):
        1. Ordinal: "option one", "first one", "one", "1" → options[0]
        2. Yes/No shortcut: "yes"/"no" for 2-option permission prompts
        3. Direct: transcript contains option text (case-insensitive)
        4. Fuzzy: SequenceMatcher similarity above threshold
        5. Verbatim: no options available, return transcript as-is
        """

    def _try_ordinal_match(self, transcript: str, options: list[str]) -> MatchResult | None: ...
    def _try_yes_no_match(self, transcript: str, options: list[str], block_reason: BlockReason | None) -> MatchResult | None: ...
    def _try_direct_match(self, transcript: str, options: list[str]) -> MatchResult | None: ...
    def _try_fuzzy_match(self, transcript: str, options: list[str]) -> MatchResult | None: ...
```

**Ordinal parsing** handles: "option one", "option 1", "the first one", "first", "one", "1", "number one". Static lookup table:
```python
_ORDINAL_WORDS = {
    "one": 0, "first": 0, "1": 0,
    "two": 1, "second": 1, "2": 1,
    "three": 2, "third": 2, "3": 2,
    "four": 3, "fourth": 3, "4": 3,
    "five": 4, "fifth": 4, "5": 4,
    # ... up to 10
}
```

**Yes/No shortcut**: When `len(options) == 2` and `block_reason == PERMISSION_PROMPT`, "yes" maps to `options[0]`, "no" maps to `options[1]`. Also handles "yeah", "yep", "allow", "deny", "reject".

**Fuzzy matching**: `difflib.SequenceMatcher` — no external deps. Sufficient for short option strings.

### 3.5. Response Dispatcher (NEW)

**File:** `echo/stt/response_dispatcher.py`

Injects the matched response text into Claude Code's terminal. Platform-specific.

```python
class ResponseDispatcher:
    """Injects response text into the Claude Code terminal."""

    def __init__(self) -> None: ...

    async def start(self) -> None:
        """Detect platform and available injection methods."""

    async def stop(self) -> None: ...

    @property
    def is_available(self) -> bool: ...

    @property
    def method(self) -> str:
        """Return the injection method name ('applescript', 'xdotool', 'tmux')."""

    async def dispatch(self, text: str) -> bool:
        """Inject text + Enter into the Claude Code terminal.
        Returns True if dispatch succeeded, False otherwise.
        """

    async def _dispatch_applescript(self, text: str) -> bool:
        """macOS: Use osascript to send keystrokes to Terminal/iTerm2."""

    async def _dispatch_xdotool(self, text: str) -> bool:
        """Linux X11: Use xdotool to type text."""

    async def _dispatch_tmux(self, text: str) -> bool:
        """tmux: Use tmux send-keys (works cross-platform if in tmux)."""

    def _detect_method(self) -> str | None:
        """Detect the best available injection method for the current platform."""
```

**Detection priority:**
1. **tmux** — Check `TMUX` env var. If set, use `tmux send-keys`. Most reliable, cross-platform.
2. **AppleScript (macOS)** — Check `sys.platform == 'darwin'` and `shutil.which('osascript')`. Use System Events to send keystrokes.
3. **xdotool (Linux)** — Check `shutil.which('xdotool')` and `DISPLAY` env var.

All dispatch methods run via `asyncio.create_subprocess_exec()` to avoid blocking.

**AppleScript approach:**
```applescript
tell application "System Events"
    keystroke "RS256"
    delay 0.1
    keystroke return
end tell
```

**tmux approach:**
```bash
tmux send-keys "RS256" Enter
```

### 3.6. STT Engine (Orchestrator) (NEW)

**File:** `echo/stt/stt_engine.py`

The core orchestrator, following the same pattern as `TTSEngine`.

```python
class STTEngine:
    """Core STT orchestrator — coordinates microphone, transcription, matching, and dispatch."""

    def __init__(
        self,
        event_bus: EventBus,
        narration_bus: EventBus[NarrationEvent],
        response_bus: EventBus[ResponseEvent],
        *,
        alert_manager: AlertManager | None = None,
        tts_engine: TTSEngine | None = None,
    ) -> None: ...

    async def start(self) -> None:
        """Start sub-components, subscribe to event bus, begin consume loop."""

    async def stop(self) -> None:
        """Stop all sub-components, cancel active listening."""

    @property
    def state(self) -> STTState: ...

    @property
    def is_listening(self) -> bool: ...

    @property
    def stt_available(self) -> bool: ...

    @property
    def mic_available(self) -> bool: ...

    @property
    def dispatch_available(self) -> bool: ...

    # Internal
    async def _consume_loop(self) -> None:
        """Listen to EventBus for agent_blocked events with options."""

    async def _handle_blocked_event(self, event: EchoEvent) -> None:
        """Start listening when agent is blocked with options."""

    async def _listen_and_respond(
        self, session_id: str, options: list[str] | None, block_reason: BlockReason | None
    ) -> None:
        """Full cycle: capture → transcribe → match → confirm → dispatch."""

    async def _confirm_and_dispatch(self, match_result: MatchResult, session_id: str) -> None:
        """Narrate confirmation, then dispatch response."""

    async def _cancel_listening(self, session_id: str) -> None:
        """Cancel active listening for a session (e.g., alert resolved externally)."""
```

The STT engine subscribes to the **EventBus** (not NarrationBus) because it needs the original `EchoEvent.options` list. It watches for `agent_blocked` events and starts a listen task. It also watches for non-blocked events to cancel listening if the alert is resolved externally (e.g., user typed directly in terminal).

For confirmation, it uses the `tts_engine` reference to synthesize and play a short confirmation: "Sending: RS256". This is optional (works without tts_engine, just logs).

### 3.7. ActiveAlert Enhancement (MODIFY)

**File:** `echo/tts/alert_manager.py`

Add `options` field to `ActiveAlert` and `AlertManager.activate()`:

```python
class ActiveAlert:
    def __init__(
        self,
        session_id: str,
        block_reason: BlockReason | None,
        narration_text: str,
        options: list[str] | None = None,  # NEW
    ):
        ...
        self.options = options

# AlertManager.activate() signature:
async def activate(
    self,
    session_id: str,
    block_reason: BlockReason | None,
    narration_text: str,
    options: list[str] | None = None,  # NEW
) -> None:
```

### 3.8. NarrationEvent Enhancement (MODIFY)

**File:** `echo/summarizer/types.py`

Add `options` field to `NarrationEvent` so options flow through the pipeline:

```python
class NarrationEvent(BaseModel):
    ...
    options: list[str] | None = None  # NEW
```

### 3.9. Template Engine Enhancement (MODIFY)

**File:** `echo/summarizer/template_engine.py`

Pass `options` through in `render()`:
```python
return NarrationEvent(
    ...
    options=event.options,  # NEW
)
```

### 3.10. TTSEngine Enhancement (MODIFY)

**File:** `echo/tts/tts_engine.py`

Pass `options` to `AlertManager.activate()`:
```python
await self._alert_manager.activate(
    session_id=narration.session_id,
    block_reason=narration.block_reason,
    narration_text=narration.text,
    options=narration.options,  # NEW
)
```

### 3.11. Server Integration (MODIFY)

**Files:** `echo/server/app.py`, `echo/server/routes.py`

**`app.py`** — Create `response_bus`, `stt_engine` singletons, wire into lifespan:
```python
response_bus: EventBus[ResponseEvent] = EventBus()
stt_engine = STTEngine(
    event_bus=event_bus,
    narration_bus=narration_bus,
    response_bus=response_bus,
    alert_manager=tts_engine._alert_manager,
    tts_engine=tts_engine,
)
```

Lifespan adds `await stt_engine.start()` / `await stt_engine.stop()`.

**`routes.py`** — New endpoints:
- `POST /respond` — Manual text response override (bypass STT). Accepts `{"session_id": "...", "text": "RS256"}`. Useful for testing, fallback, or remote control.
- `GET /responses` — SSE stream of `ResponseEvent` for debugging.
- `GET /health` — Add `stt_state`, `stt_available`, `mic_available`, `dispatch_available`, `stt_listening` fields.

### 3.12. CLI Enhancement (MODIFY)

**File:** `echo/cli.py`

Add `--no-stt` flag to the `start` command:
```python
@click.option("--no-stt", is_flag=True, help="Disable STT voice response")
```

When set, clears `ECHO_STT_API_KEY` so STTClient stays disabled.

---

## 4. Configuration (new env vars)

**File:** `echo/config.py`

| Variable | Default | Description |
|---|---|---|
| `ECHO_STT_API_KEY` | `""` (empty = STT disabled) | OpenAI Whisper API key |
| `ECHO_STT_BASE_URL` | `https://api.openai.com` | Whisper API base URL (point to local for offline) |
| `ECHO_STT_MODEL` | `whisper-1` | Whisper model name |
| `ECHO_STT_TIMEOUT` | `10.0` | Whisper API request timeout (sec) |
| `ECHO_STT_LISTEN_TIMEOUT` | `30.0` | Max seconds to wait for speech after alert (0 = disabled) |
| `ECHO_STT_SILENCE_THRESHOLD` | `0.01` | RMS amplitude below which audio is silence |
| `ECHO_STT_SILENCE_DURATION` | `1.5` | Seconds of silence to end recording |
| `ECHO_STT_MAX_RECORD_DURATION` | `15.0` | Max recording duration per utterance |
| `ECHO_STT_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence to auto-dispatch |
| `ECHO_STT_HEALTH_CHECK_INTERVAL` | `60.0` | Re-check STT availability interval |
| `ECHO_DISPATCH_METHOD` | `""` (auto-detect) | Force dispatch method: `applescript`, `xdotool`, `tmux` |

---

## 5. File Summary

### New Files (8 source + 7 test)

| File | Purpose |
|---|---|
| `echo/stt/__init__.py` | Package init, re-exports (STTEngine, STTState, ResponseEvent) |
| `echo/stt/types.py` | `STTState`, `ResponseEvent`, `MatchMethod`, `MatchResult` |
| `echo/stt/microphone.py` | Microphone capture with energy-based VAD |
| `echo/stt/stt_client.py` | OpenAI Whisper API HTTP client |
| `echo/stt/response_matcher.py` | Transcript-to-option matching logic |
| `echo/stt/response_dispatcher.py` | Platform-specific keystroke injection |
| `echo/stt/stt_engine.py` | Core STT orchestrator |
| `tests/test_stt_types.py` | ~10 tests |
| `tests/test_microphone.py` | ~15 tests |
| `tests/test_stt_client.py` | ~20 tests |
| `tests/test_response_matcher.py` | ~30 tests |
| `tests/test_response_dispatcher.py` | ~15 tests |
| `tests/test_stt_engine.py` | ~25 tests |
| `tests/test_server_stt.py` | ~15 tests |

### Modified Files (9 source + 6 test)

| File | Changes |
|---|---|
| `echo/config.py` | Add all `ECHO_STT_*` and `ECHO_DISPATCH_*` config vars |
| `echo/tts/alert_manager.py` | Add `options` field to `ActiveAlert`, `activate()` signature |
| `echo/summarizer/types.py` | Add `options` field to `NarrationEvent` |
| `echo/summarizer/template_engine.py` | Pass `options` through in `render()` |
| `echo/tts/tts_engine.py` | Pass `narration.options` to `alert_manager.activate()` |
| `echo/tts/__init__.py` | Re-export new items if needed |
| `echo/server/app.py` | Create response_bus, stt_engine; wire into lifespan |
| `echo/server/routes.py` | Add POST /respond, GET /responses SSE, update /health |
| `echo/cli.py` | Add `--no-stt` flag |
| `tests/test_alert_manager.py` | +5 tests (options field) |
| `tests/test_narration_types.py` | +3 tests (options field) |
| `tests/test_template_engine.py` | +3 tests (options passthrough) |
| `tests/test_tts_engine.py` | +3 tests (options passed to activate) |
| `tests/test_server_tts.py` | +2 tests (new health fields) |
| `tests/conftest.py` | Add stt_engine fixture, response_bus fixture |

**Estimated: ~150 new tests, ~16 modified tests → ~719 total (553 existing + ~166 new/modified)**

---

## 6. Task Breakdown & Waves

### Wave 1 (parallel — no dependencies)

| Task | Files | Tests |
|---|---|---|
| **T1: Config** | `echo/config.py` | `tests/test_tts_config.py` (+10) |
| **T2: STT types** | `echo/stt/__init__.py`, `echo/stt/types.py` | `tests/test_stt_types.py` (~10) |
| **T3: NarrationEvent options field** | `echo/summarizer/types.py` | `tests/test_narration_types.py` (+3) |
| **T4: ActiveAlert options field** | `echo/tts/alert_manager.py` | `tests/test_alert_manager.py` (+5) |

### Wave 2 (parallel — depends on Wave 1)

| Task | Files | Tests |
|---|---|---|
| **T5: Response Matcher** | `echo/stt/response_matcher.py` | `tests/test_response_matcher.py` (~30) |
| **T6: Microphone Capture** | `echo/stt/microphone.py` | `tests/test_microphone.py` (~15) |
| **T7: STT Client** | `echo/stt/stt_client.py` | `tests/test_stt_client.py` (~20) |
| **T8: Response Dispatcher** | `echo/stt/response_dispatcher.py` | `tests/test_response_dispatcher.py` (~15) |
| **T9: Template Engine options passthrough** | `echo/summarizer/template_engine.py` | `tests/test_template_engine.py` (+3) |

### Wave 3 (parallel — depends on Waves 1-2)

| Task | Files | Tests |
|---|---|---|
| **T10: TTSEngine options passthrough** | `echo/tts/tts_engine.py` | `tests/test_tts_engine.py` (+3) |
| **T11: STT Engine (orchestrator)** | `echo/stt/stt_engine.py` | `tests/test_stt_engine.py` (~25) |

### Wave 4 (parallel — depends on Wave 3)

| Task | Files | Tests |
|---|---|---|
| **T12: Server integration** | `echo/server/app.py`, `echo/server/routes.py`, `echo/cli.py` | `tests/test_server_stt.py` (~15) |
| **T13: Test fixtures** | `tests/conftest.py` | — |

### Wave 5 (depends on Wave 4)

| Task | Files | Tests |
|---|---|---|
| **T14: Integration tests** | `tests/test_stage5_integration.py` | ~12 tests |

### Wave 6 (depends on Wave 5)

| Task | Files | Tests |
|---|---|---|
| **T15: Full test run + docs** | `docs/stage5-voice-response-implementation.md`, update `CLAUDE.md` | pytest: ~719 pass |

```
Wave 1 (parallel):  T1  |  T2  |  T3  |  T4
Wave 2 (parallel):  T5  |  T6  |  T7  |  T8  |  T9
Wave 3 (parallel):  T10  |  T11
Wave 4 (parallel):  T12  |  T13
Wave 5:             T14
Wave 6:             T15
```

---

## 7. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| STT provider | OpenAI Whisper API | Same httpx pattern as ElevenLabs. Widely available. `ECHO_STT_BASE_URL` allows local whisper.cpp server for offline. |
| VAD approach | Energy-based (RMS threshold) | Zero new dependencies. Good enough for quiet room use case. Can upgrade to webrtcvad/silero later. |
| Response matching priority | Ordinal > yes/no > direct > fuzzy > verbatim | Ordered by specificity. Developer says "option one" most naturally after hearing numbered options. |
| Fuzzy matching | `difflib.SequenceMatcher` | No external dependency. Sufficient for short option strings. |
| Keystroke injection | tmux > AppleScript > xdotool (auto-detect) | tmux is most reliable (works headless). AppleScript covers macOS. xdotool covers Linux X11. |
| STT engine subscribes to | EventBus (not NarrationBus) | Needs original `EchoEvent.options` list. NarrationEvent also carries options as enhancement. |
| When to listen | Only when `agent_blocked` event arrives | No point capturing audio when agent is not waiting. Saves resources and avoids false activations. |
| Confirmation flow | Narrate "Sending: X" before dispatch | Prevents accidental wrong answers. Adds ~1s delay but safety is worth it. |
| Manual override | POST /respond endpoint | Fallback if STT is unavailable or inaccurate. Enables remote control via HTTP. |
| New package | `echo/stt/` | Mirrors `echo/tts/` structure. Clean separation of concerns. |
| Carry options through pipeline | Add to NarrationEvent + ActiveAlert | Both need options: NarrationEvent for SSE consumers, ActiveAlert for STT matching. |
| Low confidence handling | Below threshold: narrate what was heard, ask to repeat | Prevents wrong responses from being dispatched. |
| New dependencies | **Zero** | sounddevice (mic input), httpx (STT API), numpy (audio), wave/difflib/shutil (stdlib) all already available. |

---

## 8. Error Handling & Graceful Degradation

Following the project's core principle: **never crash the pipeline**.

| Scenario | Behavior |
|---|---|
| No STT API key | STT disabled. Alerts still work, just no voice response. |
| No microphone | STT engine starts but `mic_available` is False. POST /respond still works. |
| STT transcription fails | Log warning, narrate "I couldn't understand. Please repeat or type your response." |
| No dispatch method available | Log warning, narrate the matched response and say "Please type: RS256". |
| Dispatch fails (osascript error) | Log warning, narrate "Couldn't send response. Please type: RS256". |
| Low confidence match (< threshold) | Narrate "I didn't catch that clearly. Please repeat." Do not dispatch. |
| Timeout (no speech) | Stop listening, do nothing. Alert repeat will re-trigger listening. |
| Multiple alerts (different sessions) | Queue listening. Only one microphone capture at a time. Process most recent first. |
| Alert resolved while listening | Cancel active capture immediately. |

---

## 9. Verification

1. **Unit tests**: `pytest` — all ~719 tests pass (553 existing + ~166 new/modified)
2. **Regression**: Zero failures on existing 553 tests
3. **Manual test (with both API keys)**:
   ```bash
   export ECHO_ELEVENLABS_API_KEY="sk-..."
   export ECHO_STT_API_KEY="sk-..."
   echo-copilot start

   # Trigger a question with options
   curl -X POST localhost:7865/event \
     -H "Content-Type: application/json" \
     -d '{"hook_event_name":"Notification","session_id":"test-1","type":"question","message":"Should I use RS256 or HS256?","options":["RS256","HS256"]}'
   # Expect: alert tone + narration + microphone starts listening

   # Speak: "Option one"
   # Expect: "Sending: RS256" confirmation narration
   # Expect: "RS256\n" typed into terminal

   # Test manual override
   curl -X POST localhost:7865/respond \
     -H "Content-Type: application/json" \
     -d '{"session_id":"test-2","text":"yes"}'
   ```
4. **Health check**: `curl localhost:7865/health` includes `stt_state`, `stt_available`, `mic_available`, `dispatch_available`, `stt_listening`
5. **Degradation**: No STT key → STT disabled, TTS still works, POST /respond still works
6. **No mic**: STT client available but microphone not → POST /respond works, no voice capture
7. **CLI flag**: `echo-copilot start --no-stt` → STT disabled
8. **SSE stream**: `curl localhost:7865/responses` → streams ResponseEvents
9. **Different match types**: Test ordinal ("option one"), yes/no ("yes"), direct ("RS256"), fuzzy ("are s two fifty six")

---

## 10. Dependencies

**Zero new pip dependencies.** All functionality uses existing deps:

| Dependency | Already Installed | Stage 5 Use |
|---|---|---|
| `sounddevice` | Yes (Stage 3) | Microphone InputStream for audio capture |
| `httpx` | Yes (Stage 1) | STT HTTP client for Whisper API |
| `numpy` | Yes (Stage 3) | Audio buffer manipulation, RMS calculation |
| `wave` | stdlib | WAV header wrapping for Whisper API upload |
| `difflib` | stdlib | `SequenceMatcher` for fuzzy matching |
| `shutil` | stdlib | `which()` for tool detection (osascript, xdotool, tmux) |
| `asyncio` | stdlib | `create_subprocess_exec` for dispatch commands |

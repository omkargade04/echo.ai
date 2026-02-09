"""HTTP routes for the Echo server.

Endpoints
---------
POST /event       Receives raw hook JSON from the Claude Code shell script,
                  parses it via ``parse_hook_event``, and emits the resulting
                  event on the shared :class:`EventBus`.

POST /respond     Manual text response override -- bypass STT, dispatch
                  directly to the agent.

GET  /health      Returns server health status, version, subscriber counts,
                  Ollama availability, TTS and STT state.

GET  /events      Streams all events in real time as Server-Sent Events (SSE).
                  Intended for debugging and for future front-end consumers.

GET  /narrations  Streams NarrationEvents as Server-Sent Events (SSE).
                  Intended for the TTS pipeline and front-end consumers.

GET  /responses   Streams ResponseEvents as Server-Sent Events (SSE).
                  Intended for debugging the STT pipeline.
"""

import asyncio
import logging
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from echo import __version__
from echo.events.event_bus import EventBus
from echo.interceptors.hook_handler import parse_hook_event

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_event_bus(request: Request) -> EventBus:
    """Retrieve the shared EventBus from application state."""
    return request.app.state.event_bus


def _get_narration_bus(request: Request) -> EventBus:
    """Retrieve the shared NarrationBus from application state."""
    return request.app.state.narration_bus


def _get_tts_engine(request: Request):
    """Retrieve the shared TTSEngine from application state."""
    return request.app.state.tts_engine


def _get_stt_engine(request: Request):
    """Retrieve the shared STTEngine from application state (if present)."""
    return getattr(request.app.state, "stt_engine", None)


def _get_response_bus(request: Request) -> EventBus | None:
    """Retrieve the shared ResponseBus from application state (if present)."""
    return getattr(request.app.state, "response_bus", None)


# ---------------------------------------------------------------------------
# POST /event
# ---------------------------------------------------------------------------


@router.post("/event")
async def receive_event(request: Request) -> dict:
    """Receive a hook payload from Claude Code and emit it on the event bus.

    The request body is raw JSON (not Pydantic-validated) because the hook
    payload format varies by event type.  ``parse_hook_event`` handles the
    mapping and returns ``None`` for unrecognised payloads.
    """
    event_bus = _get_event_bus(request)

    try:
        raw_json: dict = await request.json()
    except Exception:
        logger.warning("Failed to decode JSON body from hook POST")
        return {"status": "error", "reason": "invalid json"}

    hook_event_name = raw_json.get("hook_event_name", "<unknown>")
    logger.info(
        "Received hook event: %s (session=%s)",
        hook_event_name,
        raw_json.get("session_id", "?"),
    )

    event = parse_hook_event(raw_json)

    if event is not None:
        await event_bus.emit(event)
        logger.info("Emitted %s event to bus", event.type.value)
        return {"status": "ok", "event_type": event.type.value}

    logger.warning("Unrecognized or malformed hook event: %s", hook_event_name)
    return {"status": "ignored", "reason": "unrecognized event"}


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health(request: Request) -> dict:
    """Return server health information.

    Useful for the CLI ``status`` command and for external monitoring.
    """
    event_bus = _get_event_bus(request)
    narration_bus = _get_narration_bus(request)
    summarizer = request.app.state.summarizer
    tts_engine = _get_tts_engine(request)
    stt_engine = _get_stt_engine(request)

    result = {
        "status": "ok",
        "version": __version__,
        "subscribers": event_bus.subscriber_count,
        "narration_subscribers": narration_bus.subscriber_count,
        "ollama_available": summarizer.llm_available,
        "tts_state": tts_engine.state.value,
        "tts_available": tts_engine.tts_available,
        "audio_available": tts_engine.audio_available,
        "livekit_connected": tts_engine.livekit_connected,
        "alert_active": tts_engine.alert_active,
        "tts_provider": tts_engine.provider_name,
    }

    if stt_engine is not None:
        result["stt_state"] = stt_engine.state.value
        result["stt_available"] = stt_engine.stt_available
        result["mic_available"] = stt_engine.mic_available
        result["dispatch_available"] = stt_engine.dispatch_available
        result["stt_listening"] = stt_engine.is_listening

    return result


# ---------------------------------------------------------------------------
# GET /events  (Server-Sent Events)
# ---------------------------------------------------------------------------


@router.get("/events")
async def event_stream(request: Request) -> EventSourceResponse:
    """Stream all Echo events as Server-Sent Events.

    Each SSE message has:
    * ``event`` — the event type (e.g. ``tool_executed``)
    * ``data``  — the full event serialised as a JSON string

    The subscription is automatically cleaned up when the client
    disconnects or the request is cancelled.
    """
    event_bus = _get_event_bus(request)

    async def _generate():
        """Async generator that yields SSE-formatted event dicts."""
        queue = await event_bus.subscribe()
        try:
            while True:
                # Check for client disconnect before blocking on the queue.
                if await request.is_disconnected():
                    logger.debug("SSE client disconnected")
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Send a keep-alive comment to prevent proxy/client
                    # timeouts.  EventSourceResponse handles this as a
                    # comment line (": ping\n\n").
                    yield {"comment": "ping"}
                    continue

                yield {
                    "event": event.type.value,
                    "data": event.model_dump_json(),
                }
        except asyncio.CancelledError:
            logger.debug("SSE stream cancelled")
        finally:
            await event_bus.unsubscribe(queue)
            logger.debug("SSE subscriber cleaned up")

    return EventSourceResponse(_generate())


# ---------------------------------------------------------------------------
# GET /narrations  (Server-Sent Events)
# ---------------------------------------------------------------------------


@router.get("/narrations")
async def narration_stream(request: Request) -> EventSourceResponse:
    """Stream NarrationEvents as Server-Sent Events.

    Each SSE message has:
    * ``event`` — the source event type (e.g. ``tool_executed``)
    * ``data``  — the full NarrationEvent serialised as JSON

    The subscription is automatically cleaned up when the client
    disconnects or the request is cancelled.
    """
    narration_bus = _get_narration_bus(request)

    async def _generate():
        queue = await narration_bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    logger.debug("Narration SSE client disconnected")
                    break
                try:
                    narration = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"comment": "ping"}
                    continue
                yield {
                    "event": narration.source_event_type.value,
                    "data": narration.model_dump_json(),
                }
        except asyncio.CancelledError:
            logger.debug("Narration SSE stream cancelled")
        finally:
            await narration_bus.unsubscribe(queue)
            logger.debug("Narration SSE subscriber cleaned up")

    return EventSourceResponse(_generate())


# ---------------------------------------------------------------------------
# POST /respond  (manual text response)
# ---------------------------------------------------------------------------


@router.post("/respond")
async def manual_respond(request: Request) -> dict:
    """Manual text response override -- bypass STT, dispatch directly.

    Accepts JSON: ``{"session_id": "...", "text": "..."}``

    Returns ``{"status": "ok", ...}`` on success.
    """
    stt_engine = _get_stt_engine(request)

    if stt_engine is None:
        return {"status": "error", "reason": "stt engine not available"}

    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "reason": "invalid json"}

    session_id = body.get("session_id", "")
    text = body.get("text", "")

    if not session_id or not text:
        return {"status": "error", "reason": "session_id and text are required"}

    success = await stt_engine.handle_manual_response(session_id, text)
    return {
        "status": "ok" if success else "dispatch_failed",
        "text": text,
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# GET /responses  (Server-Sent Events)
# ---------------------------------------------------------------------------


@router.get("/responses")
async def response_stream(request: Request) -> EventSourceResponse:
    """Stream ResponseEvents as Server-Sent Events for debugging.

    Each SSE message has:
    * ``event`` — ``"response"``
    * ``data``  — the full ResponseEvent serialised as JSON
    """
    response_bus = _get_response_bus(request)

    async def _generate():
        if response_bus is None:
            return

        queue = await response_bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    response_event = await asyncio.wait_for(
                        queue.get(), timeout=15.0
                    )
                except asyncio.TimeoutError:
                    yield {"comment": "ping"}
                    continue
                yield {
                    "event": "response",
                    "data": response_event.model_dump_json(),
                }
        except asyncio.CancelledError:
            logger.debug("Response SSE stream cancelled")
        finally:
            await response_bus.unsubscribe(queue)
            logger.debug("Response SSE subscriber cleaned up")

    return EventSourceResponse(_generate())


# ---------------------------------------------------------------------------
# GET /test-tts  (diagnostic)
# ---------------------------------------------------------------------------


@router.get("/test-tts")
async def test_tts(request: Request) -> dict:
    """Diagnostic endpoint: synthesize a short phrase and play it.

    Returns details about each step so we can pinpoint TTS failures.
    """
    tts_engine = _get_tts_engine(request)
    result: dict = {
        "tts_available": tts_engine.tts_available,
        "audio_available": tts_engine.audio_available,
    }

    if not tts_engine.tts_available:
        result["error"] = "TTS provider not available"
        return result

    test_text = "Hello, this is an Echo test."
    try:
        pcm = await tts_engine._provider.synthesize(test_text)
    except Exception as exc:
        result["error"] = f"Synthesis exception: {exc}"
        return result

    if pcm is None:
        result["error"] = "Synthesis returned None"
        return result

    result["pcm_bytes"] = len(pcm)

    if len(pcm) == 0:
        result["error"] = "Synthesis returned empty PCM data"
        return result

    if tts_engine.audio_available:
        try:
            await tts_engine._player.play_immediate(pcm)
            result["played"] = True
        except Exception as exc:
            result["played"] = False
            result["play_error"] = str(exc)
    else:
        result["played"] = False
        result["play_error"] = "No audio output device"

    return result

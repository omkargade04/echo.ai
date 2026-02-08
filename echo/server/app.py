"""FastAPI application factory for Echo.

Creates the FastAPI app with lifespan management for the EventBus,
TranscriptWatcher, Summarizer, TTSEngine, and STTEngine.  The
``create_app()`` function is the single entry point used by the CLI
and ``uvicorn`` alike.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from echo.events.event_bus import EventBus
from echo.interceptors.transcript_watcher import TranscriptWatcher
from echo.server.routes import router
from echo.stt.stt_engine import STTEngine
from echo.stt.types import ResponseEvent
from echo.summarizer.summarizer import Summarizer
from echo.summarizer.types import NarrationEvent
from echo.tts.tts_engine import TTSEngine

logger = logging.getLogger(__name__)

# Module-level singletons shared across the process.
event_bus: EventBus = EventBus()
narration_bus: EventBus[NarrationEvent] = EventBus()
response_bus: EventBus[ResponseEvent] = EventBus()
transcript_watcher = TranscriptWatcher(event_bus=event_bus)
summarizer = Summarizer(event_bus=event_bus, narration_bus=narration_bus)
tts_engine = TTSEngine(narration_bus=narration_bus, event_bus=event_bus)
stt_engine = STTEngine(
    event_bus=event_bus,
    narration_bus=narration_bus,
    response_bus=response_bus,
    alert_manager=tts_engine._alert_manager,
    tts_engine=tts_engine,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup/shutdown of long-running background tasks.

    On startup the transcript file watcher, summarizer, TTS engine, and
    STT engine are started so that JSONL transcript changes are
    automatically turned into events on the bus, summarised into
    narrations, spoken aloud, and (when blocked) listened for voice
    responses.  On shutdown all are stopped cleanly in reverse order.
    """
    logger.info("Echo server starting up")
    await transcript_watcher.start()
    logger.info("Transcript watcher started")
    await summarizer.start()
    logger.info("Summarizer started")
    await tts_engine.start()
    logger.info("TTS engine started (state=%s)", tts_engine.state.value)
    await stt_engine.start()
    logger.info("STT engine started (state=%s)", stt_engine.state.value)
    try:
        yield
    finally:
        logger.info("Echo server shutting down")
        await stt_engine.stop()
        logger.info("STT engine stopped")
        await tts_engine.stop()
        logger.info("TTS engine stopped")
        await summarizer.stop()
        logger.info("Summarizer stopped")
        await transcript_watcher.stop()
        logger.info("Transcript watcher stopped")


def create_app() -> FastAPI:
    """Build and return a fully-configured FastAPI application.

    The returned app has:
    * ``app.state.event_bus`` — the shared :class:`EventBus` instance
    * ``app.state.narration_bus`` — the shared NarrationEvent bus
    * ``app.state.response_bus`` — the shared ResponseEvent bus
    * ``app.state.summarizer`` — the :class:`Summarizer` instance
    * ``app.state.tts_engine`` — the :class:`TTSEngine` instance
    * ``app.state.stt_engine`` — the :class:`STTEngine` instance
    * The ``/event``, ``/health``, ``/events``, ``/narrations``,
      ``/respond``, and ``/responses`` routes
    * Lifespan hooks for starting/stopping background services
    """
    app = FastAPI(
        title="Echo",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Attach shared instances to app state so route handlers can access
    # them via ``request.app.state.*``.
    app.state.event_bus = event_bus
    app.state.narration_bus = narration_bus
    app.state.response_bus = response_bus
    app.state.summarizer = summarizer
    app.state.tts_engine = tts_engine
    app.state.stt_engine = stt_engine

    app.include_router(router)

    logger.info("FastAPI app created")
    return app

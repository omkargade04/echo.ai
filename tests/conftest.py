"""Shared fixtures for Echo tests."""

from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
import httpx

from echo.events.event_bus import EventBus
from echo.events.types import EventType, EchoEvent
from echo.summarizer.summarizer import Summarizer
from echo.summarizer.types import NarrationEvent
from echo.tts.tts_engine import TTSEngine


@pytest.fixture
def event_bus() -> EventBus:
    """Return a fresh EventBus instance with a small queue for testing."""
    return EventBus(maxsize=16)


@pytest.fixture
def narration_bus() -> EventBus:
    """Return a fresh EventBus instance for NarrationEvents."""
    return EventBus(maxsize=16)


@pytest.fixture
def sample_event() -> EchoEvent:
    """Return a sample EchoEvent for use in tests."""
    return EchoEvent(
        type=EventType.TOOL_EXECUTED,
        session_id="test-session-001",
        source="hook",
        tool_name="Write",
        tool_input={"file_path": "/tmp/test.py", "content": "print('hi')"},
        tool_output={"status": "success"},
    )


@pytest.fixture
def summarizer(event_bus: EventBus, narration_bus: EventBus) -> Summarizer:
    """Return a Summarizer with LLMSummarizer patched to avoid Ollama.

    The LLMSummarizer.start/stop are no-ops and is_available returns False,
    so all summarization falls through to template/truncation paths.
    """
    with patch(
        "echo.summarizer.llm_summarizer.LLMSummarizer.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.summarizer.llm_summarizer.LLMSummarizer.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.summarizer.llm_summarizer.LLMSummarizer.is_available",
        new_callable=PropertyMock,
        return_value=False,
    ):
        s = Summarizer(event_bus=event_bus, narration_bus=narration_bus)
        yield s


@pytest.fixture
def tts_engine(event_bus: EventBus, narration_bus: EventBus) -> TTSEngine:
    """Return a TTSEngine with all sub-components mocked to avoid real I/O.

    ElevenLabsClient, AudioPlayer, LiveKitPublisher, and AlertManager are
    patched so no HTTP calls, audio device access, LiveKit SDK calls, or
    background alert tasks occur.
    """
    with patch(
        "echo.tts.tts_engine.ElevenLabsClient.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.ElevenLabsClient.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.ElevenLabsClient.is_available",
        new_callable=PropertyMock,
        return_value=False,
    ), patch(
        "echo.tts.tts_engine.AudioPlayer.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.AudioPlayer.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.AudioPlayer.is_available",
        new_callable=PropertyMock,
        return_value=False,
    ), patch(
        "echo.tts.tts_engine.LiveKitPublisher.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.LiveKitPublisher.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.LiveKitPublisher.is_connected",
        new_callable=PropertyMock,
        return_value=False,
    ), patch(
        "echo.tts.tts_engine.AlertManager.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.tts.tts_engine.AlertManager.stop",
        new_callable=AsyncMock,
    ):
        engine = TTSEngine(narration_bus=narration_bus, event_bus=event_bus)
        yield engine


@pytest.fixture
def app(
    event_bus: EventBus,
    narration_bus: EventBus,
    summarizer: Summarizer,
    tts_engine: TTSEngine,
):
    """Return a FastAPI test app with fresh EventBus, NarrationBus, Summarizer, and TTSEngine."""
    from fastapi import FastAPI
    from echo.server.routes import router

    test_app = FastAPI()
    test_app.state.event_bus = event_bus
    test_app.state.narration_bus = narration_bus
    test_app.state.summarizer = summarizer
    test_app.state.tts_engine = tts_engine
    test_app.include_router(router)
    return test_app


@pytest.fixture
async def async_client(app):
    """Return an httpx AsyncClient configured with the test FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client

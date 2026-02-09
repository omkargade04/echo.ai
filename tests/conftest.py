"""Shared fixtures for Echo tests."""

from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
import httpx

from echo.events.event_bus import EventBus
from echo.events.types import EventType, EchoEvent
from echo.summarizer.summarizer import Summarizer
from echo.summarizer.types import NarrationEvent
from echo.stt.stt_engine import STTEngine
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
def response_bus() -> EventBus:
    """Return a fresh EventBus instance for ResponseEvents."""
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

    The TTS provider (via create_tts_provider), AudioPlayer, LiveKitPublisher,
    and AlertManager are patched so no HTTP calls, audio device access, LiveKit
    SDK calls, or background alert tasks occur.
    """
    mock_provider = AsyncMock()
    mock_provider.is_available = False
    mock_provider.provider_name = "mock"
    mock_provider.start = AsyncMock()
    mock_provider.stop = AsyncMock()
    mock_provider.synthesize = AsyncMock(return_value=None)

    with patch(
        "echo.tts.tts_engine.create_tts_provider",
        return_value=mock_provider,
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
def stt_engine(
    event_bus: EventBus, narration_bus: EventBus, response_bus: EventBus
) -> STTEngine:
    """Return an STTEngine with all sub-components mocked to avoid real I/O.

    MicrophoneCapture, STTClient, and ResponseDispatcher are all patched
    so no real microphone access, HTTP calls, or subprocess calls occur.
    """
    with patch(
        "echo.stt.stt_engine.MicrophoneCapture.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.stt.stt_engine.MicrophoneCapture.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.stt.stt_engine.MicrophoneCapture.is_available",
        new_callable=PropertyMock,
        return_value=False,
    ), patch(
        "echo.stt.stt_engine.MicrophoneCapture.is_listening",
        new_callable=PropertyMock,
        return_value=False,
    ), patch(
        "echo.stt.stt_engine.STTClient.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.stt.stt_engine.STTClient.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.stt.stt_engine.STTClient.is_available",
        new_callable=PropertyMock,
        return_value=False,
    ), patch(
        "echo.stt.stt_engine.ResponseDispatcher.start",
        new_callable=AsyncMock,
    ), patch(
        "echo.stt.stt_engine.ResponseDispatcher.stop",
        new_callable=AsyncMock,
    ), patch(
        "echo.stt.stt_engine.ResponseDispatcher.is_available",
        new_callable=PropertyMock,
        return_value=False,
    ):
        engine = STTEngine(
            event_bus=event_bus,
            narration_bus=narration_bus,
            response_bus=response_bus,
        )
        yield engine


@pytest.fixture
def app(
    event_bus: EventBus,
    narration_bus: EventBus,
    response_bus: EventBus,
    summarizer: Summarizer,
    tts_engine: TTSEngine,
    stt_engine: STTEngine,
):
    """Return a FastAPI test app with all buses, Summarizer, TTSEngine, and STTEngine."""
    from fastapi import FastAPI
    from echo.server.routes import router

    test_app = FastAPI()
    test_app.state.event_bus = event_bus
    test_app.state.narration_bus = narration_bus
    test_app.state.response_bus = response_bus
    test_app.state.summarizer = summarizer
    test_app.state.tts_engine = tts_engine
    test_app.state.stt_engine = stt_engine
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

"""Shared fixtures for Echo tests."""

from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
import httpx

from echo.events.event_bus import EventBus
from echo.events.types import EventType, EchoEvent
from echo.summarizer.summarizer import Summarizer
from echo.summarizer.types import NarrationEvent


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
def app(event_bus: EventBus, narration_bus: EventBus, summarizer: Summarizer):
    """Return a FastAPI test app with fresh EventBus, NarrationBus, and Summarizer."""
    from fastapi import FastAPI
    from echo.server.routes import router

    test_app = FastAPI()
    test_app.state.event_bus = event_bus
    test_app.state.narration_bus = narration_bus
    test_app.state.summarizer = summarizer
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

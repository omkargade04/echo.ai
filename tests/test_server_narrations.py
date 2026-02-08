"""Tests for Stage 2 narration wiring — /narrations SSE, /health updates, regression checks.

Uses httpx.AsyncClient with ASGITransport for async testing.
The app, async_client, event_bus, narration_bus, and summarizer fixtures
are defined in conftest.py.
"""

import asyncio
import json
from unittest.mock import AsyncMock, PropertyMock, patch

import httpx
import pytest

from echo.events.event_bus import EventBus
from echo.events.types import EventType, EchoEvent
from echo.summarizer.summarizer import Summarizer
from echo.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_executed_payload(session_id: str = "sess-narr-1") -> dict:
    """Return a PostToolUse hook payload for testing."""
    return {
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello"},
        "tool_response": {"stdout": "hello"},
    }


def _agent_blocked_payload(session_id: str = "sess-narr-2") -> dict:
    """Return a Notification (agent_blocked) hook payload for testing."""
    return {
        "hook_event_name": "Notification",
        "session_id": session_id,
        "type": "permission_prompt",
        "message": "Allow file write?",
    }


def _session_start_payload(session_id: str = "sess-narr-3") -> dict:
    """Return a SessionStart hook payload for testing."""
    return {
        "hook_event_name": "SessionStart",
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# GET /health — narration fields
# ---------------------------------------------------------------------------


class TestHealthNarrationFields:
    """Verify that /health includes the new narration-related fields."""

    async def test_health_includes_narration_subscribers(
        self, async_client: httpx.AsyncClient
    ):
        response = await async_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "narration_subscribers" in body
        assert isinstance(body["narration_subscribers"], int)

    async def test_health_includes_ollama_available(
        self, async_client: httpx.AsyncClient
    ):
        response = await async_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "ollama_available" in body
        assert isinstance(body["ollama_available"], bool)
        # LLM is patched to unavailable in the test fixture
        assert body["ollama_available"] is False

    async def test_health_still_has_original_fields(
        self, async_client: httpx.AsyncClient
    ):
        """Regression: verify the original status, version, subscribers fields survive."""
        from echo import __version__

        response = await async_client.get("/health")
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        assert "subscribers" in body
        assert isinstance(body["subscribers"], int)


# ---------------------------------------------------------------------------
# GET /narrations SSE stream
# ---------------------------------------------------------------------------


class TestNarrationStream:
    """Tests for the GET /narrations SSE endpoint."""

    async def test_narration_route_exists(self, app):
        """Verify the /narrations route is registered on the app."""
        route_paths = [route.path for route in app.routes]
        assert "/narrations" in route_paths

    async def test_narration_subscriber_receives_event_via_bus(
        self, narration_bus: EventBus
    ):
        """Simulate what the /narrations handler does: subscribe and receive narrations."""
        queue = await narration_bus.subscribe()

        test_narration = NarrationEvent(
            text="Ran command: echo hello",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="sse-narr-test-1",
        )
        await narration_bus.emit(test_narration)

        received = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert received.text == "Ran command: echo hello"
        assert received.source_event_type == EventType.TOOL_EXECUTED
        assert received.priority == NarrationPriority.NORMAL

        # Verify JSON serialisation as the SSE handler would use
        data = json.loads(received.model_dump_json())
        assert data["source_event_type"] == "tool_executed"
        assert data["summarization_method"] == "template"

    async def test_narration_has_correct_structure(self, narration_bus: EventBus):
        """Verify NarrationEvent has text, priority, source_event_type, summarization_method."""
        queue = await narration_bus.subscribe()

        narration = NarrationEvent(
            text="Edited app.py",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="struct-test",
        )
        await narration_bus.emit(narration)

        received = await asyncio.wait_for(queue.get(), timeout=2.0)
        data = json.loads(received.model_dump_json())
        assert "text" in data
        assert "priority" in data
        assert "source_event_type" in data
        assert "summarization_method" in data
        assert "session_id" in data
        assert "timestamp" in data

    async def test_narration_keepalive_ping(self, narration_bus: EventBus):
        """Verify that when no events arrive, the ping keep-alive would be generated.

        We test the underlying mechanism: an empty queue with a short timeout
        should raise TimeoutError, which the SSE handler converts to a ping.
        """
        queue = await narration_bus.subscribe()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.1)


# ---------------------------------------------------------------------------
# End-to-end: POST /event -> Summarizer -> /narrations
# ---------------------------------------------------------------------------


class TestEndToEndNarration:
    """Post events via HTTP and verify narrations appear on the narration bus."""

    async def test_tool_executed_produces_narration(
        self,
        async_client: httpx.AsyncClient,
        event_bus: EventBus,
        narration_bus: EventBus,
        summarizer: Summarizer,
    ):
        """POST a tool_executed event, verify a narration is emitted."""
        # Start the summarizer so it subscribes and processes events
        await summarizer.start()
        try:
            narr_queue = await narration_bus.subscribe()

            response = await async_client.post(
                "/event", json=_tool_executed_payload()
            )
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

            # The batcher has a 500ms window; wait for the narration
            narration = await asyncio.wait_for(narr_queue.get(), timeout=3.0)
            assert narration.source_event_type == EventType.TOOL_EXECUTED
            assert narration.summarization_method == SummarizationMethod.TEMPLATE
            assert "echo hello" in narration.text.lower() or "command" in narration.text.lower()
        finally:
            await summarizer.stop()

    async def test_agent_blocked_produces_critical_narration(
        self,
        async_client: httpx.AsyncClient,
        event_bus: EventBus,
        narration_bus: EventBus,
        summarizer: Summarizer,
    ):
        """POST an agent_blocked event, verify narration has CRITICAL priority."""
        await summarizer.start()
        try:
            narr_queue = await narration_bus.subscribe()

            response = await async_client.post(
                "/event", json=_agent_blocked_payload()
            )
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

            narration = await asyncio.wait_for(narr_queue.get(), timeout=3.0)
            assert narration.source_event_type == EventType.AGENT_BLOCKED
            assert narration.priority == NarrationPriority.CRITICAL
            assert narration.summarization_method == SummarizationMethod.TEMPLATE
        finally:
            await summarizer.stop()

    async def test_multiple_events_produce_narrations(
        self,
        async_client: httpx.AsyncClient,
        event_bus: EventBus,
        narration_bus: EventBus,
        summarizer: Summarizer,
    ):
        """POST several events and verify that narrations appear for each."""
        await summarizer.start()
        try:
            narr_queue = await narration_bus.subscribe()

            # Post a session_start and then an agent_blocked
            await async_client.post("/event", json=_session_start_payload())
            await async_client.post("/event", json=_agent_blocked_payload("sess-multi"))

            narrations = []
            for _ in range(2):
                n = await asyncio.wait_for(narr_queue.get(), timeout=3.0)
                narrations.append(n)

            event_types = {n.source_event_type for n in narrations}
            assert EventType.SESSION_START in event_types
            assert EventType.AGENT_BLOCKED in event_types
        finally:
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Regression: existing endpoints still work
# ---------------------------------------------------------------------------


class TestExistingEndpointsRegression:
    """Verify Stage 1 endpoints are unaffected by Stage 2 wiring."""

    async def test_post_event_still_works(self, async_client: httpx.AsyncClient):
        """POST /event still returns expected response."""
        payload = {
            "hook_event_name": "PostToolUse",
            "session_id": "regression-1",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/test.py"},
            "tool_response": {"content": "print('hi')"},
        }
        response = await async_client.post("/event", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["event_type"] == "tool_executed"

    async def test_get_events_route_exists(self, app):
        """GET /events route is still registered."""
        route_paths = [route.path for route in app.routes]
        assert "/events" in route_paths

    async def test_get_events_subscriber_receives_event(self, event_bus: EventBus):
        """The EventBus subscribe/receive mechanism still works for /events."""
        queue = await event_bus.subscribe()

        test_event = EchoEvent(
            type=EventType.SESSION_END,
            session_id="regression-sse-1",
            source="hook",
        )
        await event_bus.emit(test_event)

        received = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert received.type == EventType.SESSION_END
        assert received.session_id == "regression-sse-1"

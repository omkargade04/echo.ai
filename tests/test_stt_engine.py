"""Tests for echo.stt.stt_engine — Core STT orchestrator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from echo.events.event_bus import EventBus
from echo.events.types import BlockReason, EchoEvent, EventType
from echo.stt.stt_engine import STTEngine
from echo.stt.types import MatchMethod, MatchResult, ResponseEvent, STTState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION = "test-session-stt"
_OTHER_SESSION = "other-session"
_PCM_BYTES = b"\x00\x01" * 100


def _make_event(
    event_type: EventType = EventType.AGENT_BLOCKED,
    session_id: str = _SESSION,
    block_reason: BlockReason | None = BlockReason.QUESTION,
    options: list[str] | None = None,
) -> EchoEvent:
    """Create an EchoEvent for testing."""
    return EchoEvent(
        type=event_type,
        session_id=session_id,
        source="hook",
        block_reason=block_reason if event_type == EventType.AGENT_BLOCKED else None,
        options=options if event_type == EventType.AGENT_BLOCKED else None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_microphone(monkeypatch):
    """Mock MicrophoneCapture — patched into stt_engine module."""
    mock = AsyncMock()
    mock.is_available = True
    mock.is_listening = False
    mock.capture_until_silence = AsyncMock(return_value=_PCM_BYTES)
    monkeypatch.setattr("echo.stt.stt_engine.MicrophoneCapture", lambda: mock)
    return mock


@pytest.fixture
def mock_stt_client(monkeypatch):
    """Mock STTClient — patched into stt_engine module."""
    mock = AsyncMock()
    mock.is_available = True
    mock.transcribe = AsyncMock(return_value="option one")
    monkeypatch.setattr("echo.stt.stt_engine.STTClient", lambda: mock)
    return mock


@pytest.fixture
def mock_dispatcher(monkeypatch):
    """Mock ResponseDispatcher — patched into stt_engine module."""
    mock = AsyncMock()
    mock.is_available = True
    mock.dispatch = AsyncMock(return_value=True)
    monkeypatch.setattr("echo.stt.stt_engine.ResponseDispatcher", lambda: mock)
    return mock


@pytest.fixture
def mock_matcher(monkeypatch):
    """Mock ResponseMatcher — patched into stt_engine module."""
    mock = MagicMock()
    mock.match = MagicMock(
        return_value=MatchResult(
            matched_text="RS256", confidence=0.95, method=MatchMethod.ORDINAL
        )
    )
    monkeypatch.setattr("echo.stt.stt_engine.ResponseMatcher", lambda: mock)
    return mock


@pytest.fixture
def event_bus():
    """A real EventBus for EchoEvents."""
    return EventBus(maxsize=64)


@pytest.fixture
def response_bus():
    """A real EventBus for ResponseEvents."""
    return EventBus(maxsize=64)


@pytest.fixture
def engine(mock_microphone, mock_stt_client, mock_dispatcher, mock_matcher, event_bus):
    """An STTEngine wired to mocked sub-components (not yet started)."""
    return STTEngine(event_bus)


@pytest.fixture
def engine_with_response_bus(
    mock_microphone, mock_stt_client, mock_dispatcher, mock_matcher, event_bus, response_bus
):
    """An STTEngine with a response bus for testing event emission."""
    return STTEngine(event_bus, response_bus=response_bus)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for STTEngine start() and stop() lifecycle."""

    async def test_start_starts_all_components(
        self, engine, mock_microphone, mock_stt_client, mock_dispatcher
    ):
        await engine.start()
        mock_microphone.start.assert_awaited_once()
        mock_stt_client.start.assert_awaited_once()
        mock_dispatcher.start.assert_awaited_once()
        await engine.stop()

    async def test_start_subscribes_to_event_bus(self, engine, event_bus):
        assert event_bus.subscriber_count == 0
        await engine.start()
        assert event_bus.subscriber_count == 1
        await engine.stop()

    async def test_start_launches_consume_task(self, engine):
        assert engine._consume_task is None
        await engine.start()
        assert engine._consume_task is not None
        assert not engine._consume_task.done()
        await engine.stop()

    async def test_stop_stops_all_components(
        self, engine, mock_microphone, mock_stt_client, mock_dispatcher
    ):
        await engine.start()
        mock_microphone.stop.reset_mock()
        mock_stt_client.stop.reset_mock()
        mock_dispatcher.stop.reset_mock()

        await engine.stop()
        mock_dispatcher.stop.assert_awaited_once()
        mock_stt_client.stop.assert_awaited_once()
        mock_microphone.stop.assert_awaited_once()

    async def test_stop_unsubscribes_from_bus(self, engine, event_bus):
        await engine.start()
        assert event_bus.subscriber_count == 1
        await engine.stop()
        assert event_bus.subscriber_count == 0

    async def test_stop_without_start(self, engine):
        """Calling stop() before start() does not crash."""
        await engine.stop()


# ---------------------------------------------------------------------------
# State property tests
# ---------------------------------------------------------------------------


class TestState:
    """Tests for the STTEngine state and availability properties."""

    async def test_state_active(self, engine, mock_stt_client, mock_microphone):
        """Both stt_client and microphone available -> ACTIVE."""
        mock_stt_client.is_available = True
        mock_microphone.is_available = True
        assert engine.state == STTState.ACTIVE

    async def test_state_degraded_no_mic(self, engine, mock_stt_client, mock_microphone):
        """STT available but mic not -> DEGRADED."""
        mock_stt_client.is_available = True
        mock_microphone.is_available = False
        assert engine.state == STTState.DEGRADED

    async def test_state_degraded_no_stt(self, engine, mock_stt_client, mock_microphone):
        """Mic available but STT not -> DEGRADED."""
        mock_stt_client.is_available = False
        mock_microphone.is_available = True
        assert engine.state == STTState.DEGRADED

    async def test_state_disabled(self, engine, mock_stt_client, mock_microphone):
        """Neither available -> DISABLED."""
        mock_stt_client.is_available = False
        mock_microphone.is_available = False
        assert engine.state == STTState.DISABLED

    async def test_state_listening(self, engine, mock_microphone):
        """When microphone is_listening=True -> LISTENING (takes precedence)."""
        mock_microphone.is_listening = True
        assert engine.state == STTState.LISTENING


# ---------------------------------------------------------------------------
# Event handling tests
# ---------------------------------------------------------------------------


class TestEventHandling:
    """Tests for how the STTEngine handles events from the EventBus."""

    async def test_blocked_event_starts_listening(self, engine, event_bus, mock_microphone):
        await engine.start()
        event = _make_event(
            EventType.AGENT_BLOCKED,
            options=["RS256", "HS256"],
            block_reason=BlockReason.QUESTION,
        )
        await event_bus.emit(event)
        await asyncio.sleep(0.05)
        # Listen task should have been created and started capture
        mock_microphone.capture_until_silence.assert_awaited()
        await engine.stop()

    async def test_non_blocked_event_cancels_listening(
        self, engine, event_bus, mock_microphone
    ):
        # Make capture block so we can cancel it
        capture_started = asyncio.Event()

        async def slow_capture(**kwargs):
            capture_started.set()
            await asyncio.sleep(10)
            return _PCM_BYTES

        mock_microphone.capture_until_silence = AsyncMock(side_effect=slow_capture)

        await engine.start()
        # Emit blocked event to start listening
        blocked = _make_event(
            EventType.AGENT_BLOCKED,
            session_id=_SESSION,
            options=["RS256"],
        )
        await event_bus.emit(blocked)
        # Wait for capture to begin
        await asyncio.wait_for(capture_started.wait(), timeout=2.0)

        # Emit a non-blocked event for the same session
        resolved = _make_event(EventType.TOOL_EXECUTED, session_id=_SESSION)
        await event_bus.emit(resolved)
        await asyncio.sleep(0.1)

        # Current session should be cleared
        assert engine._current_session is None
        await engine.stop()

    async def test_blocked_event_without_options_still_works(
        self, engine, event_bus, mock_microphone
    ):
        await engine.start()
        event = _make_event(
            EventType.AGENT_BLOCKED,
            options=None,
            block_reason=BlockReason.QUESTION,
        )
        await event_bus.emit(event)
        await asyncio.sleep(0.05)
        mock_microphone.capture_until_silence.assert_awaited()
        await engine.stop()

    async def test_second_blocked_event_replaces_first(
        self, engine, event_bus, mock_microphone
    ):
        """Second blocked event cancels the first listen task."""
        capture_started = asyncio.Event()
        call_count = 0

        async def slow_then_fast(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                capture_started.set()
                await asyncio.sleep(10)
                return _PCM_BYTES
            return _PCM_BYTES

        mock_microphone.capture_until_silence = AsyncMock(side_effect=slow_then_fast)

        await engine.start()
        first = _make_event(
            EventType.AGENT_BLOCKED,
            session_id="session-1",
            options=["A"],
        )
        await event_bus.emit(first)
        await asyncio.wait_for(capture_started.wait(), timeout=2.0)

        second = _make_event(
            EventType.AGENT_BLOCKED,
            session_id="session-2",
            options=["B"],
        )
        await event_bus.emit(second)
        await asyncio.sleep(0.1)

        # Second session should be current
        # (first was cancelled, second ran and completed)
        assert mock_microphone.capture_until_silence.await_count >= 2
        await engine.stop()

    async def test_non_blocked_for_different_session_ignored(
        self, engine, event_bus, mock_microphone
    ):
        """A non-blocked event for a different session does not cancel listening."""
        capture_started = asyncio.Event()

        async def slow_capture(**kwargs):
            capture_started.set()
            await asyncio.sleep(10)
            return _PCM_BYTES

        mock_microphone.capture_until_silence = AsyncMock(side_effect=slow_capture)

        await engine.start()
        blocked = _make_event(
            EventType.AGENT_BLOCKED,
            session_id=_SESSION,
            options=["RS256"],
        )
        await event_bus.emit(blocked)
        await asyncio.wait_for(capture_started.wait(), timeout=2.0)

        # Emit event for a DIFFERENT session
        other = _make_event(EventType.TOOL_EXECUTED, session_id=_OTHER_SESSION)
        await event_bus.emit(other)
        await asyncio.sleep(0.05)

        # Current session should still be active
        assert engine._current_session == _SESSION
        await engine.stop()

    async def test_consume_loop_handles_exception(
        self, engine, event_bus, mock_microphone, mock_stt_client
    ):
        """An error in _handle_event does not crash the consume loop."""
        # Make capture raise on first call, succeed on second
        mock_microphone.capture_until_silence = AsyncMock(
            side_effect=[RuntimeError("Boom"), _PCM_BYTES]
        )

        await engine.start()
        first = _make_event(EventType.AGENT_BLOCKED, session_id="s1", options=["A"])
        await event_bus.emit(first)
        await asyncio.sleep(0.1)

        second = _make_event(EventType.AGENT_BLOCKED, session_id="s2", options=["B"])
        await event_bus.emit(second)
        await asyncio.sleep(0.1)

        # The consume loop should still be running
        assert not engine._consume_task.done()
        # Second capture should have been attempted
        assert mock_microphone.capture_until_silence.await_count == 2
        await engine.stop()


# ---------------------------------------------------------------------------
# Listen and respond flow tests
# ---------------------------------------------------------------------------


class TestListenAndRespond:
    """Tests for the _listen_and_respond pipeline."""

    async def test_full_flow_capture_transcribe_match_dispatch(
        self,
        engine,
        event_bus,
        mock_microphone,
        mock_stt_client,
        mock_matcher,
        mock_dispatcher,
    ):
        """Happy path: capture -> transcribe -> match -> dispatch."""
        await engine.start()
        event = _make_event(
            EventType.AGENT_BLOCKED,
            options=["RS256", "HS256"],
            block_reason=BlockReason.QUESTION,
        )
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_microphone.capture_until_silence.assert_awaited_once()
        mock_stt_client.transcribe.assert_awaited_once_with(_PCM_BYTES)
        mock_matcher.match.assert_called_once_with(
            "option one", ["RS256", "HS256"], BlockReason.QUESTION
        )
        mock_dispatcher.dispatch.assert_awaited_once_with("RS256")
        await engine.stop()

    async def test_mic_unavailable_skips(
        self, engine, event_bus, mock_microphone, mock_stt_client
    ):
        """When mic is not available, capture is skipped entirely."""
        mock_microphone.is_available = False
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["A"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_microphone.capture_until_silence.assert_not_awaited()
        mock_stt_client.transcribe.assert_not_awaited()
        await engine.stop()

    async def test_no_speech_returns_early(
        self, engine, event_bus, mock_microphone, mock_stt_client
    ):
        """When capture returns None (no speech), transcription is skipped."""
        mock_microphone.capture_until_silence = AsyncMock(return_value=None)
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["A"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_stt_client.transcribe.assert_not_awaited()
        await engine.stop()

    async def test_stt_unavailable_skips(
        self, engine, event_bus, mock_microphone, mock_stt_client, mock_dispatcher
    ):
        """When STT client is not available, dispatch is skipped."""
        mock_stt_client.is_available = False
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["A"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_microphone.capture_until_silence.assert_awaited_once()
        mock_stt_client.transcribe.assert_not_awaited()
        mock_dispatcher.dispatch.assert_not_awaited()
        await engine.stop()

    async def test_low_confidence_not_dispatched(
        self, engine, event_bus, mock_matcher, mock_dispatcher, monkeypatch
    ):
        """When match confidence is below threshold, response is not dispatched."""
        mock_matcher.match = MagicMock(
            return_value=MatchResult(
                matched_text="maybe",
                confidence=0.3,
                method=MatchMethod.FUZZY,
            )
        )
        # Ensure the threshold is above 0.3
        monkeypatch.setattr("echo.stt.stt_engine.STT_CONFIDENCE_THRESHOLD", 0.6)

        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["RS256", "HS256"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_dispatcher.dispatch.assert_not_awaited()
        await engine.stop()

    async def test_verbatim_match_bypasses_confidence_check(
        self, engine, event_bus, mock_matcher, mock_dispatcher, monkeypatch
    ):
        """VERBATIM matches are dispatched regardless of confidence."""
        mock_matcher.match = MagicMock(
            return_value=MatchResult(
                matched_text="option one",
                confidence=0.3,
                method=MatchMethod.VERBATIM,
            )
        )
        monkeypatch.setattr("echo.stt.stt_engine.STT_CONFIDENCE_THRESHOLD", 0.6)

        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=None)
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_dispatcher.dispatch.assert_awaited_once_with("option one")
        await engine.stop()

    async def test_transcription_returns_none_skips_match(
        self, engine, event_bus, mock_stt_client, mock_matcher
    ):
        """When transcription returns None, matching is skipped."""
        mock_stt_client.transcribe = AsyncMock(return_value=None)
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["A"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_matcher.match.assert_not_called()
        await engine.stop()

    async def test_dispatch_failure_does_not_crash(
        self, engine, event_bus, mock_dispatcher
    ):
        """If dispatch returns False, the pipeline continues without error."""
        mock_dispatcher.dispatch = AsyncMock(return_value=False)
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["RS256"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_dispatcher.dispatch.assert_awaited_once()
        assert not engine._consume_task.done()
        await engine.stop()

    async def test_dispatch_unavailable_does_not_crash(
        self, engine, event_bus, mock_dispatcher
    ):
        """If dispatcher is not available, pipeline completes without dispatch."""
        mock_dispatcher.is_available = False
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["RS256"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_dispatcher.dispatch.assert_not_awaited()
        assert not engine._consume_task.done()
        await engine.stop()


# ---------------------------------------------------------------------------
# Manual response tests
# ---------------------------------------------------------------------------


class TestManualResponse:
    """Tests for handle_manual_response."""

    async def test_handle_manual_response_success(
        self, engine, mock_dispatcher
    ):
        result = await engine.handle_manual_response(_SESSION, "RS256")
        assert result is True
        mock_dispatcher.dispatch.assert_awaited_once_with("RS256")

    async def test_handle_manual_response_dispatch_unavailable(
        self, engine, mock_dispatcher
    ):
        mock_dispatcher.is_available = False
        result = await engine.handle_manual_response(_SESSION, "RS256")
        assert result is False
        mock_dispatcher.dispatch.assert_not_awaited()

    async def test_handle_manual_response_emits_response_event(
        self, engine_with_response_bus, response_bus
    ):
        queue = await response_bus.subscribe()
        await engine_with_response_bus.handle_manual_response(_SESSION, "RS256")

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, ResponseEvent)
        assert event.text == "RS256"
        assert event.transcript == "RS256"
        assert event.session_id == _SESSION
        assert event.match_method == MatchMethod.VERBATIM
        assert event.confidence == 1.0
        await response_bus.unsubscribe(queue)


# ---------------------------------------------------------------------------
# Response bus emission tests
# ---------------------------------------------------------------------------


class TestResponseBusEmission:
    """Tests for ResponseEvent emission to response_bus."""

    async def test_listen_flow_emits_response_event(
        self, engine_with_response_bus, event_bus, response_bus
    ):
        queue = await response_bus.subscribe()
        await engine_with_response_bus.start()
        event = _make_event(
            EventType.AGENT_BLOCKED,
            options=["RS256", "HS256"],
            block_reason=BlockReason.QUESTION,
        )
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        resp = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(resp, ResponseEvent)
        assert resp.text == "RS256"
        assert resp.session_id == _SESSION
        assert resp.match_method == MatchMethod.ORDINAL
        assert resp.confidence == 0.95
        assert resp.options == ["RS256", "HS256"]
        await response_bus.unsubscribe(queue)
        await engine_with_response_bus.stop()

    async def test_no_response_bus_does_not_crash(
        self, engine, event_bus, mock_dispatcher
    ):
        """When response_bus is None, pipeline completes normally."""
        assert engine._response_bus is None
        await engine.start()
        event = _make_event(EventType.AGENT_BLOCKED, options=["RS256"])
        await event_bus.emit(event)
        await asyncio.sleep(0.1)

        mock_dispatcher.dispatch.assert_awaited_once()
        await engine.stop()

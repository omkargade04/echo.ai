"""Stage 5 integration tests for the Echo STT subsystem.

These tests verify the END-TO-END flow of Stage 5 by using real component
classes (STTEngine, ResponseMatcher, etc.) while mocking only the external
I/O boundaries (sounddevice for microphone, httpx for Whisper API, subprocess
for dispatch).

Test categories:
1. Full pipeline: Event -> Listen -> Transcribe -> Match -> Dispatch
2. Options flow through pipeline
3. Alert resolution cancels listening
4. POST /respond integration
5. Graceful degradation
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import numpy as np
import pytest

from echo.events.event_bus import EventBus
from echo.events.types import BlockReason, EchoEvent, EventType
from echo.stt.stt_engine import STTEngine
from echo.stt.types import MatchMethod, ResponseEvent, STTState
from echo.summarizer.types import NarrationEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio_bytes(
    duration: float = 0.5, sample_rate: int = 16000, loud: bool = True
) -> bytes:
    """Generate fake PCM int16 audio bytes."""
    num_samples = int(sample_rate * duration)
    if loud:
        audio = (np.sin(np.linspace(0, 2 * np.pi * 440, num_samples)) * 16000).astype(
            np.int16
        )
    else:
        audio = np.zeros(num_samples, dtype=np.int16)
    return audio.tobytes()


_DEFAULT_OPTIONS = ["RS256", "HS256"]
_SENTINEL = object()


def _make_blocked_event(
    session_id: str = "test-session",
    options: list[str] | None | object = _SENTINEL,
    block_reason: BlockReason = BlockReason.QUESTION,
    message: str = "Pick one",
) -> EchoEvent:
    resolved_options = _DEFAULT_OPTIONS if options is _SENTINEL else options
    return EchoEvent(
        type=EventType.AGENT_BLOCKED,
        session_id=session_id,
        source="hook",
        block_reason=block_reason,
        message=message,
        options=resolved_options,
    )


def _make_tool_event(session_id: str = "test-session") -> EchoEvent:
    return EchoEvent(
        type=EventType.TOOL_EXECUTED,
        session_id=session_id,
        source="hook",
        tool_name="Read",
        tool_input={"file_path": "/tmp/test.py"},
    )


# ---------------------------------------------------------------------------
# Fixtures for integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def int_event_bus():
    """A real EventBus for integration tests."""
    return EventBus(maxsize=64)


@pytest.fixture
def int_response_bus():
    """A real EventBus for ResponseEvents in integration tests."""
    return EventBus(maxsize=64)


def _create_engine(
    event_bus: EventBus,
    response_bus: EventBus,
    *,
    mic_available: bool = True,
    stt_available: bool = True,
    dispatch_available: bool = True,
    capture_return=None,
    transcribe_return: str | None = "option one",
) -> tuple[STTEngine, AsyncMock, AsyncMock, AsyncMock]:
    """Build an STTEngine with mocked sub-components.

    Returns (engine, mock_mic, mock_stt_client, mock_dispatcher).
    The ResponseMatcher is left as the REAL implementation for integration testing.
    """
    mock_mic = AsyncMock()
    mock_mic.is_available = mic_available
    mock_mic.is_listening = False
    mock_mic.capture_until_silence = AsyncMock(
        return_value=capture_return if capture_return is not None else _make_audio_bytes()
    )

    mock_stt = AsyncMock()
    mock_stt.is_available = stt_available
    mock_stt.transcribe = AsyncMock(return_value=transcribe_return)

    mock_dispatch = AsyncMock()
    mock_dispatch.is_available = dispatch_available
    mock_dispatch.dispatch = AsyncMock(return_value=True)

    with patch("echo.stt.stt_engine.MicrophoneCapture", return_value=mock_mic), \
         patch("echo.stt.stt_engine.STTClient", return_value=mock_stt), \
         patch("echo.stt.stt_engine.ResponseDispatcher", return_value=mock_dispatch):
        engine = STTEngine(
            event_bus=event_bus,
            response_bus=response_bus,
        )

    return engine, mock_mic, mock_stt, mock_dispatch


# ---------------------------------------------------------------------------
# 1. Full pipeline: Event -> Listen -> Transcribe -> Match -> Dispatch
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end tests for the STT pipeline with real ResponseMatcher."""

    async def test_full_flow_ordinal_match(self, int_event_bus, int_response_bus):
        """agent_blocked event -> capture audio -> transcribe "option one" -> match to RS256 -> dispatch."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus, transcribe_return="option one"
        )

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(
                _make_blocked_event(options=["RS256", "HS256"])
            )
            await asyncio.sleep(0.2)

            # Verify the full chain executed
            mock_mic.capture_until_silence.assert_awaited_once()
            mock_stt.transcribe.assert_awaited_once()
            mock_dispatch.dispatch.assert_awaited_once_with("RS256")

            # Verify response event on the bus
            resp = await asyncio.wait_for(response_queue.get(), timeout=1.0)
            assert isinstance(resp, ResponseEvent)
            assert resp.text == "RS256"
            assert resp.transcript == "option one"
            assert resp.match_method == MatchMethod.ORDINAL
            assert resp.confidence == 0.95
            assert resp.options == ["RS256", "HS256"]
            assert resp.session_id == "test-session"
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()

    async def test_full_flow_yes_no_match(self, int_event_bus, int_response_bus):
        """Permission prompt with "yes" -> match to first option via YES_NO."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus, transcribe_return="yes"
        )

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(
                _make_blocked_event(
                    options=["Allow", "Deny"],
                    block_reason=BlockReason.PERMISSION_PROMPT,
                )
            )
            await asyncio.sleep(0.2)

            mock_dispatch.dispatch.assert_awaited_once_with("Allow")

            resp = await asyncio.wait_for(response_queue.get(), timeout=1.0)
            assert resp.text == "Allow"
            assert resp.match_method == MatchMethod.YES_NO
            assert resp.confidence == 0.9
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()

    async def test_full_flow_direct_match(self, int_event_bus, int_response_bus):
        """Transcript says "RS256" directly -> match to RS256 via DIRECT."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus, transcribe_return="RS256"
        )

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(
                _make_blocked_event(options=["RS256", "HS256"])
            )
            await asyncio.sleep(0.2)

            mock_dispatch.dispatch.assert_awaited_once_with("RS256")

            resp = await asyncio.wait_for(response_queue.get(), timeout=1.0)
            assert resp.text == "RS256"
            assert resp.match_method == MatchMethod.DIRECT
            assert resp.confidence == 0.85
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()

    async def test_full_flow_no_speech_no_dispatch(self, int_event_bus, int_response_bus):
        """No speech detected (capture returns None) -> no dispatch, no response event."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus
        )
        mock_mic.capture_until_silence = AsyncMock(return_value=None)

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(
                _make_blocked_event(options=["RS256", "HS256"])
            )
            await asyncio.sleep(0.2)

            mock_mic.capture_until_silence.assert_awaited_once()
            mock_stt.transcribe.assert_not_awaited()
            mock_dispatch.dispatch.assert_not_awaited()

            # No response event should be emitted
            assert response_queue.empty()
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()


# ---------------------------------------------------------------------------
# 2. Options flow through pipeline
# ---------------------------------------------------------------------------


class TestOptionsPipelineFlow:
    """Verify options flow correctly through the entire pipeline."""

    async def test_options_from_event_to_response_event(self, int_event_bus, int_response_bus):
        """EchoEvent.options -> STTEngine -> ResponseEvent.options."""
        opts = ["Ed25519", "RSA-4096", "ECDSA"]
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus, transcribe_return="option two"
        )

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(_make_blocked_event(options=opts))
            await asyncio.sleep(0.2)

            resp = await asyncio.wait_for(response_queue.get(), timeout=1.0)
            assert resp.options == opts
            assert resp.text == "RSA-4096"
            assert resp.match_method == MatchMethod.ORDINAL
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()

    async def test_options_none_uses_verbatim(self, int_event_bus, int_response_bus):
        """When options is None, matcher returns verbatim and dispatch still works."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus, transcribe_return="deploy to staging"
        )

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(
                _make_blocked_event(options=None)
            )
            await asyncio.sleep(0.2)

            resp = await asyncio.wait_for(response_queue.get(), timeout=1.0)
            assert resp.text == "deploy to staging"
            assert resp.match_method == MatchMethod.VERBATIM
            assert resp.confidence == 1.0
            assert resp.options is None

            mock_dispatch.dispatch.assert_awaited_once_with("deploy to staging")
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()

    async def test_options_flow_through_narration(self):
        """EchoEvent.options -> TemplateEngine -> NarrationEvent.options (verify pipeline)."""
        from echo.summarizer.template_engine import TemplateEngine

        te = TemplateEngine()
        event = _make_blocked_event(
            options=["RS256", "HS256", "EdDSA"],
            block_reason=BlockReason.QUESTION,
        )
        narration = te.render(event)
        assert isinstance(narration, NarrationEvent)
        assert narration.options == ["RS256", "HS256", "EdDSA"]
        assert narration.block_reason == BlockReason.QUESTION
        assert "Option one: RS256" in narration.text
        assert "Option two: HS256" in narration.text
        assert "Option three: EdDSA" in narration.text


# ---------------------------------------------------------------------------
# 3. Alert resolution cancels listening
# ---------------------------------------------------------------------------


class TestAlertResolution:
    """Verify that alert resolution correctly cancels STT listening."""

    async def test_non_blocked_event_cancels_listening(self, int_event_bus, int_response_bus):
        """A non-blocked event for the same session cancels the listen task."""
        capture_started = asyncio.Event()

        async def slow_capture(**kwargs):
            capture_started.set()
            await asyncio.sleep(10)
            return _make_audio_bytes()

        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus
        )
        mock_mic.capture_until_silence = AsyncMock(side_effect=slow_capture)

        await engine.start()

        try:
            # Start listening with blocked event
            await int_event_bus.emit(
                _make_blocked_event(session_id="sess-cancel", options=["A", "B"])
            )
            await asyncio.wait_for(capture_started.wait(), timeout=2.0)
            assert engine._current_session == "sess-cancel"

            # Resolve the alert with a non-blocked event for the same session
            await int_event_bus.emit(_make_tool_event(session_id="sess-cancel"))
            await asyncio.sleep(0.2)

            # Current session should be cleared
            assert engine._current_session is None
            # No dispatch should have occurred
            mock_dispatch.dispatch.assert_not_awaited()
        finally:
            await engine.stop()

    async def test_different_session_does_not_cancel(self, int_event_bus, int_response_bus):
        """A non-blocked event for a different session does NOT cancel listening."""
        capture_started = asyncio.Event()

        async def slow_capture(**kwargs):
            capture_started.set()
            await asyncio.sleep(10)
            return _make_audio_bytes()

        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus
        )
        mock_mic.capture_until_silence = AsyncMock(side_effect=slow_capture)

        await engine.start()

        try:
            # Start listening for session-A
            await int_event_bus.emit(
                _make_blocked_event(session_id="session-A", options=["X"])
            )
            await asyncio.wait_for(capture_started.wait(), timeout=2.0)
            assert engine._current_session == "session-A"

            # Emit a tool_executed event for a DIFFERENT session
            await int_event_bus.emit(_make_tool_event(session_id="session-B"))
            await asyncio.sleep(0.1)

            # Current session should still be session-A
            assert engine._current_session == "session-A"
        finally:
            await engine.stop()


# ---------------------------------------------------------------------------
# 4. POST /respond integration
# ---------------------------------------------------------------------------


class TestManualResponseIntegration:
    """Verify POST /respond works end-to-end with real STTEngine."""

    @pytest.fixture
    def _integration_app(self, int_event_bus, int_response_bus):
        """Build a minimal FastAPI app with a real STTEngine (mocked sub-components)."""
        from fastapi import FastAPI
        from unittest.mock import PropertyMock
        from echo.server.routes import router
        from echo.summarizer.summarizer import Summarizer
        from echo.tts.tts_engine import TTSEngine

        engine, _, _, _ = _create_engine(
            int_event_bus, int_response_bus
        )

        with patch(
            "echo.summarizer.llm_summarizer.LLMSummarizer.start", new_callable=AsyncMock
        ), patch(
            "echo.summarizer.llm_summarizer.LLMSummarizer.stop", new_callable=AsyncMock
        ), patch(
            "echo.summarizer.llm_summarizer.LLMSummarizer.is_available",
            new_callable=PropertyMock, return_value=False,
        ):
            narration_bus = EventBus(maxsize=16)
            summarizer = Summarizer(event_bus=int_event_bus, narration_bus=narration_bus)

        _mock_prov = AsyncMock()
        _mock_prov.is_available = False
        _mock_prov.provider_name = "mock"
        _mock_prov.start = AsyncMock()
        _mock_prov.stop = AsyncMock()
        _mock_prov.synthesize = AsyncMock(return_value=None)

        with patch("echo.tts.tts_engine.create_tts_provider", return_value=_mock_prov), \
             patch("echo.tts.tts_engine.AudioPlayer.start", new_callable=AsyncMock), \
             patch("echo.tts.tts_engine.AudioPlayer.stop", new_callable=AsyncMock), \
             patch("echo.tts.tts_engine.AudioPlayer.is_available", new_callable=PropertyMock, return_value=False), \
             patch("echo.tts.tts_engine.LiveKitPublisher.start", new_callable=AsyncMock), \
             patch("echo.tts.tts_engine.LiveKitPublisher.stop", new_callable=AsyncMock), \
             patch("echo.tts.tts_engine.LiveKitPublisher.is_connected", new_callable=PropertyMock, return_value=False), \
             patch("echo.tts.tts_engine.AlertManager.start", new_callable=AsyncMock), \
             patch("echo.tts.tts_engine.AlertManager.stop", new_callable=AsyncMock):
            tts_engine = TTSEngine(narration_bus=narration_bus, event_bus=int_event_bus)

        app = FastAPI()
        app.state.event_bus = int_event_bus
        app.state.narration_bus = narration_bus
        app.state.response_bus = int_response_bus
        app.state.summarizer = summarizer
        app.state.tts_engine = tts_engine
        app.state.stt_engine = engine
        app.include_router(router)
        return app

    async def test_manual_respond_dispatches(self, _integration_app, int_response_bus):
        """POST /respond with valid body dispatches to terminal."""
        transport = httpx.ASGITransport(app=_integration_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/respond",
                json={"session_id": "sess-manual", "text": "yes"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"
            assert body["text"] == "yes"
            assert body["session_id"] == "sess-manual"

    async def test_manual_respond_emits_response_event(
        self, _integration_app, int_response_bus
    ):
        """POST /respond emits a ResponseEvent on the response_bus."""
        response_queue = await int_response_bus.subscribe()

        try:
            transport = httpx.ASGITransport(app=_integration_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                await client.post(
                    "/respond",
                    json={"session_id": "sess-emit", "text": "allow"},
                )

            resp = await asyncio.wait_for(response_queue.get(), timeout=1.0)
            assert isinstance(resp, ResponseEvent)
            assert resp.text == "allow"
            assert resp.transcript == "allow"
            assert resp.session_id == "sess-emit"
            assert resp.match_method == MatchMethod.VERBATIM
            assert resp.confidence == 1.0
        finally:
            await int_response_bus.unsubscribe(response_queue)


# ---------------------------------------------------------------------------
# 5. Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Verify the system degrades gracefully."""

    async def test_no_stt_key_engine_starts_disabled(self, int_event_bus, int_response_bus):
        """Without STT/mic available, engine starts disabled. POST /respond still works."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus,
            int_response_bus,
            mic_available=False,
            stt_available=False,
        )

        assert engine.state == STTState.DISABLED

        await engine.start()

        try:
            # State is DISABLED
            assert engine.state == STTState.DISABLED

            # Blocked event should not trigger capture
            await int_event_bus.emit(
                _make_blocked_event(options=["A", "B"])
            )
            await asyncio.sleep(0.2)
            mock_mic.capture_until_silence.assert_not_awaited()

            # But manual response still works
            result = await engine.handle_manual_response("sess-deg", "A")
            assert result is True
            mock_dispatch.dispatch.assert_awaited_once_with("A")
        finally:
            await engine.stop()

    async def test_transcription_failure_does_not_crash(self, int_event_bus, int_response_bus):
        """When STT transcription returns None, pipeline continues without crashing."""
        engine, mock_mic, mock_stt, mock_dispatch = _create_engine(
            int_event_bus, int_response_bus, transcribe_return=None
        )

        await engine.start()
        response_queue = await int_response_bus.subscribe()

        try:
            await int_event_bus.emit(
                _make_blocked_event(options=["RS256", "HS256"])
            )
            await asyncio.sleep(0.2)

            mock_mic.capture_until_silence.assert_awaited_once()
            mock_stt.transcribe.assert_awaited_once()
            mock_dispatch.dispatch.assert_not_awaited()

            # No response event should be emitted
            assert response_queue.empty()

            # Consume loop should still be running
            assert not engine._consume_task.done()
        finally:
            await int_response_bus.unsubscribe(response_queue)
            await engine.stop()

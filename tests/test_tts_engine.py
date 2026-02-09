"""Tests for echo.tts.tts_engine — Core TTS orchestrator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from echo.events.event_bus import EventBus
from echo.events.types import BlockReason, EventType
from echo.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)
from echo.tts.tts_engine import TTSEngine
from echo.tts.types import TTSState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION = "test-session-tts"
_PCM_BYTES = b"\x00\x01" * 100


def _make_narration(
    text: str = "Test narration.",
    priority: NarrationPriority = NarrationPriority.NORMAL,
    source_event_type: EventType = EventType.AGENT_MESSAGE,
    block_reason: BlockReason | None = None,
) -> NarrationEvent:
    """Create a NarrationEvent for testing."""
    return NarrationEvent(
        text=text,
        priority=priority,
        source_event_type=source_event_type,
        summarization_method=SummarizationMethod.TEMPLATE,
        session_id=_SESSION,
        block_reason=block_reason,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_elevenlabs(monkeypatch):
    """Mock ElevenLabsClient — patched into tts_engine module."""
    mock = AsyncMock()
    mock.is_available = True
    mock.synthesize = AsyncMock(return_value=_PCM_BYTES)
    monkeypatch.setattr("echo.tts.tts_engine.ElevenLabsClient", lambda: mock)
    return mock


@pytest.fixture
def mock_player(monkeypatch):
    """Mock AudioPlayer — patched into tts_engine module."""
    mock = AsyncMock()
    mock.is_available = True
    mock.queue_depth = 0
    monkeypatch.setattr("echo.tts.tts_engine.AudioPlayer", lambda: mock)
    return mock


@pytest.fixture
def mock_livekit(monkeypatch):
    """Mock LiveKitPublisher — patched into tts_engine module."""
    mock = AsyncMock()
    mock.is_connected = False
    mock.is_configured = False
    monkeypatch.setattr("echo.tts.tts_engine.LiveKitPublisher", lambda: mock)
    return mock


@pytest.fixture
def narration_bus():
    """A real EventBus for narration events."""
    return EventBus(maxsize=64)


@pytest.fixture
def engine(mock_elevenlabs, mock_player, mock_livekit, narration_bus):
    """A TTSEngine wired to mocked sub-components (not yet started)."""
    return TTSEngine(narration_bus)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for TTSEngine start() and stop() lifecycle."""

    async def test_start_starts_all_components(self, engine, mock_elevenlabs, mock_player, mock_livekit):
        await engine.start()
        mock_elevenlabs.start.assert_awaited_once()
        mock_player.start.assert_awaited_once()
        mock_livekit.start.assert_awaited_once()
        await engine.stop()

    async def test_start_subscribes_to_bus(self, engine, narration_bus):
        assert narration_bus.subscriber_count == 0
        await engine.start()
        assert narration_bus.subscriber_count == 1
        await engine.stop()

    async def test_start_launches_consume_task(self, engine):
        assert engine._consume_task is None
        await engine.start()
        assert engine._consume_task is not None
        assert not engine._consume_task.done()
        await engine.stop()

    async def test_stop_stops_all_components(self, engine, mock_elevenlabs, mock_player, mock_livekit):
        await engine.start()
        # Reset call counts from start()
        mock_elevenlabs.stop.reset_mock()
        mock_player.stop.reset_mock()
        mock_livekit.stop.reset_mock()

        await engine.stop()
        mock_livekit.stop.assert_awaited_once()
        mock_player.stop.assert_awaited_once()
        mock_elevenlabs.stop.assert_awaited_once()

    async def test_stop_unsubscribes_from_bus(self, engine, narration_bus):
        await engine.start()
        assert narration_bus.subscriber_count == 1
        await engine.stop()
        assert narration_bus.subscriber_count == 0

    async def test_stop_cancels_consume_task(self, engine):
        await engine.start()
        task = engine._consume_task
        await engine.stop()
        assert engine._consume_task is None
        assert task.done()

    async def test_stop_without_start(self, engine):
        """Calling stop() before start() does not crash."""
        await engine.stop()

    async def test_start_stop_start(self, engine, narration_bus):
        """Engine can be restarted after being stopped."""
        await engine.start()
        assert narration_bus.subscriber_count == 1
        await engine.stop()
        assert narration_bus.subscriber_count == 0
        await engine.start()
        assert narration_bus.subscriber_count == 1
        await engine.stop()


# ---------------------------------------------------------------------------
# State property tests
# ---------------------------------------------------------------------------


class TestState:
    """Tests for the TTSEngine state and availability properties."""

    async def test_state_active(self, engine, mock_elevenlabs, mock_player):
        """Both elevenlabs and player available -> ACTIVE."""
        mock_elevenlabs.is_available = True
        mock_player.is_available = True
        assert engine.state == TTSState.ACTIVE

    async def test_state_degraded_no_audio(self, engine, mock_elevenlabs, mock_player):
        """ElevenLabs available but player not -> DEGRADED."""
        mock_elevenlabs.is_available = True
        mock_player.is_available = False
        assert engine.state == TTSState.DEGRADED

    async def test_state_degraded_no_tts(self, engine, mock_elevenlabs, mock_player):
        """Player available but elevenlabs not -> DEGRADED."""
        mock_elevenlabs.is_available = False
        mock_player.is_available = True
        assert engine.state == TTSState.DEGRADED

    async def test_state_disabled(self, engine, mock_elevenlabs, mock_player):
        """Neither available -> DISABLED."""
        mock_elevenlabs.is_available = False
        mock_player.is_available = False
        assert engine.state == TTSState.DISABLED

    async def test_tts_available_property(self, engine, mock_elevenlabs):
        """tts_available reflects elevenlabs.is_available."""
        mock_elevenlabs.is_available = True
        assert engine.tts_available is True
        mock_elevenlabs.is_available = False
        assert engine.tts_available is False

    async def test_livekit_connected_property(self, engine, mock_livekit):
        """livekit_connected reflects livekit.is_connected."""
        mock_livekit.is_connected = False
        assert engine.livekit_connected is False
        mock_livekit.is_connected = True
        assert engine.livekit_connected is True


# ---------------------------------------------------------------------------
# CRITICAL routing tests
# ---------------------------------------------------------------------------


class TestCriticalRouting:
    """Tests for CRITICAL priority narration processing."""

    async def test_critical_interrupts_player(self, engine, narration_bus, mock_player):
        await engine.start()
        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.interrupt.assert_awaited()
        await engine.stop()

    async def test_critical_plays_alert(self, engine, narration_bus, mock_player):
        await engine.start()
        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.play_alert.assert_awaited()
        await engine.stop()

    async def test_critical_synthesizes_text(self, engine, narration_bus, mock_elevenlabs):
        await engine.start()
        narration = _make_narration("Permission needed!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_elevenlabs.synthesize.assert_awaited_with("Permission needed!")
        await engine.stop()

    async def test_critical_plays_immediate(self, engine, narration_bus, mock_player):
        await engine.start()
        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.play_immediate.assert_awaited_with(_PCM_BYTES)
        await engine.stop()

    async def test_critical_publishes_to_livekit(self, engine, narration_bus, mock_livekit):
        await engine.start()
        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_livekit.publish.assert_awaited_with(_PCM_BYTES)
        await engine.stop()

    async def test_critical_no_pcm_skips_playback(
        self, engine, narration_bus, mock_elevenlabs, mock_player, mock_livekit
    ):
        """When synthesize returns None, no play_immediate or publish is called."""
        mock_elevenlabs.synthesize = AsyncMock(return_value=None)
        await engine.start()
        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        # interrupt and alert should still be called
        mock_player.interrupt.assert_awaited()
        mock_player.play_alert.assert_awaited()
        # but play_immediate and publish should NOT be called
        mock_player.play_immediate.assert_not_awaited()
        mock_livekit.publish.assert_not_awaited()
        await engine.stop()


# ---------------------------------------------------------------------------
# NORMAL routing tests
# ---------------------------------------------------------------------------


class TestNormalRouting:
    """Tests for NORMAL priority narration processing."""

    async def test_normal_synthesizes(self, engine, narration_bus, mock_elevenlabs):
        await engine.start()
        narration = _make_narration("Reading file.", NarrationPriority.NORMAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_elevenlabs.synthesize.assert_awaited_with("Reading file.")
        await engine.stop()

    async def test_normal_enqueues_priority_1(self, engine, narration_bus, mock_player):
        await engine.start()
        narration = _make_narration("Reading file.", NarrationPriority.NORMAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.enqueue.assert_awaited_with(_PCM_BYTES, priority=1)
        await engine.stop()

    async def test_normal_publishes_to_livekit(self, engine, narration_bus, mock_livekit):
        await engine.start()
        narration = _make_narration("Reading file.", NarrationPriority.NORMAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_livekit.publish.assert_awaited_with(_PCM_BYTES)
        await engine.stop()

    async def test_normal_no_pcm_skips(self, engine, narration_bus, mock_elevenlabs, mock_player, mock_livekit):
        """When synthesize returns None, no enqueue or publish is called."""
        mock_elevenlabs.synthesize = AsyncMock(return_value=None)
        await engine.start()
        narration = _make_narration("Reading file.", NarrationPriority.NORMAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.enqueue.assert_not_awaited()
        mock_livekit.publish.assert_not_awaited()
        await engine.stop()

    async def test_normal_no_interrupt(self, engine, narration_bus, mock_player):
        """NORMAL narrations should NOT trigger interrupt."""
        await engine.start()
        narration = _make_narration("Reading file.", NarrationPriority.NORMAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.interrupt.assert_not_awaited()
        await engine.stop()


# ---------------------------------------------------------------------------
# LOW routing tests
# ---------------------------------------------------------------------------


class TestLowRouting:
    """Tests for LOW priority narration processing."""

    async def test_low_under_threshold_enqueues(self, engine, narration_bus, mock_player):
        """When queue_depth is under threshold, LOW narrations are enqueued."""
        mock_player.queue_depth = 0
        await engine.start()
        narration = _make_narration("Session started.", NarrationPriority.LOW)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.enqueue.assert_awaited()
        await engine.stop()

    async def test_low_over_threshold_skips(
        self, engine, narration_bus, mock_player, mock_elevenlabs
    ):
        """When queue_depth exceeds threshold, LOW narrations are skipped entirely."""
        mock_player.queue_depth = 10  # well over default threshold of 3
        await engine.start()
        narration = _make_narration("Session started.", NarrationPriority.LOW)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_elevenlabs.synthesize.assert_not_awaited()
        mock_player.enqueue.assert_not_awaited()
        await engine.stop()

    async def test_low_publishes_to_livekit(self, engine, narration_bus, mock_livekit, mock_player):
        """LOW narrations under threshold also publish to LiveKit."""
        mock_player.queue_depth = 0
        await engine.start()
        narration = _make_narration("Session started.", NarrationPriority.LOW)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_livekit.publish.assert_awaited_with(_PCM_BYTES)
        await engine.stop()

    async def test_low_no_pcm_skips(
        self, engine, narration_bus, mock_elevenlabs, mock_player, mock_livekit
    ):
        """When synthesize returns None for LOW, no enqueue or publish."""
        mock_player.queue_depth = 0
        mock_elevenlabs.synthesize = AsyncMock(return_value=None)
        await engine.start()
        narration = _make_narration("Session started.", NarrationPriority.LOW)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.enqueue.assert_not_awaited()
        mock_livekit.publish.assert_not_awaited()
        await engine.stop()

    async def test_low_correct_priority(self, engine, narration_bus, mock_player):
        """LOW narrations are enqueued with priority=2."""
        mock_player.queue_depth = 0
        await engine.start()
        narration = _make_narration("Session started.", NarrationPriority.LOW)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_player.enqueue.assert_awaited_with(_PCM_BYTES, priority=2)
        await engine.stop()


# ---------------------------------------------------------------------------
# Consume loop tests
# ---------------------------------------------------------------------------


class TestConsumeLoop:
    """Tests for the _consume_loop behavior."""

    async def test_consume_loop_processes_event(self, engine, narration_bus, mock_elevenlabs):
        """An event emitted on the bus is picked up and processed."""
        await engine.start()
        narration = _make_narration("Hello.", NarrationPriority.NORMAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)
        mock_elevenlabs.synthesize.assert_awaited_once_with("Hello.")
        await engine.stop()

    async def test_consume_loop_handles_exception(
        self, engine, narration_bus, mock_elevenlabs, mock_player
    ):
        """If processing raises, the loop continues with the next event."""
        # First call raises, second succeeds
        mock_elevenlabs.synthesize = AsyncMock(
            side_effect=[RuntimeError("Boom"), _PCM_BYTES]
        )
        await engine.start()

        await narration_bus.emit(_make_narration("First.", NarrationPriority.NORMAL))
        await asyncio.sleep(0.05)
        await narration_bus.emit(_make_narration("Second.", NarrationPriority.NORMAL))
        await asyncio.sleep(0.05)

        # The second event should have been processed despite the first failing
        assert mock_elevenlabs.synthesize.await_count == 2
        # enqueue should have been called for the second event
        mock_player.enqueue.assert_awaited()
        await engine.stop()

    async def test_consume_loop_timeout_continues(self, engine):
        """When no events arrive, the loop times out and continues without crashing."""
        await engine.start()
        # Wait longer than the 1.0s timeout to prove it does not crash
        await asyncio.sleep(1.2)
        assert not engine._consume_task.done()
        await engine.stop()

    async def test_consume_loop_multiple_events(
        self, engine, narration_bus, mock_elevenlabs
    ):
        """Multiple events emitted in sequence are all processed."""
        await engine.start()
        for i in range(5):
            await narration_bus.emit(
                _make_narration(f"Event {i}.", NarrationPriority.NORMAL)
            )
        await asyncio.sleep(0.1)
        assert mock_elevenlabs.synthesize.await_count == 5
        await engine.stop()

    async def test_consume_loop_stops_when_not_running(self, engine, narration_bus):
        """Setting _running=False causes the loop to exit gracefully."""
        await engine.start()
        task = engine._consume_task
        engine._running = False
        # Wait for the loop to notice _running is False (at most 1.0s timeout + margin)
        await asyncio.sleep(1.3)
        assert task.done()
        # Cleanup
        engine._consume_task = None
        if engine._queue:
            await narration_bus.unsubscribe(engine._queue)
            engine._queue = None


# ---------------------------------------------------------------------------
# AlertManager integration tests
# ---------------------------------------------------------------------------


class TestAlertManagerIntegration:
    """Tests for AlertManager wiring in TTSEngine."""

    async def test_constructor_with_event_bus_creates_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus
    ):
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        assert eng._alert_manager is not None

    async def test_constructor_without_event_bus_no_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus
    ):
        eng = TTSEngine(narration_bus)
        assert eng._alert_manager is None

    async def test_start_starts_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        event_bus = EventBus(maxsize=64)
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()
        mock_am.set_repeat_callback.assert_called_once()
        mock_am.start.assert_awaited_once()
        await eng.stop()

    async def test_stop_stops_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        event_bus = EventBus(maxsize=64)
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()
        mock_am.stop.reset_mock()
        await eng.stop()
        mock_am.stop.assert_awaited_once()

    async def test_critical_passes_block_reason_to_play_alert(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        narration = _make_narration(
            "Permission needed!",
            NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
        )
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        mock_player.play_alert.assert_awaited_with(
            block_reason=BlockReason.PERMISSION_PROMPT
        )
        await eng.stop()

    async def test_critical_activates_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        narration = _make_narration(
            "Agent is blocked!",
            NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
        )
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        mock_am.activate.assert_awaited_once_with(
            session_id=_SESSION,
            block_reason=BlockReason.QUESTION,
            narration_text="Agent is blocked!",
            options=None,
        )
        await eng.stop()

    async def test_critical_without_alert_manager_still_works(
        self, engine, narration_bus, mock_player, mock_elevenlabs, mock_livekit
    ):
        """No event_bus passed — _alert_manager is None, CRITICAL still works."""
        assert engine._alert_manager is None
        await engine.start()

        narration = _make_narration(
            "Alert!",
            NarrationPriority.CRITICAL,
            block_reason=BlockReason.PERMISSION_PROMPT,
        )
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        mock_player.interrupt.assert_awaited()
        mock_player.play_alert.assert_awaited_with(
            block_reason=BlockReason.PERMISSION_PROMPT
        )
        mock_player.play_immediate.assert_awaited_with(_PCM_BYTES)
        mock_livekit.publish.assert_awaited_with(_PCM_BYTES)
        await engine.stop()

    async def test_critical_no_pcm_still_activates_alert(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        """When synthesize returns None, alert_manager.activate IS still called."""
        mock_elevenlabs.synthesize = AsyncMock(return_value=None)
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        # Alert manager should still be activated so repeat alerts work
        mock_am.activate.assert_awaited_once()
        await eng.stop()

    async def test_alert_active_true_when_alert_exists(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 1
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        assert eng.alert_active is True

    async def test_alert_active_false_when_no_alerts(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        assert eng.alert_active is False

    async def test_alert_active_false_when_no_alert_manager(self, engine):
        """No event_bus — alert_active is always False."""
        assert engine._alert_manager is None
        assert engine.alert_active is False

    async def test_repeat_callback_replays_alert_and_narration(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        """Directly call _handle_repeat_alert and verify full playback flow."""
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)

        await eng._handle_repeat_alert(
            BlockReason.PERMISSION_PROMPT, "Permission needed!"
        )

        mock_player.interrupt.assert_awaited_once()
        mock_player.play_alert.assert_awaited_once_with(
            block_reason=BlockReason.PERMISSION_PROMPT
        )
        mock_elevenlabs.synthesize.assert_awaited_once_with("Permission needed!")
        mock_player.play_immediate.assert_awaited_once_with(_PCM_BYTES)
        mock_livekit.publish.assert_awaited_once_with(_PCM_BYTES)

    async def test_repeat_callback_no_pcm_skips_playback(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        """When synthesize returns None in repeat callback, playback is skipped."""
        mock_elevenlabs.synthesize = AsyncMock(return_value=None)
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)

        await eng._handle_repeat_alert(BlockReason.QUESTION, "Question!")

        mock_player.interrupt.assert_awaited_once()
        mock_player.play_alert.assert_awaited_once_with(
            block_reason=BlockReason.QUESTION
        )
        mock_player.play_immediate.assert_not_awaited()
        mock_livekit.publish.assert_not_awaited()

    async def test_critical_passes_options_to_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        narration = NarrationEvent(
            text="Pick an option!",
            priority=NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_BLOCKED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id=_SESSION,
            block_reason=BlockReason.QUESTION,
            options=["RS256", "HS256"],
        )
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        mock_am.activate.assert_awaited_once_with(
            session_id=_SESSION,
            block_reason=BlockReason.QUESTION,
            narration_text="Pick an option!",
            options=["RS256", "HS256"],
        )
        await eng.stop()

    async def test_processing_critical_flag_set_during_critical(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        """_processing_critical is True during _handle_critical and False after."""
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        assert eng._processing_critical is False

        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        # After processing completes, flag should be False
        assert eng._processing_critical is False
        await eng.stop()

    async def test_processing_critical_flag_cleared_on_error(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        """_processing_critical is cleared even if _handle_critical raises."""
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        mock_player.interrupt = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        narration = _make_narration("Alert!", NarrationPriority.CRITICAL)
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        # Flag should be cleared even after error
        assert eng._processing_critical is False
        await eng.stop()

    async def test_critical_passes_empty_options_to_alert_manager(
        self, mock_elevenlabs, mock_player, mock_livekit, narration_bus, monkeypatch
    ):
        mock_am = AsyncMock()
        mock_am.set_repeat_callback = MagicMock()
        mock_am.active_alert_count = 0
        monkeypatch.setattr(
            "echo.tts.tts_engine.AlertManager", lambda eb: mock_am
        )
        event_bus = EventBus(maxsize=64)
        eng = TTSEngine(narration_bus, event_bus=event_bus)
        await eng.start()

        narration = NarrationEvent(
            text="Blocked!",
            priority=NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_BLOCKED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id=_SESSION,
            block_reason=BlockReason.PERMISSION_PROMPT,
            options=[],
        )
        await narration_bus.emit(narration)
        await asyncio.sleep(0.05)

        mock_am.activate.assert_awaited_once_with(
            session_id=_SESSION,
            block_reason=BlockReason.PERMISSION_PROMPT,
            narration_text="Blocked!",
            options=[],
        )
        await eng.stop()

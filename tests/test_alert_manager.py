"""Tests for echo.tts.alert_manager — AlertManager and ActiveAlert."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from echo.events.event_bus import EventBus
from echo.events.types import BlockReason, EventType, EchoEvent
from echo.tts.alert_manager import ActiveAlert, AlertManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION = "test-session-alert"
_SESSION_2 = "test-session-alert-2"


def _make_event(
    event_type: EventType = EventType.TOOL_EXECUTED,
    session_id: str = _SESSION,
    **kwargs,
) -> EchoEvent:
    """Helper to create a minimal EchoEvent for testing."""
    defaults = {
        "type": event_type,
        "session_id": session_id,
        "source": "hook",
    }
    defaults.update(kwargs)
    return EchoEvent(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    """Return a fresh EventBus for EchoEvents."""
    return EventBus(maxsize=64)


@pytest.fixture
def alert_manager(event_bus: EventBus) -> AlertManager:
    """Return an AlertManager wired to the test event bus (not yet started)."""
    return AlertManager(event_bus=event_bus)


# ---------------------------------------------------------------------------
# ActiveAlert tests
# ---------------------------------------------------------------------------


class TestActiveAlert:
    """Tests for the ActiveAlert data class."""

    def test_active_alert_stores_fields(self):
        alert = ActiveAlert(
            session_id="s1",
            block_reason=BlockReason.PERMISSION_PROMPT,
            narration_text="Agent needs permission.",
        )
        assert alert.session_id == "s1"
        assert alert.block_reason == BlockReason.PERMISSION_PROMPT
        assert alert.narration_text == "Agent needs permission."

    def test_active_alert_defaults(self):
        alert = ActiveAlert(
            session_id="s1",
            block_reason=None,
            narration_text="Blocked.",
        )
        assert alert.repeat_count == 0
        assert alert.repeat_task is None
        assert alert.created_at > 0

    def test_active_alert_stores_options(self):
        alert = ActiveAlert(
            session_id="s1",
            block_reason=BlockReason.QUESTION,
            narration_text="Which option?",
            options=["RS256", "HS256"],
        )
        assert alert.options == ["RS256", "HS256"]

    def test_active_alert_options_default_none(self):
        alert = ActiveAlert(
            session_id="s1",
            block_reason=None,
            narration_text="Blocked.",
        )
        assert alert.options is None

    def test_active_alert_options_empty_list(self):
        alert = ActiveAlert(
            session_id="s1",
            block_reason=BlockReason.IDLE_PROMPT,
            narration_text="Idle.",
            options=[],
        )
        assert alert.options == []


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestAlertManagerLifecycle:
    """Tests for AlertManager start() and stop() lifecycle."""

    async def test_start_subscribes_to_event_bus(
        self, event_bus: EventBus, alert_manager: AlertManager
    ):
        assert event_bus.subscriber_count == 0
        await alert_manager.start()
        assert event_bus.subscriber_count == 1
        await alert_manager.stop()

    async def test_stop_unsubscribes_from_event_bus(
        self, event_bus: EventBus, alert_manager: AlertManager
    ):
        await alert_manager.start()
        assert event_bus.subscriber_count == 1
        await alert_manager.stop()
        assert event_bus.subscriber_count == 0

    async def test_stop_cancels_consume_task(
        self, alert_manager: AlertManager
    ):
        await alert_manager.start()
        task = alert_manager._consume_task
        assert task is not None
        assert not task.done()

        await alert_manager.stop()
        assert alert_manager._consume_task is None
        assert task.done()

    async def test_stop_without_start_does_not_crash(
        self, alert_manager: AlertManager
    ):
        # Should be a safe no-op
        await alert_manager.stop()

    async def test_start_stop_restart(
        self, event_bus: EventBus, alert_manager: AlertManager
    ):
        await alert_manager.start()
        assert event_bus.subscriber_count == 1
        await alert_manager.stop()
        assert event_bus.subscriber_count == 0

        # Restart
        await alert_manager.start()
        assert event_bus.subscriber_count == 1
        await alert_manager.stop()
        assert event_bus.subscriber_count == 0


# ---------------------------------------------------------------------------
# Alert activation tests
# ---------------------------------------------------------------------------


class TestAlertActivation:
    """Tests for activating alerts."""

    async def test_activate_creates_active_alert(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(
            _SESSION, BlockReason.PERMISSION_PROMPT, "Needs permission."
        )
        assert alert_manager.active_alert_count == 1

        await alert_manager.stop()

    async def test_has_active_alert_true_for_active_session(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Which branch?")
        assert alert_manager.has_active_alert(_SESSION) is True

        await alert_manager.stop()

    async def test_has_active_alert_false_for_unknown_session(
        self, alert_manager: AlertManager
    ):
        await alert_manager.start()
        assert alert_manager.has_active_alert("nonexistent") is False
        await alert_manager.stop()

    async def test_active_alert_count(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        assert alert_manager.active_alert_count == 0
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Q1")
        assert alert_manager.active_alert_count == 1
        await alert_manager.activate(_SESSION_2, BlockReason.IDLE_PROMPT, "Idle")
        assert alert_manager.active_alert_count == 2

        await alert_manager.stop()

    async def test_get_active_alert_returns_alert(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(
            _SESSION, BlockReason.PERMISSION_PROMPT, "Allow write?"
        )
        alert = alert_manager.get_active_alert(_SESSION)
        assert alert is not None
        assert alert.session_id == _SESSION
        assert alert.block_reason == BlockReason.PERMISSION_PROMPT
        assert alert.narration_text == "Allow write?"

        await alert_manager.stop()

    async def test_get_active_alert_none_for_unknown(
        self, alert_manager: AlertManager
    ):
        await alert_manager.start()
        assert alert_manager.get_active_alert("unknown") is None
        await alert_manager.stop()

    async def test_activate_replaces_existing_alert(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "First question")
        await alert_manager.activate(_SESSION, BlockReason.IDLE_PROMPT, "Now idle")

        assert alert_manager.active_alert_count == 1
        alert = alert_manager.get_active_alert(_SESSION)
        assert alert.block_reason == BlockReason.IDLE_PROMPT
        assert alert.narration_text == "Now idle"

        await alert_manager.stop()

    async def test_activate_stores_options(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(
            _SESSION,
            BlockReason.QUESTION,
            "Which branch?",
            options=["main", "develop"],
        )
        alert = alert_manager.get_active_alert(_SESSION)
        assert alert is not None
        assert alert.options == ["main", "develop"]

        await alert_manager.stop()

    async def test_activate_options_default_none(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(
            _SESSION,
            BlockReason.PERMISSION_PROMPT,
            "Allow write?",
        )
        alert = alert_manager.get_active_alert(_SESSION)
        assert alert is not None
        assert alert.options is None

        await alert_manager.stop()

    async def test_multiple_sessions_tracked_independently(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Q?")
        await alert_manager.activate(_SESSION_2, BlockReason.IDLE_PROMPT, "Idle")

        assert alert_manager.has_active_alert(_SESSION) is True
        assert alert_manager.has_active_alert(_SESSION_2) is True

        a1 = alert_manager.get_active_alert(_SESSION)
        a2 = alert_manager.get_active_alert(_SESSION_2)
        assert a1.block_reason == BlockReason.QUESTION
        assert a2.block_reason == BlockReason.IDLE_PROMPT

        await alert_manager.stop()


# ---------------------------------------------------------------------------
# Alert resolution tests
# ---------------------------------------------------------------------------


class TestAlertResolution:
    """Tests for alert resolution via EventBus events."""

    async def test_tool_executed_clears_alert(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.PERMISSION_PROMPT, "Blocked")

        await event_bus.emit(_make_event(EventType.TOOL_EXECUTED, tool_name="Read"))
        await asyncio.sleep(0.05)

        assert alert_manager.has_active_alert(_SESSION) is False
        assert alert_manager.active_alert_count == 0

        await alert_manager.stop()

    async def test_agent_message_clears_alert(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        await event_bus.emit(_make_event(EventType.AGENT_MESSAGE, text="Resuming."))
        await asyncio.sleep(0.05)

        assert alert_manager.has_active_alert(_SESSION) is False

        await alert_manager.stop()

    async def test_session_end_clears_alert(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.IDLE_PROMPT, "Idle")

        await event_bus.emit(_make_event(EventType.SESSION_END))
        await asyncio.sleep(0.05)

        assert alert_manager.has_active_alert(_SESSION) is False

        await alert_manager.stop()

    async def test_agent_blocked_does_not_clear_alert(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        # Another blocked event should NOT clear the alert
        await event_bus.emit(
            _make_event(
                EventType.AGENT_BLOCKED,
                block_reason=BlockReason.PERMISSION_PROMPT,
                message="Another block",
            )
        )
        await asyncio.sleep(0.05)

        assert alert_manager.has_active_alert(_SESSION) is True
        assert alert_manager.active_alert_count == 1

        await alert_manager.stop()

    async def test_event_for_different_session_does_not_clear(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        # Event for a different session
        await event_bus.emit(
            _make_event(EventType.TOOL_EXECUTED, session_id=_SESSION_2, tool_name="Edit")
        )
        await asyncio.sleep(0.05)

        assert alert_manager.has_active_alert(_SESSION) is True

        await alert_manager.stop()

    async def test_clear_nonexistent_session_is_noop(
        self, alert_manager: AlertManager
    ):
        await alert_manager.start()
        # Should not raise
        await alert_manager._clear_alert("nonexistent")
        assert alert_manager.active_alert_count == 0
        await alert_manager.stop()


# ---------------------------------------------------------------------------
# Alert repeat tests
# ---------------------------------------------------------------------------


class TestAlertRepeat:
    """Tests for the repeat timer mechanism."""

    async def test_repeat_fires_after_interval(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.1)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 5)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked?")

        # Wait long enough for at least one repeat
        await asyncio.sleep(0.25)

        assert callback.await_count >= 1

        await alert_manager.stop()

    async def test_repeat_callback_receives_correct_args(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.1)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 5)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(
            _SESSION, BlockReason.PERMISSION_PROMPT, "Allow write?"
        )

        await asyncio.sleep(0.15)

        callback.assert_awaited_with(BlockReason.PERMISSION_PROMPT, "Allow write?")

        await alert_manager.stop()

    async def test_max_repeats_respected(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 2)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Q?")

        # Wait long enough for max repeats + extra
        await asyncio.sleep(0.4)

        assert callback.await_count == 2

        await alert_manager.stop()

    async def test_repeat_cancelled_on_clear(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.1)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 10)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        # Clear alert before repeat fires
        await asyncio.sleep(0.02)
        await event_bus.emit(_make_event(EventType.TOOL_EXECUTED, tool_name="Read"))
        await asyncio.sleep(0.2)

        # Callback should not have been called (cleared before first repeat)
        assert callback.await_count == 0

        await alert_manager.stop()

    async def test_repeat_disabled_when_interval_zero(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 5)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        # No repeat task should have been created
        alert = alert_manager.get_active_alert(_SESSION)
        assert alert.repeat_task is None

        await asyncio.sleep(0.15)
        assert callback.await_count == 0

        await alert_manager.stop()

    async def test_repeat_callback_exception_does_not_crash(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 3)

        callback = AsyncMock(side_effect=RuntimeError("Callback failed"))
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        # Wait for repeats to fire — should not crash
        await asyncio.sleep(0.25)

        # Callback was called despite exceptions
        assert callback.await_count >= 2

        await alert_manager.stop()

    async def test_repeat_increments_count(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 5)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")

        await asyncio.sleep(0.15)

        alert = alert_manager.get_active_alert(_SESSION)
        assert alert is not None
        assert alert.repeat_count >= 1

        await alert_manager.stop()


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestAlertManagerErrorHandling:
    """Tests for error handling and resilience."""

    async def test_exception_in_consume_loop_does_not_crash(
        self, event_bus: EventBus, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        # Activate an alert so _handle_event has something to work with
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Q?")

        # Monkey-patch _handle_event to raise on first call
        original_handle = alert_manager._handle_event
        call_count = 0

        async def failing_then_ok(event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated error")
            return await original_handle(event)

        alert_manager._handle_event = failing_then_ok

        # Emit two events: first will fail, second should succeed
        await event_bus.emit(_make_event(EventType.TOOL_EXECUTED, tool_name="Read"))
        await asyncio.sleep(0.05)
        await event_bus.emit(_make_event(EventType.TOOL_EXECUTED, tool_name="Edit"))
        await asyncio.sleep(0.05)

        # Second event should have cleared the alert
        assert alert_manager.has_active_alert(_SESSION) is False

        await alert_manager.stop()

    async def test_stop_cancels_all_repeat_tasks(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 10.0)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 100)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Q1")
        await alert_manager.activate(_SESSION_2, BlockReason.IDLE_PROMPT, "Idle")

        # Both should have repeat tasks
        a1 = alert_manager.get_active_alert(_SESSION)
        a2 = alert_manager.get_active_alert(_SESSION_2)
        assert a1 is not None and a1.repeat_task is not None
        assert a2 is not None and a2.repeat_task is not None

        task1 = a1.repeat_task
        task2 = a2.repeat_task

        await alert_manager.stop()

        # After stop, both tasks should be done (cancelled)
        assert task1.done()
        assert task2.done()

    async def test_stop_clears_all_active_alerts(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0)
        await alert_manager.start()

        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Q1")
        await alert_manager.activate(_SESSION_2, BlockReason.IDLE_PROMPT, "Idle")
        assert alert_manager.active_alert_count == 2

        await alert_manager.stop()
        assert alert_manager.active_alert_count == 0


# ---------------------------------------------------------------------------
# set_repeat_callback tests
# ---------------------------------------------------------------------------


class TestSetRepeatCallback:
    """Tests for the set_repeat_callback method."""

    async def test_set_callback_before_start(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 1)

        callback = AsyncMock()
        alert_manager.set_repeat_callback(callback)

        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")
        await asyncio.sleep(0.15)

        assert callback.await_count == 1

        await alert_manager.stop()

    async def test_no_callback_set_repeat_is_noop(
        self, alert_manager: AlertManager, monkeypatch
    ):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 2)

        # No callback set — repeat loop should run without error
        await alert_manager.start()
        await alert_manager.activate(_SESSION, BlockReason.QUESTION, "Blocked")
        await asyncio.sleep(0.2)

        # Alert repeat_count should still increment
        alert = alert_manager.get_active_alert(_SESSION)
        assert alert is not None
        assert alert.repeat_count >= 1

        await alert_manager.stop()

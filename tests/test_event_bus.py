"""Tests for voice_copilot.events.event_bus — Async fan-out event bus."""

import asyncio

import pytest

from voice_copilot.events.event_bus import EventBus
from voice_copilot.events.types import EventType, VoiceCopilotEvent


def _make_event(event_type: EventType = EventType.TOOL_EXECUTED) -> VoiceCopilotEvent:
    """Helper to create a minimal event for testing."""
    return VoiceCopilotEvent(
        type=event_type,
        session_id="bus-test-session",
        source="hook",
    )


class TestSubscribe:
    """Tests for EventBus.subscribe()."""

    async def test_subscribe_creates_a_new_queue(self, event_bus: EventBus):
        queue = await event_bus.subscribe()
        assert isinstance(queue, asyncio.Queue)

    async def test_subscribe_increments_subscriber_count(self, event_bus: EventBus):
        assert event_bus.subscriber_count == 0
        await event_bus.subscribe()
        assert event_bus.subscriber_count == 1
        await event_bus.subscribe()
        assert event_bus.subscriber_count == 2

    async def test_multiple_subscribes_return_different_queues(
        self, event_bus: EventBus
    ):
        q1 = await event_bus.subscribe()
        q2 = await event_bus.subscribe()
        assert q1 is not q2


class TestEmit:
    """Tests for EventBus.emit()."""

    async def test_emit_delivers_event_to_subscriber(self, event_bus: EventBus):
        queue = await event_bus.subscribe()
        event = _make_event()
        await event_bus.emit(event)
        received = queue.get_nowait()
        assert received is event

    async def test_emit_fanout_to_multiple_subscribers(self, event_bus: EventBus):
        q1 = await event_bus.subscribe()
        q2 = await event_bus.subscribe()
        q3 = await event_bus.subscribe()

        event = _make_event()
        await event_bus.emit(event)

        assert q1.get_nowait() is event
        assert q2.get_nowait() is event
        assert q3.get_nowait() is event

    async def test_emit_to_empty_bus_does_not_error(self, event_bus: EventBus):
        """Emitting when there are no subscribers should be a no-op."""
        event = _make_event()
        # Should not raise
        await event_bus.emit(event)

    async def test_emit_multiple_events_preserves_order(self, event_bus: EventBus):
        queue = await event_bus.subscribe()
        e1 = _make_event(EventType.SESSION_START)
        e2 = _make_event(EventType.TOOL_EXECUTED)
        e3 = _make_event(EventType.SESSION_END)

        await event_bus.emit(e1)
        await event_bus.emit(e2)
        await event_bus.emit(e3)

        assert queue.get_nowait() is e1
        assert queue.get_nowait() is e2
        assert queue.get_nowait() is e3

    async def test_emit_drops_event_when_queue_is_full(self):
        """When a subscriber queue is full, the event is dropped (not raised)."""
        bus = EventBus(maxsize=2)
        queue = await bus.subscribe()

        # Fill the queue
        await bus.emit(_make_event())
        await bus.emit(_make_event())
        assert queue.qsize() == 2

        # Third emit should not raise; the event is silently dropped
        await bus.emit(_make_event())
        # Queue still has exactly 2 items (the third was dropped)
        assert queue.qsize() == 2


class TestUnsubscribe:
    """Tests for EventBus.unsubscribe()."""

    async def test_unsubscribe_removes_subscriber(self, event_bus: EventBus):
        queue = await event_bus.subscribe()
        assert event_bus.subscriber_count == 1
        await event_bus.unsubscribe(queue)
        assert event_bus.subscriber_count == 0

    async def test_unsubscribe_stops_event_delivery(self, event_bus: EventBus):
        queue = await event_bus.subscribe()
        await event_bus.unsubscribe(queue)

        await event_bus.emit(_make_event())
        assert queue.empty()

    async def test_unsubscribe_unknown_queue_is_noop(self, event_bus: EventBus):
        """Unsubscribing a queue that was never registered should not raise."""
        foreign_queue: asyncio.Queue[VoiceCopilotEvent] = asyncio.Queue()
        # Should not raise
        await event_bus.unsubscribe(foreign_queue)

    async def test_double_unsubscribe_is_noop(self, event_bus: EventBus):
        queue = await event_bus.subscribe()
        await event_bus.unsubscribe(queue)
        # Second unsubscribe should not raise
        await event_bus.unsubscribe(queue)
        assert event_bus.subscriber_count == 0


class TestSubscriberCount:
    """Tests for EventBus.subscriber_count property."""

    async def test_subscriber_count_starts_at_zero(self, event_bus: EventBus):
        assert event_bus.subscriber_count == 0

    async def test_subscriber_count_tracks_subscribes_and_unsubscribes(
        self, event_bus: EventBus
    ):
        q1 = await event_bus.subscribe()
        q2 = await event_bus.subscribe()
        assert event_bus.subscriber_count == 2

        await event_bus.unsubscribe(q1)
        assert event_bus.subscriber_count == 1

        await event_bus.unsubscribe(q2)
        assert event_bus.subscriber_count == 0

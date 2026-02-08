"""Tests for voice_copilot.summarizer.event_batcher — time-windowed batching."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from voice_copilot.events.types import EventType, VoiceCopilotEvent
from voice_copilot.summarizer.event_batcher import EventBatcher
from voice_copilot.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-001"


def _make_event(tool_name: str = "Write", index: int = 0) -> VoiceCopilotEvent:
    """Create a tool_executed event for testing."""
    return VoiceCopilotEvent(
        type=EventType.TOOL_EXECUTED,
        session_id=SESSION_ID,
        source="hook",
        tool_name=tool_name,
        tool_input={"file_path": f"/tmp/file_{index}.py"},
        tool_output={"status": "success"},
    )


def _make_narration(text: str = "Batched narration") -> NarrationEvent:
    """Create a NarrationEvent for mock return values."""
    return NarrationEvent(
        text=text,
        priority=NarrationPriority.NORMAL,
        source_event_type=EventType.TOOL_EXECUTED,
        summarization_method=SummarizationMethod.TEMPLATE,
        session_id=SESSION_ID,
    )


def _mock_renderer(narration: NarrationEvent | None = None):
    """Return a mock render_batch callable that returns a fixed NarrationEvent.

    Also records the events it was called with for assertion purposes.
    """
    if narration is None:
        narration = _make_narration()

    calls: list[list[VoiceCopilotEvent]] = []

    def render_batch(events: list[VoiceCopilotEvent]) -> NarrationEvent:
        calls.append(list(events))
        return narration

    render_batch.calls = calls  # type: ignore[attr-defined]
    return render_batch


@pytest.fixture
def renderer():
    """Provide a mock render_batch callable."""
    return _mock_renderer()


@pytest.fixture
def batcher(renderer):
    """Provide a fresh EventBatcher with a short window for fast tests."""
    b = EventBatcher(render_batch=renderer)
    b.BATCH_WINDOW_SEC = 0.05  # 50ms — fast for tests, no flakiness
    return b


# ---------------------------------------------------------------------------
# add() — accumulation behavior
# ---------------------------------------------------------------------------


class TestAdd:
    """Tests for EventBatcher.add()."""

    async def test_first_event_returns_none(self, batcher):
        """First event should accumulate; add() returns None."""
        result = await batcher.add(_make_event())
        assert result is None

    async def test_second_event_returns_none(self, batcher):
        """Subsequent events within the window also return None."""
        await batcher.add(_make_event(index=0))
        result = await batcher.add(_make_event(index=1))
        assert result is None

    async def test_multiple_rapid_adds_accumulate(self, batcher):
        """Adding several events rapidly should all return None (below max)."""
        results = []
        for i in range(5):
            r = await batcher.add(_make_event(index=i))
            results.append(r)
        assert all(r is None for r in results)

    async def test_max_batch_size_triggers_flush(self, batcher, renderer):
        """When batch reaches MAX_BATCH_SIZE, add() returns the NarrationEvent."""
        for i in range(EventBatcher.MAX_BATCH_SIZE - 1):
            result = await batcher.add(_make_event(index=i))
            assert result is None

        # The 10th event should trigger immediate flush
        result = await batcher.add(_make_event(index=EventBatcher.MAX_BATCH_SIZE - 1))
        assert result is not None
        assert isinstance(result, NarrationEvent)
        assert result.text == "Batched narration"

    async def test_max_batch_flush_clears_batch(self, batcher):
        """After max-size flush, batch should be empty."""
        for i in range(EventBatcher.MAX_BATCH_SIZE):
            await batcher.add(_make_event(index=i))
        assert not batcher.has_pending()

    async def test_max_batch_flush_calls_renderer_with_all_events(self, batcher, renderer):
        """The renderer should receive all MAX_BATCH_SIZE events."""
        events = [_make_event(index=i) for i in range(EventBatcher.MAX_BATCH_SIZE)]
        for e in events:
            await batcher.add(e)

        assert len(renderer.calls) == 1
        rendered_events = renderer.calls[0]
        assert len(rendered_events) == EventBatcher.MAX_BATCH_SIZE
        # Verify the events are the ones we added (by tool_input file path)
        for i, e in enumerate(rendered_events):
            assert e.tool_input["file_path"] == f"/tmp/file_{i}.py"


# ---------------------------------------------------------------------------
# flush() — explicit flush behavior
# ---------------------------------------------------------------------------


class TestFlush:
    """Tests for EventBatcher.flush()."""

    async def test_flush_empty_returns_none(self, batcher):
        """Flushing an empty batch should return None."""
        result = await batcher.flush()
        assert result is None

    async def test_flush_returns_narration_event(self, batcher, renderer):
        """Flushing a non-empty batch returns the rendered NarrationEvent."""
        await batcher.add(_make_event())
        result = await batcher.flush()
        assert result is not None
        assert isinstance(result, NarrationEvent)
        assert result.text == "Batched narration"

    async def test_flush_clears_batch(self, batcher):
        """After flushing, the batch should be empty."""
        await batcher.add(_make_event())
        assert batcher.has_pending()
        await batcher.flush()
        assert not batcher.has_pending()

    async def test_flush_calls_renderer_with_correct_events(self, batcher, renderer):
        """The renderer should receive the exact events that were batched."""
        e1 = _make_event(tool_name="Read", index=0)
        e2 = _make_event(tool_name="Write", index=1)
        await batcher.add(e1)
        await batcher.add(e2)
        await batcher.flush()

        assert len(renderer.calls) == 1
        assert len(renderer.calls[0]) == 2
        assert renderer.calls[0][0].tool_name == "Read"
        assert renderer.calls[0][1].tool_name == "Write"

    async def test_flush_cancels_pending_timer(self, batcher):
        """Flushing should cancel any pending timer task."""
        await batcher.add(_make_event())
        # Timer task should exist after first add
        assert batcher._flush_task is not None
        task = batcher._flush_task

        await batcher.flush()
        # Timer task reference should be cleared
        assert batcher._flush_task is None
        # Give the event loop a tick to process the cancellation
        await asyncio.sleep(0)
        assert task.done()

    async def test_double_flush_returns_none_on_second(self, batcher):
        """Flushing twice: second flush returns None (batch already empty)."""
        await batcher.add(_make_event())
        first = await batcher.flush()
        second = await batcher.flush()
        assert first is not None
        assert second is None


# ---------------------------------------------------------------------------
# has_pending()
# ---------------------------------------------------------------------------


class TestHasPending:
    """Tests for EventBatcher.has_pending()."""

    async def test_empty_batcher_has_no_pending(self, batcher):
        """A fresh batcher should have no pending events."""
        assert not batcher.has_pending()

    async def test_has_pending_after_add(self, batcher):
        """After adding an event, has_pending should return True."""
        await batcher.add(_make_event())
        assert batcher.has_pending()

    async def test_no_pending_after_flush(self, batcher):
        """After flushing, has_pending should return False."""
        await batcher.add(_make_event())
        await batcher.flush()
        assert not batcher.has_pending()


# ---------------------------------------------------------------------------
# Timer-based flush
# ---------------------------------------------------------------------------


class TestTimerFlush:
    """Tests for the automatic timer-based flush mechanism."""

    async def test_timer_fires_and_flushes(self, batcher, renderer):
        """After BATCH_WINDOW_SEC, the timer should flush the batch."""
        await batcher.add(_make_event())
        assert batcher.has_pending()

        # Wait for the timer to fire (50ms window + small buffer)
        await asyncio.sleep(0.1)

        assert not batcher.has_pending()
        assert len(renderer.calls) == 1

    async def test_timer_invokes_flush_callback(self, batcher):
        """When the timer fires, the flush_callback should be called."""
        callback = AsyncMock()
        batcher.set_flush_callback(callback)

        await batcher.add(_make_event())
        await asyncio.sleep(0.1)

        callback.assert_awaited_once()
        narration = callback.call_args[0][0]
        assert isinstance(narration, NarrationEvent)

    async def test_timer_does_not_invoke_callback_when_not_set(self, batcher, renderer):
        """Timer flush should work even without a callback (no crash)."""
        await batcher.add(_make_event())
        await asyncio.sleep(0.1)

        # Batch should be flushed without errors
        assert not batcher.has_pending()
        assert len(renderer.calls) == 1

    async def test_explicit_flush_prevents_timer_double_flush(self, batcher, renderer):
        """If we flush explicitly, the timer should not flush again."""
        await batcher.add(_make_event())
        await batcher.flush()

        # Wait for where the timer would have fired
        await asyncio.sleep(0.1)

        # Renderer should have been called exactly once (from explicit flush)
        assert len(renderer.calls) == 1


# ---------------------------------------------------------------------------
# Batch lifecycle — flush and re-add
# ---------------------------------------------------------------------------


class TestBatchLifecycle:
    """Tests for flush-then-re-add batch lifecycle."""

    async def test_flush_then_readd_starts_new_batch(self, batcher, renderer):
        """After flushing, adding new events should start a fresh batch."""
        await batcher.add(_make_event(tool_name="Read", index=0))
        first = await batcher.flush()
        assert first is not None

        await batcher.add(_make_event(tool_name="Write", index=1))
        second = await batcher.flush()
        assert second is not None

        # Two separate render calls
        assert len(renderer.calls) == 2
        assert renderer.calls[0][0].tool_name == "Read"
        assert renderer.calls[1][0].tool_name == "Write"

    async def test_max_flush_then_continue_adding(self, batcher, renderer):
        """After a max-size flush, we can immediately start a new batch."""
        # Fill first batch to max
        for i in range(EventBatcher.MAX_BATCH_SIZE):
            await batcher.add(_make_event(index=i))

        # Batch was auto-flushed
        assert not batcher.has_pending()
        assert len(renderer.calls) == 1

        # Start a new batch
        await batcher.add(_make_event(index=100))
        assert batcher.has_pending()

        result = await batcher.flush()
        assert result is not None
        assert len(renderer.calls) == 2
        assert len(renderer.calls[1]) == 1


# ---------------------------------------------------------------------------
# Defensive behavior
# ---------------------------------------------------------------------------


class TestDefensiveBehavior:
    """Tests for error handling and edge cases."""

    async def test_add_never_raises_on_renderer_error(self):
        """If render_batch raises, add() should catch and return None."""

        def broken_renderer(events):
            raise RuntimeError("renderer exploded")

        batcher = EventBatcher(render_batch=broken_renderer)
        batcher.BATCH_WINDOW_SEC = 0.05

        # Fill to max to trigger flush (which calls the broken renderer)
        result = None
        for i in range(EventBatcher.MAX_BATCH_SIZE):
            result = await batcher.add(_make_event(index=i))

        # Should not raise; returns None because the flush error was caught
        assert result is None

    async def test_flush_never_raises_on_renderer_error(self):
        """If render_batch raises, flush() should catch and return None."""

        def broken_renderer(events):
            raise ValueError("bad events")

        batcher = EventBatcher(render_batch=broken_renderer)
        batcher.BATCH_WINDOW_SEC = 0.05

        await batcher.add(_make_event())
        result = await batcher.flush()
        assert result is None

    async def test_timer_flush_handles_callback_error(self):
        """If flush_callback raises, the timer should not propagate."""

        async def broken_callback(narration):
            raise RuntimeError("callback failed")

        renderer = _mock_renderer()
        batcher = EventBatcher(render_batch=renderer)
        batcher.BATCH_WINDOW_SEC = 0.05
        batcher.set_flush_callback(broken_callback)

        await batcher.add(_make_event())
        # Should not raise — timer handles the error internally
        await asyncio.sleep(0.1)

        # Batch should still be flushed despite callback error
        assert not batcher.has_pending()

    async def test_set_flush_callback_replaces_previous(self, batcher):
        """Setting a new callback replaces the old one."""
        first_callback = AsyncMock()
        second_callback = AsyncMock()

        batcher.set_flush_callback(first_callback)
        batcher.set_flush_callback(second_callback)

        await batcher.add(_make_event())
        await asyncio.sleep(0.1)

        first_callback.assert_not_awaited()
        second_callback.assert_awaited_once()

"""Tests for voice_copilot.summarizer.summarizer — Core Summarizer orchestrator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voice_copilot.events.event_bus import EventBus
from voice_copilot.events.types import BlockReason, EventType, VoiceCopilotEvent
from voice_copilot.summarizer.summarizer import Summarizer
from voice_copilot.summarizer.template_engine import TemplateEngine
from voice_copilot.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION = "test-session-summarizer"


def _make_event(
    event_type: EventType = EventType.TOOL_EXECUTED,
    **kwargs,
) -> VoiceCopilotEvent:
    """Helper to create a minimal VoiceCopilotEvent for testing."""
    defaults = {
        "type": event_type,
        "session_id": _SESSION,
        "source": "hook",
    }
    defaults.update(kwargs)
    return VoiceCopilotEvent(**defaults)


def _make_narration(
    text: str = "Test narration.",
    priority: NarrationPriority = NarrationPriority.NORMAL,
    source_event_type: EventType = EventType.TOOL_EXECUTED,
    method: SummarizationMethod = SummarizationMethod.TEMPLATE,
) -> NarrationEvent:
    """Helper to create a NarrationEvent for mock return values."""
    return NarrationEvent(
        text=text,
        priority=priority,
        source_event_type=source_event_type,
        summarization_method=method,
        session_id=_SESSION,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def narration_bus() -> EventBus:
    """Return a fresh EventBus for narration events."""
    return EventBus(maxsize=64)


@pytest.fixture
def source_bus() -> EventBus:
    """Return a fresh EventBus for source VoiceCopilotEvents."""
    return EventBus(maxsize=64)


@pytest.fixture
def summarizer(source_bus: EventBus, narration_bus: EventBus) -> Summarizer:
    """Return a Summarizer wired to the test buses (not yet started)."""
    return Summarizer(event_bus=source_bus, narration_bus=narration_bus)


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for Summarizer start() and stop() lifecycle."""

    async def test_start_subscribes_to_event_bus(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        assert source_bus.subscriber_count == 0

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        assert source_bus.subscriber_count == 1

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_start_creates_consume_task(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        assert summarizer._task is None

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        assert summarizer._task is not None
        assert not summarizer._task.done()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_stop_cancels_consume_task(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        task = summarizer._task

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

        assert summarizer._task is None
        assert task.done()

    async def test_stop_unsubscribes_from_event_bus(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        assert source_bus.subscriber_count == 1

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

        assert source_bus.subscriber_count == 0

    async def test_stop_calls_llm_summarizer_stop(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        with patch.object(
            summarizer._llm_summarizer, "stop", new_callable=AsyncMock
        ) as mock_stop:
            await summarizer.stop()

        mock_stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# Routing — tool_executed
# ---------------------------------------------------------------------------


class TestRouteToolExecuted:
    """Tests for routing tool_executed events through the batcher."""

    async def test_tool_executed_goes_through_batcher(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        event = _make_event(
            EventType.TOOL_EXECUTED,
            tool_name="Read",
            tool_input={"file_path": "/tmp/foo.py"},
        )

        mock_narration = _make_narration("Read foo.py")

        with patch.object(
            summarizer._batcher, "add", new_callable=AsyncMock, return_value=mock_narration
        ) as mock_add:
            with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
                await summarizer.start()

            await source_bus.emit(event)
            await asyncio.sleep(0.05)

            mock_add.assert_awaited_once_with(event)

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_tool_executed_batcher_returns_none_no_emission(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        """When batcher returns None (still accumulating), nothing is emitted."""
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(EventType.TOOL_EXECUTED, tool_name="Read")

        with patch.object(
            summarizer._batcher, "add", new_callable=AsyncMock, return_value=None
        ):
            with patch.object(
                summarizer._batcher, "has_pending", return_value=False
            ):
                with patch.object(
                    summarizer._llm_summarizer, "start", new_callable=AsyncMock
                ):
                    await summarizer.start()

                await source_bus.emit(event)
                await asyncio.sleep(0.05)

        assert narration_queue.empty()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Routing — agent_message
# ---------------------------------------------------------------------------


class TestRouteAgentMessage:
    """Tests for routing agent_message events through the LLM summarizer."""

    async def test_agent_message_goes_through_llm_summarizer(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        event = _make_event(
            EventType.AGENT_MESSAGE,
            text="I have finished refactoring the module.",
        )

        mock_narration = _make_narration(
            "Finished refactoring.",
            source_event_type=EventType.AGENT_MESSAGE,
            method=SummarizationMethod.LLM,
        )

        with patch.object(
            summarizer._llm_summarizer,
            "summarize",
            new_callable=AsyncMock,
            return_value=mock_narration,
        ) as mock_summarize:
            with patch.object(
                summarizer._llm_summarizer, "start", new_callable=AsyncMock
            ):
                await summarizer.start()

            await source_bus.emit(event)
            await asyncio.sleep(0.05)

            mock_summarize.assert_awaited_once_with(event)

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_agent_message_emits_narration(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        mock_narration = _make_narration(
            "Summary text.",
            source_event_type=EventType.AGENT_MESSAGE,
        )

        with patch.object(
            summarizer._llm_summarizer,
            "summarize",
            new_callable=AsyncMock,
            return_value=mock_narration,
        ):
            with patch.object(
                summarizer._llm_summarizer, "start", new_callable=AsyncMock
            ):
                await summarizer.start()

            event = _make_event(EventType.AGENT_MESSAGE, text="Some message.")
            await source_bus.emit(event)
            await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert received.text == "Summary text."

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Routing — agent_blocked
# ---------------------------------------------------------------------------


class TestRouteAgentBlocked:
    """Tests for routing agent_blocked events through the template engine."""

    async def test_agent_blocked_goes_through_template_engine(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(
            EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow file write?",
        )

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert received.priority == NarrationPriority.CRITICAL
        assert "permission" in received.text.lower()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_agent_blocked_flushes_batcher_first(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        batch_narration = _make_narration("Edited 2 files.")
        blocked_event = _make_event(
            EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Which branch?",
        )

        with patch.object(
            summarizer._batcher, "has_pending", side_effect=[True, False, False]
        ):
            with patch.object(
                summarizer._batcher,
                "flush",
                new_callable=AsyncMock,
                return_value=batch_narration,
            ) as mock_flush:
                with patch.object(
                    summarizer._llm_summarizer, "start", new_callable=AsyncMock
                ):
                    await summarizer.start()

                await source_bus.emit(blocked_event)
                await asyncio.sleep(0.05)

                mock_flush.assert_awaited_once()

        # Should have emitted the batch narration THEN the blocked narration
        first = narration_queue.get_nowait()
        assert first.text == "Edited 2 files."
        second = narration_queue.get_nowait()
        assert "question" in second.text.lower()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Routing — agent_stopped, session_start, session_end
# ---------------------------------------------------------------------------


class TestRouteTemplateEvents:
    """Tests for routing agent_stopped, session_start, session_end through templates."""

    async def test_agent_stopped_goes_through_template(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(EventType.AGENT_STOPPED, stop_reason="task complete")

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert "stopped" in received.text.lower() or "finished" in received.text.lower()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_session_start_goes_through_template(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(EventType.SESSION_START)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert "session" in received.text.lower()
        assert received.priority == NarrationPriority.LOW

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_session_end_goes_through_template(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(EventType.SESSION_END)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert "session" in received.text.lower()
        assert "ended" in received.text.lower()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Batcher flush on non-tool events
# ---------------------------------------------------------------------------


class TestBatcherFlush:
    """Tests verifying the batcher is flushed when non-tool events arrive."""

    async def test_agent_message_flushes_pending_batch(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        batch_narration = _make_narration("Read 3 files.")
        msg_narration = _make_narration(
            "Summarized.",
            source_event_type=EventType.AGENT_MESSAGE,
            method=SummarizationMethod.TRUNCATION,
        )

        with patch.object(
            summarizer._batcher, "has_pending", side_effect=[True, False, False]
        ):
            with patch.object(
                summarizer._batcher,
                "flush",
                new_callable=AsyncMock,
                return_value=batch_narration,
            ) as mock_flush:
                with patch.object(
                    summarizer._llm_summarizer,
                    "summarize",
                    new_callable=AsyncMock,
                    return_value=msg_narration,
                ):
                    with patch.object(
                        summarizer._llm_summarizer, "start", new_callable=AsyncMock
                    ):
                        await summarizer.start()

                    event = _make_event(EventType.AGENT_MESSAGE, text="Hello")
                    await source_bus.emit(event)
                    await asyncio.sleep(0.05)

                    mock_flush.assert_awaited_once()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_agent_stopped_flushes_pending_batch(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        batch_narration = _make_narration("Ran 2 commands.")

        with patch.object(
            summarizer._batcher, "has_pending", side_effect=[True, False, False]
        ):
            with patch.object(
                summarizer._batcher,
                "flush",
                new_callable=AsyncMock,
                return_value=batch_narration,
            ) as mock_flush:
                with patch.object(
                    summarizer._llm_summarizer, "start", new_callable=AsyncMock
                ):
                    await summarizer.start()

                event = _make_event(EventType.AGENT_STOPPED)
                await source_bus.emit(event)
                await asyncio.sleep(0.05)

                mock_flush.assert_awaited_once()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_no_flush_when_batcher_empty(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        with patch.object(
            summarizer._batcher, "has_pending", return_value=False
        ):
            with patch.object(
                summarizer._batcher, "flush", new_callable=AsyncMock
            ) as mock_flush:
                with patch.object(
                    summarizer._llm_summarizer, "start", new_callable=AsyncMock
                ):
                    await summarizer.start()

                event = _make_event(EventType.SESSION_END)
                await source_bus.emit(event)
                await asyncio.sleep(0.05)

                mock_flush.assert_not_awaited()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Narration bus emission
# ---------------------------------------------------------------------------


class TestNarrationEmission:
    """Tests verifying narrations reach the narration bus."""

    async def test_emitted_narration_reaches_narration_bus(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(EventType.SESSION_START)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        assert not narration_queue.empty()
        received = narration_queue.get_nowait()
        assert isinstance(received, NarrationEvent)

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_narration_has_correct_session_id(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        event = _make_event(EventType.SESSION_START)

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert received.session_id == _SESSION

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# End-to-end tests (real EventBus + TemplateEngine, mocked LLM)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """End-to-end tests using real EventBus and TemplateEngine, mocked LLM."""

    async def test_tool_executed_produces_narration(self):
        """tool_executed -> batcher -> template render_batch -> narration bus."""
        source_bus = EventBus(maxsize=16)
        narration_bus = EventBus(maxsize=16)
        summarizer = Summarizer(source_bus, narration_bus)

        narration_queue = await narration_bus.subscribe()

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        # Send a tool event; batcher will accumulate it. We then flush by
        # sending a non-tool event (session_end) to trigger the flush.
        tool_event = _make_event(
            EventType.TOOL_EXECUTED,
            tool_name="Edit",
            tool_input={"file_path": "/src/main.py"},
        )
        end_event = _make_event(EventType.SESSION_END)

        await source_bus.emit(tool_event)
        await asyncio.sleep(0.02)
        await source_bus.emit(end_event)
        await asyncio.sleep(0.05)

        # We expect two narrations: the flushed batch, then the session_end
        narrations = []
        while not narration_queue.empty():
            narrations.append(narration_queue.get_nowait())

        assert len(narrations) == 2
        # First narration is the flushed tool batch
        assert narrations[0].source_event_type == EventType.TOOL_EXECUTED
        assert narrations[0].summarization_method == SummarizationMethod.TEMPLATE
        # Second narration is session_end
        assert narrations[1].source_event_type == EventType.SESSION_END

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_agent_blocked_produces_critical_narration(self):
        """agent_blocked -> template -> CRITICAL priority narration."""
        source_bus = EventBus(maxsize=16)
        narration_bus = EventBus(maxsize=16)
        summarizer = Summarizer(source_bus, narration_bus)

        narration_queue = await narration_bus.subscribe()

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        event = _make_event(
            EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow bash command?",
        )

        await source_bus.emit(event)
        await asyncio.sleep(0.05)

        received = narration_queue.get_nowait()
        assert received.priority == NarrationPriority.CRITICAL
        assert received.source_event_type == EventType.AGENT_BLOCKED
        assert "permission" in received.text.lower()

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

    async def test_multiple_tool_events_then_blocked(self):
        """Multiple tool events batched, then agent_blocked flushes them first."""
        source_bus = EventBus(maxsize=32)
        narration_bus = EventBus(maxsize=32)
        summarizer = Summarizer(source_bus, narration_bus)

        narration_queue = await narration_bus.subscribe()

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        # Send 3 tool events quickly
        for i in range(3):
            await source_bus.emit(
                _make_event(
                    EventType.TOOL_EXECUTED,
                    tool_name="Edit",
                    tool_input={"file_path": f"/src/file{i}.py"},
                )
            )
            await asyncio.sleep(0.01)

        # Then send a blocked event to trigger flush
        await source_bus.emit(
            _make_event(
                EventType.AGENT_BLOCKED,
                block_reason=BlockReason.IDLE_PROMPT,
            )
        )
        await asyncio.sleep(0.1)

        narrations = []
        while not narration_queue.empty():
            narrations.append(narration_queue.get_nowait())

        # Expect: flushed batch narration, then blocked narration
        assert len(narrations) == 2
        assert narrations[0].source_event_type == EventType.TOOL_EXECUTED
        assert "3" in narrations[0].text  # "Edited 3 files."
        assert narrations[1].priority == NarrationPriority.CRITICAL

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests verifying error handling and resilience."""

    async def test_error_in_processing_does_not_crash_loop(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        """If _process_event raises, the loop continues with the next event."""
        summarizer = Summarizer(source_bus, narration_bus)
        narration_queue = await narration_bus.subscribe()

        # First event will cause an error, second should succeed
        with patch.object(
            summarizer._llm_summarizer, "start", new_callable=AsyncMock
        ):
            await summarizer.start()

        # Temporarily make _process_event raise on first call
        original_process = summarizer._process_event
        call_count = 0

        async def failing_then_ok(event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated processing error")
            return await original_process(event)

        summarizer._process_event = failing_then_ok

        # Emit two events: first will fail, second should succeed
        await source_bus.emit(_make_event(EventType.SESSION_START))
        await asyncio.sleep(0.05)
        await source_bus.emit(_make_event(EventType.SESSION_END))
        await asyncio.sleep(0.05)

        # The second event should have produced a narration
        assert not narration_queue.empty()
        received = narration_queue.get_nowait()
        assert received.source_event_type == EventType.SESSION_END

        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()


# ---------------------------------------------------------------------------
# stop() flushes pending batch
# ---------------------------------------------------------------------------


class TestStopFlush:
    """Tests verifying stop() flushes any pending batch."""

    async def test_stop_flushes_pending_batch(self):
        source_bus = EventBus(maxsize=16)
        narration_bus = EventBus(maxsize=16)
        summarizer = Summarizer(source_bus, narration_bus)

        narration_queue = await narration_bus.subscribe()

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        # Add a tool event that will be batched (not immediately flushed)
        await source_bus.emit(
            _make_event(
                EventType.TOOL_EXECUTED,
                tool_name="Read",
                tool_input={"file_path": "/tmp/file.py"},
            )
        )
        # Wait for the event to be consumed but NOT for the batch timer to expire
        await asyncio.sleep(0.05)

        # stop() should flush the pending batch
        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

        # The flushed batch should appear on the narration bus
        assert not narration_queue.empty()
        received = narration_queue.get_nowait()
        assert received.source_event_type == EventType.TOOL_EXECUTED
        assert received.summarization_method == SummarizationMethod.TEMPLATE

    async def test_stop_no_flush_when_batcher_empty(self):
        source_bus = EventBus(maxsize=16)
        narration_bus = EventBus(maxsize=16)
        summarizer = Summarizer(source_bus, narration_bus)

        narration_queue = await narration_bus.subscribe()

        with patch.object(summarizer._llm_summarizer, "start", new_callable=AsyncMock):
            await summarizer.start()

        # No events sent — batcher should be empty
        with patch.object(summarizer._llm_summarizer, "stop", new_callable=AsyncMock):
            await summarizer.stop()

        assert narration_queue.empty()


# ---------------------------------------------------------------------------
# llm_available property
# ---------------------------------------------------------------------------


class TestLLMAvailable:
    """Tests for the llm_available property."""

    async def test_llm_available_delegates_to_llm_summarizer(
        self, source_bus: EventBus, narration_bus: EventBus
    ):
        summarizer = Summarizer(source_bus, narration_bus)

        # Default: LLMSummarizer._ollama_available is False
        assert summarizer.llm_available is False

        # Simulate availability
        summarizer._llm_summarizer._ollama_available = True
        assert summarizer.llm_available is True

        summarizer._llm_summarizer._ollama_available = False
        assert summarizer.llm_available is False

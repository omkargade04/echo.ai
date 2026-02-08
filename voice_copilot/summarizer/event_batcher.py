"""Time-windowed event batcher that collapses rapid tool_executed events."""

import asyncio
import logging
import time
from typing import Callable, Awaitable

from voice_copilot.events.types import VoiceCopilotEvent, EventType
from voice_copilot.summarizer.types import NarrationEvent

logger = logging.getLogger(__name__)

# This will be injected — TemplateEngine.render_batch
BatchRenderer = Callable[[list[VoiceCopilotEvent]], NarrationEvent]


class EventBatcher:
    """Collapses rapid consecutive tool_executed events into a single narration.

    When tool_executed events arrive in rapid succession, they are accumulated
    in a batch. The batch is flushed (rendered into a single NarrationEvent)
    when:
    1. The batch window (500ms) expires
    2. A non-tool_executed event arrives
    3. The batch reaches MAX_BATCH_SIZE (10)
    """

    BATCH_WINDOW_SEC: float = 0.5  # 500ms
    MAX_BATCH_SIZE: int = 10

    def __init__(self, render_batch: BatchRenderer) -> None:
        self._render_batch = render_batch
        self._batch: list[VoiceCopilotEvent] = []
        self._flush_task: asyncio.Task | None = None
        self._flush_callback: Callable[[NarrationEvent], Awaitable[None]] | None = None

    def set_flush_callback(
        self, callback: Callable[[NarrationEvent], Awaitable[None]]
    ) -> None:
        """Set the async callback invoked when a batch flushes on timer."""
        self._flush_callback = callback

    async def add(self, event: VoiceCopilotEvent) -> NarrationEvent | None:
        """Add a tool_executed event to the batch.

        Returns a NarrationEvent immediately if the batch should flush
        (max size reached). Otherwise returns None (batch is accumulating,
        will flush on timer or when flush() is called explicitly).
        """
        try:
            self._batch.append(event)
            logger.debug(
                "Added event to batch (tool=%s, batch_size=%d)",
                event.tool_name,
                len(self._batch),
            )

            if len(self._batch) >= self.MAX_BATCH_SIZE:
                logger.debug(
                    "Batch reached MAX_BATCH_SIZE=%d, flushing immediately",
                    self.MAX_BATCH_SIZE,
                )
                return await self.flush()

            # First event in the batch — start the flush timer
            if len(self._batch) == 1:
                await self._schedule_flush()

            return None
        except Exception:
            logger.debug("Error in EventBatcher.add", exc_info=True)
            return None

    async def flush(self) -> NarrationEvent | None:
        """Force-flush the current batch. Returns the batched NarrationEvent
        or None if the batch is empty. Cancels any pending flush timer."""
        try:
            # Cancel pending timer first
            if self._flush_task is not None:
                self._flush_task.cancel()
                self._flush_task = None

            if not self._batch:
                logger.debug("Flush called on empty batch, returning None")
                return None

            events = list(self._batch)
            self._batch.clear()

            logger.debug("Flushing batch of %d events", len(events))
            narration = self._render_batch(events)
            return narration
        except Exception:
            logger.debug("Error in EventBatcher.flush", exc_info=True)
            return None

    def has_pending(self) -> bool:
        """Return True if there are events in the batch."""
        return len(self._batch) > 0

    async def _schedule_flush(self) -> None:
        """Start a timer that flushes the batch after BATCH_WINDOW_SEC."""
        # Cancel any existing flush task
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None

        self._flush_task = asyncio.create_task(self._timer_flush())
        logger.debug(
            "Scheduled flush timer for %.3fs", self.BATCH_WINDOW_SEC
        )

    async def _timer_flush(self) -> None:
        """Timer callback: flush batch and invoke flush_callback."""
        try:
            await asyncio.sleep(self.BATCH_WINDOW_SEC)
            narration = await self.flush()
            if narration is not None and self._flush_callback is not None:
                await self._flush_callback(narration)
        except asyncio.CancelledError:
            logger.debug("Flush timer cancelled")
        except Exception:
            logger.debug("Error in EventBatcher._timer_flush", exc_info=True)

"""Core Summarizer — subscribes to EventBus, routes events, emits NarrationEvents.

The Summarizer is the main orchestrator for Stage 2. It:
1. Subscribes to the Stage 1 EventBus
2. Routes each event to the appropriate handler:
   - tool_executed → EventBatcher (batches rapid tool events)
   - agent_message → LLMSummarizer (Ollama or truncation fallback)
   - agent_blocked, agent_stopped, session_start, session_end → TemplateEngine
3. Emits NarrationEvents to the NarrationBus for Stage 3 consumption
"""

import asyncio
import logging

from voice_copilot.events.event_bus import EventBus
from voice_copilot.events.types import EventType, VoiceCopilotEvent
from voice_copilot.summarizer.event_batcher import EventBatcher
from voice_copilot.summarizer.llm_summarizer import LLMSummarizer
from voice_copilot.summarizer.template_engine import TemplateEngine
from voice_copilot.summarizer.types import NarrationEvent

logger = logging.getLogger(__name__)


class Summarizer:
    """Async orchestrator that converts raw events into narration text.

    Lifecycle:
        summarizer = Summarizer(event_bus, narration_bus)
        await summarizer.start()   # subscribes + starts consume loop
        ...
        await summarizer.stop()    # cancels loop + unsubscribes
    """

    def __init__(
        self,
        event_bus: EventBus,
        narration_bus: EventBus,
    ) -> None:
        self._event_bus = event_bus
        self._narration_bus = narration_bus

        self._template_engine = TemplateEngine()
        self._llm_summarizer = LLMSummarizer()
        self._batcher = EventBatcher(render_batch=self._template_engine.render_batch)

        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Subscribe to event bus, start LLM summarizer, begin consume loop."""
        # Wire batcher's timer-flush to emit narrations
        self._batcher.set_flush_callback(self._emit_narration)

        await self._llm_summarizer.start()
        self._queue = await self._event_bus.subscribe()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("Summarizer started")

    async def stop(self) -> None:
        """Cancel consume loop, flush batcher, unsubscribe, stop LLM."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Flush any pending batched events
        if self._batcher.has_pending():
            narration = await self._batcher.flush()
            if narration:
                await self._emit_narration(narration)

        if self._queue:
            await self._event_bus.unsubscribe(self._queue)
            self._queue = None

        await self._llm_summarizer.stop()
        logger.info("Summarizer stopped")

    @property
    def llm_available(self) -> bool:
        """Whether the LLM (Ollama) is currently available."""
        return self._llm_summarizer.is_available

    async def _consume_loop(self) -> None:
        """Main loop: pull events from queue and process them."""
        logger.debug("Summarizer consume loop started")
        try:
            while True:
                event = await self._queue.get()
                try:
                    await self._process_event(event)
                except Exception:
                    logger.exception(
                        "Error processing event %s — skipping",
                        event.type.value,
                    )
        except asyncio.CancelledError:
            logger.debug("Summarizer consume loop cancelled")
            raise

    async def _process_event(self, event: VoiceCopilotEvent) -> None:
        """Route a single event to the appropriate handler."""
        logger.debug("Processing event: %s", event.type.value)

        if event.type == EventType.TOOL_EXECUTED:
            await self._handle_tool_executed(event)
        elif event.type == EventType.AGENT_MESSAGE:
            await self._handle_agent_message(event)
        elif event.type == EventType.AGENT_BLOCKED:
            await self._handle_agent_blocked(event)
        else:
            # agent_stopped, session_start, session_end — template only
            await self._handle_template_event(event)

    async def _handle_tool_executed(self, event: VoiceCopilotEvent) -> None:
        """Route tool_executed through the batcher."""
        # Batcher returns a NarrationEvent immediately if batch hit max size
        narration = await self._batcher.add(event)
        if narration:
            await self._emit_narration(narration)

    async def _handle_agent_message(self, event: VoiceCopilotEvent) -> None:
        """Route agent_message through LLM summarizer (with truncation fallback)."""
        # First, flush any pending tool batch (different event type arrived)
        await self._flush_batcher()

        narration = await self._llm_summarizer.summarize(event)
        await self._emit_narration(narration)

    async def _handle_agent_blocked(self, event: VoiceCopilotEvent) -> None:
        """Route agent_blocked through template engine. CRITICAL priority — flush batcher first."""
        # agent_blocked is CRITICAL — immediately flush any pending batch
        await self._flush_batcher()

        narration = self._template_engine.render(event)
        await self._emit_narration(narration)

    async def _handle_template_event(self, event: VoiceCopilotEvent) -> None:
        """Route other events (agent_stopped, session_start/end) through templates."""
        # Flush any pending tool batch when a non-tool event arrives
        await self._flush_batcher()

        narration = self._template_engine.render(event)
        await self._emit_narration(narration)

    async def _flush_batcher(self) -> None:
        """Flush the event batcher if it has pending events."""
        if self._batcher.has_pending():
            narration = await self._batcher.flush()
            if narration:
                await self._emit_narration(narration)

    async def _emit_narration(self, narration: NarrationEvent) -> None:
        """Push a NarrationEvent to the narration bus."""
        await self._narration_bus.emit(narration)
        logger.info(
            "Narration emitted: [%s] %s",
            narration.priority.value,
            narration.text[:80],
        )

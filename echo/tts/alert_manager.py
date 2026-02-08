"""Alert state manager with repeat/escalation for blocked events.

Subscribes to the EventBus to detect when blocked sessions are
resolved (any non-agent_blocked event clears the alert).  Manages
repeat timers that re-fire alerts at configurable intervals until
the developer responds or max repeats is reached.
"""

import asyncio
import logging
import time
from typing import Callable, Awaitable

from echo.config import ALERT_REPEAT_INTERVAL, ALERT_MAX_REPEATS
from echo.events.event_bus import EventBus
from echo.events.types import BlockReason, EventType, EchoEvent

logger = logging.getLogger(__name__)


class ActiveAlert:
    """Represents an active alert for a blocked session."""

    def __init__(
        self,
        session_id: str,
        block_reason: BlockReason | None,
        narration_text: str,
    ):
        self.session_id = session_id
        self.block_reason = block_reason
        self.narration_text = narration_text
        self.created_at: float = time.monotonic()
        self.repeat_count: int = 0
        self.repeat_task: asyncio.Task | None = None


class AlertManager:
    """Tracks active blocked alerts and manages repeat timers.

    Subscribes to the EventBus to detect:
    1. Non-agent_blocked events for active sessions -> clear alert
    2. Fires repeat alerts via callback at configured intervals
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._active_alerts: dict[str, ActiveAlert] = {}
        self._queue: asyncio.Queue | None = None
        self._consume_task: asyncio.Task | None = None
        self._running: bool = False
        self._repeat_callback: Callable[[BlockReason | None, str], Awaitable[None]] | None = None

    def set_repeat_callback(
        self, callback: Callable[[BlockReason | None, str], Awaitable[None]]
    ) -> None:
        """Set the async callback for repeat alerts.

        The callback signature is: ``async callback(block_reason, narration_text)``
        """
        self._repeat_callback = callback

    async def start(self) -> None:
        """Subscribe to the event bus and start the consume loop."""
        self._queue = await self._event_bus.subscribe()
        self._running = True
        self._consume_task = asyncio.create_task(self._consume_loop())
        logger.info("AlertManager started")

    async def stop(self) -> None:
        """Stop the consume loop and cancel all repeat timers."""
        self._running = False

        # Cancel all active repeat tasks
        for alert in self._active_alerts.values():
            if alert.repeat_task and not alert.repeat_task.done():
                alert.repeat_task.cancel()
        self._active_alerts.clear()

        if self._consume_task:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
            self._consume_task = None

        if self._queue is not None:
            await self._event_bus.unsubscribe(self._queue)
            self._queue = None

        logger.info("AlertManager stopped")

    @property
    def active_alert_count(self) -> int:
        """Number of currently active alerts."""
        return len(self._active_alerts)

    def has_active_alert(self, session_id: str) -> bool:
        """Return True if the given session has an active alert."""
        return session_id in self._active_alerts

    def get_active_alert(self, session_id: str) -> ActiveAlert | None:
        """Return the active alert for the session, or None."""
        return self._active_alerts.get(session_id)

    async def activate(
        self,
        session_id: str,
        block_reason: BlockReason | None,
        narration_text: str,
    ) -> None:
        """Register an active alert and start the repeat timer.

        If an alert already exists for this session, it is replaced
        (old repeat timer cancelled).
        """
        # Clear any existing alert for this session
        await self._clear_alert(session_id)

        alert = ActiveAlert(
            session_id=session_id,
            block_reason=block_reason,
            narration_text=narration_text,
        )
        self._active_alerts[session_id] = alert

        # Start repeat timer if interval > 0
        if ALERT_REPEAT_INTERVAL > 0 and self._running:
            alert.repeat_task = asyncio.create_task(
                self._repeat_loop(session_id)
            )
            logger.debug(
                "Repeat timer started for session %s (interval=%.1fs, max=%d)",
                session_id, ALERT_REPEAT_INTERVAL, ALERT_MAX_REPEATS,
            )

        logger.info(
            "Alert activated for session %s (reason=%s)",
            session_id, block_reason,
        )

    async def _consume_loop(self) -> None:
        """Listen to EventBus for alert resolution events."""
        while self._running:
            try:
                event: EchoEvent = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._handle_event(event)
            except Exception:
                logger.warning("AlertManager error processing event", exc_info=True)

    async def _handle_event(self, event: EchoEvent) -> None:
        """Clear alert if a non-blocked event arrives for an active session."""
        if event.type != EventType.AGENT_BLOCKED:
            if event.session_id in self._active_alerts:
                logger.info(
                    "Alert resolved for session %s (event: %s)",
                    event.session_id, event.type.value,
                )
                await self._clear_alert(event.session_id)

    async def _clear_alert(self, session_id: str) -> None:
        """Remove an active alert and cancel its repeat timer."""
        alert = self._active_alerts.pop(session_id, None)
        if alert and alert.repeat_task and not alert.repeat_task.done():
            alert.repeat_task.cancel()
            try:
                await alert.repeat_task
            except asyncio.CancelledError:
                pass

    async def _repeat_loop(self, session_id: str) -> None:
        """Repeatedly fire alerts until max repeats or cleared."""
        try:
            while self._running:
                await asyncio.sleep(ALERT_REPEAT_INTERVAL)

                alert = self._active_alerts.get(session_id)
                if alert is None:
                    break  # Alert was cleared

                if alert.repeat_count >= ALERT_MAX_REPEATS:
                    logger.info(
                        "Max alert repeats (%d) reached for session %s",
                        ALERT_MAX_REPEATS, session_id,
                    )
                    break

                alert.repeat_count += 1
                logger.info(
                    "Repeating alert for session %s (repeat %d/%d)",
                    session_id, alert.repeat_count, ALERT_MAX_REPEATS,
                )

                if self._repeat_callback:
                    try:
                        await self._repeat_callback(
                            alert.block_reason, alert.narration_text
                        )
                    except Exception:
                        logger.warning(
                            "Repeat callback failed for session %s",
                            session_id, exc_info=True,
                        )
        except asyncio.CancelledError:
            pass

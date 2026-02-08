"""Async fan-out event bus for arbitrary event types."""

import asyncio
import logging
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_SIZE = 256

T = TypeVar("T")


class EventBus(Generic[T]):
    """Fan-out event bus backed by asyncio.Queue.

    Each subscriber gets its own queue. When an event is emitted,
    it is pushed to every active subscriber queue. If a subscriber's
    queue is full the event is dropped for that subscriber (with a
    warning) so that slow consumers never block the producer.
    """

    def __init__(self, maxsize: int = _DEFAULT_QUEUE_SIZE) -> None:
        self._subscribers: list[asyncio.Queue[T]] = []
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    async def emit(self, event: T) -> None:
        """Push *event* to every subscriber queue.

        Queues that are full receive a warning log and the event is
        silently dropped for that subscriber.
        """
        async with self._lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Subscriber queue full — dropping event %s for one subscriber",
                    getattr(event, "type", type(event).__name__),
                )

    async def subscribe(self) -> asyncio.Queue[T]:
        """Create and return a new subscriber queue."""
        queue: asyncio.Queue[T] = asyncio.Queue(
            maxsize=self._maxsize,
        )
        async with self._lock:
            self._subscribers.append(queue)
        logger.debug("New subscriber added (total: %d)", len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[T]) -> None:
        """Remove a subscriber queue.  No-op if the queue is not registered."""
        async with self._lock:
            try:
                self._subscribers.remove(queue)
                logger.debug(
                    "Subscriber removed (remaining: %d)", len(self._subscribers)
                )
            except ValueError:
                logger.debug("Attempted to unsubscribe an unknown queue — ignoring")

    @property
    def subscriber_count(self) -> int:
        """Return the current number of active subscribers."""
        return len(self._subscribers)

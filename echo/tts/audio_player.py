"""Priority-queued audio player with interrupt support for CRITICAL events."""

import asyncio
import logging

import numpy as np
import sounddevice as sd

from echo.config import AUDIO_BACKLOG_THRESHOLD, AUDIO_SAMPLE_RATE
from echo.events.types import BlockReason
from echo.tts.alert_tones import generate_alert_for_reason

logger = logging.getLogger(__name__)


class AudioPlayer:
    """Priority-queued audio player with interrupt support for CRITICAL events.

    Items in the queue are ``(priority_int, sequence_counter, pcm_bytes)``
    tuples where *priority_int* maps to:

    - 0 = CRITICAL  (highest priority)
    - 1 = NORMAL
    - 2 = LOW       (lowest priority, may be dropped under backlog)

    Within the same priority level, items are played in FIFO order via a
    monotonically increasing *sequence_counter*.
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[tuple[int, int, bytes]] = (
            asyncio.PriorityQueue()
        )
        self._sequence: int = 0
        self._worker_task: asyncio.Task[None] | None = None
        self._interrupt_event: asyncio.Event = asyncio.Event()
        self._playing: bool = False
        self._audio_available: bool = False
        self._alert_tones: dict[BlockReason | None, bytes] = {}
        self._stopped: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Probe for an output device, cache the alert tone, and start the worker."""
        try:
            sd.query_devices(kind="output")
            self._audio_available = True
            logger.info("Audio output device detected — playback enabled")
        except Exception:
            self._audio_available = False
            logger.warning("No audio output device — playback disabled")
            return

        for reason in [None, BlockReason.PERMISSION_PROMPT, BlockReason.QUESTION, BlockReason.IDLE_PROMPT]:
            tone_array = generate_alert_for_reason(reason, AUDIO_SAMPLE_RATE)
            pcm16 = np.clip(tone_array * 32767, -32768, 32767).astype(np.int16)
            self._alert_tones[reason] = pcm16.tobytes()

        self._worker_task = asyncio.create_task(self._playback_worker())

    async def stop(self) -> None:
        """Cancel the worker, drain the queue, and halt any in-progress playback."""
        self._stopped = True

        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # Drain the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        try:
            sd.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Whether an audio output device was detected at startup."""
        return self._audio_available

    @property
    def queue_depth(self) -> int:
        """Number of items currently waiting in the playback queue."""
        return self._queue.qsize()

    # ------------------------------------------------------------------
    # Enqueue / interrupt
    # ------------------------------------------------------------------

    async def enqueue(self, pcm_bytes: bytes, priority: int = 1) -> None:
        """Add PCM audio to the playback queue.

        LOW-priority items (priority 2) are silently dropped when the queue
        depth exceeds ``AUDIO_BACKLOG_THRESHOLD``.  CRITICAL items (priority 0)
        are always enqueued.
        """
        if not self._audio_available or self._stopped:
            return

        if priority == 2 and self.queue_depth > AUDIO_BACKLOG_THRESHOLD:
            logger.warning("Dropping LOW priority audio — backlog")
            return

        self._sequence += 1
        await self._queue.put((priority, self._sequence, pcm_bytes))

    async def interrupt(self) -> None:
        """Signal an interrupt: drain non-CRITICAL items and stop current playback."""
        self._interrupt_event.set()

        # Drain non-critical items, re-enqueue critical ones
        kept: list[tuple[int, int, bytes]] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item[0] == 0:  # CRITICAL
                kept.append(item)

        for item in kept:
            await self._queue.put(item)

        try:
            sd.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Direct playback helpers
    # ------------------------------------------------------------------

    async def play_alert(self, block_reason: BlockReason | None = None) -> None:
        """Play the alert tone for the given block reason."""
        if not self._audio_available or not self._alert_tones:
            return
        tone_bytes = self._alert_tones.get(block_reason, self._alert_tones[None])
        await asyncio.to_thread(self._play_sync, tone_bytes)

    async def play_immediate(self, pcm_bytes: bytes) -> None:
        """Play raw PCM bytes immediately, bypassing the queue."""
        if not self._audio_available:
            return
        await asyncio.to_thread(self._play_sync, pcm_bytes)

    # ------------------------------------------------------------------
    # Internal playback
    # ------------------------------------------------------------------

    def _play_sync(self, pcm_bytes: bytes) -> None:
        """Convert int16 PCM bytes to float32 and play via sounddevice.

        This method is intended to run in a worker thread via
        ``asyncio.to_thread``.
        """
        audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        sd.play(audio_float32, samplerate=AUDIO_SAMPLE_RATE)
        sd.wait()

    async def _playback_worker(self) -> None:
        """Background task that dequeues and plays audio in priority order."""
        while not self._stopped:
            try:
                priority, _seq, pcm = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            # During an interrupt, discard non-critical items
            if self._interrupt_event.is_set() and priority > 0:
                continue

            self._playing = True
            self._interrupt_event.clear()

            try:
                await asyncio.to_thread(self._play_sync, pcm)
            except Exception:
                logger.warning("Audio playback failed", exc_info=True)

            self._playing = False

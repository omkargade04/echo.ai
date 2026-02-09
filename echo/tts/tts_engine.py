"""Core TTS orchestrator — subscribes to NarrationBus, synthesizes speech, plays audio.

The TTSEngine is the main orchestrator for Stage 3. It:
1. Subscribes to the Stage 2 NarrationBus
2. Routes each NarrationEvent by priority:
   - CRITICAL → interrupt + alert + immediate playback
   - NORMAL  → synthesize + enqueue at priority 1
   - LOW     → skip if backlogged, else synthesize + enqueue at priority 2
3. Publishes audio to LiveKit for remote listeners (when connected)
"""

import asyncio
import logging

from echo.config import AUDIO_BACKLOG_THRESHOLD
from echo.events.event_bus import EventBus
from echo.events.types import BlockReason
from echo.summarizer.types import NarrationEvent, NarrationPriority
from echo.tts.alert_manager import AlertManager
from echo.tts.audio_player import AudioPlayer
from echo.tts.elevenlabs_client import ElevenLabsClient
from echo.tts.livekit_publisher import LiveKitPublisher
from echo.tts.types import TTSState

logger = logging.getLogger(__name__)


class TTSEngine:
    """Core TTS orchestrator — subscribes to NarrationBus, synthesizes speech, and plays audio."""

    def __init__(self, narration_bus: EventBus, *, event_bus: EventBus | None = None) -> None:
        self._narration_bus = narration_bus
        self._elevenlabs = ElevenLabsClient()
        self._player = AudioPlayer()
        self._livekit = LiveKitPublisher()
        self._queue: asyncio.Queue | None = None
        self._consume_task: asyncio.Task | None = None
        self._running: bool = False
        self._processing_critical: bool = False
        self._critical_complete: asyncio.Event = asyncio.Event()
        self._critical_complete.set()  # Initially: no critical work pending
        self._event_bus = event_bus
        self._alert_manager: AlertManager | None = None
        if event_bus is not None:
            self._alert_manager = AlertManager(event_bus)

    async def start(self) -> None:
        """Start sub-components, subscribe to narration bus, begin consume loop."""
        await self._elevenlabs.start()
        await self._player.start()
        await self._livekit.start()

        self._queue = await self._narration_bus.subscribe()
        self._running = True
        self._consume_task = asyncio.create_task(self._consume_loop())

        if self._alert_manager:
            self._alert_manager.set_repeat_callback(self._handle_repeat_alert)
            await self._alert_manager.start()

        logger.info("TTS engine started (state=%s)", self.state.value)

    async def stop(self) -> None:
        """Cancel consume loop, unsubscribe, stop sub-components in reverse order."""
        self._running = False

        if self._alert_manager:
            await self._alert_manager.stop()

        if self._consume_task is not None:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
            self._consume_task = None

        if self._queue is not None:
            await self._narration_bus.unsubscribe(self._queue)
            self._queue = None

        await self._livekit.stop()
        await self._player.stop()
        await self._elevenlabs.stop()
        logger.info("TTS engine stopped")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> TTSState:
        """Operational state of the TTS subsystem."""
        tts_ok = self._elevenlabs.is_available
        audio_ok = self._player.is_available
        if tts_ok and audio_ok:
            return TTSState.ACTIVE
        if tts_ok or audio_ok:
            return TTSState.DEGRADED
        return TTSState.DISABLED

    @property
    def tts_available(self) -> bool:
        """Whether ElevenLabs TTS is currently available."""
        return self._elevenlabs.is_available

    @property
    def audio_available(self) -> bool:
        """Whether the local audio player is available."""
        return self._player.is_available

    @property
    def livekit_connected(self) -> bool:
        """Whether the LiveKit publisher is connected."""
        return self._livekit.is_connected

    @property
    def alert_active(self) -> bool:
        """Return True if any session has an active alert."""
        if self._alert_manager:
            return self._alert_manager.active_alert_count > 0
        return False

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Main loop: pull narration events from queue and process them."""
        logger.debug("TTS consume loop started")
        while self._running:
            try:
                narration = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self._process_narration(narration)
            except Exception:
                logger.warning("Error processing narration", exc_info=True)

    async def _process_narration(self, narration: NarrationEvent) -> None:
        """Route a narration event by priority to the appropriate playback path."""
        if narration.priority == NarrationPriority.CRITICAL:
            await self._handle_critical(narration)
        elif narration.priority == NarrationPriority.NORMAL:
            await self._handle_normal(narration)
        else:
            await self._handle_low(narration)

    # ------------------------------------------------------------------
    # Priority handlers
    # ------------------------------------------------------------------

    async def _handle_critical(self, narration: NarrationEvent) -> None:
        """CRITICAL: interrupt current playback, play reason-specific alert, synthesize, play immediately."""
        self._critical_complete.clear()
        self._processing_critical = True
        try:
            await self._player.interrupt()
            await self._player.play_alert(block_reason=narration.block_reason)

            # Activate alert manager BEFORE synthesis so repeat alerts work
            # even if ElevenLabs is unavailable.
            if self._alert_manager:
                await self._alert_manager.activate(
                    session_id=narration.session_id,
                    block_reason=narration.block_reason,
                    narration_text=narration.text,
                    options=narration.options,
                )

            logger.info(
                "Synthesizing critical narration (tts_available=%s, text_len=%d)",
                self._elevenlabs.is_available,
                len(narration.text),
            )
            pcm = await self._elevenlabs.synthesize(narration.text)
            if pcm is None:
                logger.warning(
                    "Critical narration TTS failed — synthesize returned None "
                    "(tts_available=%s)",
                    self._elevenlabs.is_available,
                )
                return

            if len(pcm) == 0:
                logger.warning("Critical narration TTS returned empty PCM data")
                return

            logger.info("Synthesis OK — %d bytes PCM, playing now", len(pcm))
            await self._player.play_immediate(pcm)
            await self._livekit.publish(pcm)

            logger.info("CRITICAL narration played: %s", narration.text[:80])
        finally:
            self._processing_critical = False
            self._critical_complete.set()

    async def _handle_normal(self, narration: NarrationEvent) -> None:
        """NORMAL: synthesize and enqueue at priority 1."""
        pcm = await self._elevenlabs.synthesize(narration.text)
        if pcm is None:
            logger.debug("Skipping narration — TTS unavailable")
            return

        await self._player.enqueue(pcm, priority=1)
        await self._livekit.publish(pcm)
        logger.info("NORMAL narration: %s", narration.text[:80])

    async def _handle_low(self, narration: NarrationEvent) -> None:
        """LOW: skip if backlogged, otherwise synthesize and enqueue at priority 2."""
        if self._player.queue_depth > AUDIO_BACKLOG_THRESHOLD:
            logger.warning("Skipping LOW narration — audio backlog")
            return

        pcm = await self._elevenlabs.synthesize(narration.text)
        if pcm is None:
            logger.debug("Skipping narration — TTS unavailable")
            return

        await self._player.enqueue(pcm, priority=2)
        await self._livekit.publish(pcm)

    async def _handle_repeat_alert(
        self, block_reason: BlockReason | None, text: str
    ) -> None:
        """Callback for AlertManager repeat alerts."""
        await self._player.interrupt()
        await self._player.play_alert(block_reason=block_reason)

        pcm = await self._elevenlabs.synthesize(text)
        if pcm is None:
            return

        await self._player.play_immediate(pcm)
        await self._livekit.publish(pcm)

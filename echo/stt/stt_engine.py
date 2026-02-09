"""Core STT orchestrator — coordinates microphone, transcription, matching, and dispatch.

The STTEngine subscribes to the EventBus (not NarrationBus) because it needs the
original EchoEvent.options list. It watches for agent_blocked events and starts
a listen task. It also watches for non-blocked events to cancel listening if the
alert is resolved externally.
"""

import asyncio
import logging

from echo.config import STT_CONFIDENCE_THRESHOLD
from echo.events.event_bus import EventBus
from echo.events.types import BlockReason, EchoEvent, EventType
from echo.stt.microphone import MicrophoneCapture
from echo.stt.response_dispatcher import ResponseDispatcher
from echo.stt.response_matcher import ResponseMatcher
from echo.stt.stt_client import STTClient
from echo.stt.types import MatchMethod, MatchResult, ResponseEvent, STTState

logger = logging.getLogger(__name__)

# Maximum time to wait for TTS to finish playing before starting capture.
_TTS_WAIT_TIMEOUT: float = 20.0
_TTS_WAIT_POLL: float = 0.2
# Initial delay to let the pipeline propagate (EventBus → Summarizer → NarrationBus → TTS).
_TTS_WAIT_INITIAL: float = 0.5


class STTEngine:
    """Core STT orchestrator — coordinates microphone, transcription, matching, and dispatch."""

    def __init__(
        self,
        event_bus: EventBus,
        narration_bus: EventBus | None = None,
        response_bus: EventBus | None = None,
        *,
        alert_manager=None,
        tts_engine=None,
    ) -> None:
        self._event_bus = event_bus
        self._narration_bus = narration_bus
        self._response_bus = response_bus
        self._alert_manager = alert_manager
        self._tts_engine = tts_engine

        self._microphone = MicrophoneCapture()
        self._stt_client = STTClient()
        self._matcher = ResponseMatcher()
        self._dispatcher = ResponseDispatcher()

        self._queue: asyncio.Queue | None = None
        self._consume_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        self._running: bool = False
        self._current_session: str | None = None

    async def start(self) -> None:
        """Start sub-components, subscribe to event bus, begin consume loop."""
        await self._microphone.start()
        await self._stt_client.start()
        await self._dispatcher.start()

        self._queue = await self._event_bus.subscribe()
        self._running = True
        self._consume_task = asyncio.create_task(self._consume_loop())

        logger.info("STT engine started (state=%s)", self.state.value)

    async def stop(self) -> None:
        """Stop all sub-components, cancel active listening."""
        self._running = False

        # Cancel active listening task
        if self._listen_task and not self._listen_task.done():
            self._microphone.cancel()
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        # Cancel consume loop
        if self._consume_task is not None:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
            self._consume_task = None

        # Unsubscribe from event bus
        if self._queue is not None:
            await self._event_bus.unsubscribe(self._queue)
            self._queue = None

        # Stop sub-components in reverse order
        await self._dispatcher.stop()
        await self._stt_client.stop()
        await self._microphone.stop()

        self._current_session = None
        logger.info("STT engine stopped")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> STTState:
        """Operational state of the STT subsystem."""
        if self.is_listening:
            return STTState.LISTENING
        stt_ok = self._stt_client.is_available
        mic_ok = self._microphone.is_available
        if stt_ok and mic_ok:
            return STTState.ACTIVE
        if stt_ok or mic_ok:
            return STTState.DEGRADED
        return STTState.DISABLED

    @property
    def is_listening(self) -> bool:
        return self._microphone.is_listening

    @property
    def stt_available(self) -> bool:
        return self._stt_client.is_available

    @property
    def mic_available(self) -> bool:
        return self._microphone.is_available

    @property
    def dispatch_available(self) -> bool:
        return self._dispatcher.is_available

    # ------------------------------------------------------------------
    # Consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Listen to EventBus for agent_blocked events with options."""
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
                logger.warning("STTEngine error processing event", exc_info=True)

    async def _handle_event(self, event: EchoEvent) -> None:
        """Handle incoming events from the EventBus."""
        if event.type == EventType.AGENT_BLOCKED:
            await self._handle_blocked_event(event)
        else:
            # Non-blocked event for active session -> cancel listening
            if self._current_session and event.session_id == self._current_session:
                await self._cancel_listening(event.session_id)

    async def _handle_blocked_event(self, event: EchoEvent) -> None:
        """Start listening when agent is blocked with options."""
        # Cancel any existing listen task
        if self._listen_task and not self._listen_task.done():
            self._microphone.cancel()
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        self._current_session = event.session_id
        self._listen_task = asyncio.create_task(
            self._listen_and_respond(
                event.session_id, event.options, event.block_reason
            )
        )

    async def _wait_for_tts(self) -> None:
        """Wait for TTSEngine to finish playing the critical narration.

        The STTEngine receives events from EventBus BEFORE the TTSEngine
        receives the corresponding NarrationEvent (which goes through the
        Summarizer first).  A short initial delay lets the pipeline
        propagate the event to TTSEngine so ``_critical_complete`` is
        cleared before we check it.  Then we await the Event for proper
        synchronization instead of busy-polling a boolean flag.
        """
        if not self._tts_engine:
            return

        # Give the pipeline time: EventBus → Summarizer → NarrationBus → TTS
        await asyncio.sleep(_TTS_WAIT_INITIAL)

        critical_complete = getattr(self._tts_engine, "_critical_complete", None)
        if critical_complete is None:
            # Fallback: poll the boolean flag
            elapsed = 0.0
            while elapsed < _TTS_WAIT_TIMEOUT:
                if not getattr(self._tts_engine, "_processing_critical", False):
                    return
                await asyncio.sleep(_TTS_WAIT_POLL)
                elapsed += _TTS_WAIT_POLL
            logger.warning("Timed out waiting for TTS (poll fallback)")
            return

        try:
            await asyncio.wait_for(critical_complete.wait(), timeout=_TTS_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for TTS to finish critical playback")

    async def _listen_and_respond(
        self,
        session_id: str,
        options: list[str] | None,
        block_reason: BlockReason | None,
    ) -> None:
        """Full cycle: capture -> transcribe -> match -> confirm -> dispatch."""
        try:
            # Wait for TTS to finish playing the alert + narration before
            # opening the microphone, to avoid sounddevice conflicts.
            await self._wait_for_tts()

            # Step 1: Capture audio
            if not self._microphone.is_available:
                logger.info("Microphone not available — skipping voice capture")
                return

            audio_bytes = await self._microphone.capture_until_silence()
            if audio_bytes is None:
                logger.info("No speech detected for session %s", session_id)
                return

            # Step 2: Transcribe
            if not self._stt_client.is_available:
                logger.info("STT not available — cannot transcribe")
                return

            transcript = await self._stt_client.transcribe(audio_bytes)
            if transcript is None:
                logger.warning(
                    "STT transcription returned empty for session %s", session_id
                )
                return

            logger.info("Transcript for session %s: %s", session_id, transcript)

            # Step 3: Match
            match_result = self._matcher.match(transcript, options, block_reason)
            logger.info(
                "Match result for session %s: text=%s, confidence=%.2f, method=%s",
                session_id,
                match_result.matched_text,
                match_result.confidence,
                match_result.method.value,
            )

            # Step 4: Check confidence
            if (
                match_result.method != MatchMethod.VERBATIM
                and match_result.confidence < STT_CONFIDENCE_THRESHOLD
            ):
                logger.info(
                    "Low confidence (%.2f < %.2f) for session %s — not dispatching",
                    match_result.confidence,
                    STT_CONFIDENCE_THRESHOLD,
                    session_id,
                )
                return

            # Step 5: Emit response event
            response_event = ResponseEvent(
                text=match_result.matched_text,
                transcript=transcript,
                session_id=session_id,
                match_method=match_result.method,
                confidence=match_result.confidence,
                options=options,
            )
            if self._response_bus:
                await self._response_bus.emit(response_event)

            # Step 6: Confirm and dispatch
            await self._confirm_and_dispatch(match_result, session_id)

        except asyncio.CancelledError:
            logger.debug("Listen task cancelled for session %s", session_id)
            raise
        except Exception:
            logger.warning(
                "Listen and respond failed for session %s",
                session_id,
                exc_info=True,
            )
        finally:
            if self._current_session == session_id:
                self._current_session = None

    async def _confirm_and_dispatch(
        self, match_result: MatchResult, session_id: str
    ) -> None:
        """Narrate confirmation, then dispatch response."""
        confirmation_text = f"Sending: {match_result.matched_text}"

        # Optional: use TTS engine to confirm
        if self._tts_engine and hasattr(self._tts_engine, "_elevenlabs"):
            try:
                pcm = await self._tts_engine._elevenlabs.synthesize(confirmation_text)
                if pcm and hasattr(self._tts_engine, "_player"):
                    await self._tts_engine._player.play_immediate(pcm)
            except Exception:
                logger.debug("Confirmation TTS failed — continuing with dispatch")

        # Dispatch the response
        if self._dispatcher.is_available:
            success = await self._dispatcher.dispatch(match_result.matched_text)
            if success:
                logger.info(
                    "Response dispatched for session %s: %s",
                    session_id,
                    match_result.matched_text,
                )
            else:
                logger.warning("Response dispatch failed for session %s", session_id)
        else:
            logger.info(
                "Dispatch unavailable — matched response: %s (please type manually)",
                match_result.matched_text,
            )

        # Clear the alert so it stops repeating
        if self._alert_manager and hasattr(self._alert_manager, "clear_alert"):
            await self._alert_manager.clear_alert(session_id)

    async def _cancel_listening(self, session_id: str) -> None:
        """Cancel active listening for a session."""
        if self._listen_task and not self._listen_task.done():
            logger.info(
                "Cancelling listening for resolved session %s", session_id
            )
            # Signal the microphone thread to stop BEFORE cancelling the task.
            self._microphone.cancel()
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._current_session == session_id:
            self._current_session = None

    async def handle_manual_response(self, session_id: str, text: str) -> bool:
        """Handle a manual text response (from POST /respond endpoint).

        Bypasses STT capture and matching — dispatches directly.
        Returns True if dispatch succeeded.
        """
        # Cancel any active listening for this session
        await self._cancel_listening(session_id)

        if self._response_bus:
            response_event = ResponseEvent(
                text=text,
                transcript=text,
                session_id=session_id,
                match_method=MatchMethod.VERBATIM,
                confidence=1.0,
            )
            await self._response_bus.emit(response_event)

        if self._dispatcher.is_available:
            success = await self._dispatcher.dispatch(text)
            logger.info(
                "Manual response dispatched for %s: %s (success=%s)",
                session_id,
                text,
                success,
            )
            # Clear the alert so it stops repeating
            if success and self._alert_manager and hasattr(self._alert_manager, "clear_alert"):
                await self._alert_manager.clear_alert(session_id)
            return success
        else:
            logger.warning("Dispatch unavailable for manual response: %s", text)
            # Still clear alert even if dispatch is unavailable
            if self._alert_manager and hasattr(self._alert_manager, "clear_alert"):
                await self._alert_manager.clear_alert(session_id)
            return False

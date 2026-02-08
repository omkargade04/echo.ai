"""LiveKit room audio publisher for remote TTS streaming.

Publishes PCM audio frames to a LiveKit Cloud room so remote listeners
can hear narration in real time.  Gracefully disabled when credentials
are missing or the ``livekit`` SDK is not installed.
"""

import logging

import numpy as np

from echo.config import (
    LIVEKIT_URL,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    AUDIO_SAMPLE_RATE,
)

logger = logging.getLogger(__name__)

try:
    from livekit import rtc, api as livekit_api

    LIVEKIT_SDK_AVAILABLE = True
except ImportError:
    LIVEKIT_SDK_AVAILABLE = False


class LiveKitPublisher:
    """Publishes TTS audio to a LiveKit Cloud room for remote listeners."""

    def __init__(self) -> None:
        self._connected: bool = False
        self._room: "rtc.Room | None" = None
        self._audio_source: "rtc.AudioSource | None" = None
        self._track: "rtc.LocalAudioTrack | None" = None

    @property
    def is_connected(self) -> bool:
        """Whether the publisher is currently connected to a LiveKit room."""
        return self._connected

    @property
    def is_configured(self) -> bool:
        """Whether all LiveKit credentials are present and the SDK is available."""
        return bool(
            LIVEKIT_URL
            and LIVEKIT_API_KEY
            and LIVEKIT_API_SECRET
            and LIVEKIT_SDK_AVAILABLE
        )

    async def start(self) -> None:
        """Connect to the LiveKit room and publish an audio track."""
        if not self.is_configured:
            logger.info("LiveKit not configured — remote audio disabled")
            return

        try:
            token = (
                livekit_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
                .with_identity("echo-server")
                .with_grants(
                    livekit_api.VideoGrants(
                        room_join=True,
                        room="echo-tts",
                        can_publish=True,
                    )
                )
                .to_jwt()
            )

            self._room = rtc.Room()
            await self._room.connect(LIVEKIT_URL, token)

            self._audio_source = rtc.AudioSource(AUDIO_SAMPLE_RATE, 1)
            self._track = rtc.LocalAudioTrack.create_audio_track(
                "echo-narration", self._audio_source
            )
            await self._room.local_participant.publish_track(self._track)

            self._connected = True
            logger.info("Connected to LiveKit room")
        except Exception:
            logger.warning("Failed to connect to LiveKit", exc_info=True)
            self._connected = False

    async def stop(self) -> None:
        """Disconnect from the LiveKit room and release resources."""
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception:
                logger.warning("Error disconnecting from LiveKit", exc_info=True)

        self._room = None
        self._audio_source = None
        self._track = None
        self._connected = False
        logger.info("Disconnected from LiveKit")

    async def publish(self, pcm_bytes: bytes) -> None:
        """Publish raw PCM16 audio bytes to the LiveKit room.

        Args:
            pcm_bytes: Raw little-endian int16 PCM audio at AUDIO_SAMPLE_RATE.
        """
        if not self._connected or self._audio_source is None:
            return

        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            frame = rtc.AudioFrame(
                data=samples.tobytes(),
                sample_rate=AUDIO_SAMPLE_RATE,
                num_channels=1,
                samples_per_channel=len(samples),
            )
            await self._audio_source.capture_frame(frame)
        except Exception:
            logger.warning("Failed to publish audio to LiveKit", exc_info=True)

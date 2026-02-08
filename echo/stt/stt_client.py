"""OpenAI Whisper API HTTP client with health checking and graceful degradation.

Sends audio to the Whisper speech-to-text API and returns transcript text.
Falls back silently (returns None) when the API is unreachable or the key
is missing, following the same lifecycle pattern as ElevenLabsClient.
"""

import io
import logging
import time
import wave

import httpx

from echo.config import (
    STT_API_KEY,
    STT_BASE_URL,
    STT_HEALTH_CHECK_INTERVAL,
    STT_MODEL,
    STT_TIMEOUT,
)

logger = logging.getLogger(__name__)


class STTClient:
    """OpenAI Whisper API HTTP client with health checking and graceful degradation."""

    def __init__(self) -> None:
        self._available: bool = False
        self._last_health_check: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the HTTP client and run initial health check."""
        if not STT_API_KEY:
            self._available = False
            logger.info("No STT API key — STT disabled")
            return

        self._client = httpx.AsyncClient(
            base_url=STT_BASE_URL,
            timeout=STT_TIMEOUT,
            headers={"Authorization": f"Bearer {STT_API_KEY}"},
        )
        await self._check_health()

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def is_available(self) -> bool:
        """Whether the Whisper API is currently available."""
        return self._available

    async def transcribe(self, audio_bytes: bytes) -> str | None:
        """Send PCM audio to Whisper API, return transcript text.

        Audio is wrapped in a WAV header before upload (Whisper needs a file format).
        Returns None on any failure (network, auth, timeout).
        """
        await self._maybe_recheck_health()

        if not self._available or not self._client:
            return None

        try:
            wav_buffer = self._wrap_wav(audio_bytes)
            response = await self._client.post(
                "/v1/audio/transcriptions",
                data={"model": STT_MODEL},
                files={"file": ("audio.wav", wav_buffer, "audio/wav")},
            )
            response.raise_for_status()
            result = response.json()
            transcript = result.get("text", "").strip()
            if not transcript:
                return None
            logger.debug("STT transcript: %s", transcript)
            return transcript
        except Exception:
            logger.warning("STT transcription failed", exc_info=True)
            return None

    async def _check_health(self) -> None:
        """Validate API key via GET /v1/models."""
        self._last_health_check = time.monotonic()
        if not self._client:
            self._available = False
            return
        try:
            resp = await self._client.get("/v1/models")
            if resp.status_code == 200:
                self._available = True
                logger.info(
                    "STT (Whisper) available at %s (model: %s)",
                    STT_BASE_URL,
                    STT_MODEL,
                )
            else:
                self._available = False
                logger.warning(
                    "Whisper API returned status %d — STT unavailable",
                    resp.status_code,
                )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            self._available = False
            logger.warning(
                "Whisper API not available at %s — STT disabled: %s",
                STT_BASE_URL,
                exc,
            )

    async def _maybe_recheck_health(self) -> None:
        """Re-check Whisper availability if enough time has passed."""
        if not self._available:
            elapsed = time.monotonic() - self._last_health_check
            if elapsed >= STT_HEALTH_CHECK_INTERVAL:
                await self._check_health()

    @staticmethod
    def _wrap_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> io.BytesIO:
        """Wrap raw PCM int16 bytes in a WAV header."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        buf.seek(0)
        return buf

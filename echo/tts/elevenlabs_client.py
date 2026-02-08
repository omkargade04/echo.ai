"""ElevenLabs TTS HTTP client with health checking and graceful degradation.

Sends text to the ElevenLabs text-to-speech API and returns raw PCM audio
bytes. Falls back silently (returns None) when the API is unreachable or
the key is missing, following the same lifecycle pattern as LLMSummarizer.
"""

import logging
import time

import httpx

from echo.config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_BASE_URL,
    TTS_VOICE_ID,
    TTS_MODEL,
    TTS_TIMEOUT,
    TTS_HEALTH_CHECK_INTERVAL,
)

logger = logging.getLogger(__name__)


class ElevenLabsClient:
    """ElevenLabs TTS HTTP client with health checking and graceful degradation."""

    def __init__(self) -> None:
        self._available: bool = False
        self._last_health_check: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the HTTP client and run initial health check."""
        if not ELEVENLABS_API_KEY:
            self._available = False
            logger.info("No ElevenLabs API key — TTS disabled")
            return

        self._client = httpx.AsyncClient(
            base_url=ELEVENLABS_BASE_URL,
            timeout=TTS_TIMEOUT,
            headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
        await self._check_health()

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def is_available(self) -> bool:
        """Whether ElevenLabs is currently available."""
        return self._available

    async def synthesize(self, text: str) -> bytes | None:
        """Synthesize text to PCM audio bytes via ElevenLabs.

        Returns raw PCM 16kHz bytes on success, None on any failure.
        """
        await self._maybe_recheck_health()

        if not self._available or not self._client:
            return None

        try:
            response = await self._client.post(
                f"/v1/text-to-speech/{TTS_VOICE_ID}",
                json={"text": text, "model_id": TTS_MODEL},
                params={"output_format": "pcm_16000"},
            )
            response.raise_for_status()
            return response.content
        except Exception:
            logger.warning("ElevenLabs synthesis failed", exc_info=True)
            return None

    async def _check_health(self) -> None:
        """Validate the API key via GET /v1/user."""
        self._last_health_check = time.monotonic()
        if not self._client:
            self._available = False
            return
        try:
            resp = await self._client.get("/v1/user")
            if resp.status_code == 200:
                self._available = True
                logger.info(
                    "ElevenLabs TTS available at %s (voice: %s, model: %s)",
                    ELEVENLABS_BASE_URL,
                    TTS_VOICE_ID,
                    TTS_MODEL,
                )
            else:
                self._available = False
                logger.warning(
                    "ElevenLabs returned status %d — TTS unavailable",
                    resp.status_code,
                )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            self._available = False
            logger.warning(
                "ElevenLabs not available at %s — TTS disabled: %s",
                ELEVENLABS_BASE_URL,
                exc,
            )

    async def _maybe_recheck_health(self) -> None:
        """Re-check ElevenLabs availability if enough time has passed."""
        if not self._available:
            elapsed = time.monotonic() - self._last_health_check
            if elapsed >= TTS_HEALTH_CHECK_INTERVAL:
                await self._check_health()

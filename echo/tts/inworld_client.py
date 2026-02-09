"""Inworld TTS HTTP client with health checking and graceful degradation.

Sends text to the Inworld text-to-speech API and returns raw PCM audio
bytes. Falls back silently (returns None) when the API is unreachable or
the key is missing, following the same lifecycle pattern as LLMSummarizer.
"""

import base64
import logging
import time

import httpx

from echo.config import (
    INWORLD_API_KEY,
    INWORLD_BASE_URL,
    INWORLD_VOICE_ID,
    INWORLD_MODEL,
    INWORLD_TIMEOUT,
    INWORLD_TEMPERATURE,
    INWORLD_SPEAKING_RATE,
    TTS_HEALTH_CHECK_INTERVAL,
    AUDIO_SAMPLE_RATE,
)
from echo.tts.provider import TTSProvider

logger = logging.getLogger(__name__)


class InworldClient(TTSProvider):
    """Inworld TTS HTTP client with health checking and graceful degradation."""

    def __init__(self) -> None:
        self._available: bool = False
        self._last_health_check: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the HTTP client and run initial health check."""
        if not INWORLD_API_KEY:
            self._available = False
            logger.info("No Inworld API key — TTS disabled")
            return

        logger.info(
            "Inworld API key loaded: %s...%s (len=%d)",
            INWORLD_API_KEY[:5],
            INWORLD_API_KEY[-5:],
            len(INWORLD_API_KEY),
        )
        self._client = httpx.AsyncClient(
            base_url=INWORLD_BASE_URL,
            timeout=INWORLD_TIMEOUT,
            headers={"Authorization": f"Basic {INWORLD_API_KEY}"},
        )
        await self._check_health()

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def is_available(self) -> bool:
        """Whether Inworld is currently available."""
        return self._available

    @property
    def provider_name(self) -> str:
        """Human-readable provider name for health/status display."""
        return "inworld"

    async def synthesize(self, text: str) -> bytes | None:
        """Synthesize text to PCM audio bytes via Inworld.

        Returns raw PCM 16kHz bytes on success, None on any failure.
        """
        await self._maybe_recheck_health()

        if not self._available or not self._client:
            return None

        try:
            response = await self._client.post(
                "/tts/v1/voice",
                json={
                    "text": text,
                    "voiceId": INWORLD_VOICE_ID,
                    "modelId": INWORLD_MODEL,
                    "audioConfig": {
                        "audioEncoding": "LINEAR16",
                        "sampleRateHertz": AUDIO_SAMPLE_RATE,
                        "speakingRate": INWORLD_SPEAKING_RATE,
                    },
                    "temperature": INWORLD_TEMPERATURE,
                },
            )
            if response.status_code != 200:
                logger.warning(
                    "Inworld synthesis status=%d body=%s headers_sent=%s",
                    response.status_code,
                    response.text[:500],
                    {k: v[:8] + "..." for k, v in response.request.headers.items() if k == "Authorization"},
                )
            response.raise_for_status()

            result = response.json()
            audio_content = result.get("result", {}).get("audioContent")
            if not audio_content:
                logger.warning("Inworld response missing audioContent field")
                return None

            audio_bytes = base64.b64decode(audio_content)

            if audio_bytes[:4] == b"RIFF":
                audio_bytes = audio_bytes[44:]

            return audio_bytes
        except Exception:
            logger.warning("Inworld synthesis failed", exc_info=True)
            return None

    async def _check_health(self) -> None:
        """Validate the API key via minimal synthesis request.

        Inworld has no dedicated health endpoint, so we use a minimal
        synthesis request (text='.') to verify the key actually works.
        """
        self._last_health_check = time.monotonic()
        if not self._client:
            self._available = False
            return
        try:
            resp = await self._client.post(
                "/tts/v1/voice",
                json={
                    "text": ".",
                    "voiceId": INWORLD_VOICE_ID,
                    "modelId": INWORLD_MODEL,
                    "audioConfig": {
                        "audioEncoding": "LINEAR16",
                        "sampleRateHertz": AUDIO_SAMPLE_RATE,
                        "speakingRate": INWORLD_SPEAKING_RATE,
                    },
                    "temperature": INWORLD_TEMPERATURE,
                },
            )
            if resp.status_code == 200:
                self._available = True
                logger.info(
                    "Inworld TTS available at %s (voice: %s, model: %s)",
                    INWORLD_BASE_URL,
                    INWORLD_VOICE_ID,
                    INWORLD_MODEL,
                )
            else:
                self._available = False
                logger.warning(
                    "Inworld health check returned status %d — TTS unavailable "
                    "(check your API key)",
                    resp.status_code,
                )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            self._available = False
            logger.warning(
                "Inworld not available at %s — TTS disabled: %s",
                INWORLD_BASE_URL,
                exc,
            )

    async def _maybe_recheck_health(self) -> None:
        """Re-check Inworld availability if enough time has passed."""
        if not self._available:
            elapsed = time.monotonic() - self._last_health_check
            if elapsed >= TTS_HEALTH_CHECK_INTERVAL:
                await self._check_health()

"""Ollama-based LLM summarizer for agent_message events.

Uses a local Ollama instance to summarize long assistant text into
concise narration suitable for TTS. Falls back to text truncation
when Ollama is unavailable.
"""

import asyncio
import logging
import time

import httpx

from echo.config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_HEALTH_CHECK_INTERVAL,
)
from echo.events.types import EventType, EchoEvent
from echo.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)

logger = logging.getLogger(__name__)

_SUMMARIZATION_PROMPT = (
    "Summarize this AI coding assistant message in one short sentence "
    "(under 20 words) suitable for text-to-speech narration. "
    "Focus on what was done or decided, not how.\n\n"
    "Message:\n{text}\n\nSummary:"
)

_MAX_TRUNCATION_LENGTH = 1000
_TRUNCATED_LENGTH = 990


class LLMSummarizer:
    """Summarizes agent_message text via Ollama LLM with truncation fallback."""

    def __init__(self) -> None:
        self._ollama_available: bool = False
        self._last_health_check: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the HTTP client and run initial health check."""
        self._client = httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=OLLAMA_TIMEOUT)
        await self._check_health()

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def is_available(self) -> bool:
        """Whether Ollama is currently available."""
        return self._ollama_available

    async def summarize(self, event: EchoEvent) -> NarrationEvent:
        """Summarize an agent_message event into a NarrationEvent.

        Tries Ollama first; falls back to truncation on failure.
        """
        text = event.text or ""

        # Periodically re-check Ollama availability
        await self._maybe_recheck_health()

        if self._ollama_available and self._client:
            try:
                summary = await self._call_ollama(text)
                return NarrationEvent(
                    text=summary.strip(),
                    priority=NarrationPriority.NORMAL,
                    source_event_type=EventType.AGENT_MESSAGE,
                    summarization_method=SummarizationMethod.LLM,
                    session_id=event.session_id,
                    source_event_id=event.event_id,
                )
            except Exception:
                logger.warning("Ollama summarization failed — falling back to truncation", exc_info=True)

        # Fallback: truncation
        return self._truncate(event)

    async def _call_ollama(self, text: str) -> str:
        """Call the Ollama /api/generate endpoint."""
        prompt = _SUMMARIZATION_PROMPT.format(text=text)
        response = await self._client.post(
            "/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 50, "temperature": 0.3},
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()

    def _truncate(self, event: EchoEvent) -> NarrationEvent:
        """Produce a NarrationEvent via text truncation (fallback)."""
        text = event.text or ""
        if len(text) <= _MAX_TRUNCATION_LENGTH:
            summary = text
        else:
            summary = text[:_TRUNCATED_LENGTH].rstrip() + "..."

        return NarrationEvent(
            text=summary,
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.AGENT_MESSAGE,
            summarization_method=SummarizationMethod.TRUNCATION,
            session_id=event.session_id,
            source_event_id=event.event_id,
        )

    async def _check_health(self) -> None:
        """Ping Ollama /api/tags to check availability."""
        self._last_health_check = time.monotonic()
        if not self._client:
            self._ollama_available = False
            return
        try:
            resp = await self._client.get("/api/tags")
            self._ollama_available = resp.status_code == 200
            if self._ollama_available:
                logger.info("Ollama is available at %s (model: %s)", OLLAMA_BASE_URL, OLLAMA_MODEL)
            else:
                logger.warning("Ollama returned status %d — using truncation fallback", resp.status_code)
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as exc:
            self._ollama_available = False
            logger.warning("Ollama not available at %s — using truncation fallback: %s", OLLAMA_BASE_URL, exc)

    async def _maybe_recheck_health(self) -> None:
        """Re-check Ollama availability if enough time has passed."""
        if not self._ollama_available:
            elapsed = time.monotonic() - self._last_health_check
            if elapsed >= OLLAMA_HEALTH_CHECK_INTERVAL:
                await self._check_health()

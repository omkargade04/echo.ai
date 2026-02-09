"""Abstract base class for TTS providers.

All TTS providers must implement this interface. The TTSEngine uses it to
synthesize speech without knowing which provider is active. Providers
handle their own health checking and graceful degradation internally.
"""

from abc import ABC, abstractmethod


class TTSProvider(ABC):
    """Abstract base class for TTS providers.

    All providers must return raw PCM 16kHz int16 mono bytes from
    synthesize(), or None on failure. Providers must never raise from
    synthesize() — failures are returned as None and logged internally.
    """

    @abstractmethod
    async def start(self) -> None:
        """Initialize the provider (HTTP clients, health checks, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down the provider and release resources."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether the provider is currently healthy and can synthesize."""

    @abstractmethod
    async def synthesize(self, text: str) -> bytes | None:
        """Synthesize text to raw PCM 16kHz int16 mono bytes.

        Returns None on any failure. Never raises.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name for health/status display."""

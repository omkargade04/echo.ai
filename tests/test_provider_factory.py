"""Tests for echo.tts.provider_factory — TTS provider selection and instantiation."""

import pytest

from echo.tts.elevenlabs_client import ElevenLabsClient
from echo.tts.inworld_client import InworldClient
from echo.tts.provider import TTSProvider
from echo.tts.provider_factory import create_tts_provider


# ---------------------------------------------------------------------------
# TestProviderSelection — provider instantiation based on config
# ---------------------------------------------------------------------------


class TestProviderSelection:
    """Tests for create_tts_provider() factory function."""

    def test_default_creates_elevenlabs(self, monkeypatch):
        """When TTS_PROVIDER is not set, should create ElevenLabsClient."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "elevenlabs")

        provider = create_tts_provider()

        assert isinstance(provider, ElevenLabsClient)

    def test_explicit_elevenlabs(self, monkeypatch):
        """When TTS_PROVIDER is 'elevenlabs', should create ElevenLabsClient."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "elevenlabs")

        provider = create_tts_provider()

        assert isinstance(provider, ElevenLabsClient)

    def test_inworld_creates_inworld(self, monkeypatch):
        """When TTS_PROVIDER is 'inworld', should create InworldClient."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "inworld")

        provider = create_tts_provider()

        assert isinstance(provider, InworldClient)

    def test_case_insensitive(self, monkeypatch):
        """TTS_PROVIDER should be case-insensitive."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "INWORLD")

        provider = create_tts_provider()

        assert isinstance(provider, InworldClient)

    def test_unknown_falls_back_to_elevenlabs(self, monkeypatch):
        """Unknown provider name should fall back to ElevenLabsClient."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "unknown")

        provider = create_tts_provider()

        assert isinstance(provider, ElevenLabsClient)

    def test_returns_tts_provider(self, monkeypatch):
        """All created providers should be instances of TTSProvider."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "elevenlabs")
        provider_el = create_tts_provider()

        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "inworld")
        provider_iw = create_tts_provider()

        assert isinstance(provider_el, TTSProvider)
        assert isinstance(provider_iw, TTSProvider)

    def test_elevenlabs_provider_name(self, monkeypatch):
        """ElevenLabsClient should report provider_name as 'elevenlabs'."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "elevenlabs")

        provider = create_tts_provider()

        assert provider.provider_name == "elevenlabs"

    def test_inworld_provider_name(self, monkeypatch):
        """InworldClient should report provider_name as 'inworld'."""
        monkeypatch.setattr("echo.tts.provider_factory.TTS_PROVIDER", "inworld")

        provider = create_tts_provider()

        assert provider.provider_name == "inworld"

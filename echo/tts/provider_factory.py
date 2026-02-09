"""TTS provider factory — selects and instantiates the correct provider based on config."""

import logging

from echo.config import TTS_PROVIDER
from echo.tts.provider import TTSProvider

logger = logging.getLogger(__name__)


def create_tts_provider() -> TTSProvider:
    """Create the TTS provider instance based on ECHO_TTS_PROVIDER config.

    Returns:
        ElevenLabsClient if TTS_PROVIDER is "elevenlabs" (default)
        InworldClient if TTS_PROVIDER is "inworld"
    """
    provider_name = TTS_PROVIDER.lower()

    if provider_name == "inworld":
        from echo.tts.inworld_client import InworldClient
        logger.info("Creating Inworld TTS provider")
        return InworldClient()

    # Default to ElevenLabs
    from echo.tts.elevenlabs_client import ElevenLabsClient
    logger.info("Creating ElevenLabs TTS provider")
    return ElevenLabsClient()

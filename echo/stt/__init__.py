"""Speech-to-text subsystem for Echo voice response."""

from echo.stt.microphone import MicrophoneCapture
from echo.stt.response_dispatcher import ResponseDispatcher
from echo.stt.response_matcher import ResponseMatcher
from echo.stt.stt_client import STTClient
from echo.stt.stt_engine import STTEngine
from echo.stt.types import MatchMethod, MatchResult, ResponseEvent, STTState

__all__ = [
    "MatchMethod",
    "MatchResult",
    "MicrophoneCapture",
    "ResponseDispatcher",
    "ResponseEvent",
    "ResponseMatcher",
    "STTClient",
    "STTEngine",
    "STTState",
]

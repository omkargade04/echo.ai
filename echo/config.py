"""Configuration constants and helpers for Echo."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (if present) so that env vars are
# available even when the user hasn't explicitly exported them.
_project_env = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_project_env, override=False)

DEFAULT_PORT: int = 7865

CLAUDE_SETTINGS_PATH: Path = Path.home() / ".claude" / "settings.json"
CLAUDE_PROJECTS_PATH: Path = Path.home() / ".claude" / "projects"

ECHO_DIR: Path = Path.home() / ".echo-copilot"
HOOKS_DIR: Path = ECHO_DIR / "hooks"
PID_FILE: Path = ECHO_DIR / "server.pid"


def get_port() -> int:
    """Return the server port from ECHO_PORT env var, or DEFAULT_PORT."""
    raw = os.environ.get("ECHO_PORT")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            return DEFAULT_PORT
    return DEFAULT_PORT


# --- Ollama / LLM configuration ---

OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("ECHO_LLM_MODEL", "qwen2.5:0.5b")
OLLAMA_TIMEOUT: float = float(os.environ.get("ECHO_LLM_TIMEOUT", "5.0"))
OLLAMA_HEALTH_CHECK_INTERVAL: float = 60.0  # Re-check Ollama availability every 60s


# --- TTS provider selection ---

TTS_PROVIDER: str = os.environ.get("ECHO_TTS_PROVIDER", "elevenlabs")


# --- ElevenLabs TTS configuration ---

ELEVENLABS_API_KEY: str = os.environ.get("ECHO_ELEVENLABS_API_KEY", "")
ELEVENLABS_BASE_URL: str = os.environ.get(
    "ECHO_ELEVENLABS_BASE_URL", "https://api.elevenlabs.io"
)
TTS_VOICE_ID: str = os.environ.get("ECHO_TTS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
TTS_MODEL: str = os.environ.get("ECHO_TTS_MODEL", "eleven_turbo_v2_5")
TTS_TIMEOUT: float = float(os.environ.get("ECHO_TTS_TIMEOUT", "10.0"))
TTS_HEALTH_CHECK_INTERVAL: float = float(
    os.environ.get("ECHO_TTS_HEALTH_CHECK_INTERVAL", "60.0")
)


# --- Inworld TTS configuration ---

INWORLD_API_KEY: str = os.environ.get("ECHO_INWORLD_API_KEY", "")
INWORLD_BASE_URL: str = os.environ.get(
    "ECHO_INWORLD_BASE_URL", "https://api.inworld.ai"
)
INWORLD_VOICE_ID: str = os.environ.get("ECHO_INWORLD_VOICE_ID", "Ashley")
INWORLD_MODEL: str = os.environ.get(
    "ECHO_INWORLD_MODEL", "inworld-tts-1.5-max"
)
INWORLD_TIMEOUT: float = float(os.environ.get("ECHO_INWORLD_TIMEOUT", "10.0"))
INWORLD_TEMPERATURE: float = float(
    os.environ.get("ECHO_INWORLD_TEMPERATURE", "1.1")
)
INWORLD_SPEAKING_RATE: float = float(
    os.environ.get("ECHO_INWORLD_SPEAKING_RATE", "1.0")
)


# --- LiveKit configuration ---

LIVEKIT_URL: str = os.environ.get("LIVEKIT_URL", "")
LIVEKIT_API_KEY: str = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET: str = os.environ.get("LIVEKIT_API_SECRET", "")


# --- Audio pipeline configuration ---

AUDIO_SAMPLE_RATE: int = int(os.environ.get("ECHO_AUDIO_SAMPLE_RATE", "16000"))
AUDIO_BACKLOG_THRESHOLD: int = int(
    os.environ.get("ECHO_AUDIO_BACKLOG_THRESHOLD", "3")
)


# --- Alert configuration ---

ALERT_REPEAT_INTERVAL: float = float(
    os.environ.get("ECHO_ALERT_REPEAT_INTERVAL", "30.0")
)  # Seconds between repeat alerts. 0 = no repeat.

ALERT_MAX_REPEATS: int = int(
    os.environ.get("ECHO_ALERT_MAX_REPEATS", "5")
)  # Maximum number of repeat alerts before stopping.


# --- STT / Speech-to-Text configuration ---

STT_API_KEY: str = os.environ.get("ECHO_STT_API_KEY", "")
STT_BASE_URL: str = os.environ.get("ECHO_STT_BASE_URL", "https://api.openai.com")
STT_MODEL: str = os.environ.get("ECHO_STT_MODEL", "whisper-1")
STT_TIMEOUT: float = float(os.environ.get("ECHO_STT_TIMEOUT", "10.0"))
STT_LISTEN_TIMEOUT: float = float(os.environ.get("ECHO_STT_LISTEN_TIMEOUT", "30.0"))
STT_SILENCE_THRESHOLD: float = float(os.environ.get("ECHO_STT_SILENCE_THRESHOLD", "0.01"))
STT_SILENCE_DURATION: float = float(os.environ.get("ECHO_STT_SILENCE_DURATION", "1.5"))
STT_MAX_RECORD_DURATION: float = float(os.environ.get("ECHO_STT_MAX_RECORD_DURATION", "15.0"))
STT_CONFIDENCE_THRESHOLD: float = float(os.environ.get("ECHO_STT_CONFIDENCE_THRESHOLD", "0.6"))
STT_HEALTH_CHECK_INTERVAL: float = float(os.environ.get("ECHO_STT_HEALTH_CHECK_INTERVAL", "60.0"))

# --- Response dispatch configuration ---

DISPATCH_METHOD: str = os.environ.get("ECHO_DISPATCH_METHOD", "")

"""Configuration constants and helpers for Echo."""

import os
from pathlib import Path

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

"""Configuration constants and helpers for Voice Copilot."""

import os
from pathlib import Path

DEFAULT_PORT: int = 7865

CLAUDE_SETTINGS_PATH: Path = Path.home() / ".claude" / "settings.json"
CLAUDE_PROJECTS_PATH: Path = Path.home() / ".claude" / "projects"

VOICE_COPILOT_DIR: Path = Path.home() / ".voice-copilot"
HOOKS_DIR: Path = VOICE_COPILOT_DIR / "hooks"
PID_FILE: Path = VOICE_COPILOT_DIR / "server.pid"


def get_port() -> int:
    """Return the server port from VOICE_COPILOT_PORT env var, or DEFAULT_PORT."""
    raw = os.environ.get("VOICE_COPILOT_PORT")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            return DEFAULT_PORT
    return DEFAULT_PORT


# --- Ollama / LLM configuration ---

OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.environ.get("VOICE_COPILOT_LLM_MODEL", "qwen2.5:0.5b")
OLLAMA_TIMEOUT: float = float(os.environ.get("VOICE_COPILOT_LLM_TIMEOUT", "5.0"))
OLLAMA_HEALTH_CHECK_INTERVAL: float = 60.0  # Re-check Ollama availability every 60s

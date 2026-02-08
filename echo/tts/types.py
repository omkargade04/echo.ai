"""Pydantic-compatible types for Stage 3 TTS pipeline."""

from enum import Enum


class TTSState(str, Enum):
    """Operational state of the TTS subsystem."""

    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"

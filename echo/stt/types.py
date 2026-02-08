"""Pydantic models and enums for the STT subsystem."""

import time
from enum import Enum

from pydantic import BaseModel, Field


class STTState(str, Enum):
    """Operational state of the STT subsystem."""

    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    LISTENING = "listening"


class MatchMethod(str, Enum):
    """How a transcript was matched to an option."""

    ORDINAL = "ordinal"
    DIRECT = "direct"
    YES_NO = "yes_no"
    FUZZY = "fuzzy"
    VERBATIM = "verbatim"


class MatchResult(BaseModel):
    """Result of matching a transcript to available options."""

    matched_text: str
    confidence: float
    method: MatchMethod


class ResponseEvent(BaseModel):
    """A matched response ready for dispatch to the agent."""

    text: str
    transcript: str
    session_id: str
    match_method: MatchMethod
    confidence: float
    timestamp: float = Field(default_factory=time.time)
    options: list[str] | None = None

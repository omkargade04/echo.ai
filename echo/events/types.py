"""Pydantic models for Echo events."""

import time
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events emitted by the Echo system."""

    TOOL_EXECUTED = "tool_executed"
    AGENT_BLOCKED = "agent_blocked"
    AGENT_STOPPED = "agent_stopped"
    AGENT_MESSAGE = "agent_message"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


class BlockReason(str, Enum):
    """Reasons the agent may be blocked and waiting for user input."""

    PERMISSION_PROMPT = "permission_prompt"
    IDLE_PROMPT = "idle_prompt"
    QUESTION = "question"


class EchoEvent(BaseModel):
    """A single event flowing through the Echo event bus.

    Every event has a type, timestamp, session_id, source, and event_id.
    Additional fields are populated depending on the event type:
      - tool_executed: tool_name, tool_input, tool_output
      - agent_blocked: block_reason, message, options
      - agent_message: text
      - agent_stopped: stop_reason
    """

    type: EventType
    timestamp: float = Field(default_factory=time.time)
    session_id: str
    source: Literal["hook", "transcript"]
    event_id: str = Field(default_factory=lambda: str(uuid4()))

    # tool_executed
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: dict | None = None

    # agent_blocked
    block_reason: BlockReason | None = None
    message: str | None = None
    options: list[str] | None = None

    # agent_message (from transcript watcher)
    text: str | None = None

    # agent_stopped
    stop_reason: str | None = None

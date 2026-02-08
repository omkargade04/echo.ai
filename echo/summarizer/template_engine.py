"""Deterministic event-to-narration-text mapper using string templates.

Handles 5 of 6 event types (all except ``agent_message``, which requires
LLM summarization) by filling static template strings with event data.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from echo.events.types import BlockReason, EventType, EchoEvent
from echo.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)

# ---------------------------------------------------------------------------
# Priority lookup
# ---------------------------------------------------------------------------

_PRIORITY_MAP: dict[EventType, NarrationPriority] = {
    EventType.AGENT_BLOCKED: NarrationPriority.CRITICAL,
    EventType.TOOL_EXECUTED: NarrationPriority.NORMAL,
    EventType.AGENT_MESSAGE: NarrationPriority.NORMAL,
    EventType.AGENT_STOPPED: NarrationPriority.NORMAL,
    EventType.SESSION_START: NarrationPriority.LOW,
    EventType.SESSION_END: NarrationPriority.LOW,
}

# ---------------------------------------------------------------------------
# Batch verb mapping  (tool_name -> past-tense verb for batched narration)
# ---------------------------------------------------------------------------

_BATCH_VERB: dict[str, str] = {
    "Edit": "Edited",
    "Read": "Read",
    "Write": "Created",
    "Bash": "Ran",
    "Glob": "Searched",
    "Grep": "Searched",
}

# Maximum length for Bash command text in narration.
_BASH_CMD_MAX_LEN = 60


class TemplateEngine:
    """Deterministic event-to-narration-text mapper using string templates."""

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    def render(self, event: EchoEvent) -> NarrationEvent:
        """Convert a single event to a NarrationEvent using templates."""
        text = self._render_text(event)
        priority = _PRIORITY_MAP.get(event.type, NarrationPriority.NORMAL)
        return NarrationEvent(
            text=text.strip(),
            priority=priority,
            source_event_type=event.type,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id=event.session_id,
            source_event_id=event.event_id,
        )

    def render_batch(self, events: list[EchoEvent]) -> NarrationEvent:
        """Convert a batch of tool_executed events into a single NarrationEvent.

        Counts events per ``tool_name`` and produces a combined narration:
        * If all events share the same tool, e.g. "Edited 3 files."
        * If mixed, combine with "and", e.g. "Edited 2 files and ran a command."
        """
        counts: Counter[str] = Counter()
        for ev in events:
            tool = ev.tool_name or "Unknown"
            counts[tool] += 1

        parts: list[str] = []
        for tool_name, count in counts.items():
            verb = _BATCH_VERB.get(tool_name, "Used")
            noun = self._batch_noun(tool_name, count)
            parts.append(f"{verb} {count} {noun}" if count > 1 else f"{verb} {noun}")

        text = " and ".join(parts) + "."

        first = events[0]
        return NarrationEvent(
            text=text.strip(),
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id=first.session_id,
            source_event_id=first.event_id,
        )

    # --------------------------------------------------------------------- #
    # Internal dispatch
    # --------------------------------------------------------------------- #

    def _render_text(self, event: EchoEvent) -> str:
        """Dispatch to the appropriate template renderer by event type."""
        try:
            if event.type == EventType.TOOL_EXECUTED:
                return self._render_tool_executed(event)
            if event.type == EventType.AGENT_BLOCKED:
                return self._render_agent_blocked(event)
            if event.type == EventType.AGENT_STOPPED:
                return self._render_agent_stopped(event)
            if event.type == EventType.SESSION_START:
                return "New coding session started."
            if event.type == EventType.SESSION_END:
                return "Session ended."
            # agent_message or any unknown type -- fall through
            return f"Agent event: {event.type.value}."
        except Exception:
            # Never raise -- always produce some narration.
            return "An event occurred."

    # --------------------------------------------------------------------- #
    # tool_executed
    # --------------------------------------------------------------------- #

    def _render_tool_executed(self, event: EchoEvent) -> str:
        tool_name = event.tool_name or "Unknown"
        tool_input: dict = event.tool_input or {}

        if tool_name == "Bash":
            command = str(tool_input.get("command", ""))
            if len(command) > _BASH_CMD_MAX_LEN:
                command = command[:_BASH_CMD_MAX_LEN] + "..."
            return f"Ran command: {command}"

        if tool_name == "Read":
            file_path = tool_input.get("file_path", "a file")
            return f"Read {self._basename(file_path)}"

        if tool_name == "Edit":
            file_path = tool_input.get("file_path", "a file")
            return f"Edited {self._basename(file_path)}"

        if tool_name == "Write":
            file_path = tool_input.get("file_path", "a file")
            return f"Created {self._basename(file_path)}"

        if tool_name == "Glob":
            pattern = tool_input.get("pattern", "a pattern")
            return f"Searched for files matching {pattern}"

        if tool_name == "Grep":
            pattern = tool_input.get("pattern", "a pattern")
            return f"Searched code for {pattern}"

        if tool_name == "Task":
            return "Launched a sub-agent"

        if tool_name == "WebFetch":
            return "Fetched a web page"

        if tool_name == "WebSearch":
            query = tool_input.get("query", "something")
            return f"Searched the web for {query}"

        # Unknown / other tool
        return f"Used {tool_name} tool"

    # --------------------------------------------------------------------- #
    # agent_blocked
    # --------------------------------------------------------------------- #

    def _render_agent_blocked(self, event: EchoEvent) -> str:
        reason = event.block_reason
        message = event.message

        if reason == BlockReason.PERMISSION_PROMPT:
            base = f"The agent needs permission. {message}" if message else "The agent needs permission."
        elif reason == BlockReason.IDLE_PROMPT:
            base = "The agent is waiting for your input."
        elif reason == BlockReason.QUESTION:
            base = f"The agent has a question. {message}" if message else "The agent has a question."
        else:
            base = "The agent is blocked and needs attention."

        # Append options if present.
        if event.options:
            base += " " + self._format_options(event.options)

        return base

    # --------------------------------------------------------------------- #
    # agent_stopped
    # --------------------------------------------------------------------- #

    def _render_agent_stopped(self, event: EchoEvent) -> str:
        if event.stop_reason:
            return f"Agent stopped: {event.stop_reason}."
        return "Agent finished."

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    @staticmethod
    def _basename(file_path: str) -> str:
        """Return just the filename from a full path, for TTS readability."""
        if not file_path or file_path == "a file":
            return "a file"
        return Path(file_path).name

    @staticmethod
    def _format_options(options: list[str]) -> str:
        """Format a list of options into a natural-language string.

        * 1 item:  "Options are: foo."
        * 2 items: "Options are: foo and bar."
        * 3+ items: "Options are: foo, bar, or baz."  (Oxford comma)
        """
        if len(options) == 1:
            return f"Options are: {options[0]}."
        if len(options) == 2:
            return f"Options are: {options[0]} and {options[1]}."
        # 3 or more -- Oxford comma with "or" before the last.
        head = ", ".join(options[:-1])
        return f"Options are: {head}, or {options[-1]}."

    @staticmethod
    def _batch_noun(tool_name: str, count: int) -> str:
        """Return the noun phrase for a batched tool narration."""
        if tool_name in ("Edit", "Read", "Write"):
            return "files" if count > 1 else "a file"
        if tool_name == "Bash":
            return "commands" if count > 1 else "a command"
        if tool_name in ("Glob", "Grep"):
            return "searches" if count > 1 else "a search"
        # Unknown tools
        return "tools" if count > 1 else "a tool"

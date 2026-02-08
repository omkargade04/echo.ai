"""Parse raw Claude Code hook JSON into EchoEvent instances."""

import logging

from echo.events.types import BlockReason, EventType, EchoEvent

logger = logging.getLogger(__name__)

# Claude Code hook event names we handle.
_HOOK_POST_TOOL_USE = "PostToolUse"
_HOOK_NOTIFICATION = "Notification"
_HOOK_STOP = "Stop"
_HOOK_SESSION_START = "SessionStart"
_HOOK_SESSION_END = "SessionEnd"


def parse_hook_event(raw_json: dict) -> EchoEvent | None:
    """Convert a raw Claude Code hook payload into a EchoEvent.

    Claude Code invokes hook scripts with a JSON object on stdin.  This
    function inspects the ``hook_event_name`` field and maps it to the
    appropriate ``EventType``, extracting relevant fields.

    Returns ``None`` (and logs a warning) when the hook event name is
    unrecognised or the payload is malformed.
    """
    hook_event_name: str = raw_json.get("hook_event_name", "")
    session_id: str = raw_json.get("session_id", "unknown")

    logger.debug(
        "Parsing hook event: name=%s session_id=%s", hook_event_name, session_id
    )

    try:
        if hook_event_name == _HOOK_POST_TOOL_USE:
            return _parse_post_tool_use(raw_json, session_id)

        if hook_event_name == _HOOK_NOTIFICATION:
            return _parse_notification(raw_json, session_id)

        if hook_event_name == _HOOK_STOP:
            return _parse_stop(raw_json, session_id)

        if hook_event_name == _HOOK_SESSION_START:
            return _parse_session_start(raw_json, session_id)

        if hook_event_name == _HOOK_SESSION_END:
            return _parse_session_end(raw_json, session_id)

        logger.warning("Unrecognised hook event name: %r — skipping", hook_event_name)
        return None

    except Exception:
        logger.exception(
            "Failed to construct EchoEvent from hook payload "
            "(hook_event_name=%s)",
            hook_event_name,
        )
        return None


# ---------------------------------------------------------------------------
# Per-event parsers
# ---------------------------------------------------------------------------


def _parse_post_tool_use(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``PostToolUse`` hook payload to ``EventType.TOOL_EXECUTED``."""
    tool_name: str | None = raw.get("tool_name")
    tool_input: dict | None = raw.get("tool_input")
    tool_output: dict | None = raw.get("tool_response")

    logger.debug(
        "PostToolUse: tool_name=%s input_keys=%s",
        tool_name,
        list(tool_input.keys()) if isinstance(tool_input, dict) else None,
    )

    return EchoEvent(
        type=EventType.TOOL_EXECUTED,
        session_id=session_id,
        source="hook",
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
    )


def _parse_notification(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``Notification`` hook payload to ``EventType.AGENT_BLOCKED``.

    The notification type is inferred from the ``type`` field (or falling
    back to inspecting the ``message`` content).
    """
    notification_type: str = raw.get("type", "")
    message: str | None = raw.get("message")

    block_reason = _infer_block_reason(notification_type, message)

    logger.debug(
        "Notification: notification_type=%s block_reason=%s",
        notification_type,
        block_reason,
    )

    return EchoEvent(
        type=EventType.AGENT_BLOCKED,
        session_id=session_id,
        source="hook",
        block_reason=block_reason,
        message=message,
    )


def _infer_block_reason(
    notification_type: str, message: str | None
) -> BlockReason | None:
    """Determine the ``BlockReason`` from notification metadata.

    Checks the explicit ``type`` field first.  If that is not conclusive,
    falls back to keyword matching against the ``message`` body.
    """
    lowered = notification_type.lower()

    if "permission" in lowered:
        return BlockReason.PERMISSION_PROMPT
    if "idle" in lowered:
        return BlockReason.IDLE_PROMPT
    if "question" in lowered:
        return BlockReason.QUESTION

    # Fallback: inspect message content.
    if message:
        msg_lower = message.lower()
        if "permission" in msg_lower:
            return BlockReason.PERMISSION_PROMPT
        if "idle" in msg_lower:
            return BlockReason.IDLE_PROMPT

    logger.debug(
        "Could not determine block_reason from notification_type=%r, message=%r",
        notification_type,
        message,
    )
    return None


def _parse_stop(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``Stop`` hook payload to ``EventType.AGENT_STOPPED``."""
    stop_reason: str | None = raw.get("stop_reason") or raw.get("reason")

    logger.debug("Stop: stop_reason=%s", stop_reason)

    return EchoEvent(
        type=EventType.AGENT_STOPPED,
        session_id=session_id,
        source="hook",
        stop_reason=stop_reason,
    )


def _parse_session_start(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``SessionStart`` hook payload to ``EventType.SESSION_START``."""
    logger.debug("SessionStart: session_id=%s", session_id)

    return EchoEvent(
        type=EventType.SESSION_START,
        session_id=session_id,
        source="hook",
    )


def _parse_session_end(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``SessionEnd`` hook payload to ``EventType.SESSION_END``."""
    logger.debug("SessionEnd: session_id=%s", session_id)

    return EchoEvent(
        type=EventType.SESSION_END,
        session_id=session_id,
        source="hook",
    )

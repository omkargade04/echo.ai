"""Parse raw Claude Code hook JSON into EchoEvent instances."""

import logging

from echo.events.types import BlockReason, EventType, EchoEvent

logger = logging.getLogger(__name__)

# Claude Code hook event names we handle.
_HOOK_POST_TOOL_USE = "PostToolUse"
_HOOK_NOTIFICATION = "Notification"
_HOOK_PERMISSION_REQUEST = "PermissionRequest"
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

        if hook_event_name == _HOOK_PERMISSION_REQUEST:
            return _parse_permission_request(raw_json, session_id)

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


def _parse_permission_request(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``PermissionRequest`` hook payload to ``EventType.AGENT_BLOCKED``.

    Fires when a permission dialog is about to be shown to the user.
    The payload contains ``tool_name`` and ``tool_input`` describing the
    action that needs approval.
    """
    tool_name: str = raw.get("tool_name", "unknown tool")
    tool_input: dict | None = raw.get("tool_input")

    message = _build_permission_message(tool_name, tool_input)

    logger.debug(
        "PermissionRequest: tool_name=%s message=%s",
        tool_name, message,
    )

    # For AskUserQuestion, use the actual question option labels so
    # the narration reads them and STT can match spoken responses.
    options: list[str] = ["Allow", "Deny"]
    if tool_name == "AskUserQuestion" and tool_input:
        question_options = _extract_question_option_labels(tool_input)
        if question_options:
            options = question_options

    return EchoEvent(
        type=EventType.AGENT_BLOCKED,
        session_id=session_id,
        source="hook",
        block_reason=BlockReason.PERMISSION_PROMPT,
        message=message,
        options=options,
        tool_name=tool_name,
        tool_input=tool_input,
    )


def _build_permission_message(tool_name: str, tool_input: dict | None) -> str:
    """Build a human-readable message for a permission request."""
    if tool_input and isinstance(tool_input, dict):
        if tool_name == "Bash" and "command" in tool_input:
            return f"Claude wants to run: {tool_input['command']}"
        if tool_name == "Write" and "file_path" in tool_input:
            return f"Claude wants to write to: {tool_input['file_path']}"
        if tool_name == "Edit" and "file_path" in tool_input:
            return f"Claude wants to edit: {tool_input['file_path']}"
        if tool_name == "AskUserQuestion":
            return _build_ask_user_question_message(tool_input)
    return f"Claude wants to use {tool_name}"


def _extract_question_option_labels(tool_input: dict) -> list[str] | None:
    """Extract option labels from AskUserQuestion tool_input.

    Returns a list of label strings, or None if extraction fails.
    """
    questions = tool_input.get("questions")
    if not questions or not isinstance(questions, list):
        return None
    first_q = questions[0]
    if not isinstance(first_q, dict):
        return None
    options = first_q.get("options", [])
    if not options or not isinstance(options, list):
        return None
    labels = []
    for opt in options:
        if isinstance(opt, dict):
            labels.append(opt.get("label", str(opt)))
        else:
            labels.append(str(opt))
    return labels if labels else None


def _build_ask_user_question_message(tool_input: dict) -> str:
    """Extract the actual question and options from AskUserQuestion tool_input."""
    questions = tool_input.get("questions")
    if not questions or not isinstance(questions, list):
        return "Claude wants to ask you a question"

    first_q = questions[0]
    if not isinstance(first_q, dict):
        return "Claude wants to ask you a question"

    question_text = first_q.get("question", "")
    options = first_q.get("options", [])

    parts = [f"Claude is asking: {question_text}"] if question_text else ["Claude wants to ask you a question"]

    if options and isinstance(options, list):
        option_labels = []
        for opt in options:
            if isinstance(opt, dict):
                option_labels.append(opt.get("label", str(opt)))
            else:
                option_labels.append(str(opt))
        if option_labels:
            parts.append("The choices are: " + ", ".join(option_labels))

    return " ".join(parts)


def _parse_notification(raw: dict, session_id: str) -> EchoEvent:
    """Map a ``Notification`` hook payload to ``EventType.AGENT_BLOCKED``.

    The notification type is inferred from the ``type`` field (or falling
    back to inspecting the ``message`` content).
    """
    notification_type: str = raw.get("type", "")
    message: str | None = raw.get("message")
    options: list[str] | None = raw.get("options")

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
        options=options,
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

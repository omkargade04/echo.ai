"""Tests for echo.interceptors.hook_handler — Parse hook JSON to events."""

from echo.events.types import BlockReason, EventType
from echo.interceptors.hook_handler import parse_hook_event


# ---------------------------------------------------------------------------
# PostToolUse -> tool_executed
# ---------------------------------------------------------------------------


class TestParsePostToolUse:
    """PostToolUse hook payloads should map to TOOL_EXECUTED events."""

    def test_parse_post_tool_use_event(self):
        raw = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess-100",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_response": {"stdout": "On branch main", "exit_code": 0},
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.TOOL_EXECUTED
        assert event.session_id == "sess-100"
        assert event.source == "hook"
        assert event.tool_name == "Bash"
        assert event.tool_input == {"command": "git status"}
        assert event.tool_output == {"stdout": "On branch main", "exit_code": 0}

    def test_post_tool_use_with_missing_optional_fields(self):
        """tool_name, tool_input, tool_response may all be absent."""
        raw = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess-200",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.TOOL_EXECUTED
        assert event.tool_name is None
        assert event.tool_input is None
        assert event.tool_output is None

    def test_post_tool_use_with_write_tool(self):
        raw = {
            "hook_event_name": "PostToolUse",
            "session_id": "sess-300",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/out.py", "content": "x = 1"},
            "tool_response": {"status": "success"},
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.tool_name == "Write"


# ---------------------------------------------------------------------------
# Notification -> agent_blocked
# ---------------------------------------------------------------------------


class TestParseNotification:
    """Notification hook payloads should map to AGENT_BLOCKED events."""

    def test_notification_permission_prompt_via_type_field(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-400",
            "type": "permission_prompt",
            "message": "Allow running: rm -rf /tmp/test?",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.AGENT_BLOCKED
        assert event.block_reason == BlockReason.PERMISSION_PROMPT
        assert event.message == "Allow running: rm -rf /tmp/test?"

    def test_notification_idle_prompt_via_type_field(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-500",
            "type": "idle_prompt",
            "message": "Claude is waiting for your input.",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.AGENT_BLOCKED
        assert event.block_reason == BlockReason.IDLE_PROMPT

    def test_notification_question_via_type_field(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-550",
            "type": "question",
            "message": "Which database should I use?",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.block_reason == BlockReason.QUESTION

    def test_notification_permission_inferred_from_message_fallback(self):
        """When the type field is empty, block_reason is inferred from message."""
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-600",
            "type": "",
            "message": "Permission required: execute command",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.block_reason == BlockReason.PERMISSION_PROMPT

    def test_notification_idle_inferred_from_message_fallback(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-700",
            "type": "",
            "message": "Agent is idle, waiting for input.",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.block_reason == BlockReason.IDLE_PROMPT

    def test_notification_with_unknown_type_and_no_keywords(self):
        """When neither type nor message yield a keyword, block_reason is None."""
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-800",
            "type": "something_else",
            "message": "General notification",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.AGENT_BLOCKED
        assert event.block_reason is None

    def test_notification_with_missing_message(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-850",
            "type": "permission_prompt",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.block_reason == BlockReason.PERMISSION_PROMPT
        assert event.message is None


# ---------------------------------------------------------------------------
# Stop -> agent_stopped
# ---------------------------------------------------------------------------


class TestParseStop:
    """Stop hook payloads should map to AGENT_STOPPED events."""

    def test_parse_stop_with_stop_reason(self):
        raw = {
            "hook_event_name": "Stop",
            "session_id": "sess-900",
            "stop_reason": "user_cancelled",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.AGENT_STOPPED
        assert event.session_id == "sess-900"
        assert event.stop_reason == "user_cancelled"

    def test_parse_stop_with_reason_field_fallback(self):
        """The handler also checks 'reason' as an alternative key."""
        raw = {
            "hook_event_name": "Stop",
            "session_id": "sess-1000",
            "reason": "task_complete",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.stop_reason == "task_complete"

    def test_parse_stop_with_no_reason(self):
        raw = {
            "hook_event_name": "Stop",
            "session_id": "sess-1100",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.stop_reason is None


# ---------------------------------------------------------------------------
# SessionStart -> session_start
# ---------------------------------------------------------------------------


class TestParseSessionStart:
    """SessionStart hook payloads should map to SESSION_START events."""

    def test_parse_session_start(self):
        raw = {
            "hook_event_name": "SessionStart",
            "session_id": "sess-1200",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.SESSION_START
        assert event.session_id == "sess-1200"
        assert event.source == "hook"


# ---------------------------------------------------------------------------
# SessionEnd -> session_end
# ---------------------------------------------------------------------------


class TestParseSessionEnd:
    """SessionEnd hook payloads should map to SESSION_END events."""

    def test_parse_session_end(self):
        raw = {
            "hook_event_name": "SessionEnd",
            "session_id": "sess-1300",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.SESSION_END
        assert event.session_id == "sess-1300"
        assert event.source == "hook"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Error handling and edge-case behaviour."""

    def test_unknown_hook_event_name_returns_none(self):
        raw = {
            "hook_event_name": "SomeNewHook",
            "session_id": "sess-1400",
        }
        event = parse_hook_event(raw)
        assert event is None

    def test_empty_hook_event_name_returns_none(self):
        raw = {
            "hook_event_name": "",
            "session_id": "sess-1500",
        }
        event = parse_hook_event(raw)
        assert event is None

    def test_missing_hook_event_name_returns_none(self):
        raw = {
            "session_id": "sess-1600",
        }
        event = parse_hook_event(raw)
        assert event is None

    def test_missing_session_id_uses_default_unknown(self):
        """When session_id is absent, it defaults to 'unknown'."""
        raw = {
            "hook_event_name": "SessionStart",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.session_id == "unknown"

    def test_empty_dict_returns_none(self):
        event = parse_hook_event({})
        assert event is None

    def test_extra_fields_do_not_cause_errors(self):
        """Unexpected fields in the payload should be silently ignored."""
        raw = {
            "hook_event_name": "SessionStart",
            "session_id": "sess-1700",
            "unexpected_key": "unexpected_value",
            "nested": {"foo": "bar"},
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.SESSION_START

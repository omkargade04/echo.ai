"""Tests for echo.events.types — Pydantic event models."""

import time

from echo.events.types import BlockReason, EventType, EchoEvent


# ---------------------------------------------------------------------------
# EventType enum
# ---------------------------------------------------------------------------


class TestEventType:
    """Verify that all expected EventType enum values exist."""

    def test_tool_executed_value(self):
        assert EventType.TOOL_EXECUTED == "tool_executed"
        assert EventType.TOOL_EXECUTED.value == "tool_executed"

    def test_agent_blocked_value(self):
        assert EventType.AGENT_BLOCKED == "agent_blocked"
        assert EventType.AGENT_BLOCKED.value == "agent_blocked"

    def test_agent_stopped_value(self):
        assert EventType.AGENT_STOPPED == "agent_stopped"
        assert EventType.AGENT_STOPPED.value == "agent_stopped"

    def test_agent_message_value(self):
        assert EventType.AGENT_MESSAGE == "agent_message"
        assert EventType.AGENT_MESSAGE.value == "agent_message"

    def test_session_start_value(self):
        assert EventType.SESSION_START == "session_start"
        assert EventType.SESSION_START.value == "session_start"

    def test_session_end_value(self):
        assert EventType.SESSION_END == "session_end"
        assert EventType.SESSION_END.value == "session_end"

    def test_enum_has_exactly_six_members(self):
        assert len(EventType) == 6


# ---------------------------------------------------------------------------
# BlockReason enum
# ---------------------------------------------------------------------------


class TestBlockReason:
    """Verify that all expected BlockReason enum values exist."""

    def test_permission_prompt_value(self):
        assert BlockReason.PERMISSION_PROMPT == "permission_prompt"
        assert BlockReason.PERMISSION_PROMPT.value == "permission_prompt"

    def test_idle_prompt_value(self):
        assert BlockReason.IDLE_PROMPT == "idle_prompt"
        assert BlockReason.IDLE_PROMPT.value == "idle_prompt"

    def test_question_value(self):
        assert BlockReason.QUESTION == "question"
        assert BlockReason.QUESTION.value == "question"

    def test_enum_has_exactly_three_members(self):
        assert len(BlockReason) == 3


# ---------------------------------------------------------------------------
# EchoEvent model
# ---------------------------------------------------------------------------


class TestEchoEvent:
    """Tests for the EchoEvent Pydantic model."""

    def test_create_with_required_fields(self):
        event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="session-abc",
            source="hook",
        )
        assert event.type == EventType.TOOL_EXECUTED
        assert event.session_id == "session-abc"
        assert event.source == "hook"

    def test_default_timestamp_is_auto_populated(self):
        before = time.time()
        event = EchoEvent(
            type=EventType.SESSION_START,
            session_id="s1",
            source="hook",
        )
        after = time.time()
        assert before <= event.timestamp <= after

    def test_explicit_timestamp_overrides_default(self):
        event = EchoEvent(
            type=EventType.SESSION_START,
            session_id="s1",
            source="hook",
            timestamp=1234567890.0,
        )
        assert event.timestamp == 1234567890.0

    def test_source_accepts_hook(self):
        event = EchoEvent(
            type=EventType.SESSION_START,
            session_id="s1",
            source="hook",
        )
        assert event.source == "hook"

    def test_source_accepts_transcript(self):
        event = EchoEvent(
            type=EventType.AGENT_MESSAGE,
            session_id="s1",
            source="transcript",
        )
        assert event.source == "transcript"

    def test_optional_tool_fields_default_to_none(self):
        event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="s1",
            source="hook",
        )
        assert event.tool_name is None
        assert event.tool_input is None
        assert event.tool_output is None

    def test_optional_block_fields_default_to_none(self):
        event = EchoEvent(
            type=EventType.AGENT_BLOCKED,
            session_id="s1",
            source="hook",
        )
        assert event.block_reason is None
        assert event.message is None
        assert event.options is None

    def test_optional_text_field_default_to_none(self):
        event = EchoEvent(
            type=EventType.AGENT_MESSAGE,
            session_id="s1",
            source="transcript",
        )
        assert event.text is None

    def test_optional_stop_reason_default_to_none(self):
        event = EchoEvent(
            type=EventType.AGENT_STOPPED,
            session_id="s1",
            source="hook",
        )
        assert event.stop_reason is None

    def test_tool_executed_event_with_all_fields(self):
        event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="s1",
            source="hook",
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            tool_output={"stdout": "file1.py\nfile2.py"},
        )
        assert event.tool_name == "Bash"
        assert event.tool_input == {"command": "ls -la"}
        assert event.tool_output == {"stdout": "file1.py\nfile2.py"}

    def test_agent_blocked_event_with_all_fields(self):
        event = EchoEvent(
            type=EventType.AGENT_BLOCKED,
            session_id="s1",
            source="hook",
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow Bash command?",
            options=["yes", "no"],
        )
        assert event.block_reason == BlockReason.PERMISSION_PROMPT
        assert event.message == "Allow Bash command?"
        assert event.options == ["yes", "no"]

    def test_serialization_to_dict(self):
        event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="s1",
            source="hook",
            timestamp=1000.0,
            tool_name="Read",
        )
        d = event.model_dump()
        assert isinstance(d, dict)
        assert d["type"] == EventType.TOOL_EXECUTED
        assert d["session_id"] == "s1"
        assert d["source"] == "hook"
        assert d["timestamp"] == 1000.0
        assert d["tool_name"] == "Read"

    def test_serialization_to_json(self):
        event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="s1",
            source="hook",
            timestamp=1000.0,
            tool_name="Read",
        )
        json_str = event.model_dump_json()
        assert isinstance(json_str, str)
        assert '"tool_executed"' in json_str
        assert '"s1"' in json_str
        assert '"Read"' in json_str

    def test_model_roundtrip(self):
        """Serialize to dict and reconstruct — should produce equal object."""
        original = EchoEvent(
            type=EventType.AGENT_BLOCKED,
            session_id="sess-42",
            source="hook",
            timestamp=999.0,
            block_reason=BlockReason.IDLE_PROMPT,
            message="Waiting for input",
        )
        rebuilt = EchoEvent(**original.model_dump())
        assert rebuilt == original

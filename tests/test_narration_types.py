"""Tests for voice_copilot.summarizer.types — narration event models."""

import json
import time

import pytest
from pydantic import ValidationError

from voice_copilot.events.types import EventType
from voice_copilot.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)


# ---------------------------------------------------------------------------
# NarrationPriority enum
# ---------------------------------------------------------------------------


class TestNarrationPriority:
    """Verify that all expected NarrationPriority enum values exist."""

    def test_critical_value(self):
        assert NarrationPriority.CRITICAL == "critical"
        assert NarrationPriority.CRITICAL.value == "critical"

    def test_normal_value(self):
        assert NarrationPriority.NORMAL == "normal"
        assert NarrationPriority.NORMAL.value == "normal"

    def test_low_value(self):
        assert NarrationPriority.LOW == "low"
        assert NarrationPriority.LOW.value == "low"

    def test_enum_has_exactly_three_members(self):
        assert len(NarrationPriority) == 3


# ---------------------------------------------------------------------------
# SummarizationMethod enum
# ---------------------------------------------------------------------------


class TestSummarizationMethod:
    """Verify that all expected SummarizationMethod enum values exist."""

    def test_template_value(self):
        assert SummarizationMethod.TEMPLATE == "template"
        assert SummarizationMethod.TEMPLATE.value == "template"

    def test_llm_value(self):
        assert SummarizationMethod.LLM == "llm"
        assert SummarizationMethod.LLM.value == "llm"

    def test_truncation_value(self):
        assert SummarizationMethod.TRUNCATION == "truncation"
        assert SummarizationMethod.TRUNCATION.value == "truncation"

    def test_enum_has_exactly_three_members(self):
        assert len(SummarizationMethod) == 3


# ---------------------------------------------------------------------------
# NarrationEvent model
# ---------------------------------------------------------------------------


class TestNarrationEvent:
    """Tests for the NarrationEvent Pydantic model."""

    def test_create_with_all_fields(self):
        event = NarrationEvent(
            text="Reading file config.py",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="session-abc",
            timestamp=1000.0,
            source_event_id="evt-123",
        )
        assert event.text == "Reading file config.py"
        assert event.priority == NarrationPriority.NORMAL
        assert event.source_event_type == EventType.TOOL_EXECUTED
        assert event.summarization_method == SummarizationMethod.TEMPLATE
        assert event.session_id == "session-abc"
        assert event.timestamp == 1000.0
        assert event.source_event_id == "evt-123"

    def test_create_with_minimal_fields(self):
        """source_event_id and timestamp should default when omitted."""
        event = NarrationEvent(
            text="Agent is waiting for permission",
            priority=NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_BLOCKED,
            summarization_method=SummarizationMethod.LLM,
            session_id="s1",
        )
        assert event.source_event_id is None
        assert isinstance(event.timestamp, float)

    def test_default_timestamp_is_auto_populated(self):
        before = time.time()
        event = NarrationEvent(
            text="Session started",
            priority=NarrationPriority.LOW,
            source_event_type=EventType.SESSION_START,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="s1",
        )
        after = time.time()
        assert before <= event.timestamp <= after

    def test_explicit_timestamp_overrides_default(self):
        event = NarrationEvent(
            text="Done",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.SESSION_END,
            summarization_method=SummarizationMethod.TRUNCATION,
            session_id="s1",
            timestamp=1234567890.0,
        )
        assert event.timestamp == 1234567890.0

    def test_serialization_to_dict(self):
        event = NarrationEvent(
            text="Ran bash command",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="s1",
            timestamp=1000.0,
            source_event_id="evt-1",
        )
        d = event.model_dump()
        assert isinstance(d, dict)
        assert d["text"] == "Ran bash command"
        assert d["priority"] == NarrationPriority.NORMAL
        assert d["source_event_type"] == EventType.TOOL_EXECUTED
        assert d["summarization_method"] == SummarizationMethod.TEMPLATE
        assert d["session_id"] == "s1"
        assert d["timestamp"] == 1000.0
        assert d["source_event_id"] == "evt-1"

    def test_serialization_to_json(self):
        event = NarrationEvent(
            text="Agent stopped",
            priority=NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_STOPPED,
            summarization_method=SummarizationMethod.LLM,
            session_id="s1",
            timestamp=2000.0,
        )
        json_str = event.model_dump_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["text"] == "Agent stopped"
        assert parsed["priority"] == "critical"
        assert parsed["source_event_type"] == "agent_stopped"
        assert parsed["summarization_method"] == "llm"

    def test_model_roundtrip(self):
        """Serialize to dict and reconstruct — should produce equal object."""
        original = NarrationEvent(
            text="Editing main.py",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="sess-42",
            timestamp=999.0,
            source_event_id="evt-99",
        )
        rebuilt = NarrationEvent(**original.model_dump())
        assert rebuilt == original

    def test_priority_critical(self):
        event = NarrationEvent(
            text="Permission needed",
            priority=NarrationPriority.CRITICAL,
            source_event_type=EventType.AGENT_BLOCKED,
            summarization_method=SummarizationMethod.TEMPLATE,
            session_id="s1",
        )
        assert event.priority == NarrationPriority.CRITICAL

    def test_priority_low(self):
        event = NarrationEvent(
            text="Viewed profile",
            priority=NarrationPriority.LOW,
            source_event_type=EventType.TOOL_EXECUTED,
            summarization_method=SummarizationMethod.TRUNCATION,
            session_id="s1",
        )
        assert event.priority == NarrationPriority.LOW

    def test_summarization_method_truncation(self):
        event = NarrationEvent(
            text="Long output was truncated...",
            priority=NarrationPriority.NORMAL,
            source_event_type=EventType.AGENT_MESSAGE,
            summarization_method=SummarizationMethod.TRUNCATION,
            session_id="s1",
        )
        assert event.summarization_method == SummarizationMethod.TRUNCATION

    def test_validation_error_missing_text(self):
        with pytest.raises(ValidationError):
            NarrationEvent(
                priority=NarrationPriority.NORMAL,
                source_event_type=EventType.TOOL_EXECUTED,
                summarization_method=SummarizationMethod.TEMPLATE,
                session_id="s1",
            )

    def test_validation_error_missing_priority(self):
        with pytest.raises(ValidationError):
            NarrationEvent(
                text="Hello",
                source_event_type=EventType.TOOL_EXECUTED,
                summarization_method=SummarizationMethod.TEMPLATE,
                session_id="s1",
            )

    def test_validation_error_missing_session_id(self):
        with pytest.raises(ValidationError):
            NarrationEvent(
                text="Hello",
                priority=NarrationPriority.NORMAL,
                source_event_type=EventType.TOOL_EXECUTED,
                summarization_method=SummarizationMethod.TEMPLATE,
            )

    def test_validation_error_invalid_priority(self):
        with pytest.raises(ValidationError):
            NarrationEvent(
                text="Hello",
                priority="urgent",
                source_event_type=EventType.TOOL_EXECUTED,
                summarization_method=SummarizationMethod.TEMPLATE,
                session_id="s1",
            )

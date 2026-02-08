"""Tests for echo.summarizer.template_engine — TemplateEngine."""

import pytest

from echo.events.types import BlockReason, EventType, EchoEvent
from echo.summarizer.template_engine import TemplateEngine
from echo.summarizer.types import (
    NarrationPriority,
    SummarizationMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(**kwargs) -> EchoEvent:
    """Shorthand factory — fills in required fields with sensible defaults."""
    defaults = {
        "type": EventType.TOOL_EXECUTED,
        "session_id": "sess-1",
        "source": "hook",
    }
    defaults.update(kwargs)
    return EchoEvent(**defaults)


@pytest.fixture
def engine() -> TemplateEngine:
    return TemplateEngine()


# ---------------------------------------------------------------------------
# tool_executed templates — one test per tool_name
# ---------------------------------------------------------------------------


class TestToolExecutedTemplates:
    """Each known tool_name produces the expected narration text."""

    def test_bash_template(self, engine: TemplateEngine):
        event = _make_event(tool_name="Bash", tool_input={"command": "ls -la"})
        result = engine.render(event)
        assert result.text == "Ran command: ls -la"

    def test_bash_command_truncation_at_60_chars(self, engine: TemplateEngine):
        long_cmd = "a" * 80
        event = _make_event(tool_name="Bash", tool_input={"command": long_cmd})
        result = engine.render(event)
        assert result.text == f"Ran command: {'a' * 60}..."
        # Total text after "Ran command: " is exactly 63 chars (60 + "...")
        assert len(result.text) == len("Ran command: ") + 60 + 3

    def test_bash_command_exactly_60_chars_not_truncated(self, engine: TemplateEngine):
        cmd = "b" * 60
        event = _make_event(tool_name="Bash", tool_input={"command": cmd})
        result = engine.render(event)
        assert result.text == f"Ran command: {cmd}"
        assert "..." not in result.text

    def test_read_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Read",
            tool_input={"file_path": "/Users/foo/project/src/auth.ts"},
        )
        result = engine.render(event)
        assert result.text == "Read auth.ts"

    def test_edit_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Edit",
            tool_input={"file_path": "/home/user/app/main.py"},
        )
        result = engine.render(event)
        assert result.text == "Edited main.py"

    def test_write_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Write",
            tool_input={"file_path": "/tmp/new_file.json"},
        )
        result = engine.render(event)
        assert result.text == "Created new_file.json"

    def test_glob_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Glob",
            tool_input={"pattern": "**/*.py"},
        )
        result = engine.render(event)
        assert result.text == "Searched for files matching **/*.py"

    def test_grep_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Grep",
            tool_input={"pattern": "TODO"},
        )
        result = engine.render(event)
        assert result.text == "Searched code for TODO"

    def test_task_template(self, engine: TemplateEngine):
        event = _make_event(tool_name="Task", tool_input={"prompt": "do stuff"})
        result = engine.render(event)
        assert result.text == "Launched a sub-agent"

    def test_webfetch_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="WebFetch",
            tool_input={"url": "https://example.com"},
        )
        result = engine.render(event)
        assert result.text == "Fetched a web page"

    def test_websearch_template(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="WebSearch",
            tool_input={"query": "python asyncio tutorial"},
        )
        result = engine.render(event)
        assert result.text == "Searched the web for python asyncio tutorial"

    def test_unknown_tool_template(self, engine: TemplateEngine):
        event = _make_event(tool_name="NotebookEdit", tool_input={})
        result = engine.render(event)
        assert result.text == "Used NotebookEdit tool"


# ---------------------------------------------------------------------------
# File path basename extraction
# ---------------------------------------------------------------------------


class TestBasenamExtraction:
    """File paths should be reduced to just the filename for TTS."""

    def test_long_absolute_path_becomes_basename(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Read",
            tool_input={"file_path": "/very/deep/nested/dir/config.yaml"},
        )
        result = engine.render(event)
        assert result.text == "Read config.yaml"

    def test_default_when_file_path_missing(self, engine: TemplateEngine):
        event = _make_event(tool_name="Edit", tool_input={})
        result = engine.render(event)
        assert result.text == "Edited a file"


# ---------------------------------------------------------------------------
# Defensive: tool_input is None
# ---------------------------------------------------------------------------


class TestToolInputNone:
    """When tool_input is None, defaults should kick in without errors."""

    def test_bash_with_none_tool_input(self, engine: TemplateEngine):
        event = _make_event(tool_name="Bash", tool_input=None)
        result = engine.render(event)
        # Empty command becomes "" -> "Ran command: " -> stripped to "Ran command:"
        assert result.text == "Ran command:"

    def test_read_with_none_tool_input(self, engine: TemplateEngine):
        event = _make_event(tool_name="Read", tool_input=None)
        result = engine.render(event)
        assert result.text == "Read a file"

    def test_none_tool_name(self, engine: TemplateEngine):
        event = _make_event(tool_name=None, tool_input=None)
        result = engine.render(event)
        assert result.text == "Used Unknown tool"


# ---------------------------------------------------------------------------
# agent_blocked templates
# ---------------------------------------------------------------------------


class TestAgentBlockedTemplates:
    """Each block_reason produces the expected narration text."""

    def test_permission_prompt_with_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow running rm -rf?",
        )
        result = engine.render(event)
        assert result.text == (
            "The agent needs your permission and is waiting for your answer."
            " It's asking: Allow running rm -rf?"
        )

    def test_permission_prompt_without_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message=None,
        )
        result = engine.render(event)
        assert result.text == "The agent needs your permission and is waiting for your answer."

    def test_idle_prompt(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
        )
        result = engine.render(event)
        assert result.text == "The agent is idle and waiting for your input."

    def test_question_with_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Which database do you want?",
        )
        result = engine.render(event)
        assert result.text == (
            "The agent has a question and is waiting for your answer."
            " It's asking: Which database do you want?"
        )

    def test_question_without_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message=None,
        )
        result = engine.render(event)
        assert result.text == "The agent has a question and is waiting for your answer."

    def test_unknown_block_reason(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=None,
        )
        result = engine.render(event)
        assert result.text == "The agent is blocked and needs your attention."


# ---------------------------------------------------------------------------
# agent_blocked with options
# ---------------------------------------------------------------------------


class TestAgentBlockedOptions:
    """Options list should be formatted as numbered list for TTS readability."""

    def test_two_options_numbered(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow?",
            options=["yes", "no"],
        )
        result = engine.render(event)
        assert result.text == (
            "The agent needs your permission and is waiting for your answer."
            " It's asking: Allow?"
            " Option one: yes. Option two: no."
        )

    def test_three_options_numbered(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Pick a color.",
            options=["red", "green", "blue"],
        )
        result = engine.render(event)
        assert result.text == (
            "The agent has a question and is waiting for your answer."
            " It's asking: Pick a color."
            " Option one: red. Option two: green. Option three: blue."
        )

    def test_four_options_numbered(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Choose.",
            options=["a", "b", "c", "d"],
        )
        result = engine.render(event)
        assert result.text == (
            "The agent has a question and is waiting for your answer."
            " It's asking: Choose."
            " Option one: a. Option two: b. Option three: c. Option four: d."
        )

    def test_single_option_numbered(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
            options=["continue"],
        )
        result = engine.render(event)
        assert result.text == (
            "The agent is idle and waiting for your input."
            " Option one: continue."
        )

    def test_empty_options_list_not_appended(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
            options=[],
        )
        result = engine.render(event)
        assert result.text == "The agent is idle and waiting for your input."


# ---------------------------------------------------------------------------
# agent_stopped
# ---------------------------------------------------------------------------


class TestAgentStopped:
    """agent_stopped with and without stop_reason."""

    def test_with_stop_reason(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_STOPPED,
            stop_reason="user cancelled",
        )
        result = engine.render(event)
        assert result.text == "Agent stopped: user cancelled."

    def test_without_stop_reason(self, engine: TemplateEngine):
        event = _make_event(type=EventType.AGENT_STOPPED)
        result = engine.render(event)
        assert result.text == "Agent finished."


# ---------------------------------------------------------------------------
# session_start and session_end
# ---------------------------------------------------------------------------


class TestSessionEvents:
    """session_start and session_end produce fixed narration text."""

    def test_session_start(self, engine: TemplateEngine):
        event = _make_event(type=EventType.SESSION_START)
        result = engine.render(event)
        assert result.text == "New coding session started."

    def test_session_end(self, engine: TemplateEngine):
        event = _make_event(type=EventType.SESSION_END)
        result = engine.render(event)
        assert result.text == "Session ended."


# ---------------------------------------------------------------------------
# render_batch
# ---------------------------------------------------------------------------


class TestRenderBatch:
    """Batched tool_executed events are combined into a single narration."""

    def test_batch_same_tool_edit(self, engine: TemplateEngine):
        events = [
            _make_event(tool_name="Edit", tool_input={"file_path": f"/src/f{i}.py"})
            for i in range(3)
        ]
        result = engine.render_batch(events)
        assert result.text == "Edited 3 files."

    def test_batch_same_tool_read(self, engine: TemplateEngine):
        events = [
            _make_event(tool_name="Read", tool_input={"file_path": f"/src/f{i}.py"})
            for i in range(5)
        ]
        result = engine.render_batch(events)
        assert result.text == "Read 5 files."

    def test_batch_same_tool_single_event(self, engine: TemplateEngine):
        events = [_make_event(tool_name="Write", tool_input={"file_path": "/a.py"})]
        result = engine.render_batch(events)
        assert result.text == "Created a file."

    def test_batch_mixed_tools(self, engine: TemplateEngine):
        events = [
            _make_event(tool_name="Edit", tool_input={"file_path": "/a.py"}),
            _make_event(tool_name="Edit", tool_input={"file_path": "/b.py"}),
            _make_event(tool_name="Bash", tool_input={"command": "npm test"}),
        ]
        result = engine.render_batch(events)
        assert result.text == "Edited 2 files and Ran a command."

    def test_batch_uses_first_event_session_id(self, engine: TemplateEngine):
        events = [
            _make_event(tool_name="Read", session_id="first-session"),
            _make_event(tool_name="Read", session_id="second-session"),
        ]
        result = engine.render_batch(events)
        assert result.session_id == "first-session"

    def test_batch_priority_is_normal(self, engine: TemplateEngine):
        events = [_make_event(tool_name="Grep", tool_input={"pattern": "x"})]
        result = engine.render_batch(events)
        assert result.priority == NarrationPriority.NORMAL

    def test_batch_source_event_type_is_tool_executed(self, engine: TemplateEngine):
        events = [_make_event(tool_name="Glob")]
        result = engine.render_batch(events)
        assert result.source_event_type == EventType.TOOL_EXECUTED


# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------


class TestPriorityMapping:
    """Each event type gets the correct narration priority."""

    def test_agent_blocked_is_critical(self, engine: TemplateEngine):
        event = _make_event(type=EventType.AGENT_BLOCKED, block_reason=None)
        result = engine.render(event)
        assert result.priority == NarrationPriority.CRITICAL

    def test_tool_executed_is_normal(self, engine: TemplateEngine):
        event = _make_event(tool_name="Read", tool_input={"file_path": "/a.py"})
        result = engine.render(event)
        assert result.priority == NarrationPriority.NORMAL

    def test_agent_stopped_is_normal(self, engine: TemplateEngine):
        event = _make_event(type=EventType.AGENT_STOPPED)
        result = engine.render(event)
        assert result.priority == NarrationPriority.NORMAL

    def test_session_start_is_low(self, engine: TemplateEngine):
        event = _make_event(type=EventType.SESSION_START)
        result = engine.render(event)
        assert result.priority == NarrationPriority.LOW

    def test_session_end_is_low(self, engine: TemplateEngine):
        event = _make_event(type=EventType.SESSION_END)
        result = engine.render(event)
        assert result.priority == NarrationPriority.LOW


# ---------------------------------------------------------------------------
# SummarizationMethod is always TEMPLATE
# ---------------------------------------------------------------------------


class TestSummarizationMethod:
    """Every narration from TemplateEngine should use SummarizationMethod.TEMPLATE."""

    def test_render_uses_template_method(self, engine: TemplateEngine):
        event = _make_event(tool_name="Bash", tool_input={"command": "echo hi"})
        result = engine.render(event)
        assert result.summarization_method == SummarizationMethod.TEMPLATE

    def test_render_batch_uses_template_method(self, engine: TemplateEngine):
        events = [_make_event(tool_name="Read", tool_input={"file_path": "/x.py"})]
        result = engine.render_batch(events)
        assert result.summarization_method == SummarizationMethod.TEMPLATE


# ---------------------------------------------------------------------------
# Enhanced blocked templates (PRD-style narration)
# ---------------------------------------------------------------------------


class TestEnhancedBlockedTemplates:
    """Enhanced blocked-event templates with richer TTS-friendly narration."""

    def test_permission_prompt_enhanced_text(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
        )
        result = engine.render(event)
        assert result.text.startswith(
            "The agent needs your permission and is waiting for your answer."
        )

    def test_permission_prompt_with_message_appends_asking(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow file write?",
        )
        result = engine.render(event)
        assert "It's asking: Allow file write?" in result.text

    def test_question_enhanced_text(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
        )
        result = engine.render(event)
        assert result.text.startswith(
            "The agent has a question and is waiting for your answer."
        )

    def test_question_with_message_appends_asking(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Which branch?",
        )
        result = engine.render(event)
        assert "It's asking: Which branch?" in result.text

    def test_idle_enhanced_text(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
        )
        result = engine.render(event)
        assert result.text == "The agent is idle and waiting for your input."

    def test_blocked_no_reason_enhanced(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=None,
        )
        result = engine.render(event)
        assert result.text == "The agent is blocked and needs your attention."

    def test_blocked_no_reason_with_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=None,
            message="Something unexpected happened.",
        )
        result = engine.render(event)
        assert result.text == (
            "The agent is blocked and needs your attention."
            " Something unexpected happened."
        )


# ---------------------------------------------------------------------------
# _format_options_numbered
# ---------------------------------------------------------------------------


class TestFormatOptionsNumbered:
    """Tests for the _format_options_numbered static method."""

    def test_format_options_numbered_two(self, engine: TemplateEngine):
        result = TemplateEngine._format_options_numbered(["RS256", "HS256"])
        assert result == "Option one: RS256. Option two: HS256."

    def test_format_options_numbered_three(self, engine: TemplateEngine):
        result = TemplateEngine._format_options_numbered(["a", "b", "c"])
        assert result == "Option one: a. Option two: b. Option three: c."

    def test_format_options_numbered_single(self, engine: TemplateEngine):
        result = TemplateEngine._format_options_numbered(["yes"])
        assert result == "Option one: yes."

    def test_format_options_numbered_over_ten(self, engine: TemplateEngine):
        options = [f"opt{i}" for i in range(1, 12)]  # 11 options
        result = TemplateEngine._format_options_numbered(options)
        # First 10 use ordinals, 11th uses numeric
        assert "Option ten: opt10." in result
        assert "Option 11: opt11." in result


# ---------------------------------------------------------------------------
# block_reason passthrough to NarrationEvent
# ---------------------------------------------------------------------------


class TestBlockReasonPassthrough:
    """Tests verifying block_reason flows from EchoEvent to NarrationEvent."""

    def test_block_reason_passed_to_narration_event(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow?",
        )
        result = engine.render(event)
        assert result.block_reason == BlockReason.PERMISSION_PROMPT

    def test_block_reason_none_for_tool_executed(self, engine: TemplateEngine):
        event = _make_event(
            tool_name="Read",
            tool_input={"file_path": "/a.py"},
        )
        result = engine.render(event)
        assert result.block_reason is None

    def test_block_reason_none_for_session_start(self, engine: TemplateEngine):
        event = _make_event(type=EventType.SESSION_START)
        result = engine.render(event)
        assert result.block_reason is None

    def test_block_reason_question_passed_through(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
        )
        result = engine.render(event)
        assert result.block_reason == BlockReason.QUESTION


# ---------------------------------------------------------------------------
# options passthrough to NarrationEvent
# ---------------------------------------------------------------------------


class TestOptionsPassthrough:
    """Tests verifying options flow from EchoEvent to NarrationEvent."""

    def test_options_passed_through_render(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow?",
            options=["RS256", "HS256"],
        )
        result = engine.render(event)
        assert result.options == ["RS256", "HS256"]

    def test_options_none_when_not_set(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.TOOL_EXECUTED,
            tool_name="Read",
            tool_input={"file_path": "/a.py"},
        )
        result = engine.render(event)
        assert result.options is None

    def test_options_empty_list_passed_through(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            options=[],
        )
        result = engine.render(event)
        assert result.options == []

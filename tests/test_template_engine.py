"""Tests for voice_copilot.summarizer.template_engine — TemplateEngine."""

import pytest

from voice_copilot.events.types import BlockReason, EventType, VoiceCopilotEvent
from voice_copilot.summarizer.template_engine import TemplateEngine
from voice_copilot.summarizer.types import (
    NarrationPriority,
    SummarizationMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(**kwargs) -> VoiceCopilotEvent:
    """Shorthand factory — fills in required fields with sensible defaults."""
    defaults = {
        "type": EventType.TOOL_EXECUTED,
        "session_id": "sess-1",
        "source": "hook",
    }
    defaults.update(kwargs)
    return VoiceCopilotEvent(**defaults)


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
        assert result.text == "The agent needs permission. Allow running rm -rf?"

    def test_permission_prompt_without_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message=None,
        )
        result = engine.render(event)
        assert result.text == "The agent needs permission."

    def test_idle_prompt(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
        )
        result = engine.render(event)
        assert result.text == "The agent is waiting for your input."

    def test_question_with_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Which database do you want?",
        )
        result = engine.render(event)
        assert result.text == "The agent has a question. Which database do you want?"

    def test_question_without_message(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message=None,
        )
        result = engine.render(event)
        assert result.text == "The agent has a question."

    def test_unknown_block_reason(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=None,
        )
        result = engine.render(event)
        assert result.text == "The agent is blocked and needs attention."


# ---------------------------------------------------------------------------
# agent_blocked with options
# ---------------------------------------------------------------------------


class TestAgentBlockedOptions:
    """Options list should be formatted naturally in narration."""

    def test_two_options_uses_and(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.PERMISSION_PROMPT,
            message="Allow?",
            options=["yes", "no"],
        )
        result = engine.render(event)
        assert result.text == "The agent needs permission. Allow? Options are: yes and no."

    def test_three_options_uses_oxford_comma_and_or(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Pick a color.",
            options=["red", "green", "blue"],
        )
        result = engine.render(event)
        assert result.text == "The agent has a question. Pick a color. Options are: red, green, or blue."

    def test_four_options(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.QUESTION,
            message="Choose.",
            options=["a", "b", "c", "d"],
        )
        result = engine.render(event)
        assert result.text == "The agent has a question. Choose. Options are: a, b, c, or d."

    def test_single_option(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
            options=["continue"],
        )
        result = engine.render(event)
        assert result.text == "The agent is waiting for your input. Options are: continue."

    def test_empty_options_list_not_appended(self, engine: TemplateEngine):
        event = _make_event(
            type=EventType.AGENT_BLOCKED,
            block_reason=BlockReason.IDLE_PROMPT,
            options=[],
        )
        result = engine.render(event)
        assert result.text == "The agent is waiting for your input."


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

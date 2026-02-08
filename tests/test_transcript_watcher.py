"""Tests for voice_copilot.interceptors.transcript_watcher.

Tests the helper functions (_extract_assistant_text, _extract_session_id)
and the file-processing logic of _TranscriptFileHandler.  Uses tmp_path
for temporary JSONL files and mocks the event bus.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from voice_copilot.events.event_bus import EventBus
from voice_copilot.events.types import EventType
from voice_copilot.interceptors.transcript_watcher import (
    TranscriptWatcher,
    _extract_assistant_text,
    _extract_session_id,
    _TranscriptFileHandler,
)


# ---------------------------------------------------------------------------
# _extract_assistant_text
# ---------------------------------------------------------------------------


class TestExtractAssistantText:
    """Tests for the _extract_assistant_text helper."""

    def test_valid_assistant_message_single_text_block(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Hello, world!"},
                ],
            },
        }
        result = _extract_assistant_text(entry)
        assert result == "Hello, world!"

    def test_valid_assistant_message_multiple_text_blocks(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "First paragraph."},
                    {"type": "text", "text": "Second paragraph."},
                ],
            },
        }
        result = _extract_assistant_text(entry)
        assert result == "First paragraph.\n\nSecond paragraph."

    def test_mixed_content_ignores_tool_use_blocks(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check that."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "text", "text": "Here are the results."},
                ],
            },
        }
        result = _extract_assistant_text(entry)
        assert result == "Let me check that.\n\nHere are the results."

    def test_non_assistant_type_returns_none(self):
        entry = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hi there."}],
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_system_type_returns_none(self):
        entry = {
            "type": "system",
            "message": {"role": "system", "content": "You are helpful."},
        }
        assert _extract_assistant_text(entry) is None

    def test_missing_type_field_returns_none(self):
        entry = {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_missing_message_field_returns_none(self):
        entry = {"type": "assistant"}
        assert _extract_assistant_text(entry) is None

    def test_message_role_not_assistant_returns_none(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_content_not_a_list_returns_none(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "Just a string",
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_empty_content_list_returns_none(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [],
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_only_tool_use_blocks_returns_none(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {}},
                ],
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_whitespace_only_text_returns_none(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "   \n  "},
                ],
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_text_is_stripped(self):
        entry = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "  trimmed  "},
                ],
            },
        }
        result = _extract_assistant_text(entry)
        assert result == "trimmed"


# ---------------------------------------------------------------------------
# _extract_session_id
# ---------------------------------------------------------------------------


class TestExtractSessionId:
    """Tests for the _extract_session_id helper."""

    def test_session_id_from_entry(self):
        entry = {"sessionId": "abc-123", "type": "assistant"}
        result = _extract_session_id(entry, Path("/fake/some-uuid.jsonl"))
        assert result == "abc-123"

    def test_fallback_to_file_stem_when_no_session_id(self):
        entry = {"type": "assistant"}
        result = _extract_session_id(entry, Path("/fake/dir/my-session-uuid.jsonl"))
        assert result == "my-session-uuid"

    def test_fallback_when_session_id_is_not_string(self):
        entry = {"sessionId": 12345, "type": "assistant"}
        result = _extract_session_id(entry, Path("/fake/fallback-stem.jsonl"))
        assert result == "fallback-stem"

    def test_fallback_when_session_id_is_empty_string(self):
        entry = {"sessionId": "", "type": "assistant"}
        result = _extract_session_id(entry, Path("/fake/the-stem.jsonl"))
        assert result == "the-stem"


# ---------------------------------------------------------------------------
# _TranscriptFileHandler — incremental reading
# ---------------------------------------------------------------------------


class TestTranscriptFileHandlerProcessing:
    """Tests for _TranscriptFileHandler._process_file and _handle_line."""

    def _make_handler(self) -> _TranscriptFileHandler:
        """Create a handler with a mock event bus and a mock event loop."""
        mock_bus = MagicMock(spec=EventBus)
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        return _TranscriptFileHandler(event_bus=mock_bus, loop=mock_loop)

    def _write_jsonl(self, path: Path, entries: list[dict]) -> None:
        """Write a list of dicts as JSONL to the given path."""
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def _append_jsonl(self, path: Path, entries: list[dict]) -> None:
        """Append entries to an existing JSONL file."""
        with open(path, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_process_file_reads_assistant_message(self, tmp_path):
        handler = self._make_handler()
        jsonl_file = tmp_path / "session.jsonl"

        self._write_jsonl(
            jsonl_file,
            [
                {
                    "type": "assistant",
                    "sessionId": "s1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Hello from Claude."}],
                    },
                }
            ],
        )

        handler._process_file(jsonl_file)

        # The handler should have called loop.call_soon_threadsafe
        handler._loop.call_soon_threadsafe.assert_called()

    def test_incremental_reading_only_processes_new_lines(self, tmp_path):
        handler = self._make_handler()
        jsonl_file = tmp_path / "session.jsonl"

        first_entry = {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": 1000000.0,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "First message."}],
            },
        }
        self._write_jsonl(jsonl_file, [first_entry])
        handler._process_file(jsonl_file)

        call_count_after_first = handler._loop.call_soon_threadsafe.call_count

        # Append a second entry with a distinctly different timestamp to
        # avoid deduplication (the dedup key uses 100ms granularity).
        second_entry = {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": 2000000.0,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Second message."}],
            },
        }
        self._append_jsonl(jsonl_file, [second_entry])
        handler._process_file(jsonl_file)

        call_count_after_second = handler._loop.call_soon_threadsafe.call_count
        # Should have exactly one more call (for the second entry only)
        assert call_count_after_second == call_count_after_first + 1

    def test_malformed_jsonl_line_is_skipped(self, tmp_path):
        handler = self._make_handler()
        jsonl_file = tmp_path / "session.jsonl"

        # Write a bad line followed by a good one
        with open(jsonl_file, "w", encoding="utf-8") as f:
            f.write("this is not valid json\n")
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "s1",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "Valid message after bad line."}
                            ],
                        },
                    }
                )
                + "\n"
            )

        handler._process_file(jsonl_file)

        # Should have processed the valid line (called call_soon_threadsafe)
        handler._loop.call_soon_threadsafe.assert_called()

    def test_non_assistant_lines_are_skipped(self, tmp_path):
        handler = self._make_handler()
        jsonl_file = tmp_path / "session.jsonl"

        self._write_jsonl(
            jsonl_file,
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "User message."}],
                    },
                },
            ],
        )

        handler._process_file(jsonl_file)

        # No events should be emitted for user messages
        handler._loop.call_soon_threadsafe.assert_not_called()

    def test_empty_file_does_nothing(self, tmp_path):
        handler = self._make_handler()
        jsonl_file = tmp_path / "empty.jsonl"
        jsonl_file.write_text("")

        handler._process_file(jsonl_file)

        handler._loop.call_soon_threadsafe.assert_not_called()

    def test_on_deleted_clears_offset(self, tmp_path):
        handler = self._make_handler()
        jsonl_file = tmp_path / "session.jsonl"
        self._write_jsonl(
            jsonl_file,
            [
                {
                    "type": "assistant",
                    "sessionId": "s1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "data"}],
                    },
                }
            ],
        )
        handler._process_file(jsonl_file)
        assert str(jsonl_file) in handler._offsets

        # Simulate on_deleted
        mock_event = MagicMock()
        mock_event.is_directory = False
        mock_event.src_path = str(jsonl_file)
        handler.on_deleted(mock_event)
        assert str(jsonl_file) not in handler._offsets


# ---------------------------------------------------------------------------
# TranscriptWatcher.start — missing directory
# ---------------------------------------------------------------------------


class TestTranscriptWatcherStart:
    """Tests for TranscriptWatcher.start() with missing directory."""

    async def test_missing_directory_does_not_crash(self, monkeypatch, tmp_path):
        """start() should log a warning and return when the directory is missing."""
        import voice_copilot.interceptors.transcript_watcher as tw_mod

        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr(tw_mod, "CLAUDE_PROJECTS_PATH", nonexistent)

        bus = EventBus()
        watcher = TranscriptWatcher(event_bus=bus)

        # Should not raise
        await watcher.start()

        # Observer should not have been created
        assert watcher._observer is None

    async def test_path_is_file_not_directory_does_not_crash(
        self, monkeypatch, tmp_path
    ):
        """start() should log a warning if the path is a file, not a directory."""
        import voice_copilot.interceptors.transcript_watcher as tw_mod

        some_file = tmp_path / "not_a_dir"
        some_file.write_text("I am a file")
        monkeypatch.setattr(tw_mod, "CLAUDE_PROJECTS_PATH", some_file)

        bus = EventBus()
        watcher = TranscriptWatcher(event_bus=bus)

        await watcher.start()
        assert watcher._observer is None

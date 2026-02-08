"""Watch Claude Code JSONL transcript files for new assistant messages.

Claude Code stores conversation transcripts as JSONL files under
``~/.claude/projects/``.  Each line is a JSON object representing one
message in the conversation.  This module uses the *watchdog* library to
monitor those files for new content and emits ``AGENT_MESSAGE`` events to
the event bus whenever a new assistant text message is detected.

This provides a **complementary** data source to the hook system -- hooks
give structured tool events, while the transcript watcher gives us the
assistant's natural-language text messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from voice_copilot.config import CLAUDE_PROJECTS_PATH
from voice_copilot.events.event_bus import EventBus
from voice_copilot.events.types import EventType, VoiceCopilotEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEDUP_TTL_SECONDS: float = 1.0
"""How long a deduplication hash stays valid."""

_DEDUP_CLEANUP_INTERVAL: int = 50
"""Run dedup-cache cleanup every N events processed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_assistant_text(entry: dict[str, Any]) -> str | None:
    """Return the concatenated text content from an assistant JSONL entry.

    Claude Code transcript lines have this shape::

        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "..."},
                    {"type": "tool_use", ...}
                ]
            },
            "sessionId": "...",
            ...
        }

    We only care about entries where ``type == "assistant"`` and the
    ``message.content`` list contains at least one ``{"type": "text"}``
    block.  Tool-use entries are ignored here (they arrive via hooks).

    Returns the joined text blocks, or ``None`` if there is nothing to
    emit.
    """
    if entry.get("type") != "assistant":
        return None

    message = entry.get("message")
    if not isinstance(message, dict):
        return None

    if message.get("role") != "assistant":
        return None

    content = message.get("content")
    if not isinstance(content, list):
        return None

    text_parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text and text.strip():
                text_parts.append(text.strip())

    if not text_parts:
        return None

    return "\n\n".join(text_parts)


def _extract_session_id(entry: dict[str, Any], file_path: Path) -> str:
    """Derive a session ID from the JSONL entry or from the file path.

    The JSONL entry typically contains a top-level ``sessionId`` field.
    If missing, we fall back to the file stem (the JSONL filename without
    extension), which in Claude Code is the session UUID.
    """
    session_id = entry.get("sessionId")
    if session_id and isinstance(session_id, str):
        return session_id
    return file_path.stem


def _dedup_key(session_id: str, timestamp: float) -> str:
    """Create a deduplication key by hashing session + coarse timestamp.

    The timestamp is rounded to the nearest 100 ms so that events
    arriving within the same 100 ms window from both the hook system and
    the transcript watcher are collapsed.
    """
    coarse_ts = round(timestamp * 10) / 10  # 100 ms granularity
    raw = f"{session_id}:{coarse_ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Watchdog handler (runs on a background thread)
# ---------------------------------------------------------------------------


class _TranscriptFileHandler(FileSystemEventHandler):
    """Handle ``on_modified`` / ``on_created`` / ``on_deleted`` for JSONL files.

    Because watchdog callbacks run on a background thread, all async work
    is scheduled on the provided *loop* via ``call_soon_threadsafe``.
    """

    def __init__(
        self,
        event_bus: EventBus,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self._event_bus = event_bus
        self._loop = loop

        # file_path -> byte offset of the last-read position
        self._offsets: dict[str, int] = {}

        # dedup: hash -> insertion timestamp
        self._seen: dict[str, float] = {}
        self._events_processed: int = 0

    # -- FileSystemEventHandler overrides ------------------------------------

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = str(event.src_path)
        if not src.endswith(".jsonl"):
            return
        self._process_file(Path(src))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = str(event.src_path)
        if not src.endswith(".jsonl"):
            return
        logger.info("New transcript file discovered: %s", src)
        self._process_file(Path(src))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src = str(event.src_path)
        if not src.endswith(".jsonl"):
            return
        self._offsets.pop(src, None)
        logger.debug("Removed offset tracking for deleted file: %s", src)

    # -- Internal processing ------------------------------------------------

    def _process_file(self, file_path: Path) -> None:
        """Read new lines from *file_path* and schedule event emission."""
        path_str = str(file_path)

        try:
            file_size = file_path.stat().st_size
        except OSError as exc:
            logger.warning("Cannot stat transcript file %s: %s", path_str, exc)
            return

        last_offset = self._offsets.get(path_str, 0)

        # Handle file truncation (e.g. the file was recreated).
        if file_size < last_offset:
            logger.debug(
                "File %s appears truncated (size=%d < offset=%d) — resetting",
                path_str,
                file_size,
                last_offset,
            )
            last_offset = 0

        if file_size == last_offset:
            return  # nothing new

        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                fh.seek(last_offset)
                new_data = fh.read()
                new_offset = fh.tell()
        except PermissionError:
            logger.warning("Permission denied reading transcript file: %s", path_str)
            return
        except OSError as exc:
            logger.warning("Error reading transcript file %s: %s", path_str, exc)
            return

        self._offsets[path_str] = new_offset

        for raw_line in new_data.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            self._handle_line(raw_line, file_path)

    def _handle_line(self, raw_line: str, file_path: Path) -> None:
        """Parse a single JSONL line and potentially emit an event."""
        try:
            entry: dict[str, Any] = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Malformed JSONL line in %s: %s (line starts with: %s)",
                file_path,
                exc,
                raw_line[:80],
            )
            return

        if not isinstance(entry, dict):
            return

        text = _extract_assistant_text(entry)
        if text is None:
            return

        session_id = _extract_session_id(entry, file_path)

        # Use the entry's own timestamp if available, otherwise wall-clock.
        entry_ts_raw = entry.get("timestamp")
        if isinstance(entry_ts_raw, str):
            try:
                from datetime import datetime, timezone

                entry_ts = datetime.fromisoformat(
                    entry_ts_raw.replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                entry_ts = time.time()
        elif isinstance(entry_ts_raw, (int, float)):
            entry_ts = float(entry_ts_raw)
        else:
            entry_ts = time.time()

        # Deduplication check.
        key = _dedup_key(session_id, entry_ts)
        now = time.time()

        if key in self._seen:
            logger.debug(
                "Duplicate event suppressed (key=%s, session=%s)", key, session_id
            )
            return

        self._seen[key] = now
        self._events_processed += 1

        # Periodic cleanup of stale dedup entries.
        if self._events_processed % _DEDUP_CLEANUP_INTERVAL == 0:
            self._cleanup_dedup_cache(now)

        event = VoiceCopilotEvent(
            type=EventType.AGENT_MESSAGE,
            timestamp=entry_ts,
            session_id=session_id,
            source="transcript",
            text=text,
        )

        logger.debug(
            "Emitting AGENT_MESSAGE from transcript: session=%s text=%s",
            session_id,
            text[:120] if len(text) > 120 else text,
        )

        # Schedule the async emit on the event loop from this background thread.
        self._loop.call_soon_threadsafe(
            self._loop.create_task,
            self._event_bus.emit(event),
        )

    def _cleanup_dedup_cache(self, now: float) -> None:
        """Remove dedup entries older than ``_DEDUP_TTL_SECONDS``."""
        expired = [
            k for k, ts in self._seen.items() if now - ts > _DEDUP_TTL_SECONDS
        ]
        for k in expired:
            del self._seen[k]
        if expired:
            logger.debug("Cleaned %d stale dedup entries", len(expired))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TranscriptWatcher:
    """Watch Claude Code JSONL transcript files and emit events.

    Uses the *watchdog* library to recursively monitor
    ``~/.claude/projects/`` for ``.jsonl`` file changes.  When an
    assistant text message is detected in a new transcript line, a
    ``VoiceCopilotEvent`` of type ``AGENT_MESSAGE`` is emitted on the
    event bus.

    Usage::

        watcher = TranscriptWatcher(event_bus)
        await watcher.start()
        ...
        await watcher.stop()
    """

    def __init__(self, event_bus: EventBus) -> None:
        """Initialize the transcript watcher.

        Args:
            event_bus: The event bus to emit events to.
        """
        self._event_bus = event_bus
        self._observer: Observer | None = None
        self._handler: _TranscriptFileHandler | None = None

    async def start(self) -> None:
        """Start watching for transcript file changes.

        If ``~/.claude/projects/`` does not exist, a warning is logged
        and the watcher does **not** start (but does not raise).
        """
        watch_path = CLAUDE_PROJECTS_PATH

        if not watch_path.exists():
            logger.warning(
                "Claude projects directory does not exist: %s — "
                "transcript watcher will not start",
                watch_path,
            )
            return

        if not watch_path.is_dir():
            logger.warning(
                "Claude projects path is not a directory: %s — "
                "transcript watcher will not start",
                watch_path,
            )
            return

        loop = asyncio.get_running_loop()

        self._handler = _TranscriptFileHandler(
            event_bus=self._event_bus,
            loop=loop,
        )

        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(watch_path),
            recursive=True,
        )
        self._observer.daemon = True
        self._observer.start()

        logger.info(
            "Transcript watcher started — monitoring %s for .jsonl changes",
            watch_path,
        )

    async def stop(self) -> None:
        """Stop watching and clean up resources."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            logger.info("Transcript watcher stopped")
            self._observer = None
            self._handler = None

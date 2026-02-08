"""Integration tests for Stage 4 — Question Detection & Alert.

Tests the complete alert pipeline from hook event to TTS playback,
including alert tone selection, AlertManager state tracking, repeat
timers, and alert resolution.
"""

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from echo.events.event_bus import EventBus
from echo.events.types import EchoEvent, EventType, BlockReason
from echo.interceptors.hook_handler import parse_hook_event
from echo.summarizer.template_engine import TemplateEngine
from echo.summarizer.types import NarrationEvent, NarrationPriority, SummarizationMethod
from echo.tts.alert_manager import AlertManager
from echo.tts.alert_tones import generate_alert_for_reason


# ---------------------------------------------------------------------------
# Hook JSON -> EchoEvent flow
# ---------------------------------------------------------------------------


class TestHookToEventFlow:
    """Test that hook JSON with options produces correct EchoEvents."""

    def test_notification_with_options_produces_agent_blocked(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-1",
            "type": "question",
            "message": "Which database should I use?",
            "options": ["PostgreSQL", "MongoDB", "MySQL"],
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.AGENT_BLOCKED
        assert event.block_reason == BlockReason.QUESTION
        assert event.options == ["PostgreSQL", "MongoDB", "MySQL"]
        assert event.message == "Which database should I use?"

    def test_permission_notification_with_options(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-1",
            "type": "permission_prompt",
            "message": "Allow running: rm -rf /tmp?",
            "options": ["yes", "no"],
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.block_reason == BlockReason.PERMISSION_PROMPT
        assert event.options == ["yes", "no"]

    def test_idle_notification_produces_agent_blocked(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-1",
            "type": "idle",
            "message": "Agent is waiting for input.",
        }
        event = parse_hook_event(raw)
        assert event is not None
        assert event.type == EventType.AGENT_BLOCKED
        assert event.block_reason == BlockReason.IDLE_PROMPT


# ---------------------------------------------------------------------------
# Hook -> Template -> NarrationEvent flow
# ---------------------------------------------------------------------------


class TestHookToNarrationFlow:
    """Test the full path from hook JSON through template rendering to NarrationEvent."""

    def test_question_with_options_produces_critical_narration_with_numbered_options(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-1",
            "type": "question",
            "message": "Which algorithm?",
            "options": ["RSA", "AES", "ChaCha20"],
        }
        event = parse_hook_event(raw)
        assert event is not None

        engine = TemplateEngine()
        narration = engine.render(event)

        assert narration.priority == NarrationPriority.CRITICAL
        assert narration.source_event_type == EventType.AGENT_BLOCKED
        assert narration.block_reason == BlockReason.QUESTION
        assert narration.session_id == "sess-1"
        assert "question" in narration.text.lower() or "asking" in narration.text.lower()
        assert "Option one: RSA." in narration.text
        assert "Option two: AES." in narration.text
        assert "Option three: ChaCha20." in narration.text

    def test_permission_produces_critical_narration(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-perm",
            "type": "permission_prompt",
            "message": "Allow running: pip install numpy?",
            "options": ["yes", "no"],
        }
        event = parse_hook_event(raw)
        engine = TemplateEngine()
        narration = engine.render(event)

        assert narration.priority == NarrationPriority.CRITICAL
        assert narration.block_reason == BlockReason.PERMISSION_PROMPT
        assert "permission" in narration.text.lower()
        assert "Option one: yes." in narration.text
        assert "Option two: no." in narration.text


# ---------------------------------------------------------------------------
# Alert tone differentiation
# ---------------------------------------------------------------------------


class TestAlertToneDifferentiation:
    """Test that different block reasons produce different tones."""

    def test_permission_tone_differs_from_question(self):
        perm = generate_alert_for_reason(BlockReason.PERMISSION_PROMPT)
        question = generate_alert_for_reason(BlockReason.QUESTION)
        assert len(perm) != len(question)

    def test_all_three_reasons_have_distinct_tones(self):
        lengths = {
            reason: len(generate_alert_for_reason(reason))
            for reason in [BlockReason.PERMISSION_PROMPT, BlockReason.QUESTION, BlockReason.IDLE_PROMPT]
        }
        assert len(set(lengths.values())) == 3


# ---------------------------------------------------------------------------
# AlertManager end-to-end with real EventBus
# ---------------------------------------------------------------------------


class TestAlertManagerEndToEnd:
    """Test AlertManager with real EventBus — activate, resolve, repeat."""

    async def test_alert_activated_then_resolved_by_tool_executed(self):
        bus = EventBus()
        manager = AlertManager(bus)
        await manager.start()

        await manager.activate("sess-1", BlockReason.QUESTION, "Which DB?")
        assert manager.has_active_alert("sess-1")

        resolve_event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="sess-1",
            source="hook",
        )
        await bus.emit(resolve_event)
        await asyncio.sleep(0.1)

        assert not manager.has_active_alert("sess-1")
        await manager.stop()

    async def test_alert_not_resolved_by_different_session(self):
        bus = EventBus()
        manager = AlertManager(bus)
        await manager.start()

        await manager.activate("sess-1", BlockReason.QUESTION, "Which DB?")

        other_event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="sess-other",
            source="hook",
        )
        await bus.emit(other_event)
        await asyncio.sleep(0.1)

        assert manager.has_active_alert("sess-1")
        await manager.stop()

    async def test_repeat_fires_and_invokes_callback(self, monkeypatch):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 2)

        bus = EventBus()
        manager = AlertManager(bus)
        callback = AsyncMock()
        manager.set_repeat_callback(callback)
        await manager.start()

        await manager.activate("sess-1", BlockReason.PERMISSION_PROMPT, "Allow?")
        await asyncio.sleep(0.15)

        assert callback.await_count >= 1
        callback.assert_awaited_with(BlockReason.PERMISSION_PROMPT, "Allow?")
        await manager.stop()

    async def test_alert_cleared_cancels_repeat(self, monkeypatch):
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_REPEAT_INTERVAL", 0.05)
        monkeypatch.setattr("echo.tts.alert_manager.ALERT_MAX_REPEATS", 10)

        bus = EventBus()
        manager = AlertManager(bus)
        callback = AsyncMock()
        manager.set_repeat_callback(callback)
        await manager.start()

        await manager.activate("sess-1", BlockReason.QUESTION, "Which?")

        resolve_event = EchoEvent(
            type=EventType.TOOL_EXECUTED,
            session_id="sess-1",
            source="hook",
        )
        await bus.emit(resolve_event)
        await asyncio.sleep(0.2)

        assert callback.await_count == 0
        await manager.stop()


# ---------------------------------------------------------------------------
# Full pipeline: hook JSON -> parse -> template -> alert tone selection
# ---------------------------------------------------------------------------


class TestFullPipelineFlow:
    """Test the complete flow from raw hook JSON through to alert tone selection."""

    def test_question_hook_produces_matching_alert_tone(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-q",
            "type": "question",
            "message": "Which framework?",
            "options": ["React", "Vue"],
        }
        event = parse_hook_event(raw)
        assert event is not None

        engine = TemplateEngine()
        narration = engine.render(event)
        assert narration.block_reason == BlockReason.QUESTION

        tone = generate_alert_for_reason(narration.block_reason)
        expected_question_tone = generate_alert_for_reason(BlockReason.QUESTION)
        assert len(tone) == len(expected_question_tone)

    def test_permission_hook_produces_matching_alert_tone(self):
        raw = {
            "hook_event_name": "Notification",
            "session_id": "sess-p",
            "type": "permission_prompt",
            "message": "Allow write to /etc/hosts?",
            "options": ["yes", "no"],
        }
        event = parse_hook_event(raw)
        engine = TemplateEngine()
        narration = engine.render(event)
        assert narration.block_reason == BlockReason.PERMISSION_PROMPT

        tone = generate_alert_for_reason(narration.block_reason)
        expected_perm_tone = generate_alert_for_reason(BlockReason.PERMISSION_PROMPT)
        assert len(tone) == len(expected_perm_tone)
        # Permission tone should be longer (more urgent) than question tone
        question_tone = generate_alert_for_reason(BlockReason.QUESTION)
        assert len(tone) > len(question_tone)


# ---------------------------------------------------------------------------
# /health endpoint alert_active field
# ---------------------------------------------------------------------------


class TestHealthEndpointAlertField:
    """Test the /health endpoint includes alert_active."""

    async def test_health_shows_alert_active_field(self, async_client):
        response = await async_client.get("/health")
        body = response.json()
        assert "alert_active" in body
        assert isinstance(body["alert_active"], bool)

    async def test_health_alert_active_defaults_false(self, async_client):
        response = await async_client.get("/health")
        body = response.json()
        assert body["alert_active"] is False

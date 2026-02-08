"""Tests for Stage 3 TTS integration into the server.

Verifies that the TTSEngine is wired into the FastAPI app, that the
/health endpoint exposes TTS status fields, and that the --no-tts CLI
flag correctly disables TTS by clearing the API key.

Uses httpx.AsyncClient with ASGITransport for async testing.
The app, async_client, event_bus, narration_bus, summarizer, and
tts_engine fixtures are defined in conftest.py.
"""

import os
from unittest.mock import AsyncMock, PropertyMock, patch

import click.testing
import httpx
import pytest

from echo.events.event_bus import EventBus
from echo.tts.tts_engine import TTSEngine


# ---------------------------------------------------------------------------
# GET /health — TTS fields
# ---------------------------------------------------------------------------


class TestHealthTTSFields:
    """Verify that /health includes the TTS-related fields."""

    async def test_health_includes_tts_state(self, async_client: httpx.AsyncClient):
        response = await async_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "tts_state" in body
        assert isinstance(body["tts_state"], str)

    async def test_health_includes_tts_available(
        self, async_client: httpx.AsyncClient
    ):
        response = await async_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "tts_available" in body
        assert isinstance(body["tts_available"], bool)

    async def test_health_includes_audio_available(
        self, async_client: httpx.AsyncClient
    ):
        response = await async_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "audio_available" in body
        assert isinstance(body["audio_available"], bool)

    async def test_health_includes_livekit_connected(
        self, async_client: httpx.AsyncClient
    ):
        response = await async_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "livekit_connected" in body
        assert isinstance(body["livekit_connected"], bool)

    async def test_health_tts_state_disabled_no_key(
        self, async_client: httpx.AsyncClient
    ):
        """With mocked sub-components all unavailable, tts_state should be disabled."""
        response = await async_client.get("/health")
        body = response.json()
        assert body["tts_state"] == "disabled"
        assert body["tts_available"] is False
        assert body["audio_available"] is False
        assert body["livekit_connected"] is False

    async def test_health_still_has_existing_fields(
        self, async_client: httpx.AsyncClient
    ):
        """Regression: verify original fields survive the TTS additions."""
        from echo import __version__

        response = await async_client.get("/health")
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        assert "subscribers" in body
        assert "narration_subscribers" in body
        assert "ollama_available" in body


# ---------------------------------------------------------------------------
# CLI --no-tts flag
# ---------------------------------------------------------------------------


class TestCLINoTTSFlag:
    """Tests for the --no-tts option on the start command."""

    def test_start_no_tts_flag_exists(self):
        """The --no-tts option should be registered on the start command."""
        from echo.cli import start

        param_names = [p.name for p in start.params]
        assert "no_tts" in param_names

    def test_start_no_tts_sets_env_var(self, monkeypatch):
        """When --no-tts is used, ECHO_ELEVENLABS_API_KEY should be set to empty string."""
        from echo.cli import cli

        # Ensure the env var starts with a value
        monkeypatch.setenv("ECHO_ELEVENLABS_API_KEY", "test-key-123")

        runner = click.testing.CliRunner()
        # We need to prevent the server from actually starting, so we
        # patch _run_server and _daemonize, plus hook installation.
        with patch("echo.cli._run_server"), \
             patch("echo.cli._daemonize"), \
             patch("echo.cli._read_pid", return_value=None), \
             patch("echo.cli.PID_FILE") as mock_pid:
            mock_pid.write_text = lambda x: None
            mock_pid.unlink = lambda missing_ok=True: None
            result = runner.invoke(cli, ["start", "--no-tts", "--skip-hooks"])

        assert "TTS disabled via --no-tts flag" in result.output
        assert os.environ.get("ECHO_ELEVENLABS_API_KEY") == ""

        # Restore for other tests
        monkeypatch.setenv("ECHO_ELEVENLABS_API_KEY", "test-key-123")

    def test_start_without_no_tts(self, monkeypatch):
        """Normal start (without --no-tts) should not clear the API key."""
        from echo.cli import cli

        monkeypatch.setenv("ECHO_ELEVENLABS_API_KEY", "real-key-456")

        runner = click.testing.CliRunner()
        with patch("echo.cli._run_server"), \
             patch("echo.cli._daemonize"), \
             patch("echo.cli._read_pid", return_value=None), \
             patch("echo.cli.PID_FILE") as mock_pid:
            mock_pid.write_text = lambda x: None
            mock_pid.unlink = lambda missing_ok=True: None
            result = runner.invoke(cli, ["start", "--skip-hooks"])

        assert "TTS disabled" not in result.output
        assert os.environ.get("ECHO_ELEVENLABS_API_KEY") == "real-key-456"

    def test_start_no_tts_flag_is_flag_type(self):
        """The --no-tts option should be a boolean flag (not a value option)."""
        from echo.cli import start

        no_tts_param = [p for p in start.params if p.name == "no_tts"][0]
        assert no_tts_param.is_flag is True


# ---------------------------------------------------------------------------
# App integration
# ---------------------------------------------------------------------------


class TestAppIntegration:
    """Verify TTSEngine is properly wired into the FastAPI application."""

    def test_app_state_has_tts_engine(self, app):
        """app.state.tts_engine should be set."""
        assert hasattr(app.state, "tts_engine")
        assert isinstance(app.state.tts_engine, TTSEngine)

    def test_tts_engine_receives_narration_bus(
        self, app, narration_bus: EventBus
    ):
        """The TTSEngine should be wired to the same narration_bus as the app."""
        tts = app.state.tts_engine
        assert tts._narration_bus is narration_bus

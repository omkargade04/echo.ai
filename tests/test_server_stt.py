"""Tests for Stage 5 STT integration into the server.

Verifies that the STTEngine is wired into the FastAPI app, that the
/health endpoint exposes STT status fields, that POST /respond handles
manual responses correctly, and that the --no-stt CLI flag disables STT.

Uses httpx.AsyncClient with ASGITransport for async testing.
The app, event_bus, narration_bus, summarizer, and tts_engine fixtures
are defined in conftest.py.  STT-specific fixtures are defined locally.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import click.testing
import httpx
import pytest

from echo.events.event_bus import EventBus
from echo.stt.types import ResponseEvent


# ---------------------------------------------------------------------------
# STT-specific fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stt_engine_mock():
    """Return a mock STTEngine with all properties stubbed."""
    mock = AsyncMock()
    mock.state = MagicMock()
    mock.state.value = "disabled"
    mock.stt_available = False
    mock.mic_available = False
    mock.dispatch_available = False
    mock.is_listening = False
    mock.handle_manual_response = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def response_bus():
    """Return a fresh EventBus for ResponseEvents."""
    return EventBus(maxsize=16)


@pytest.fixture
def stt_app(app, stt_engine_mock, response_bus):
    """Extend the existing conftest app fixture with STT state."""
    app.state.stt_engine = stt_engine_mock
    app.state.response_bus = response_bus
    return app


@pytest.fixture
async def stt_client(stt_app):
    """Return an httpx AsyncClient configured with the STT-enabled test app."""
    transport = httpx.ASGITransport(app=stt_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# GET /health — STT fields
# ---------------------------------------------------------------------------


class TestHealthSTTFields:
    """Verify that /health includes the STT-related fields."""

    async def test_health_includes_stt_state(self, stt_client: httpx.AsyncClient):
        response = await stt_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "stt_state" in body
        assert isinstance(body["stt_state"], str)

    async def test_health_includes_stt_available(self, stt_client: httpx.AsyncClient):
        response = await stt_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "stt_available" in body
        assert isinstance(body["stt_available"], bool)

    async def test_health_includes_mic_available(self, stt_client: httpx.AsyncClient):
        response = await stt_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "mic_available" in body
        assert isinstance(body["mic_available"], bool)

    async def test_health_includes_dispatch_available(
        self, stt_client: httpx.AsyncClient
    ):
        response = await stt_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "dispatch_available" in body
        assert isinstance(body["dispatch_available"], bool)

    async def test_health_includes_stt_listening(self, stt_client: httpx.AsyncClient):
        response = await stt_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert "stt_listening" in body
        assert isinstance(body["stt_listening"], bool)

    async def test_health_stt_fields_absent_without_stt_engine(self):
        """When stt_engine is not in app.state, STT fields should be absent.

        Uses a standalone app without stt_engine to avoid fixture
        contamination from the stt_engine_mock defined in this module.
        """
        from unittest.mock import PropertyMock as _PM, AsyncMock as _AM, patch as _p
        from fastapi import FastAPI as _FA
        from echo.server.routes import router as _r
        from echo.summarizer.summarizer import Summarizer as _S
        from echo.tts.tts_engine import TTSEngine as _T

        _eb = EventBus(maxsize=4)
        _nb = EventBus(maxsize=4)

        with _p("echo.summarizer.llm_summarizer.LLMSummarizer.start", new_callable=_AM), \
             _p("echo.summarizer.llm_summarizer.LLMSummarizer.stop", new_callable=_AM), \
             _p("echo.summarizer.llm_summarizer.LLMSummarizer.is_available", new_callable=_PM, return_value=False):
            _sum = _S(event_bus=_eb, narration_bus=_nb)

        with _p("echo.tts.tts_engine.ElevenLabsClient.start", new_callable=_AM), \
             _p("echo.tts.tts_engine.ElevenLabsClient.stop", new_callable=_AM), \
             _p("echo.tts.tts_engine.ElevenLabsClient.is_available", new_callable=_PM, return_value=False), \
             _p("echo.tts.tts_engine.AudioPlayer.start", new_callable=_AM), \
             _p("echo.tts.tts_engine.AudioPlayer.stop", new_callable=_AM), \
             _p("echo.tts.tts_engine.AudioPlayer.is_available", new_callable=_PM, return_value=False), \
             _p("echo.tts.tts_engine.LiveKitPublisher.start", new_callable=_AM), \
             _p("echo.tts.tts_engine.LiveKitPublisher.stop", new_callable=_AM), \
             _p("echo.tts.tts_engine.LiveKitPublisher.is_connected", new_callable=_PM, return_value=False), \
             _p("echo.tts.tts_engine.AlertManager.start", new_callable=_AM), \
             _p("echo.tts.tts_engine.AlertManager.stop", new_callable=_AM):
            _tts = _T(narration_bus=_nb, event_bus=_eb)

        bare_app = _FA()
        bare_app.state.event_bus = _eb
        bare_app.state.narration_bus = _nb
        bare_app.state.summarizer = _sum
        bare_app.state.tts_engine = _tts
        # Deliberately NOT setting stt_engine
        bare_app.include_router(_r)

        transport = httpx.ASGITransport(app=bare_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            body = response.json()
            assert "stt_state" not in body
            assert "stt_available" not in body
            assert "stt_listening" not in body

    async def test_health_still_has_existing_fields(
        self, stt_client: httpx.AsyncClient
    ):
        """Regression: verify original fields survive the STT additions."""
        from echo import __version__

        response = await stt_client.get("/health")
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        assert "subscribers" in body
        assert "narration_subscribers" in body
        assert "ollama_available" in body
        assert "tts_state" in body


# ---------------------------------------------------------------------------
# POST /respond
# ---------------------------------------------------------------------------


class TestPostRespond:
    """Verify the POST /respond endpoint for manual text responses."""

    async def test_respond_success(self, stt_client: httpx.AsyncClient):
        response = await stt_client.post(
            "/respond",
            json={"session_id": "sess-001", "text": "yes"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["text"] == "yes"
        assert body["session_id"] == "sess-001"

    async def test_respond_invalid_json(self, stt_client: httpx.AsyncClient):
        response = await stt_client.post(
            "/respond",
            content=b"not json at all",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["reason"] == "invalid json"

    async def test_respond_missing_session_id(self, stt_client: httpx.AsyncClient):
        response = await stt_client.post(
            "/respond",
            json={"text": "yes"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert "session_id" in body["reason"]

    async def test_respond_missing_text(self, stt_client: httpx.AsyncClient):
        response = await stt_client.post(
            "/respond",
            json={"session_id": "sess-001"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert "text" in body["reason"]

    async def test_respond_dispatch_failure(
        self, stt_client: httpx.AsyncClient, stt_engine_mock
    ):
        stt_engine_mock.handle_manual_response = AsyncMock(return_value=False)
        response = await stt_client.post(
            "/respond",
            json={"session_id": "sess-002", "text": "option 1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "dispatch_failed"
        assert body["text"] == "option 1"
        assert body["session_id"] == "sess-002"

    async def test_respond_calls_handle_manual_response(
        self, stt_client: httpx.AsyncClient, stt_engine_mock
    ):
        await stt_client.post(
            "/respond",
            json={"session_id": "sess-003", "text": "allow"},
        )
        stt_engine_mock.handle_manual_response.assert_awaited_once_with(
            "sess-003", "allow"
        )


# ---------------------------------------------------------------------------
# CLI --no-stt flag
# ---------------------------------------------------------------------------


class TestCLINoSTTFlag:
    """Tests for the --no-stt option on the start command."""

    def test_start_no_stt_flag_exists(self):
        from echo.cli import start

        param_names = [p.name for p in start.params]
        assert "no_stt" in param_names

    def test_start_no_stt_sets_env_var(self, monkeypatch):
        from echo.cli import cli

        monkeypatch.setenv("ECHO_STT_API_KEY", "test-stt-key-123")

        runner = click.testing.CliRunner()
        with patch("echo.cli._run_server"), \
             patch("echo.cli._daemonize"), \
             patch("echo.cli._read_pid", return_value=None), \
             patch("echo.cli.PID_FILE") as mock_pid:
            mock_pid.write_text = lambda x: None
            mock_pid.unlink = lambda missing_ok=True: None
            result = runner.invoke(cli, ["start", "--no-stt", "--skip-hooks"])

        assert "STT disabled via --no-stt flag" in result.output
        assert os.environ.get("ECHO_STT_API_KEY") == ""

        # Restore for other tests
        monkeypatch.setenv("ECHO_STT_API_KEY", "test-stt-key-123")

    def test_start_no_stt_flag_is_flag_type(self):
        from echo.cli import start

        no_stt_param = [p for p in start.params if p.name == "no_stt"][0]
        assert no_stt_param.is_flag is True


# ---------------------------------------------------------------------------
# App integration
# ---------------------------------------------------------------------------


class TestAppSTTIntegration:
    """Verify STTEngine and ResponseBus are properly wired into the app."""

    def test_app_state_has_stt_engine(self, stt_app):
        assert hasattr(stt_app.state, "stt_engine")
        assert stt_app.state.stt_engine is not None

    def test_app_state_has_response_bus(self, stt_app, response_bus):
        assert hasattr(stt_app.state, "response_bus")
        assert stt_app.state.response_bus is response_bus

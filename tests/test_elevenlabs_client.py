"""Tests for echo.tts.elevenlabs_client — ElevenLabs TTS HTTP client."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from echo.tts.elevenlabs_client import ElevenLabsClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_health_response(status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for GET /v1/user."""
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "/v1/user"),
    )


def _mock_synthesize_response(content: bytes = b"\x00\x01\x02\x03") -> httpx.Response:
    """Build a fake httpx.Response for POST /v1/text-to-speech/{voice_id}."""
    return httpx.Response(
        status_code=200,
        content=content,
        request=httpx.Request("POST", "/v1/text-to-speech/test-voice"),
    )


def _mock_error_response(status_code: int = 500) -> httpx.Response:
    """Build a fake httpx.Response with an error status."""
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "/v1/text-to-speech/test-voice"),
    )


# ---------------------------------------------------------------------------
# TestStartup — initialization and shutdown
# ---------------------------------------------------------------------------


class TestStartup:
    """Tests for start() and stop() lifecycle."""

    async def test_start_no_api_key(self, monkeypatch):
        """When API key is empty, start() should not create a client."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "")
        client = ElevenLabsClient()
        await client.start()

        assert client.is_available is False
        assert client._client is None

    async def test_start_with_api_key(self, monkeypatch):
        """When API key is set and health check succeeds, should be available."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            assert client.is_available is True
            assert client._client is instance
            MockClient.assert_called_once()

    async def test_start_health_check_fails(self, monkeypatch):
        """When health check returns 401, should not be available."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "bad-key")

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(401))
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            assert client.is_available is False

    async def test_start_health_check_connection_error(self, monkeypatch):
        """When health check raises ConnectError, should not be available."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            assert client.is_available is False

    async def test_stop_closes_client(self, monkeypatch):
        """stop() should close the HTTP client and set it to None."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            instance.aclose = AsyncMock()
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()
            assert client._client is not None

            await client.stop()
            instance.aclose.assert_awaited_once()
            assert client._client is None

    async def test_stop_without_start(self):
        """stop() on a fresh instance should not raise."""
        client = ElevenLabsClient()
        await client.stop()  # Should not raise


# ---------------------------------------------------------------------------
# TestSynthesize — synthesize() behavior
# ---------------------------------------------------------------------------


class TestSynthesize:
    """Tests for synthesize() method."""

    async def test_synthesize_success(self):
        """When available and POST succeeds, should return PCM bytes."""
        pcm_bytes = b"\x00\x01\x02\x03\x04\x05"
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(pcm_bytes)
        )

        result = await client.synthesize("Hello world")

        assert result == pcm_bytes

    async def test_synthesize_correct_url(self, monkeypatch):
        """POST URL should include the configured voice ID."""
        monkeypatch.setattr(
            "echo.tts.elevenlabs_client.TTS_VOICE_ID", "voice-abc-123"
        )
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(b"\x00")
        )

        await client.synthesize("Test text")

        call_args = client._client.post.call_args
        url = call_args[0][0]
        assert "/v1/text-to-speech/voice-abc-123" == url

    async def test_synthesize_correct_body(self, monkeypatch):
        """POST body should contain text and model_id."""
        monkeypatch.setattr(
            "echo.tts.elevenlabs_client.TTS_MODEL", "eleven_turbo_v2_5"
        )
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(b"\x00")
        )

        await client.synthesize("Say this aloud")

        call_args = client._client.post.call_args
        json_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert json_body["text"] == "Say this aloud"
        assert json_body["model_id"] == "eleven_turbo_v2_5"

    async def test_synthesize_correct_query_param(self):
        """POST should include output_format=pcm_16000 as a query param."""
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(b"\x00")
        )

        await client.synthesize("Test")

        call_args = client._client.post.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params")
        assert params == {"output_format": "pcm_16000"}

    async def test_synthesize_not_available(self):
        """When not available, should return None without making HTTP call."""
        client = ElevenLabsClient()
        client._available = False
        client._client = AsyncMock()

        result = await client.synthesize("Hello")

        assert result is None
        client._client.post.assert_not_awaited()

    async def test_synthesize_no_client(self):
        """When client is None, should return None."""
        client = ElevenLabsClient()
        client._available = True
        client._client = None

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_http_error(self):
        """When POST returns 500, should return None."""
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()

        error_response = _mock_error_response(500)
        client._client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "/v1/text-to-speech/test"),
                response=error_response,
            )
        )

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_timeout(self):
        """When POST raises TimeoutException, should return None."""
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_connection_error(self):
        """When POST raises ConnectError, should return None."""
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_empty_text(self):
        """Synthesizing empty string should still make the API call."""
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(b"\x00")
        )

        result = await client.synthesize("")

        assert result is not None
        client._client.post.assert_awaited_once()
        call_args = client._client.post.call_args
        json_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert json_body["text"] == ""


# ---------------------------------------------------------------------------
# TestHealthCheck — _check_health and _maybe_recheck_health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for health check behavior."""

    async def test_health_check_success(self):
        """GET /v1/user returning 200 should set available to True."""
        client = ElevenLabsClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))

        await client._check_health()

        assert client._available is True

    async def test_health_check_unauthorized(self):
        """GET /v1/user returning 401 should set available to False."""
        client = ElevenLabsClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(401))

        await client._check_health()

        assert client._available is False

    async def test_maybe_recheck_when_available(self):
        """When already available, _maybe_recheck_health should not re-check."""
        client = ElevenLabsClient()
        client._available = True
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic() - 120.0

        await client._maybe_recheck_health()

        client._client.get.assert_not_awaited()

    async def test_maybe_recheck_when_unavailable_too_soon(self):
        """When unavailable but interval not elapsed, should not re-check."""
        client = ElevenLabsClient()
        client._available = False
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic()

        await client._maybe_recheck_health()

        client._client.get.assert_not_awaited()

    async def test_maybe_recheck_when_unavailable_enough_time(self):
        """When unavailable and interval has elapsed, should re-check."""
        client = ElevenLabsClient()
        client._available = False
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic() - 120.0

        await client._maybe_recheck_health()

        client._client.get.assert_awaited_once()
        assert client._available is True

    async def test_health_check_updates_timestamp(self):
        """_check_health should update _last_health_check timestamp."""
        client = ElevenLabsClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))

        before = time.monotonic()
        await client._check_health()
        after = time.monotonic()

        assert before <= client._last_health_check <= after


# ---------------------------------------------------------------------------
# TestProperties — is_available property
# ---------------------------------------------------------------------------


class TestProperties:
    """Tests for the is_available property."""

    async def test_is_available_default_false(self):
        """A fresh instance should not be available."""
        client = ElevenLabsClient()
        assert client.is_available is False

    async def test_is_available_after_successful_start(self, monkeypatch):
        """After successful start with valid key, should be available."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            assert client.is_available is True

    async def test_is_available_after_failed_start(self, monkeypatch):
        """After start with failed health check, should not be available."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            assert client.is_available is False


# ---------------------------------------------------------------------------
# TestClientConfiguration — verify config values are wired correctly
# ---------------------------------------------------------------------------


class TestClientConfiguration:
    """Tests that config values are passed to the HTTP client."""

    async def test_client_uses_elevenlabs_base_url(self, monkeypatch):
        """AsyncClient should be initialized with ELEVENLABS_BASE_URL."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")
        monkeypatch.setattr(
            "echo.tts.elevenlabs_client.ELEVENLABS_BASE_URL",
            "https://custom.elevenlabs.io",
        )

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["base_url"] == "https://custom.elevenlabs.io"

    async def test_client_uses_tts_timeout(self, monkeypatch):
        """AsyncClient should be initialized with TTS_TIMEOUT."""
        monkeypatch.setattr("echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "test-key")
        monkeypatch.setattr("echo.tts.elevenlabs_client.TTS_TIMEOUT", 15.0)

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["timeout"] == 15.0

    async def test_client_uses_api_key_header(self, monkeypatch):
        """AsyncClient should include xi-api-key header."""
        monkeypatch.setattr(
            "echo.tts.elevenlabs_client.ELEVENLABS_API_KEY", "sk-my-secret"
        )

        with patch("echo.tts.elevenlabs_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = ElevenLabsClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["headers"] == {"xi-api-key": "sk-my-secret"}

    async def test_health_check_no_client_sets_unavailable(self):
        """_check_health with no client should set _available to False."""
        client = ElevenLabsClient()
        client._client = None

        await client._check_health()

        assert client._available is False

    async def test_health_check_timeout_exception(self):
        """_check_health should handle TimeoutException gracefully."""
        client = ElevenLabsClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        await client._check_health()

        assert client._available is False

    async def test_health_check_os_error(self):
        """_check_health should handle OSError gracefully."""
        client = ElevenLabsClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(
            side_effect=OSError("Network unreachable")
        )

        await client._check_health()

        assert client._available is False

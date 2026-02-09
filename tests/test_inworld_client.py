"""Tests for echo.tts.inworld_client — Inworld TTS HTTP client."""

import base64
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from echo.tts.inworld_client import InworldClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_health_response(status_code: int = 200, pcm_bytes: bytes = b"\x00\x01\x02\x03") -> httpx.Response:
    """Build a fake httpx.Response for POST /tts/v1/voice health check."""
    audio_b64 = base64.b64encode(pcm_bytes).decode()
    return httpx.Response(
        status_code=status_code,
        json={"result": {"audioContent": audio_b64}},
        request=httpx.Request("POST", "/tts/v1/voice"),
    )


def _mock_synthesize_response(pcm_bytes: bytes = b"\x00\x01\x02\x03") -> httpx.Response:
    """Build a fake httpx.Response for POST /tts/v1/voice synthesis."""
    audio_b64 = base64.b64encode(pcm_bytes).decode()
    return httpx.Response(
        status_code=200,
        json={"result": {"audioContent": audio_b64}},
        request=httpx.Request("POST", "/tts/v1/voice"),
    )


def _mock_wav_synthesize_response(pcm_bytes: bytes = b"\x00\x01\x02\x03") -> httpx.Response:
    """Build a fake httpx.Response with RIFF WAV header (44 bytes)."""
    wav_header = b"RIFF" + b"\x00" * 40  # 44 bytes total
    audio_b64 = base64.b64encode(wav_header + pcm_bytes).decode()
    return httpx.Response(
        status_code=200,
        json={"result": {"audioContent": audio_b64}},
        request=httpx.Request("POST", "/tts/v1/voice"),
    )


def _mock_error_response(status_code: int = 500) -> httpx.Response:
    """Build a fake httpx.Response with an error status."""
    return httpx.Response(
        status_code=status_code,
        json={"error": "Internal server error"},
        request=httpx.Request("POST", "/tts/v1/voice"),
    )


# ---------------------------------------------------------------------------
# TestStartup — initialization and shutdown
# ---------------------------------------------------------------------------


class TestStartup:
    """Tests for start() and stop() lifecycle."""

    async def test_start_no_api_key(self, monkeypatch):
        """When API key is empty, start() should not create a client."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "")
        client = InworldClient()
        await client.start()

        assert client.is_available is False
        assert client._client is None

    async def test_start_with_api_key(self, monkeypatch):
        """When API key is set and health check succeeds, should be available."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            assert client.is_available is True
            assert client._client is instance
            MockClient.assert_called_once()

    async def test_start_health_check_fails(self, monkeypatch):
        """When health check returns 401, should not be available."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "bad-key")

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(401))
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            assert client.is_available is False

    async def test_start_health_check_connection_error(self, monkeypatch):
        """When health check raises ConnectError, should not be available."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            assert client.is_available is False

    async def test_stop_closes_client(self, monkeypatch):
        """stop() should close the HTTP client and set it to None."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(200))
            instance.aclose = AsyncMock()
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()
            assert client._client is not None

            await client.stop()
            instance.aclose.assert_awaited_once()
            assert client._client is None

    async def test_stop_without_start(self):
        """stop() on a fresh instance should not raise."""
        client = InworldClient()
        await client.stop()  # Should not raise


# ---------------------------------------------------------------------------
# TestSynthesize — synthesize() behavior
# ---------------------------------------------------------------------------


class TestSynthesize:
    """Tests for synthesize() method."""

    async def test_synthesize_success_with_base64_decode(self):
        """When available and POST succeeds, should decode base64 and return PCM bytes."""
        pcm_bytes = b"\x00\x01\x02\x03\x04\x05"
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(pcm_bytes)
        )

        result = await client.synthesize("Hello world")

        assert result == pcm_bytes

    async def test_synthesize_success_with_wav_header_strip(self):
        """When response has RIFF header, should strip first 44 bytes."""
        pcm_bytes = b"\x00\x01\x02\x03\x04\x05"
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_wav_synthesize_response(pcm_bytes)
        )

        result = await client.synthesize("Test")

        assert result == pcm_bytes

    async def test_synthesize_correct_url(self):
        """POST URL should be /tts/v1/voice."""
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(b"\x00")
        )

        await client.synthesize("Test text")

        call_args = client._client.post.call_args
        url = call_args[0][0]
        assert url == "/tts/v1/voice"

    async def test_synthesize_correct_body(self, monkeypatch):
        """POST body should contain text, voiceId, modelId, audioConfig, temperature."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_VOICE_ID", "voice-123")
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_MODEL", "model-abc")
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_TEMPERATURE", 1.2)
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_SPEAKING_RATE", 0.9)
        monkeypatch.setattr("echo.tts.inworld_client.AUDIO_SAMPLE_RATE", 16000)

        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_synthesize_response(b"\x00")
        )

        await client.synthesize("Say this aloud")

        call_args = client._client.post.call_args
        json_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert json_body["text"] == "Say this aloud"
        assert json_body["voiceId"] == "voice-123"
        assert json_body["modelId"] == "model-abc"
        assert json_body["temperature"] == 1.2
        assert json_body["audioConfig"]["audioEncoding"] == "LINEAR16"
        assert json_body["audioConfig"]["sampleRateHertz"] == 16000
        assert json_body["audioConfig"]["speakingRate"] == 0.9

    async def test_synthesize_not_available(self):
        """When not available, should return None without making HTTP call."""
        client = InworldClient()
        client._available = False
        client._client = AsyncMock()
        client._last_health_check = time.monotonic()  # Prevent recheck

        result = await client.synthesize("Hello")

        assert result is None
        client._client.post.assert_not_awaited()

    async def test_synthesize_no_client(self):
        """When client is None, should return None."""
        client = InworldClient()
        client._available = True
        client._client = None

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_http_error(self):
        """When POST returns 500, should return None."""
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()

        error_response = _mock_error_response(500)
        client._client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "/tts/v1/voice"),
                response=error_response,
            )
        )

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_timeout(self):
        """When POST raises TimeoutException, should return None."""
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_connection_error(self):
        """When POST raises ConnectError, should return None."""
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await client.synthesize("Hello")

        assert result is None

    async def test_synthesize_empty_text(self):
        """Synthesizing empty string should still make the API call."""
        client = InworldClient()
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

    async def test_synthesize_missing_audio_content(self):
        """When response lacks audioContent field, should return None."""
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=httpx.Response(
                status_code=200,
                json={"result": {}},
                request=httpx.Request("POST", "/tts/v1/voice"),
            )
        )

        result = await client.synthesize("Test")

        assert result is None


# ---------------------------------------------------------------------------
# TestHealthCheck — _check_health and _maybe_recheck_health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for health check behavior."""

    async def test_health_check_success(self):
        """POST /tts/v1/voice returning 200 should set available to True."""
        client = InworldClient()
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=_mock_health_response(200))

        await client._check_health()

        assert client._available is True

    async def test_health_check_unauthorized(self):
        """POST /tts/v1/voice returning 401 should set available to False."""
        client = InworldClient()
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=_mock_health_response(401))

        await client._check_health()

        assert client._available is False

    async def test_maybe_recheck_when_available(self):
        """When already available, _maybe_recheck_health should not re-check."""
        client = InworldClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic() - 120.0

        await client._maybe_recheck_health()

        client._client.post.assert_not_awaited()

    async def test_maybe_recheck_when_unavailable_too_soon(self):
        """When unavailable but interval not elapsed, should not re-check."""
        client = InworldClient()
        client._available = False
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic()

        await client._maybe_recheck_health()

        client._client.post.assert_not_awaited()

    async def test_maybe_recheck_when_unavailable_enough_time(self):
        """When unavailable and interval has elapsed, should re-check."""
        client = InworldClient()
        client._available = False
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic() - 120.0

        await client._maybe_recheck_health()

        client._client.post.assert_awaited_once()
        assert client._available is True

    async def test_health_check_updates_timestamp(self):
        """_check_health should update _last_health_check timestamp."""
        client = InworldClient()
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=_mock_health_response(200))

        before = time.monotonic()
        await client._check_health()
        after = time.monotonic()

        assert before <= client._last_health_check <= after


# ---------------------------------------------------------------------------
# TestProperties — is_available and provider_name properties
# ---------------------------------------------------------------------------


class TestProperties:
    """Tests for is_available and provider_name properties."""

    async def test_is_available_default_false(self):
        """A fresh instance should not be available."""
        client = InworldClient()
        assert client.is_available is False

    async def test_is_available_after_successful_start(self, monkeypatch):
        """After successful start with valid key, should be available."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            assert client.is_available is True

    async def test_is_available_after_failed_start(self, monkeypatch):
        """After start with failed health check, should not be available."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            assert client.is_available is False

    async def test_provider_name(self):
        """provider_name should return 'inworld'."""
        client = InworldClient()
        assert client.provider_name == "inworld"


# ---------------------------------------------------------------------------
# TestClientConfiguration — verify config values are wired correctly
# ---------------------------------------------------------------------------


class TestClientConfiguration:
    """Tests that config values are passed to the HTTP client."""

    async def test_client_uses_inworld_base_url(self, monkeypatch):
        """AsyncClient should be initialized with INWORLD_BASE_URL."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")
        monkeypatch.setattr(
            "echo.tts.inworld_client.INWORLD_BASE_URL",
            "https://custom.inworld.ai",
        )

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["base_url"] == "https://custom.inworld.ai"

    async def test_client_uses_inworld_timeout(self, monkeypatch):
        """AsyncClient should be initialized with INWORLD_TIMEOUT."""
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_API_KEY", "test-key")
        monkeypatch.setattr("echo.tts.inworld_client.INWORLD_TIMEOUT", 15.0)

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["timeout"] == 15.0

    async def test_client_uses_basic_auth_header(self, monkeypatch):
        """AsyncClient should include Authorization: Basic header."""
        monkeypatch.setattr(
            "echo.tts.inworld_client.INWORLD_API_KEY", "my-secret-key"
        )

        with patch("echo.tts.inworld_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = InworldClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["headers"] == {"Authorization": "Basic my-secret-key"}

    async def test_health_check_no_client_sets_unavailable(self):
        """_check_health with no client should set _available to False."""
        client = InworldClient()
        client._client = None

        await client._check_health()

        assert client._available is False

    async def test_health_check_timeout_exception(self):
        """_check_health should handle TimeoutException gracefully."""
        client = InworldClient()
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        await client._check_health()

        assert client._available is False

    async def test_health_check_os_error(self):
        """_check_health should handle OSError gracefully."""
        client = InworldClient()
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=OSError("Network unreachable")
        )

        await client._check_health()

        assert client._available is False

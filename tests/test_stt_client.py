"""Tests for echo.stt.stt_client — OpenAI Whisper STT HTTP client."""

import io
import time
import wave
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from echo.stt.stt_client import STTClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_health_response(status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for GET /v1/models."""
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "/v1/models"),
    )


def _mock_transcribe_response(text: str = "hello world") -> httpx.Response:
    """Build a fake httpx.Response for POST /v1/audio/transcriptions."""
    import json

    return httpx.Response(
        status_code=200,
        content=json.dumps({"text": text}).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "/v1/audio/transcriptions"),
    )


def _mock_error_response(status_code: int = 500) -> httpx.Response:
    """Build a fake httpx.Response with an error status."""
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "/v1/audio/transcriptions"),
    )


def _pcm_silence(num_frames: int = 160) -> bytes:
    """Return silent PCM int16 bytes (all zeros)."""
    return b"\x00\x00" * num_frames


# ---------------------------------------------------------------------------
# TestStartup — initialization and shutdown
# ---------------------------------------------------------------------------


class TestStartup:
    """Tests for start() and stop() lifecycle."""

    async def test_start_no_api_key_disables(self, monkeypatch):
        """When API key is empty, start() should not create a client."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "")
        client = STTClient()
        await client.start()

        assert client.is_available is False
        assert client._client is None

    async def test_start_with_key_health_check_success(self, monkeypatch):
        """When API key is set and health check succeeds, should be available."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "test-key-123")

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = STTClient()
            await client.start()

            assert client.is_available is True
            assert client._client is instance
            MockClient.assert_called_once()

    async def test_start_with_key_health_check_failure(self, monkeypatch):
        """When health check returns 401, should not be available."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "bad-key")

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(401))
            MockClient.return_value = instance

            client = STTClient()
            await client.start()

            assert client.is_available is False

    async def test_start_with_key_health_check_connection_error(self, monkeypatch):
        """When health check raises ConnectError, should not be available."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "test-key-123")

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            MockClient.return_value = instance

            client = STTClient()
            await client.start()

            assert client.is_available is False

    async def test_stop_closes_client(self, monkeypatch):
        """stop() should close the HTTP client and set it to None."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "test-key-123")

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            instance.aclose = AsyncMock()
            MockClient.return_value = instance

            client = STTClient()
            await client.start()
            assert client._client is not None

            await client.stop()
            instance.aclose.assert_awaited_once()
            assert client._client is None

    async def test_stop_without_start(self):
        """stop() on a fresh instance should not raise."""
        client = STTClient()
        await client.stop()  # Should not raise


# ---------------------------------------------------------------------------
# TestTranscribe — transcribe() behavior
# ---------------------------------------------------------------------------


class TestTranscribe:
    """Tests for transcribe() method."""

    async def test_transcribe_success(self):
        """When available and POST succeeds, should return transcript text."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_transcribe_response("hello world")
        )

        result = await client.transcribe(_pcm_silence())

        assert result == "hello world"

    async def test_transcribe_returns_none_when_unavailable(self):
        """When not available, should return None without making HTTP call."""
        client = STTClient()
        client._available = False
        client._client = AsyncMock()

        result = await client.transcribe(_pcm_silence())

        assert result is None
        client._client.post.assert_not_awaited()

    async def test_transcribe_returns_none_on_http_error(self):
        """When POST returns 500, should return None."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()

        error_response = _mock_error_response(500)
        client._client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "/v1/audio/transcriptions"),
                response=error_response,
            )
        )

        result = await client.transcribe(_pcm_silence())

        assert result is None

    async def test_transcribe_returns_none_on_timeout(self):
        """When POST raises TimeoutException, should return None."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        result = await client.transcribe(_pcm_silence())

        assert result is None

    async def test_transcribe_returns_none_on_empty_text(self):
        """When API returns empty text, should return None."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_transcribe_response("")
        )

        result = await client.transcribe(_pcm_silence())

        assert result is None

    async def test_transcribe_sends_wav_format(self):
        """POST request should include a WAV file via the files parameter."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_transcribe_response("test")
        )

        await client.transcribe(_pcm_silence())

        call_args = client._client.post.call_args
        files_param = call_args.kwargs.get("files") or call_args[1].get("files")
        assert files_param is not None
        # files={"file": ("audio.wav", wav_buffer, "audio/wav")}
        file_tuple = files_param["file"]
        assert file_tuple[0] == "audio.wav"
        assert file_tuple[2] == "audio/wav"
        # Second element should be a BytesIO containing WAV data
        wav_data = file_tuple[1]
        wav_data.seek(0)
        raw = wav_data.read(4)
        assert raw == b"RIFF"

    async def test_transcribe_sends_correct_model(self, monkeypatch):
        """POST request should include the configured STT_MODEL."""
        monkeypatch.setattr("echo.stt.stt_client.STT_MODEL", "whisper-1")
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_transcribe_response("test")
        )

        await client.transcribe(_pcm_silence())

        call_args = client._client.post.call_args
        data_param = call_args.kwargs.get("data") or call_args[1].get("data")
        assert data_param["model"] == "whisper-1"

    async def test_transcribe_strips_whitespace(self):
        """Transcript text should be stripped of leading/trailing whitespace."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            return_value=_mock_transcribe_response("  hello  ")
        )

        result = await client.transcribe(_pcm_silence())

        assert result == "hello"

    async def test_transcribe_no_client(self):
        """When client is None, should return None."""
        client = STTClient()
        client._available = True
        client._client = None

        result = await client.transcribe(_pcm_silence())

        assert result is None

    async def test_transcribe_connection_error(self):
        """When POST raises ConnectError, should return None."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await client.transcribe(_pcm_silence())

        assert result is None


# ---------------------------------------------------------------------------
# TestHealthCheck — _check_health and _maybe_recheck_health
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for health check behavior."""

    async def test_maybe_recheck_when_available_skips(self):
        """When already available, _maybe_recheck_health should not re-check."""
        client = STTClient()
        client._available = True
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic() - 120.0

        await client._maybe_recheck_health()

        client._client.get.assert_not_awaited()

    async def test_maybe_recheck_when_not_elapsed_skips(self):
        """When unavailable but interval not elapsed, should not re-check."""
        client = STTClient()
        client._available = False
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic()

        await client._maybe_recheck_health()

        client._client.get.assert_not_awaited()

    async def test_maybe_recheck_when_elapsed_rechecks(self):
        """When unavailable and interval has elapsed, should re-check."""
        client = STTClient()
        client._available = False
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))
        client._last_health_check = time.monotonic() - 120.0

        await client._maybe_recheck_health()

        client._client.get.assert_awaited_once()
        assert client._available is True

    async def test_recheck_restores_availability(self):
        """After failure, once interval elapses and health succeeds, available again."""
        client = STTClient()
        client._available = False
        client._client = AsyncMock()
        # First call fails, second succeeds
        client._client.get = AsyncMock(
            side_effect=[
                _mock_health_response(503),
                _mock_health_response(200),
            ]
        )

        # First recheck: fails (interval elapsed)
        client._last_health_check = time.monotonic() - 120.0
        await client._maybe_recheck_health()
        assert client._available is False

        # Second recheck: succeeds (set timestamp back again)
        client._last_health_check = time.monotonic() - 120.0
        await client._maybe_recheck_health()
        assert client._available is True

    async def test_health_check_updates_timestamp(self):
        """_check_health should update _last_health_check timestamp."""
        client = STTClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(return_value=_mock_health_response(200))

        before = time.monotonic()
        await client._check_health()
        after = time.monotonic()

        assert before <= client._last_health_check <= after

    async def test_health_check_no_client_sets_unavailable(self):
        """_check_health with no client should set _available to False."""
        client = STTClient()
        client._client = None

        await client._check_health()

        assert client._available is False

    async def test_health_check_timeout_exception(self):
        """_check_health should handle TimeoutException gracefully."""
        client = STTClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )

        await client._check_health()

        assert client._available is False

    async def test_health_check_os_error(self):
        """_check_health should handle OSError gracefully."""
        client = STTClient()
        client._client = AsyncMock()
        client._client.get = AsyncMock(
            side_effect=OSError("Network unreachable")
        )

        await client._check_health()

        assert client._available is False


# ---------------------------------------------------------------------------
# TestWrapWav — _wrap_wav static method
# ---------------------------------------------------------------------------


class TestWrapWav:
    """Tests for the _wrap_wav static method."""

    def test_wrap_wav_returns_valid_wav(self):
        """Output should start with the RIFF header."""
        pcm = _pcm_silence(160)
        result = STTClient._wrap_wav(pcm)
        raw = result.read(4)
        assert raw == b"RIFF"

    def test_wrap_wav_has_correct_params(self):
        """WAV should have 1 channel, 16-bit, 16000 Hz."""
        pcm = _pcm_silence(160)
        result = STTClient._wrap_wav(pcm)
        result.seek(0)
        with wave.open(result, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000

    def test_wrap_wav_contains_audio_data(self):
        """WAV frames should match the input PCM bytes."""
        pcm = b"\x01\x02\x03\x04"  # 2 frames of int16
        result = STTClient._wrap_wav(pcm)
        result.seek(0)
        with wave.open(result, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            assert frames == pcm

    def test_wrap_wav_custom_sample_rate(self):
        """_wrap_wav should accept a custom sample rate."""
        pcm = _pcm_silence(160)
        result = STTClient._wrap_wav(pcm, sample_rate=48000)
        result.seek(0)
        with wave.open(result, "rb") as wf:
            assert wf.getframerate() == 48000

    def test_wrap_wav_returns_seeked_to_start(self):
        """Returned BytesIO should be seeked to position 0."""
        pcm = _pcm_silence(160)
        result = STTClient._wrap_wav(pcm)
        assert result.tell() == 0


# ---------------------------------------------------------------------------
# TestClientConfiguration — verify config values are wired correctly
# ---------------------------------------------------------------------------


class TestClientConfiguration:
    """Tests that config values are passed to the HTTP client."""

    async def test_client_uses_stt_base_url(self, monkeypatch):
        """AsyncClient should be initialized with STT_BASE_URL."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "test-key")
        monkeypatch.setattr(
            "echo.stt.stt_client.STT_BASE_URL",
            "https://custom.openai.com",
        )

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = STTClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["base_url"] == "https://custom.openai.com"

    async def test_client_uses_stt_timeout(self, monkeypatch):
        """AsyncClient should be initialized with STT_TIMEOUT."""
        monkeypatch.setattr("echo.stt.stt_client.STT_API_KEY", "test-key")
        monkeypatch.setattr("echo.stt.stt_client.STT_TIMEOUT", 15.0)

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = STTClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["timeout"] == 15.0

    async def test_client_uses_bearer_auth_header(self, monkeypatch):
        """AsyncClient should include Authorization: Bearer header."""
        monkeypatch.setattr(
            "echo.stt.stt_client.STT_API_KEY", "sk-my-secret-key"
        )

        with patch("echo.stt.stt_client.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            client = STTClient()
            await client.start()

            call_kwargs = MockClient.call_args.kwargs
            assert call_kwargs["headers"] == {
                "Authorization": "Bearer sk-my-secret-key"
            }

    async def test_is_available_default_false(self):
        """A fresh instance should not be available."""
        client = STTClient()
        assert client.is_available is False

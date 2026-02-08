"""Tests for echo.tts.livekit_publisher — LiveKit room audio publisher."""

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers: build a fake ``livekit`` package so the module can be imported
# even when the real SDK is absent.
# ---------------------------------------------------------------------------

def _build_mock_livekit():
    """Create mock livekit, livekit.rtc, and livekit.api modules."""
    # --- rtc mocks ---
    mock_room = MagicMock(name="Room")
    mock_room_instance = MagicMock(name="Room()")
    mock_room_instance.connect = AsyncMock()
    mock_room_instance.disconnect = AsyncMock()
    mock_room_instance.local_participant = MagicMock()
    mock_room_instance.local_participant.publish_track = AsyncMock()
    mock_room.return_value = mock_room_instance

    mock_audio_source = MagicMock(name="AudioSource")
    mock_audio_source_instance = MagicMock(name="AudioSource()")
    mock_audio_source_instance.capture_frame = AsyncMock()
    mock_audio_source.return_value = mock_audio_source_instance

    mock_local_audio_track = MagicMock(name="LocalAudioTrack")
    mock_track_instance = MagicMock(name="track")
    mock_local_audio_track.create_audio_track = MagicMock(
        return_value=mock_track_instance
    )

    mock_audio_frame = MagicMock(name="AudioFrame")

    rtc_module = types.ModuleType("livekit.rtc")
    rtc_module.Room = mock_room
    rtc_module.AudioSource = mock_audio_source
    rtc_module.LocalAudioTrack = mock_local_audio_track
    rtc_module.AudioFrame = mock_audio_frame

    # --- api mocks ---
    mock_video_grants = MagicMock(name="VideoGrants")

    mock_access_token = MagicMock(name="AccessToken")
    mock_token_instance = MagicMock(name="AccessToken()")
    mock_token_instance.with_identity.return_value = mock_token_instance
    mock_token_instance.with_grants.return_value = mock_token_instance
    mock_token_instance.to_jwt.return_value = "mock-jwt-token"
    mock_access_token.return_value = mock_token_instance

    api_module = types.ModuleType("livekit.api")
    api_module.AccessToken = mock_access_token
    api_module.VideoGrants = mock_video_grants

    # --- top-level livekit package ---
    livekit_module = types.ModuleType("livekit")
    livekit_module.rtc = rtc_module
    livekit_module.api = api_module

    return {
        "livekit": livekit_module,
        "livekit.rtc": rtc_module,
        "livekit.api": api_module,
        # stash mocks for assertion access
        "_room": mock_room,
        "_room_instance": mock_room_instance,
        "_audio_source": mock_audio_source,
        "_audio_source_instance": mock_audio_source_instance,
        "_local_audio_track": mock_local_audio_track,
        "_track_instance": mock_track_instance,
        "_audio_frame": mock_audio_frame,
        "_access_token": mock_access_token,
        "_token_instance": mock_token_instance,
        "_video_grants": mock_video_grants,
    }


@pytest.fixture()
def mock_livekit(monkeypatch):
    """Install a fake livekit SDK and re-import the publisher module.

    Yields a dict with all mock objects so tests can make assertions on
    the SDK calls.
    """
    mocks = _build_mock_livekit()

    # Inject fake modules into sys.modules so the livekit import succeeds.
    monkeypatch.setitem(sys.modules, "livekit", mocks["livekit"])
    monkeypatch.setitem(sys.modules, "livekit.rtc", mocks["livekit.rtc"])
    monkeypatch.setitem(sys.modules, "livekit.api", mocks["livekit.api"])

    # The echo.tts package __init__ imports AudioPlayer which needs sounddevice.
    # Ensure sounddevice is present (as a mock) so the package can be loaded.
    if "sounddevice" not in sys.modules:
        monkeypatch.setitem(sys.modules, "sounddevice", MagicMock(name="sounddevice"))

    # Provide valid credentials by default
    monkeypatch.setattr("echo.config.LIVEKIT_URL", "wss://test.livekit.cloud")
    monkeypatch.setattr("echo.config.LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr("echo.config.LIVEKIT_API_SECRET", "test-secret")

    # Force-reimport so the module picks up the mocked livekit SDK
    if "echo.tts.livekit_publisher" in sys.modules:
        monkeypatch.delitem(sys.modules, "echo.tts.livekit_publisher")

    import echo.tts.livekit_publisher as mod

    importlib.reload(mod)

    # Patch the config references inside the reloaded module
    monkeypatch.setattr(mod, "LIVEKIT_URL", "wss://test.livekit.cloud")
    monkeypatch.setattr(mod, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(mod, "LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setattr(mod, "LIVEKIT_SDK_AVAILABLE", True)

    mocks["module"] = mod
    yield mocks


@pytest.fixture()
def publisher(mock_livekit):
    """Return a fresh LiveKitPublisher using the mocked SDK."""
    return mock_livekit["module"].LiveKitPublisher()


# -----------------------------------------------------------------------
# Configuration tests
# -----------------------------------------------------------------------

class TestConfiguration:

    async def test_not_configured_no_url(self, mock_livekit, monkeypatch):
        mod = mock_livekit["module"]
        monkeypatch.setattr(mod, "LIVEKIT_URL", "")
        pub = mod.LiveKitPublisher()
        assert pub.is_configured is False

    async def test_not_configured_no_key(self, mock_livekit, monkeypatch):
        mod = mock_livekit["module"]
        monkeypatch.setattr(mod, "LIVEKIT_API_KEY", "")
        pub = mod.LiveKitPublisher()
        assert pub.is_configured is False

    async def test_not_configured_no_secret(self, mock_livekit, monkeypatch):
        mod = mock_livekit["module"]
        monkeypatch.setattr(mod, "LIVEKIT_API_SECRET", "")
        pub = mod.LiveKitPublisher()
        assert pub.is_configured is False

    async def test_configured_all_present(self, publisher):
        assert publisher.is_configured is True


# -----------------------------------------------------------------------
# Startup / shutdown tests
# -----------------------------------------------------------------------

class TestStartupShutdown:

    async def test_start_not_configured(self, mock_livekit, monkeypatch):
        mod = mock_livekit["module"]
        monkeypatch.setattr(mod, "LIVEKIT_URL", "")
        pub = mod.LiveKitPublisher()
        await pub.start()
        assert pub.is_connected is False

    async def test_start_success(self, publisher, mock_livekit):
        await publisher.start()
        assert publisher.is_connected is True
        mock_livekit["_room_instance"].connect.assert_awaited_once()
        mock_livekit["_room_instance"].local_participant.publish_track.assert_awaited_once()

    async def test_start_connection_failure(self, publisher, mock_livekit):
        mock_livekit["_room_instance"].connect.side_effect = RuntimeError("refused")
        await publisher.start()
        assert publisher.is_connected is False

    async def test_stop_disconnects(self, publisher, mock_livekit):
        await publisher.start()
        assert publisher.is_connected is True
        await publisher.stop()
        mock_livekit["_room_instance"].disconnect.assert_awaited_once()
        assert publisher.is_connected is False

    async def test_stop_without_start(self, publisher):
        await publisher.stop()
        assert publisher.is_connected is False


# -----------------------------------------------------------------------
# Publishing tests
# -----------------------------------------------------------------------

def _make_pcm_bytes(num_samples: int = 160) -> bytes:
    """Generate deterministic PCM16 test data."""
    samples = np.arange(num_samples, dtype=np.int16)
    return samples.tobytes()


class TestPublishing:

    async def test_publish_success(self, publisher, mock_livekit):
        await publisher.start()
        pcm = _make_pcm_bytes()
        await publisher.publish(pcm)
        mock_livekit["_audio_source_instance"].capture_frame.assert_awaited_once()

    async def test_publish_not_connected(self, publisher, mock_livekit):
        pcm = _make_pcm_bytes()
        await publisher.publish(pcm)
        mock_livekit["_audio_source_instance"].capture_frame.assert_not_awaited()

    async def test_publish_correct_format(self, publisher, mock_livekit):
        await publisher.start()
        num_samples = 160
        pcm = _make_pcm_bytes(num_samples)
        await publisher.publish(pcm)

        # Inspect the AudioFrame constructor call
        mock_livekit["_audio_frame"].assert_called_once()
        call_kwargs = mock_livekit["_audio_frame"].call_args
        assert call_kwargs.kwargs["sample_rate"] == 16000
        assert call_kwargs.kwargs["num_channels"] == 1
        assert call_kwargs.kwargs["samples_per_channel"] == num_samples

    async def test_publish_error_handled(self, publisher, mock_livekit):
        await publisher.start()
        mock_livekit["_audio_source_instance"].capture_frame.side_effect = (
            RuntimeError("audio error")
        )
        pcm = _make_pcm_bytes()
        await publisher.publish(pcm)
        # No exception raised — error is logged and swallowed

    async def test_publish_converts_pcm_bytes(self, publisher, mock_livekit):
        await publisher.start()
        original = np.array([100, -200, 300], dtype=np.int16)
        pcm = original.tobytes()
        await publisher.publish(pcm)

        call_kwargs = mock_livekit["_audio_frame"].call_args
        # The data kwarg should be the bytes of the numpy array
        frame_data = call_kwargs.kwargs["data"]
        reconstructed = np.frombuffer(frame_data, dtype=np.int16)
        np.testing.assert_array_equal(reconstructed, original)


# -----------------------------------------------------------------------
# Property tests
# -----------------------------------------------------------------------

class TestProperties:

    async def test_is_connected_default_false(self, publisher):
        assert publisher.is_connected is False

    async def test_is_connected_after_start(self, publisher):
        await publisher.start()
        assert publisher.is_connected is True

    async def test_is_connected_after_stop(self, publisher):
        await publisher.start()
        assert publisher.is_connected is True
        await publisher.stop()
        assert publisher.is_connected is False


# -----------------------------------------------------------------------
# SDK unavailable tests
# -----------------------------------------------------------------------

class TestSDKUnavailable:

    async def test_not_configured_when_sdk_missing(self, mock_livekit, monkeypatch):
        mod = mock_livekit["module"]
        monkeypatch.setattr(mod, "LIVEKIT_SDK_AVAILABLE", False)
        pub = mod.LiveKitPublisher()
        assert pub.is_configured is False

    async def test_start_noop_when_sdk_missing(self, mock_livekit, monkeypatch):
        mod = mock_livekit["module"]
        monkeypatch.setattr(mod, "LIVEKIT_SDK_AVAILABLE", False)
        pub = mod.LiveKitPublisher()
        await pub.start()
        assert pub.is_connected is False

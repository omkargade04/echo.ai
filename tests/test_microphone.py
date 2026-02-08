"""Tests for echo.stt.microphone — microphone audio capture with VAD."""

import asyncio

import numpy as np
import pytest

from echo.stt.microphone import MicrophoneCapture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _silent_frame(n_samples: int = 1600) -> np.ndarray:
    """Return a chunk of silence (all zeros) as int16."""
    return np.zeros((n_samples, 1), dtype=np.int16)


def _loud_frame(n_samples: int = 1600, amplitude: int = 16000) -> np.ndarray:
    """Return a chunk of loud audio (constant amplitude) as int16."""
    return np.full((n_samples, 1), amplitude, dtype=np.int16)


class MockInputStream:
    """Mock sounddevice.InputStream context manager.

    Accepts a list of (ndarray, overflowed) tuples to return from read().
    Once exhausted, returns silence.
    """

    def __init__(self, read_data: list[tuple[np.ndarray, bool]], **kwargs):
        self._read_data = iter(read_data)
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, frames):
        try:
            return next(self._read_data)
        except StopIteration:
            return _silent_frame(frames), False


def _mock_query_devices_success(*args, **kwargs):
    """Simulate a valid input device being present."""
    return {"name": "test-mic", "max_input_channels": 1}


def _mock_query_devices_fail(*args, **kwargs):
    """Simulate no input device available."""
    raise OSError("No input device")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:

    async def test_initial_state(self):
        mic = MicrophoneCapture()
        assert mic.is_available is False
        assert mic.is_listening is False

    async def test_start_detects_input_device(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        mic = MicrophoneCapture()
        await mic.start()
        assert mic.is_available is True

    async def test_start_no_device(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_fail
        )
        mic = MicrophoneCapture()
        await mic.start()
        assert mic.is_available is False

    async def test_stop_clears_state(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        mic = MicrophoneCapture()
        await mic.start()
        assert mic.is_available is True
        await mic.stop()
        assert mic.is_available is False
        assert mic.is_listening is False


# ---------------------------------------------------------------------------
# capture_until_silence
# ---------------------------------------------------------------------------

class TestCaptureUntilSilence:

    async def test_capture_returns_none_when_unavailable(self):
        mic = MicrophoneCapture()
        # _available is False by default
        result = await mic.capture_until_silence()
        assert result is None

    async def test_capture_returns_bytes_on_speech(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        # 3 loud frames then 20 silent frames (exceeds silence_duration)
        chunk_samples = 1600  # 100ms at 16kHz
        read_data = (
            [(_loud_frame(chunk_samples), False) for _ in range(3)]
            + [(_silent_frame(chunk_samples), False) for _ in range(20)]
        )
        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: MockInputStream(read_data, **kwargs),
        )

        mic = MicrophoneCapture()
        await mic.start()
        result = await mic.capture_until_silence(
            silence_duration=0.5, listen_timeout=5.0
        )
        assert result is not None
        assert isinstance(result, bytes)
        assert len(result) > 0

    async def test_capture_returns_none_on_no_speech(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        chunk_samples = 1600
        # All silent — no speech onset detected within timeout
        read_data = [(_silent_frame(chunk_samples), False) for _ in range(400)]
        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: MockInputStream(read_data, **kwargs),
        )

        mic = MicrophoneCapture()
        await mic.start()
        result = await mic.capture_until_silence(listen_timeout=1.0)
        assert result is None

    async def test_capture_sets_listening_flag(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        chunk_samples = 1600
        # Enough frames to keep capture running for a bit
        read_data = (
            [(_loud_frame(chunk_samples), False) for _ in range(5)]
            + [(_silent_frame(chunk_samples), False) for _ in range(20)]
        )
        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: MockInputStream(read_data, **kwargs),
        )

        mic = MicrophoneCapture()
        await mic.start()

        # Before capture
        assert mic.is_listening is False

        result = await mic.capture_until_silence(
            silence_duration=0.5, listen_timeout=5.0
        )

        # After capture completes, listening should be False
        assert mic.is_listening is False
        # And we should have gotten valid audio
        assert result is not None

    async def test_capture_respects_max_duration(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        chunk_samples = 1600
        # Continuous loud audio — never goes silent
        read_data = [(_loud_frame(chunk_samples), False) for _ in range(200)]
        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: MockInputStream(read_data, **kwargs),
        )

        mic = MicrophoneCapture()
        await mic.start()
        result = await mic.capture_until_silence(
            max_duration=0.5, listen_timeout=5.0
        )
        assert result is not None
        assert isinstance(result, bytes)
        # max_duration=0.5s at 16kHz mono int16 = 0.5 * 16000 * 2 = 16000 bytes
        # Allow some tolerance (the first frame is from onset detection)
        assert len(result) <= 20000

    async def test_capture_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )

        def _raise_on_create(**kwargs):
            raise RuntimeError("Simulated device error")

        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream", _raise_on_create
        )

        mic = MicrophoneCapture()
        await mic.start()
        result = await mic.capture_until_silence()
        assert result is None
        # listening flag should be cleared even on error
        assert mic.is_listening is False

    async def test_capture_silence_detection(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )
        chunk_samples = 1600
        # 5 loud frames, then 10 silent frames (1.0s silence > 0.5s threshold)
        read_data = (
            [(_loud_frame(chunk_samples), False) for _ in range(5)]
            + [(_silent_frame(chunk_samples), False) for _ in range(10)]
        )
        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: MockInputStream(read_data, **kwargs),
        )

        mic = MicrophoneCapture()
        await mic.start()
        result = await mic.capture_until_silence(
            silence_duration=0.5, max_duration=10.0, listen_timeout=5.0
        )
        assert result is not None
        # Should have captured the loud frames plus some silent frames
        # but stopped well before max_duration (10s)
        # 5 loud + ~5 silent (to reach 0.5s silence) = ~10 frames * 1600 samples * 2 bytes
        assert len(result) < 50000


# ---------------------------------------------------------------------------
# _compute_rms
# ---------------------------------------------------------------------------

class TestComputeRms:

    def test_compute_rms_silence(self):
        data = np.zeros((1600, 1), dtype=np.int16)
        rms = MicrophoneCapture._compute_rms(data)
        assert rms == 0.0

    def test_compute_rms_loud(self):
        # Max int16 values
        data = np.full((1600, 1), 32767, dtype=np.int16)
        rms = MicrophoneCapture._compute_rms(data)
        # Should be very close to 1.0 (32767/32768 ~ 0.99997)
        assert rms > 0.99
        assert rms <= 1.0


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

class TestConfigIntegration:

    async def test_default_params_from_config(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )

        captured_args: list[tuple] = []
        chunk_samples = 1600

        # One loud frame then silence to trigger capture path
        read_data = (
            [(_loud_frame(chunk_samples), False)]
            + [(_silent_frame(chunk_samples), False) for _ in range(200)]
        )

        class SpyInputStream:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self._iter = iter(read_data)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, frames):
                try:
                    return next(self._iter)
                except StopIteration:
                    return _silent_frame(frames), False

        original_capture_sync = MicrophoneCapture._capture_sync

        def _spy_capture_sync(self_inner, max_dur, sil_thresh, sil_dur, sr, timeout):
            captured_args.append((max_dur, sil_thresh, sil_dur, sr, timeout))
            return original_capture_sync(
                self_inner, max_dur, sil_thresh, sil_dur, sr, timeout
            )

        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: SpyInputStream(**kwargs),
        )
        monkeypatch.setattr(MicrophoneCapture, "_capture_sync", _spy_capture_sync)

        mic = MicrophoneCapture()
        await mic.start()
        await mic.capture_until_silence()

        assert len(captured_args) == 1
        max_dur, sil_thresh, sil_dur, sr, timeout = captured_args[0]

        from echo.config import (
            AUDIO_SAMPLE_RATE as CFG_SR,
            STT_LISTEN_TIMEOUT as CFG_LT,
            STT_MAX_RECORD_DURATION as CFG_MAX,
            STT_SILENCE_DURATION as CFG_SD,
            STT_SILENCE_THRESHOLD as CFG_ST,
        )
        assert max_dur == CFG_MAX
        assert sil_thresh == CFG_ST
        assert sil_dur == CFG_SD
        assert sr == CFG_SR
        assert timeout == CFG_LT

    async def test_custom_params_override(self, monkeypatch):
        monkeypatch.setattr(
            "echo.stt.microphone.sd.query_devices", _mock_query_devices_success
        )

        captured_args: list[tuple] = []
        chunk_samples = 1600

        read_data = (
            [(_loud_frame(chunk_samples), False)]
            + [(_silent_frame(chunk_samples), False) for _ in range(200)]
        )

        original_capture_sync = MicrophoneCapture._capture_sync

        def _spy_capture_sync(self_inner, max_dur, sil_thresh, sil_dur, sr, timeout):
            captured_args.append((max_dur, sil_thresh, sil_dur, sr, timeout))
            return original_capture_sync(
                self_inner, max_dur, sil_thresh, sil_dur, sr, timeout
            )

        monkeypatch.setattr(
            "echo.stt.microphone.sd.InputStream",
            lambda **kwargs: MockInputStream(read_data, **kwargs),
        )
        monkeypatch.setattr(MicrophoneCapture, "_capture_sync", _spy_capture_sync)

        mic = MicrophoneCapture()
        await mic.start()
        await mic.capture_until_silence(
            max_duration=5.0,
            silence_threshold=0.05,
            silence_duration=2.0,
            sample_rate=8000,
            listen_timeout=10.0,
        )

        assert len(captured_args) == 1
        max_dur, sil_thresh, sil_dur, sr, timeout = captured_args[0]
        assert max_dur == 5.0
        assert sil_thresh == 0.05
        assert sil_dur == 2.0
        assert sr == 8000
        assert timeout == 10.0

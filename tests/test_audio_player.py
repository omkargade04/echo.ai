"""Tests for echo.tts.audio_player — priority-queued audio player."""

import asyncio

import numpy as np
import pytest

from echo.events.types import BlockReason
from echo.tts.audio_player import AudioPlayer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pcm_bytes(n_samples: int = 160) -> bytes:
    """Generate trivial int16 PCM bytes for testing."""
    return np.zeros(n_samples, dtype=np.int16).tobytes()


def _mock_query_devices_success(*args, **kwargs):
    """Simulate a valid output device being present."""
    return {"name": "test-speaker", "max_output_channels": 2}


def _mock_query_devices_fail(*args, **kwargs):
    """Simulate no output device available."""
    raise OSError("No output device")


def _noop(*args, **kwargs):
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _patch_sd(monkeypatch):
    """Patch all sounddevice calls to no-ops (with successful device query)."""
    monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
    monkeypatch.setattr("echo.tts.audio_player.sd.play", _noop)
    monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
    monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)


@pytest.fixture
def _patch_sd_no_device(monkeypatch):
    """Patch sounddevice to simulate no output device."""
    monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_fail)
    monkeypatch.setattr("echo.tts.audio_player.sd.play", _noop)
    monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
    monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

class TestStartup:

    @pytest.mark.usefixtures("_patch_sd")
    async def test_start_with_audio_device(self):
        player = AudioPlayer()
        await player.start()
        assert player.is_available is True
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd_no_device")
    async def test_start_no_audio_device(self):
        player = AudioPlayer()
        await player.start()
        assert player.is_available is False

    @pytest.mark.usefixtures("_patch_sd")
    async def test_stop_cancels_worker(self):
        player = AudioPlayer()
        await player.start()
        assert player._worker_task is not None
        task = player._worker_task
        await player.stop()
        assert task.cancelled() or task.done()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_stop_without_start(self):
        player = AudioPlayer()
        await player.stop()  # should not raise

    @pytest.mark.usefixtures("_patch_sd")
    async def test_alert_tone_cached_on_start(self):
        player = AudioPlayer()
        await player.start()
        assert len(player._alert_tones) > 0
        for tone_bytes in player._alert_tones.values():
            assert isinstance(tone_bytes, bytes)
            assert len(tone_bytes) > 0
        await player.stop()


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:

    @pytest.mark.usefixtures("_patch_sd")
    async def test_enqueue_normal(self):
        player = AudioPlayer()
        await player.start()
        await player.enqueue(_pcm_bytes(), priority=1)
        assert player.queue_depth == 1
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd_no_device")
    async def test_enqueue_when_not_available(self):
        player = AudioPlayer()
        await player.start()
        await player.enqueue(_pcm_bytes(), priority=1)
        assert player.queue_depth == 0

    @pytest.mark.usefixtures("_patch_sd")
    async def test_enqueue_low_within_threshold(self):
        player = AudioPlayer()
        await player.start()
        # Queue is empty (0) which is <= threshold (3), so LOW should be accepted
        await player.enqueue(_pcm_bytes(), priority=2)
        assert player.queue_depth == 1
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_enqueue_low_over_threshold(self, monkeypatch):
        monkeypatch.setattr("echo.tts.audio_player.AUDIO_BACKLOG_THRESHOLD", 2)
        player = AudioPlayer()
        await player.start()
        # Fill queue above threshold with NORMAL items
        for _ in range(3):
            await player.enqueue(_pcm_bytes(), priority=1)
        assert player.queue_depth == 3
        # LOW should be dropped since queue_depth (3) > threshold (2)
        await player.enqueue(_pcm_bytes(), priority=2)
        assert player.queue_depth == 3
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_enqueue_critical_always(self, monkeypatch):
        monkeypatch.setattr("echo.tts.audio_player.AUDIO_BACKLOG_THRESHOLD", 0)
        player = AudioPlayer()
        await player.start()
        # Fill some items so queue > threshold
        await player.enqueue(_pcm_bytes(), priority=1)
        # CRITICAL should always be enqueued
        await player.enqueue(_pcm_bytes(), priority=0)
        assert player.queue_depth == 2
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_queue_ordering(self):
        player = AudioPlayer()
        player._audio_available = True  # bypass start to avoid worker consuming
        # Enqueue NORMAL first, then CRITICAL
        await player.enqueue(_pcm_bytes(100), priority=1)
        await player.enqueue(_pcm_bytes(200), priority=0)
        # CRITICAL (priority 0) should come out first
        item = player._queue.get_nowait()
        assert item[0] == 0  # priority
        item2 = player._queue.get_nowait()
        assert item2[0] == 1


# ---------------------------------------------------------------------------
# Interrupt
# ---------------------------------------------------------------------------

class TestInterrupt:

    @pytest.mark.usefixtures("_patch_sd")
    async def test_interrupt_sets_event(self):
        player = AudioPlayer()
        player._audio_available = True
        assert not player._interrupt_event.is_set()
        await player.interrupt()
        assert player._interrupt_event.is_set()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_interrupt_drains_non_critical(self):
        player = AudioPlayer()
        player._audio_available = True
        # Add mixed-priority items
        await player.enqueue(_pcm_bytes(), priority=0)  # CRITICAL
        await player.enqueue(_pcm_bytes(), priority=1)  # NORMAL
        await player.enqueue(_pcm_bytes(), priority=2)  # LOW
        assert player.queue_depth == 3
        await player.interrupt()
        # Only CRITICAL should remain
        assert player.queue_depth == 1
        remaining = player._queue.get_nowait()
        assert remaining[0] == 0

    @pytest.mark.usefixtures("_patch_sd")
    async def test_interrupt_drains_all_when_no_critical(self):
        player = AudioPlayer()
        player._audio_available = True
        await player.enqueue(_pcm_bytes(), priority=1)
        await player.enqueue(_pcm_bytes(), priority=2)
        assert player.queue_depth == 2
        await player.interrupt()
        assert player.queue_depth == 0

    async def test_interrupt_calls_sd_stop(self, monkeypatch):
        calls = []
        monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
        monkeypatch.setattr("echo.tts.audio_player.sd.play", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.stop", lambda: calls.append("stop"))
        player = AudioPlayer()
        player._audio_available = True
        await player.interrupt()
        assert "stop" in calls

    @pytest.mark.usefixtures("_patch_sd")
    async def test_interrupt_on_empty_queue(self):
        player = AudioPlayer()
        player._audio_available = True
        await player.interrupt()  # should not raise
        assert player._interrupt_event.is_set()
        assert player.queue_depth == 0


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

class TestPlayback:

    async def test_play_immediate_calls_sd(self, monkeypatch):
        play_calls = []
        monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
        monkeypatch.setattr(
            "echo.tts.audio_player.sd.play",
            lambda data, samplerate: play_calls.append((data, samplerate)),
        )
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)

        player = AudioPlayer()
        player._audio_available = True
        pcm = _pcm_bytes(80)
        await player.play_immediate(pcm)
        assert len(play_calls) == 1
        played_data, sr = play_calls[0]
        assert played_data.dtype == np.float32
        assert sr == 16000

    @pytest.mark.usefixtures("_patch_sd_no_device")
    async def test_play_immediate_not_available(self):
        player = AudioPlayer()
        await player.start()
        # Should be a no-op, not raise
        await player.play_immediate(_pcm_bytes())

    async def test_play_alert_plays_tone(self, monkeypatch):
        play_calls = []
        monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
        monkeypatch.setattr(
            "echo.tts.audio_player.sd.play",
            lambda data, samplerate: play_calls.append((data, samplerate)),
        )
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)

        player = AudioPlayer()
        await player.start()
        await player.play_alert()
        assert len(play_calls) >= 1
        played_data, _ = play_calls[-1]
        assert played_data.dtype == np.float32
        assert len(played_data) > 0
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd_no_device")
    async def test_play_alert_not_available(self):
        player = AudioPlayer()
        await player.start()
        await player.play_alert()  # should not raise

    async def test_play_sync_converts_bytes_to_float(self, monkeypatch):
        play_calls = []
        monkeypatch.setattr(
            "echo.tts.audio_player.sd.play",
            lambda data, samplerate: play_calls.append((data.copy(), samplerate)),
        )
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)

        player = AudioPlayer()
        # Create known int16 samples
        samples = np.array([0, 16384, -16384, 32767, -32768], dtype=np.int16)
        pcm = samples.tobytes()
        player._play_sync(pcm)

        assert len(play_calls) == 1
        played_data, _ = play_calls[0]
        assert played_data.dtype == np.float32
        # Verify conversion: int16 / 32768.0
        np.testing.assert_allclose(played_data[0], 0.0, atol=1e-6)
        np.testing.assert_allclose(played_data[1], 16384.0 / 32768.0, atol=1e-4)
        np.testing.assert_allclose(played_data[2], -16384.0 / 32768.0, atol=1e-4)

    @pytest.mark.usefixtures("_patch_sd")
    async def test_worker_processes_queue(self):
        player = AudioPlayer()
        await player.start()
        assert player.queue_depth == 0
        await player.enqueue(_pcm_bytes(), priority=1)
        # Give the worker time to pick up and process the item
        await asyncio.sleep(0.1)
        assert player.queue_depth == 0
        await player.stop()


# ---------------------------------------------------------------------------
# Per-reason alert tones
# ---------------------------------------------------------------------------

class TestPerReasonAlertTones:

    async def test_play_alert_permission_prompt(self, monkeypatch):
        play_calls = []
        monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
        monkeypatch.setattr(
            "echo.tts.audio_player.sd.play",
            lambda data, samplerate: play_calls.append((data, samplerate)),
        )
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)

        player = AudioPlayer()
        await player.start()
        await player.play_alert(BlockReason.PERMISSION_PROMPT)
        assert len(play_calls) == 1
        played_data, sr = play_calls[0]
        assert played_data.dtype == np.float32
        assert len(played_data) > 0
        assert sr == 16000
        await player.stop()

    async def test_play_alert_question(self, monkeypatch):
        play_calls = []
        monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
        monkeypatch.setattr(
            "echo.tts.audio_player.sd.play",
            lambda data, samplerate: play_calls.append((data, samplerate)),
        )
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)

        player = AudioPlayer()
        await player.start()
        await player.play_alert(BlockReason.QUESTION)
        assert len(play_calls) == 1
        played_data, _ = play_calls[0]
        assert played_data.dtype == np.float32
        assert len(played_data) > 0
        await player.stop()

    async def test_play_alert_none_default(self, monkeypatch):
        play_calls = []
        monkeypatch.setattr("echo.tts.audio_player.sd.query_devices", _mock_query_devices_success)
        monkeypatch.setattr(
            "echo.tts.audio_player.sd.play",
            lambda data, samplerate: play_calls.append((data, samplerate)),
        )
        monkeypatch.setattr("echo.tts.audio_player.sd.wait", _noop)
        monkeypatch.setattr("echo.tts.audio_player.sd.stop", _noop)

        player = AudioPlayer()
        await player.start()
        # Call with no arguments — should use default (None) tone
        await player.play_alert()
        assert len(play_calls) == 1
        played_data, _ = play_calls[0]
        assert played_data.dtype == np.float32
        assert len(played_data) > 0
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_play_alert_different_reasons_different_bytes(self):
        player = AudioPlayer()
        await player.start()
        # Permission tone is longer (~0.60s) than question tone (~0.35s)
        perm_bytes = player._alert_tones[BlockReason.PERMISSION_PROMPT]
        question_bytes = player._alert_tones[BlockReason.QUESTION]
        default_bytes = player._alert_tones[None]
        idle_bytes = player._alert_tones[BlockReason.IDLE_PROMPT]
        # Permission is the longest (7 segments ~0.60s vs 3 segments ~0.35s)
        assert len(perm_bytes) > len(question_bytes)
        # Each reason produces distinct byte content
        assert perm_bytes != question_bytes
        assert perm_bytes != default_bytes
        assert question_bytes != idle_bytes
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_alert_tones_cached_at_startup(self):
        player = AudioPlayer()
        await player.start()
        assert len(player._alert_tones) == 4
        assert None in player._alert_tones
        assert BlockReason.PERMISSION_PROMPT in player._alert_tones
        assert BlockReason.QUESTION in player._alert_tones
        assert BlockReason.IDLE_PROMPT in player._alert_tones
        await player.stop()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestProperties:

    async def test_is_available_default_false(self):
        player = AudioPlayer()
        assert player.is_available is False

    async def test_queue_depth_starts_zero(self):
        player = AudioPlayer()
        assert player.queue_depth == 0

    @pytest.mark.usefixtures("_patch_sd")
    async def test_queue_depth_after_enqueue(self):
        player = AudioPlayer()
        player._audio_available = True
        await player.enqueue(_pcm_bytes(), priority=1)
        assert player.queue_depth == 1
        await player.enqueue(_pcm_bytes(), priority=0)
        assert player.queue_depth == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    @pytest.mark.usefixtures("_patch_sd")
    async def test_enqueue_after_stop(self):
        player = AudioPlayer()
        await player.start()
        await player.stop()
        await player.enqueue(_pcm_bytes(), priority=1)
        assert player.queue_depth == 0

    @pytest.mark.usefixtures("_patch_sd")
    async def test_double_start(self):
        player = AudioPlayer()
        await player.start()
        # Second start should not crash (creates another worker)
        await player.start()
        assert player.is_available is True
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_double_stop(self):
        player = AudioPlayer()
        await player.start()
        await player.stop()
        await player.stop()  # should not raise

    @pytest.mark.usefixtures("_patch_sd")
    async def test_worker_skips_non_critical_during_interrupt(self):
        player = AudioPlayer()
        await player.start()
        # Set interrupt before enqueue
        player._interrupt_event.set()
        await player.enqueue(_pcm_bytes(), priority=1)
        # Give worker time to process
        await asyncio.sleep(0.15)
        # The NORMAL item should have been discarded by the worker
        assert player.queue_depth == 0
        await player.stop()

    @pytest.mark.usefixtures("_patch_sd")
    async def test_sequence_counter_increments(self):
        player = AudioPlayer()
        player._audio_available = True
        assert player._sequence == 0
        await player.enqueue(_pcm_bytes(), priority=1)
        assert player._sequence == 1
        await player.enqueue(_pcm_bytes(), priority=1)
        assert player._sequence == 2

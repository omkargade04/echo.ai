"""Tests for echo.tts.alert_tone — alert tone generation."""

import numpy as np
import pytest

from echo.tts.alert_tone import (
    SILENCE_DURATION,
    TONE_DURATION,
    generate_alert_tone,
    generate_alert_tone_pcm16,
)


# Expected total duration: tone + silence + tone
EXPECTED_DURATION = TONE_DURATION + SILENCE_DURATION + TONE_DURATION  # 0.350s
DEFAULT_SAMPLE_RATE = 16000


class TestGenerateAlertTone:

    def test_returns_numpy_array(self):
        result = generate_alert_tone()
        assert isinstance(result, np.ndarray)

    def test_dtype_float32(self):
        result = generate_alert_tone()
        assert result.dtype == np.float32

    def test_shape_1d(self):
        result = generate_alert_tone()
        assert result.ndim == 1

    def test_correct_duration(self):
        result = generate_alert_tone()
        expected_samples = int(EXPECTED_DURATION * DEFAULT_SAMPLE_RATE)
        # Within 1% tolerance
        assert abs(len(result) - expected_samples) / expected_samples < 0.01

    def test_amplitude_range(self):
        result = generate_alert_tone()
        assert np.all(result >= -1.0)
        assert np.all(result <= 1.0)

    def test_not_all_zeros(self):
        result = generate_alert_tone()
        assert np.any(result != 0.0)

    def test_has_silence_gap(self):
        result = generate_alert_tone()
        sr = DEFAULT_SAMPLE_RATE
        # The silence gap starts after tone_1 (150ms) and lasts 50ms.
        # Check the middle of the silence region.
        silence_start = int(TONE_DURATION * sr)
        silence_end = silence_start + int(SILENCE_DURATION * sr)
        silence_section = result[silence_start:silence_end]
        assert np.allclose(silence_section, 0.0, atol=1e-6)

    def test_custom_sample_rate(self):
        sr = 44100
        result = generate_alert_tone(sample_rate=sr)
        expected_samples = int(EXPECTED_DURATION * sr)
        assert abs(len(result) - expected_samples) / expected_samples < 0.01
        assert result.dtype == np.float32


class TestGenerateAlertTonePCM16:

    def test_pcm16_returns_bytes(self):
        result = generate_alert_tone_pcm16()
        assert isinstance(result, bytes)

    def test_pcm16_correct_length(self):
        float_samples = generate_alert_tone()
        pcm_bytes = generate_alert_tone_pcm16()
        # int16 = 2 bytes per sample
        assert len(pcm_bytes) == 2 * len(float_samples)

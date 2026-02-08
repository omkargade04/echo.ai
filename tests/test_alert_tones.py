"""Tests for echo.tts.alert_tones — per-block-reason alert tone generation."""

import numpy as np
import pytest

from echo.events.types import BlockReason
from echo.tts.alert_tones import (
    generate_alert_for_reason,
    generate_alert_for_reason_pcm16,
)


class TestGenerateAlertForReason:
    """Tests for per-block-reason alert tone generation."""

    def test_permission_returns_float32_array(self):
        tone = generate_alert_for_reason(BlockReason.PERMISSION_PROMPT)
        assert tone.dtype == np.float32

    def test_question_returns_float32_array(self):
        tone = generate_alert_for_reason(BlockReason.QUESTION)
        assert tone.dtype == np.float32

    def test_idle_returns_float32_array(self):
        tone = generate_alert_for_reason(BlockReason.IDLE_PROMPT)
        assert tone.dtype == np.float32

    def test_none_returns_default_tone(self):
        tone = generate_alert_for_reason(None)
        assert tone.dtype == np.float32

    def test_permission_is_longer_than_question(self):
        # Permission is more urgent — double-beep is longer
        perm = generate_alert_for_reason(BlockReason.PERMISSION_PROMPT)
        question = generate_alert_for_reason(BlockReason.QUESTION)
        assert len(perm) > len(question)

    def test_all_tones_amplitude_in_range(self):
        for reason in [BlockReason.PERMISSION_PROMPT, BlockReason.QUESTION, BlockReason.IDLE_PROMPT, None]:
            tone = generate_alert_for_reason(reason)
            assert tone.max() <= 1.0
            assert tone.min() >= -1.0

    def test_all_tones_are_1d(self):
        for reason in [BlockReason.PERMISSION_PROMPT, BlockReason.QUESTION, BlockReason.IDLE_PROMPT, None]:
            assert generate_alert_for_reason(reason).ndim == 1

    def test_all_tones_are_nonempty(self):
        for reason in [BlockReason.PERMISSION_PROMPT, BlockReason.QUESTION, BlockReason.IDLE_PROMPT, None]:
            assert len(generate_alert_for_reason(reason)) > 0

    def test_different_reasons_produce_different_lengths(self):
        lengths = set()
        for reason in [BlockReason.PERMISSION_PROMPT, BlockReason.QUESTION, BlockReason.IDLE_PROMPT]:
            lengths.add(len(generate_alert_for_reason(reason)))
        assert len(lengths) == 3  # All different

    def test_custom_sample_rate(self):
        tone_8k = generate_alert_for_reason(BlockReason.QUESTION, sample_rate=8000)
        tone_16k = generate_alert_for_reason(BlockReason.QUESTION, sample_rate=16000)
        # 16kHz should be ~2x the samples of 8kHz
        assert abs(len(tone_16k) / len(tone_8k) - 2.0) < 0.1

    def test_permission_contains_silence_gaps(self):
        # The permission tone has silence between beeps
        tone = generate_alert_for_reason(BlockReason.PERMISSION_PROMPT)
        # Should have some zeros (silence sections)
        assert np.any(tone == 0.0)

    def test_none_matches_default_tone_length(self):
        # None uses _DEFAULT_TONES which should be consistent
        tone1 = generate_alert_for_reason(None)
        tone2 = generate_alert_for_reason(None)
        assert len(tone1) == len(tone2)


class TestGenerateAlertForReasonPcm16:

    def test_returns_bytes(self):
        result = generate_alert_for_reason_pcm16(BlockReason.QUESTION)
        assert isinstance(result, bytes)

    def test_length_is_even(self):
        # PCM16 = 2 bytes per sample
        result = generate_alert_for_reason_pcm16(BlockReason.QUESTION)
        assert len(result) % 2 == 0

    def test_consistent_with_float_version(self):
        tone = generate_alert_for_reason(BlockReason.QUESTION)
        pcm = generate_alert_for_reason_pcm16(BlockReason.QUESTION)
        assert len(pcm) == len(tone) * 2  # 2 bytes per sample

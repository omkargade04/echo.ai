"""Alert tone generation for CRITICAL narration events."""

import numpy as np

# Tone frequencies (Hz)
TONE_1_FREQ = 880
TONE_2_FREQ = 1320

# Durations (seconds)
TONE_DURATION = 0.150
SILENCE_DURATION = 0.050
FADE_DURATION = 0.005


def _apply_fade(samples: np.ndarray, fade_samples: int) -> np.ndarray:
    """Apply linear fade-in and fade-out to a tone segment."""
    if fade_samples <= 0 or len(samples) < 2 * fade_samples:
        return samples
    result = samples.copy()
    fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    result[:fade_samples] *= fade_in
    result[-fade_samples:] *= fade_out
    return result


def _generate_sine(freq: float, duration: float, sample_rate: int) -> np.ndarray:
    """Generate a sine wave at the given frequency with fade applied."""
    num_samples = int(duration * sample_rate)
    t = np.arange(num_samples, dtype=np.float32) / sample_rate
    tone = np.sin(2.0 * np.pi * freq * t).astype(np.float32)
    fade_samples = int(FADE_DURATION * sample_rate)
    return _apply_fade(tone, fade_samples)


def generate_alert_tone(sample_rate: int = 16000) -> np.ndarray:
    """Generate a two-tone alert as a float32 numpy array in [-1.0, 1.0].

    Structure: 880 Hz for 150ms, 50ms silence, 1320 Hz for 150ms.
    """
    tone_1 = _generate_sine(TONE_1_FREQ, TONE_DURATION, sample_rate)
    silence = np.zeros(int(SILENCE_DURATION * sample_rate), dtype=np.float32)
    tone_2 = _generate_sine(TONE_2_FREQ, TONE_DURATION, sample_rate)
    return np.concatenate([tone_1, silence, tone_2])


def generate_alert_tone_pcm16(sample_rate: int = 16000) -> bytes:
    """Generate the alert tone as raw int16 PCM bytes."""
    float_samples = generate_alert_tone(sample_rate)
    int_samples = np.clip(float_samples * 32767, -32768, 32767).astype(np.int16)
    return int_samples.tobytes()

"""Differentiated alert tones for blocked-event types.

Each BlockReason gets a distinct audio signature so the developer can
distinguish permission requests from questions from idle prompts by
ear alone.
"""

import numpy as np

from echo.events.types import BlockReason
from echo.tts.alert_tone import apply_fade, generate_sine

# Tone specs: list of (frequency_hz, duration_sec) tuples.
# frequency=0 means silence.
_PERMISSION_TONES = [
    (880, 0.12), (0, 0.04), (1320, 0.12), (0, 0.04),
    (880, 0.12), (0, 0.04), (1320, 0.12),
]  # Urgent double-beep ~0.60s

_QUESTION_TONES = [
    (660, 0.15), (0, 0.05), (880, 0.15),
]  # Rising two-tone ~0.35s

_IDLE_TONES = [
    (440, 0.20), (0, 0.05), (550, 0.15),
]  # Gentle low tone ~0.40s

_DEFAULT_TONES = [
    (880, 0.15), (0, 0.05), (1320, 0.15),
]  # Standard alert ~0.35s (matches original)

_TONE_MAP: dict[BlockReason | None, list[tuple[int, float]]] = {
    BlockReason.PERMISSION_PROMPT: _PERMISSION_TONES,
    BlockReason.QUESTION: _QUESTION_TONES,
    BlockReason.IDLE_PROMPT: _IDLE_TONES,
    None: _DEFAULT_TONES,
}

FADE_DURATION = 0.005  # 5ms fade matching alert_tone.py


def generate_alert_for_reason(
    block_reason: BlockReason | None,
    sample_rate: int = 16000,
) -> np.ndarray:
    """Generate an alert tone specific to the given block reason.

    Returns a float32 numpy array with amplitude in [-1.0, 1.0].
    """
    tones = _TONE_MAP.get(block_reason, _DEFAULT_TONES)
    segments: list[np.ndarray] = []

    for freq, duration in tones:
        n_samples = int(duration * sample_rate)
        if freq == 0:
            segments.append(np.zeros(n_samples, dtype=np.float32))
        else:
            seg = generate_sine(freq, duration, sample_rate)
            seg = apply_fade(seg, FADE_DURATION, sample_rate)
            segments.append(seg)

    return np.concatenate(segments)


def generate_alert_for_reason_pcm16(
    block_reason: BlockReason | None,
    sample_rate: int = 16000,
) -> bytes:
    """Generate alert tone as PCM 16-bit signed little-endian bytes."""
    tone = generate_alert_for_reason(block_reason, sample_rate)
    pcm16 = np.clip(tone * 32767, -32768, 32767).astype(np.int16)
    return pcm16.tobytes()

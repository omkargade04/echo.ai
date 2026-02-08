"""Microphone audio capture with energy-based voice activity detection."""

import asyncio
import logging

import numpy as np
import sounddevice as sd

from echo.config import (
    AUDIO_SAMPLE_RATE,
    STT_LISTEN_TIMEOUT,
    STT_MAX_RECORD_DURATION,
    STT_SILENCE_DURATION,
    STT_SILENCE_THRESHOLD,
)

logger = logging.getLogger(__name__)


class MicrophoneCapture:
    """Captures audio from the default input device using sounddevice.

    Follows the same lifecycle pattern as AudioPlayer:
    probe for device at start, graceful degradation if no mic.
    """

    def __init__(self) -> None:
        self._available: bool = False
        self._listening: bool = False

    async def start(self) -> None:
        """Probe for input device. No-op if unavailable."""
        try:
            sd.query_devices(kind="input")
            self._available = True
            logger.info("Microphone input device detected — capture enabled")
        except Exception:
            self._available = False
            logger.warning("No microphone input device — capture disabled")

    async def stop(self) -> None:
        """Release resources."""
        self._listening = False
        self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def is_listening(self) -> bool:
        return self._listening

    async def capture_until_silence(
        self,
        *,
        max_duration: float | None = None,
        silence_threshold: float | None = None,
        silence_duration: float | None = None,
        sample_rate: int | None = None,
        listen_timeout: float | None = None,
    ) -> bytes | None:
        """Record audio until silence detected or max_duration reached.

        Returns PCM 16-bit mono bytes, or None if:
        - Microphone not available
        - No speech detected within listen_timeout
        - Any error occurs

        Uses energy-based VAD (RMS amplitude threshold) to detect speech
        start and end. Runs blocking InputStream in a thread via asyncio.to_thread.
        """
        if not self._available:
            return None

        max_dur = max_duration or STT_MAX_RECORD_DURATION
        sil_thresh = silence_threshold or STT_SILENCE_THRESHOLD
        sil_dur = silence_duration or STT_SILENCE_DURATION
        sr = sample_rate or AUDIO_SAMPLE_RATE
        timeout = listen_timeout or STT_LISTEN_TIMEOUT

        self._listening = True
        try:
            result = await asyncio.to_thread(
                self._capture_sync, max_dur, sil_thresh, sil_dur, sr, timeout
            )
            return result
        except Exception:
            logger.warning("Microphone capture failed", exc_info=True)
            return None
        finally:
            self._listening = False

    def _capture_sync(
        self,
        max_duration: float,
        silence_threshold: float,
        silence_duration: float,
        sample_rate: int,
        listen_timeout: float,
    ) -> bytes | None:
        """Synchronous capture — runs in a worker thread.

        Logic:
        1. Open InputStream(samplerate, channels=1, dtype='int16')
        2. Wait for speech onset (RMS > threshold) — up to listen_timeout
        3. If no speech: return None
        4. Record audio frames into buffer
        5. Monitor for silence (RMS < threshold for silence_duration seconds)
        6. Stop recording, return concatenated PCM bytes
        """
        frames: list[np.ndarray] = []
        chunk_duration = 0.1  # 100ms chunks
        chunk_samples = int(sample_rate * chunk_duration)
        speech_started = False
        silence_elapsed = 0.0
        total_elapsed = 0.0
        wait_elapsed = 0.0

        try:
            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=chunk_samples,
            ) as stream:
                # Phase 1: Wait for speech onset
                while wait_elapsed < listen_timeout:
                    data, overflowed = stream.read(chunk_samples)
                    rms = self._compute_rms(data)
                    wait_elapsed += chunk_duration

                    if rms > silence_threshold:
                        speech_started = True
                        frames.append(data.copy())
                        total_elapsed += chunk_duration
                        break

                if not speech_started:
                    return None

                # Phase 2: Record until silence or max duration
                while total_elapsed < max_duration:
                    data, overflowed = stream.read(chunk_samples)
                    frames.append(data.copy())
                    total_elapsed += chunk_duration

                    rms = self._compute_rms(data)
                    if rms < silence_threshold:
                        silence_elapsed += chunk_duration
                        if silence_elapsed >= silence_duration:
                            break
                    else:
                        silence_elapsed = 0.0

        except Exception:
            logger.warning("Microphone stream error", exc_info=True)
            if not frames:
                return None

        if not frames:
            return None

        audio = np.concatenate(frames)
        return audio.tobytes()

    @staticmethod
    def _compute_rms(data: np.ndarray) -> float:
        """Compute RMS amplitude of int16 audio data, normalized to 0.0-1.0."""
        float_data = data.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(float_data ** 2)))

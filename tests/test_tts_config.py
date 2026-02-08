"""Tests for TTS-related configuration constants in echo.config."""

import importlib


def _reload_config():
    """Force-reload echo.config so env-var changes take effect."""
    import echo.config

    return importlib.reload(echo.config)


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


class TestTTSConfigDefaults:
    """Verify that every TTS config constant exists with the correct default."""

    def test_elevenlabs_api_key_default(self):
        cfg = _reload_config()
        assert cfg.ELEVENLABS_API_KEY == ""

    def test_elevenlabs_base_url_default(self):
        cfg = _reload_config()
        assert cfg.ELEVENLABS_BASE_URL == "https://api.elevenlabs.io"

    def test_tts_voice_id_default(self):
        cfg = _reload_config()
        assert cfg.TTS_VOICE_ID == "21m00Tcm4TlvDq8ikWAM"

    def test_tts_model_default(self):
        cfg = _reload_config()
        assert cfg.TTS_MODEL == "eleven_turbo_v2_5"

    def test_tts_timeout_default(self):
        cfg = _reload_config()
        assert cfg.TTS_TIMEOUT == 10.0

    def test_tts_health_check_interval_default(self):
        cfg = _reload_config()
        assert cfg.TTS_HEALTH_CHECK_INTERVAL == 60.0

    def test_livekit_url_default(self):
        cfg = _reload_config()
        assert cfg.LIVEKIT_URL == ""

    def test_livekit_api_key_default(self):
        cfg = _reload_config()
        assert cfg.LIVEKIT_API_KEY == ""

    def test_livekit_api_secret_default(self):
        cfg = _reload_config()
        assert cfg.LIVEKIT_API_SECRET == ""

    def test_audio_sample_rate_default(self):
        cfg = _reload_config()
        assert cfg.AUDIO_SAMPLE_RATE == 16000

    def test_audio_backlog_threshold_default(self):
        cfg = _reload_config()
        assert cfg.AUDIO_BACKLOG_THRESHOLD == 3


# ---------------------------------------------------------------------------
# Type checks
# ---------------------------------------------------------------------------


class TestTTSConfigTypes:
    """Verify that config constants have the expected Python type."""

    def test_string_constants_are_strings(self):
        cfg = _reload_config()
        assert isinstance(cfg.ELEVENLABS_API_KEY, str)
        assert isinstance(cfg.ELEVENLABS_BASE_URL, str)
        assert isinstance(cfg.TTS_VOICE_ID, str)
        assert isinstance(cfg.TTS_MODEL, str)
        assert isinstance(cfg.LIVEKIT_URL, str)
        assert isinstance(cfg.LIVEKIT_API_KEY, str)
        assert isinstance(cfg.LIVEKIT_API_SECRET, str)

    def test_float_constants_are_floats(self):
        cfg = _reload_config()
        assert isinstance(cfg.TTS_TIMEOUT, float)
        assert isinstance(cfg.TTS_HEALTH_CHECK_INTERVAL, float)

    def test_int_constants_are_ints(self):
        cfg = _reload_config()
        assert isinstance(cfg.AUDIO_SAMPLE_RATE, int)
        assert isinstance(cfg.AUDIO_BACKLOG_THRESHOLD, int)


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


class TestTTSConfigEnvOverrides:
    """Verify that env vars correctly override defaults."""

    def test_elevenlabs_api_key_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_ELEVENLABS_API_KEY", "sk-test-key-123")
        cfg = _reload_config()
        assert cfg.ELEVENLABS_API_KEY == "sk-test-key-123"

    def test_elevenlabs_base_url_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_ELEVENLABS_BASE_URL", "https://custom.api.com")
        cfg = _reload_config()
        assert cfg.ELEVENLABS_BASE_URL == "https://custom.api.com"

    def test_tts_voice_id_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_TTS_VOICE_ID", "custom-voice-id")
        cfg = _reload_config()
        assert cfg.TTS_VOICE_ID == "custom-voice-id"

    def test_tts_model_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_TTS_MODEL", "eleven_multilingual_v2")
        cfg = _reload_config()
        assert cfg.TTS_MODEL == "eleven_multilingual_v2"

    def test_tts_timeout_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_TTS_TIMEOUT", "30.0")
        cfg = _reload_config()
        assert cfg.TTS_TIMEOUT == 30.0

    def test_tts_health_check_interval_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_TTS_HEALTH_CHECK_INTERVAL", "120.0")
        cfg = _reload_config()
        assert cfg.TTS_HEALTH_CHECK_INTERVAL == 120.0

    def test_livekit_url_override(self, monkeypatch):
        monkeypatch.setenv("LIVEKIT_URL", "wss://my-project.livekit.cloud")
        cfg = _reload_config()
        assert cfg.LIVEKIT_URL == "wss://my-project.livekit.cloud"

    def test_livekit_api_key_override(self, monkeypatch):
        monkeypatch.setenv("LIVEKIT_API_KEY", "APIxyz")
        cfg = _reload_config()
        assert cfg.LIVEKIT_API_KEY == "APIxyz"

    def test_livekit_api_secret_override(self, monkeypatch):
        monkeypatch.setenv("LIVEKIT_API_SECRET", "secret-val")
        cfg = _reload_config()
        assert cfg.LIVEKIT_API_SECRET == "secret-val"

    def test_audio_sample_rate_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_AUDIO_SAMPLE_RATE", "44100")
        cfg = _reload_config()
        assert cfg.AUDIO_SAMPLE_RATE == 44100

    def test_audio_backlog_threshold_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_AUDIO_BACKLOG_THRESHOLD", "5")
        cfg = _reload_config()
        assert cfg.AUDIO_BACKLOG_THRESHOLD == 5

    def test_alert_repeat_interval_from_env(self, monkeypatch):
        monkeypatch.setenv("ECHO_ALERT_REPEAT_INTERVAL", "15.0")
        cfg = _reload_config()
        assert cfg.ALERT_REPEAT_INTERVAL == 15.0

    def test_alert_max_repeats_from_env(self, monkeypatch):
        monkeypatch.setenv("ECHO_ALERT_MAX_REPEATS", "10")
        cfg = _reload_config()
        assert cfg.ALERT_MAX_REPEATS == 10


# ---------------------------------------------------------------------------
# Alert configuration defaults
# ---------------------------------------------------------------------------


class TestAlertConfigDefaults:
    """Verify that alert config constants exist with the correct defaults."""

    def test_alert_repeat_interval_default(self):
        cfg = _reload_config()
        assert cfg.ALERT_REPEAT_INTERVAL == 30.0

    def test_alert_max_repeats_default(self):
        cfg = _reload_config()
        assert cfg.ALERT_MAX_REPEATS == 5


# ---------------------------------------------------------------------------
# STT configuration defaults
# ---------------------------------------------------------------------------


class TestSTTConfigDefaults:
    """Verify that STT config constants exist with the correct defaults."""

    def test_stt_api_key_default(self):
        cfg = _reload_config()
        assert cfg.STT_API_KEY == ""

    def test_stt_base_url_default(self):
        cfg = _reload_config()
        assert cfg.STT_BASE_URL == "https://api.openai.com"

    def test_stt_model_default(self):
        cfg = _reload_config()
        assert cfg.STT_MODEL == "whisper-1"

    def test_stt_timeout_default(self):
        cfg = _reload_config()
        assert cfg.STT_TIMEOUT == 10.0

    def test_stt_listen_timeout_default(self):
        cfg = _reload_config()
        assert cfg.STT_LISTEN_TIMEOUT == 30.0

    def test_stt_silence_threshold_default(self):
        cfg = _reload_config()
        assert cfg.STT_SILENCE_THRESHOLD == 0.01

    def test_stt_silence_duration_default(self):
        cfg = _reload_config()
        assert cfg.STT_SILENCE_DURATION == 1.5

    def test_stt_max_record_duration_default(self):
        cfg = _reload_config()
        assert cfg.STT_MAX_RECORD_DURATION == 15.0

    def test_stt_confidence_threshold_default(self):
        cfg = _reload_config()
        assert cfg.STT_CONFIDENCE_THRESHOLD == 0.6

    def test_stt_health_check_interval_default(self):
        cfg = _reload_config()
        assert cfg.STT_HEALTH_CHECK_INTERVAL == 60.0

    def test_dispatch_method_default(self):
        cfg = _reload_config()
        assert cfg.DISPATCH_METHOD == ""


# ---------------------------------------------------------------------------
# STT environment variable overrides
# ---------------------------------------------------------------------------


class TestSTTConfigEnvOverrides:
    """Verify that STT env vars correctly override defaults."""

    def test_stt_api_key_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_API_KEY", "sk-test-stt-key")
        cfg = _reload_config()
        assert cfg.STT_API_KEY == "sk-test-stt-key"

    def test_stt_base_url_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_BASE_URL", "http://localhost:8080")
        cfg = _reload_config()
        assert cfg.STT_BASE_URL == "http://localhost:8080"

    def test_stt_model_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_MODEL", "whisper-large-v3")
        cfg = _reload_config()
        assert cfg.STT_MODEL == "whisper-large-v3"

    def test_stt_timeout_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_TIMEOUT", "20.0")
        cfg = _reload_config()
        assert cfg.STT_TIMEOUT == 20.0

    def test_stt_listen_timeout_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_LISTEN_TIMEOUT", "60.0")
        cfg = _reload_config()
        assert cfg.STT_LISTEN_TIMEOUT == 60.0

    def test_stt_silence_threshold_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_SILENCE_THRESHOLD", "0.05")
        cfg = _reload_config()
        assert cfg.STT_SILENCE_THRESHOLD == 0.05

    def test_stt_silence_duration_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_SILENCE_DURATION", "2.0")
        cfg = _reload_config()
        assert cfg.STT_SILENCE_DURATION == 2.0

    def test_stt_max_record_duration_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_MAX_RECORD_DURATION", "30.0")
        cfg = _reload_config()
        assert cfg.STT_MAX_RECORD_DURATION == 30.0

    def test_stt_confidence_threshold_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_CONFIDENCE_THRESHOLD", "0.8")
        cfg = _reload_config()
        assert cfg.STT_CONFIDENCE_THRESHOLD == 0.8

    def test_stt_health_check_interval_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_STT_HEALTH_CHECK_INTERVAL", "120.0")
        cfg = _reload_config()
        assert cfg.STT_HEALTH_CHECK_INTERVAL == 120.0

    def test_dispatch_method_override(self, monkeypatch):
        monkeypatch.setenv("ECHO_DISPATCH_METHOD", "tmux")
        cfg = _reload_config()
        assert cfg.DISPATCH_METHOD == "tmux"


# ---------------------------------------------------------------------------
# STT type checks
# ---------------------------------------------------------------------------


class TestSTTConfigTypes:
    """Verify that STT config constants have the expected Python types."""

    def test_string_constants(self):
        cfg = _reload_config()
        assert isinstance(cfg.STT_API_KEY, str)
        assert isinstance(cfg.STT_BASE_URL, str)
        assert isinstance(cfg.STT_MODEL, str)
        assert isinstance(cfg.DISPATCH_METHOD, str)

    def test_float_constants(self):
        cfg = _reload_config()
        assert isinstance(cfg.STT_TIMEOUT, float)
        assert isinstance(cfg.STT_LISTEN_TIMEOUT, float)
        assert isinstance(cfg.STT_SILENCE_THRESHOLD, float)
        assert isinstance(cfg.STT_SILENCE_DURATION, float)
        assert isinstance(cfg.STT_MAX_RECORD_DURATION, float)
        assert isinstance(cfg.STT_CONFIDENCE_THRESHOLD, float)
        assert isinstance(cfg.STT_HEALTH_CHECK_INTERVAL, float)

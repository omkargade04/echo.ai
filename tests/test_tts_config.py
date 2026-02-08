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

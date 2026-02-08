"""Tests for echo.tts.types — TTS state enum."""


# ---------------------------------------------------------------------------
# TTSState enum
# ---------------------------------------------------------------------------


class TestTTSState:
    """Verify TTSState enum values, membership, and string behaviour."""

    def test_active_value(self):
        from echo.tts.types import TTSState

        assert TTSState.ACTIVE == "active"
        assert TTSState.ACTIVE.value == "active"

    def test_degraded_value(self):
        from echo.tts.types import TTSState

        assert TTSState.DEGRADED == "degraded"
        assert TTSState.DEGRADED.value == "degraded"

    def test_disabled_value(self):
        from echo.tts.types import TTSState

        assert TTSState.DISABLED == "disabled"
        assert TTSState.DISABLED.value == "disabled"

    def test_enum_has_exactly_three_members(self):
        from echo.tts.types import TTSState

        assert len(TTSState) == 3

    def test_string_comparison(self):
        from echo.tts.types import TTSState

        assert TTSState.ACTIVE == "active"
        assert TTSState.DEGRADED == "degraded"
        assert TTSState.DISABLED == "disabled"

    def test_usable_as_dict_key(self):
        from echo.tts.types import TTSState

        d = {TTSState.ACTIVE: 1, TTSState.DEGRADED: 2, TTSState.DISABLED: 3}
        assert d[TTSState.ACTIVE] == 1
        assert d[TTSState.DEGRADED] == 2
        assert d[TTSState.DISABLED] == 3

    def test_usable_in_set(self):
        from echo.tts.types import TTSState

        s = {TTSState.ACTIVE, TTSState.DEGRADED, TTSState.DISABLED}
        assert len(s) == 3
        assert TTSState.ACTIVE in s

    def test_import_from_echo_tts_package(self):
        from echo.tts import TTSState

        assert TTSState.ACTIVE.value == "active"

    def test_import_from_echo_tts_types_module(self):
        from echo.tts.types import TTSState

        assert TTSState.DISABLED.value == "disabled"

"""Tests for echo.stt.types — STT type models and enums."""

import json
import time

import pytest
from pydantic import ValidationError

from echo.stt.types import MatchMethod, MatchResult, ResponseEvent, STTState


# ---------------------------------------------------------------------------
# STTState enum
# ---------------------------------------------------------------------------


class TestSTTState:
    """Verify that all expected STTState enum values exist."""

    def test_active_value(self):
        assert STTState.ACTIVE == "active"
        assert STTState.ACTIVE.value == "active"

    def test_degraded_value(self):
        assert STTState.DEGRADED == "degraded"
        assert STTState.DEGRADED.value == "degraded"

    def test_disabled_value(self):
        assert STTState.DISABLED == "disabled"
        assert STTState.DISABLED.value == "disabled"

    def test_listening_value(self):
        assert STTState.LISTENING == "listening"
        assert STTState.LISTENING.value == "listening"

    def test_enum_has_exactly_four_members(self):
        assert len(STTState) == 4


# ---------------------------------------------------------------------------
# MatchMethod enum
# ---------------------------------------------------------------------------


class TestMatchMethod:
    """Verify that all expected MatchMethod enum values exist."""

    def test_ordinal_value(self):
        assert MatchMethod.ORDINAL == "ordinal"

    def test_direct_value(self):
        assert MatchMethod.DIRECT == "direct"

    def test_yes_no_value(self):
        assert MatchMethod.YES_NO == "yes_no"

    def test_fuzzy_value(self):
        assert MatchMethod.FUZZY == "fuzzy"

    def test_verbatim_value(self):
        assert MatchMethod.VERBATIM == "verbatim"

    def test_enum_has_exactly_five_members(self):
        assert len(MatchMethod) == 5


# ---------------------------------------------------------------------------
# MatchResult model
# ---------------------------------------------------------------------------


class TestMatchResult:
    """Tests for the MatchResult Pydantic model."""

    def test_create_with_all_fields(self):
        result = MatchResult(
            matched_text="RS256",
            confidence=0.95,
            method=MatchMethod.ORDINAL,
        )
        assert result.matched_text == "RS256"
        assert result.confidence == 0.95
        assert result.method == MatchMethod.ORDINAL

    def test_serialization_to_dict(self):
        result = MatchResult(
            matched_text="yes",
            confidence=1.0,
            method=MatchMethod.YES_NO,
        )
        d = result.model_dump()
        assert d["matched_text"] == "yes"
        assert d["confidence"] == 1.0
        assert d["method"] == MatchMethod.YES_NO

    def test_serialization_to_json(self):
        result = MatchResult(
            matched_text="option A",
            confidence=0.8,
            method=MatchMethod.FUZZY,
        )
        json_str = result.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["matched_text"] == "option A"
        assert parsed["method"] == "fuzzy"

    def test_validation_error_missing_fields(self):
        with pytest.raises(ValidationError):
            MatchResult(matched_text="x")


# ---------------------------------------------------------------------------
# ResponseEvent model
# ---------------------------------------------------------------------------


class TestResponseEvent:
    """Tests for the ResponseEvent Pydantic model."""

    def test_create_with_all_fields(self):
        event = ResponseEvent(
            text="RS256",
            transcript="option one",
            session_id="sess-1",
            match_method=MatchMethod.ORDINAL,
            confidence=0.95,
            timestamp=1000.0,
            options=["RS256", "HS256"],
        )
        assert event.text == "RS256"
        assert event.transcript == "option one"
        assert event.session_id == "sess-1"
        assert event.match_method == MatchMethod.ORDINAL
        assert event.confidence == 0.95
        assert event.timestamp == 1000.0
        assert event.options == ["RS256", "HS256"]

    def test_create_with_minimal_fields(self):
        event = ResponseEvent(
            text="yes",
            transcript="yes",
            session_id="s1",
            match_method=MatchMethod.YES_NO,
            confidence=1.0,
        )
        assert event.options is None
        assert isinstance(event.timestamp, float)

    def test_default_timestamp_is_auto_populated(self):
        before = time.time()
        event = ResponseEvent(
            text="go",
            transcript="go",
            session_id="s1",
            match_method=MatchMethod.VERBATIM,
            confidence=1.0,
        )
        after = time.time()
        assert before <= event.timestamp <= after

    def test_explicit_timestamp_overrides_default(self):
        event = ResponseEvent(
            text="x",
            transcript="x",
            session_id="s1",
            match_method=MatchMethod.DIRECT,
            confidence=0.9,
            timestamp=1234567890.0,
        )
        assert event.timestamp == 1234567890.0

    def test_serialization_to_dict(self):
        event = ResponseEvent(
            text="RS256",
            transcript="option one",
            session_id="s1",
            match_method=MatchMethod.ORDINAL,
            confidence=0.95,
            timestamp=1000.0,
            options=["RS256", "HS256"],
        )
        d = event.model_dump()
        assert d["text"] == "RS256"
        assert d["options"] == ["RS256", "HS256"]

    def test_serialization_to_json(self):
        event = ResponseEvent(
            text="yes",
            transcript="yeah",
            session_id="s1",
            match_method=MatchMethod.YES_NO,
            confidence=1.0,
            timestamp=2000.0,
        )
        json_str = event.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["text"] == "yes"
        assert parsed["match_method"] == "yes_no"

    def test_model_roundtrip(self):
        original = ResponseEvent(
            text="RS256",
            transcript="option one",
            session_id="sess-42",
            match_method=MatchMethod.ORDINAL,
            confidence=0.95,
            timestamp=999.0,
            options=["RS256", "HS256"],
        )
        rebuilt = ResponseEvent(**original.model_dump())
        assert rebuilt == original

    def test_options_default_none(self):
        event = ResponseEvent(
            text="go",
            transcript="go",
            session_id="s1",
            match_method=MatchMethod.VERBATIM,
            confidence=1.0,
        )
        assert event.options is None

    def test_validation_error_missing_text(self):
        with pytest.raises(ValidationError):
            ResponseEvent(
                transcript="hello",
                session_id="s1",
                match_method=MatchMethod.VERBATIM,
                confidence=1.0,
            )

    def test_validation_error_missing_session_id(self):
        with pytest.raises(ValidationError):
            ResponseEvent(
                text="hello",
                transcript="hello",
                match_method=MatchMethod.VERBATIM,
                confidence=1.0,
            )

"""Tests for echo.stt.response_matcher — ResponseMatcher."""

import pytest

from echo.events.types import BlockReason
from echo.stt.response_matcher import ResponseMatcher
from echo.stt.types import MatchMethod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def matcher() -> ResponseMatcher:
    return ResponseMatcher()


# Standard 3-option list reused across many tests.
_OPTIONS_3 = ["RS256", "HS256", "ES512"]

# Standard 5-option list for ordinal range tests.
_OPTIONS_5 = ["alpha", "beta", "gamma", "delta", "epsilon"]


# ---------------------------------------------------------------------------
# Ordinal matching
# ---------------------------------------------------------------------------


class TestOrdinalMatching:
    """Ordinal words/numbers resolve to the correct option index."""

    def test_option_one(self, matcher: ResponseMatcher):
        result = matcher.match("option one", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.confidence == 0.95
        assert result.method == MatchMethod.ORDINAL

    def test_option_digit_1(self, matcher: ResponseMatcher):
        result = matcher.match("option 1", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.method == MatchMethod.ORDINAL

    def test_word_two(self, matcher: ResponseMatcher):
        result = matcher.match("two", _OPTIONS_3)
        assert result.matched_text == "HS256"
        assert result.method == MatchMethod.ORDINAL

    def test_the_third_one(self, matcher: ResponseMatcher):
        result = matcher.match("the third one", _OPTIONS_3)
        assert result.matched_text == "ES512"
        assert result.method == MatchMethod.ORDINAL

    def test_word_first(self, matcher: ResponseMatcher):
        result = matcher.match("first", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.method == MatchMethod.ORDINAL

    def test_number_five(self, matcher: ResponseMatcher):
        result = matcher.match("number five", _OPTIONS_5)
        assert result.matched_text == "epsilon"
        assert result.method == MatchMethod.ORDINAL

    def test_out_of_range_ordinal_falls_through(self, matcher: ResponseMatcher):
        """'ten' with only 3 options should not match ordinally."""
        result = matcher.match("ten", _OPTIONS_3)
        assert result.method != MatchMethod.ORDINAL

    def test_case_insensitive_ordinal(self, matcher: ResponseMatcher):
        result = matcher.match("Option One", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.method == MatchMethod.ORDINAL

    def test_digit_4(self, matcher: ResponseMatcher):
        result = matcher.match("4", _OPTIONS_5)
        assert result.matched_text == "delta"
        assert result.method == MatchMethod.ORDINAL

    def test_pick_option_two(self, matcher: ResponseMatcher):
        result = matcher.match("pick option two", _OPTIONS_3)
        assert result.matched_text == "HS256"
        assert result.method == MatchMethod.ORDINAL


# ---------------------------------------------------------------------------
# Yes/No matching
# ---------------------------------------------------------------------------


class TestYesNoMatching:
    """Yes/no shortcuts for 2-option permission prompts."""

    _PERM_OPTIONS = ["Allow", "Deny"]

    def test_yes_maps_to_first_option(self, matcher: ResponseMatcher):
        result = matcher.match(
            "yes", self._PERM_OPTIONS, block_reason=BlockReason.PERMISSION_PROMPT
        )
        assert result.matched_text == "Allow"
        assert result.confidence == 0.9
        assert result.method == MatchMethod.YES_NO

    def test_no_maps_to_second_option(self, matcher: ResponseMatcher):
        result = matcher.match(
            "no", self._PERM_OPTIONS, block_reason=BlockReason.PERMISSION_PROMPT
        )
        assert result.matched_text == "Deny"
        assert result.method == MatchMethod.YES_NO

    def test_yeah_maps_to_first(self, matcher: ResponseMatcher):
        result = matcher.match(
            "yeah", self._PERM_OPTIONS, block_reason=BlockReason.PERMISSION_PROMPT
        )
        assert result.matched_text == "Allow"
        assert result.method == MatchMethod.YES_NO

    def test_deny_word_maps_to_second(self, matcher: ResponseMatcher):
        result = matcher.match(
            "deny", self._PERM_OPTIONS, block_reason=BlockReason.PERMISSION_PROMPT
        )
        assert result.matched_text == "Deny"
        assert result.method == MatchMethod.YES_NO

    def test_yes_with_three_options_does_not_match(self, matcher: ResponseMatcher):
        """Yes/no shortcut only applies to exactly 2 options."""
        result = matcher.match(
            "yes", _OPTIONS_3, block_reason=BlockReason.PERMISSION_PROMPT
        )
        assert result.method != MatchMethod.YES_NO

    def test_yes_with_question_block_reason_does_not_match(self, matcher: ResponseMatcher):
        """Yes/no shortcut only applies to PERMISSION_PROMPT block reason."""
        result = matcher.match(
            "yes", self._PERM_OPTIONS, block_reason=BlockReason.QUESTION
        )
        assert result.method != MatchMethod.YES_NO


# ---------------------------------------------------------------------------
# Direct matching
# ---------------------------------------------------------------------------


class TestDirectMatching:
    """Direct substring matching (case-insensitive)."""

    def test_exact_match(self, matcher: ResponseMatcher):
        result = matcher.match("RS256", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.confidence == 0.85
        assert result.method == MatchMethod.DIRECT

    def test_partial_transcript_contains_option(self, matcher: ResponseMatcher):
        result = matcher.match("I want RS256", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.method == MatchMethod.DIRECT

    def test_case_insensitive_direct(self, matcher: ResponseMatcher):
        result = matcher.match("rs256", _OPTIONS_3)
        assert result.matched_text == "RS256"
        assert result.method == MatchMethod.DIRECT

    def test_multiple_direct_matches_picks_longest(self, matcher: ResponseMatcher):
        options = ["save", "save and exit"]
        result = matcher.match("save and exit please", options)
        assert result.matched_text == "save and exit"
        assert result.method == MatchMethod.DIRECT

    def test_transcript_contained_in_option(self, matcher: ResponseMatcher):
        """When the transcript is shorter and is contained within an option."""
        options = ["Run all tests", "Skip tests"]
        result = matcher.match("skip", options)
        assert result.matched_text == "Skip tests"
        assert result.method == MatchMethod.DIRECT


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


class TestFuzzyMatching:
    """SequenceMatcher-based fuzzy matching."""

    def test_fuzzy_match_above_threshold(self, matcher: ResponseMatcher):
        options = ["approve", "reject"]
        # "aprove" is not a substring of "approve" but is close enough for fuzzy.
        result = matcher.match("aprove", options)
        assert result.matched_text == "approve"
        assert result.method == MatchMethod.FUZZY
        assert result.confidence >= 0.6

    def test_fuzzy_below_threshold_returns_verbatim(self, matcher: ResponseMatcher):
        options = ["RS256", "HS256"]
        result = matcher.match("xyzzy abcdef ghijkl", options)
        assert result.method == MatchMethod.VERBATIM

    def test_fuzzy_best_match_selected(self, matcher: ResponseMatcher):
        options = ["authenticate", "authorize"]
        result = matcher.match("authorise", options)
        assert result.matched_text == "authorize"
        assert result.method == MatchMethod.FUZZY

    def test_fuzzy_confidence_is_ratio(self, matcher: ResponseMatcher):
        options = ["development", "production"]
        result = matcher.match("developmnt", options)
        assert result.method == MatchMethod.FUZZY
        assert 0.6 <= result.confidence <= 1.0

    def test_fuzzy_not_triggered_when_direct_matches(self, matcher: ResponseMatcher):
        """Direct match has higher priority than fuzzy."""
        options = ["approve", "reject"]
        result = matcher.match("approve", options)
        assert result.method == MatchMethod.DIRECT


# ---------------------------------------------------------------------------
# Verbatim fallback
# ---------------------------------------------------------------------------


class TestVerbatimFallback:
    """Verbatim fallback when no options or no match found."""

    def test_none_options_returns_verbatim(self, matcher: ResponseMatcher):
        result = matcher.match("some free text", None)
        assert result.matched_text == "some free text"
        assert result.confidence == 1.0
        assert result.method == MatchMethod.VERBATIM

    def test_empty_options_returns_verbatim(self, matcher: ResponseMatcher):
        result = matcher.match("hello world", [])
        assert result.matched_text == "hello world"
        assert result.confidence == 1.0
        assert result.method == MatchMethod.VERBATIM

    def test_no_match_found_returns_verbatim_with_transcript(self, matcher: ResponseMatcher):
        """When nothing matches, transcript is returned verbatim."""
        options = ["RS256", "HS256"]
        result = matcher.match("xyzzy abcdef ghijkl", options)
        assert result.matched_text == "xyzzy abcdef ghijkl"
        assert result.method == MatchMethod.VERBATIM

    def test_verbatim_strips_whitespace(self, matcher: ResponseMatcher):
        result = matcher.match("  padded text  ", None)
        assert result.matched_text == "padded text"


# ---------------------------------------------------------------------------
# Priority chain
# ---------------------------------------------------------------------------


class TestPriorityChain:
    """Matching strategies are tried in priority order."""

    def test_ordinal_beats_direct(self, matcher: ResponseMatcher):
        """When 'one' could match ordinal and direct, ordinal wins."""
        options = ["one fish", "two fish"]
        result = matcher.match("one", options)
        assert result.method == MatchMethod.ORDINAL
        assert result.matched_text == "one fish"

    def test_yes_no_beats_fuzzy(self, matcher: ResponseMatcher):
        """Yes/no shortcut has higher priority than fuzzy."""
        options = ["yes please", "no thanks"]
        result = matcher.match(
            "yes", options, block_reason=BlockReason.PERMISSION_PROMPT
        )
        assert result.method == MatchMethod.YES_NO
        assert result.matched_text == "yes please"

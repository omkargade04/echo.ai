"""Maps spoken transcript text to the best matching option from a list.

Uses a priority chain of matching strategies: ordinal, yes/no shortcut,
direct substring, fuzzy (SequenceMatcher), and verbatim fallback.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from echo.config import STT_CONFIDENCE_THRESHOLD
from echo.events.types import BlockReason
from echo.stt.types import MatchMethod, MatchResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ordinal lookup table
# ---------------------------------------------------------------------------

_ORDINAL_WORDS: dict[str, int] = {
    "one": 0, "first": 0, "1": 0,
    "two": 1, "second": 1, "2": 1,
    "three": 2, "third": 2, "3": 2,
    "four": 3, "fourth": 3, "4": 3,
    "five": 4, "fifth": 4, "5": 4,
    "six": 5, "sixth": 5, "6": 5,
    "seven": 6, "seventh": 6, "7": 6,
    "eight": 7, "eighth": 7, "8": 7,
    "nine": 8, "ninth": 8, "9": 8,
    "ten": 9, "tenth": 9, "10": 9,
}

# Prefix words to strip before ordinal lookup.
_ORDINAL_STRIP_WORDS = {"option", "the", "number", "pick"}

# ---------------------------------------------------------------------------
# Yes/No word sets
# ---------------------------------------------------------------------------

_YES_WORDS: set[str] = {
    "yes", "yeah", "yep", "yup", "sure", "allow", "approve", "accept", "ok", "okay",
}

_NO_WORDS: set[str] = {
    "no", "nah", "nope", "deny", "reject", "decline", "refuse", "block",
}


class ResponseMatcher:
    """Maps spoken text to the appropriate option from a list."""

    def match(
        self,
        transcript: str,
        options: list[str] | None,
        block_reason: BlockReason | None = None,
    ) -> MatchResult:
        """Match transcript to best option. Returns MatchResult.

        Matching priority (first match wins):
        1. Ordinal: "option one", "first one", "one", "1" -> options[0]
        2. Yes/No shortcut: "yes"/"no" for 2-option permission prompts
        3. Direct: transcript contains option text (case-insensitive)
        4. Fuzzy: SequenceMatcher similarity above threshold
        5. Verbatim: no options available, return transcript as-is
        """
        if not options:
            return MatchResult(
                matched_text=transcript.strip(),
                confidence=1.0,
                method=MatchMethod.VERBATIM,
            )

        result = self._try_ordinal_match(transcript, options)
        if result is not None:
            return result

        result = self._try_yes_no_match(transcript, options, block_reason)
        if result is not None:
            return result

        result = self._try_direct_match(transcript, options)
        if result is not None:
            return result

        result = self._try_fuzzy_match(transcript, options)
        if result is not None:
            return result

        return MatchResult(
            matched_text=transcript.strip(),
            confidence=1.0,
            method=MatchMethod.VERBATIM,
        )

    # --------------------------------------------------------------------- #
    # Ordinal matching
    # --------------------------------------------------------------------- #

    def _try_ordinal_match(
        self, transcript: str, options: list[str]
    ) -> MatchResult | None:
        words = transcript.lower().split()
        # Strip known prefix words.
        filtered = [w for w in words if w not in _ORDINAL_STRIP_WORDS]
        if not filtered:
            return None

        # Check each remaining word against the ordinal lookup.
        for word in filtered:
            index = _ORDINAL_WORDS.get(word)
            if index is not None and index < len(options):
                return MatchResult(
                    matched_text=options[index],
                    confidence=0.95,
                    method=MatchMethod.ORDINAL,
                )

        return None

    # --------------------------------------------------------------------- #
    # Yes/No shortcut
    # --------------------------------------------------------------------- #

    def _try_yes_no_match(
        self,
        transcript: str,
        options: list[str],
        block_reason: BlockReason | None,
    ) -> MatchResult | None:
        if len(options) != 2:
            return None
        if block_reason != BlockReason.PERMISSION_PROMPT:
            return None

        normalized = transcript.strip().lower()
        words = set(normalized.split())

        if words & _YES_WORDS:
            return MatchResult(
                matched_text=options[0],
                confidence=0.9,
                method=MatchMethod.YES_NO,
            )
        if words & _NO_WORDS:
            return MatchResult(
                matched_text=options[1],
                confidence=0.9,
                method=MatchMethod.YES_NO,
            )

        return None

    # --------------------------------------------------------------------- #
    # Direct substring matching
    # --------------------------------------------------------------------- #

    def _try_direct_match(
        self, transcript: str, options: list[str]
    ) -> MatchResult | None:
        transcript_lower = transcript.lower()
        matches: list[str] = []

        for option in options:
            option_lower = option.lower()
            if option_lower in transcript_lower or transcript_lower in option_lower:
                matches.append(option)

        if not matches:
            return None

        # Pick the longest matching option text.
        best = max(matches, key=len)
        return MatchResult(
            matched_text=best,
            confidence=0.85,
            method=MatchMethod.DIRECT,
        )

    # --------------------------------------------------------------------- #
    # Fuzzy matching (SequenceMatcher)
    # --------------------------------------------------------------------- #

    def _try_fuzzy_match(
        self, transcript: str, options: list[str]
    ) -> MatchResult | None:
        transcript_lower = transcript.lower()
        best_ratio = 0.0
        best_option: str | None = None

        for option in options:
            ratio = SequenceMatcher(
                None, transcript_lower, option.lower()
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_option = option

        if best_option is not None and best_ratio >= STT_CONFIDENCE_THRESHOLD:
            return MatchResult(
                matched_text=best_option,
                confidence=best_ratio,
                method=MatchMethod.FUZZY,
            )

        return None

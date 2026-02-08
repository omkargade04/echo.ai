"""Stage 2: Filter & Summarize — converts raw events into concise narration text."""

from echo.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)
from echo.summarizer.summarizer import Summarizer

__all__ = [
    "NarrationEvent",
    "NarrationPriority",
    "Summarizer",
    "SummarizationMethod",
]

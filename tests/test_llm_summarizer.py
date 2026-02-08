"""Tests for echo.summarizer.llm_summarizer — Ollama LLM summarizer."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from echo.events.types import EventType, EchoEvent
from echo.summarizer.llm_summarizer import (
    LLMSummarizer,
    _MAX_TRUNCATION_LENGTH,
    _TRUNCATED_LENGTH,
)
from echo.summarizer.types import (
    NarrationEvent,
    NarrationPriority,
    SummarizationMethod,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_message_event(
    text: str | None = "Hello from the assistant",
    session_id: str = "test-session",
    event_id: str = "evt-001",
) -> EchoEvent:
    """Create a minimal agent_message event for testing."""
    return EchoEvent(
        type=EventType.AGENT_MESSAGE,
        session_id=session_id,
        source="transcript",
        text=text,
        event_id=event_id,
    )


def _mock_health_response(status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for /api/tags."""
    return httpx.Response(status_code=status_code, request=httpx.Request("GET", "/api/tags"))


def _mock_generate_response(summary_text: str) -> httpx.Response:
    """Build a fake httpx.Response for /api/generate."""
    return httpx.Response(
        status_code=200,
        json={"response": summary_text},
        request=httpx.Request("POST", "/api/generate"),
    )


# ---------------------------------------------------------------------------
# TestLLMSummarizerStart — initialization and health checks
# ---------------------------------------------------------------------------


class TestLLMSummarizerStart:
    """Tests for start(), stop(), and initial health check behavior."""

    async def test_start_initializes_client(self):
        """start() should create an httpx.AsyncClient."""
        summarizer = LLMSummarizer()
        assert summarizer._client is None

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            await summarizer.start()
            assert summarizer._client is instance
            MockClient.assert_called_once()

    async def test_health_check_success_sets_available(self):
        """When /api/tags returns 200, is_available should be True."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            await summarizer.start()
            assert summarizer.is_available is True

    async def test_health_check_non_200_sets_unavailable(self):
        """When /api/tags returns non-200, is_available should be False."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(503))
            MockClient.return_value = instance

            await summarizer.start()
            assert summarizer.is_available is False

    async def test_health_check_connect_error_sets_unavailable(self):
        """When /api/tags raises ConnectError, is_available should be False."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            MockClient.return_value = instance

            await summarizer.start()
            assert summarizer.is_available is False

    async def test_health_check_timeout_sets_unavailable(self):
        """When /api/tags raises TimeoutException, is_available should be False."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
            MockClient.return_value = instance

            await summarizer.start()
            assert summarizer.is_available is False

    async def test_stop_closes_client(self):
        """stop() should call aclose() on the client and set it to None."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            instance.aclose = AsyncMock()
            MockClient.return_value = instance

            await summarizer.start()
            assert summarizer._client is not None

            await summarizer.stop()
            instance.aclose.assert_awaited_once()
            assert summarizer._client is None

    async def test_stop_when_no_client_is_safe(self):
        """stop() should not raise when called before start()."""
        summarizer = LLMSummarizer()
        await summarizer.stop()  # Should not raise


# ---------------------------------------------------------------------------
# TestLLMSummarizerSummarize — summarize() with Ollama available
# ---------------------------------------------------------------------------


class TestLLMSummarizerSummarize:
    """Tests for summarize() when Ollama is available."""

    async def test_summarize_with_ollama_returns_llm_method(self):
        """When Ollama is available, summarization_method should be LLM."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Fixed the authentication bug.")
        )

        event = _make_agent_message_event(text="I analyzed the code and fixed the auth bug by...")
        result = await summarizer.summarize(event)

        assert isinstance(result, NarrationEvent)
        assert result.summarization_method == SummarizationMethod.LLM

    async def test_summarize_with_ollama_uses_returned_text(self):
        """The narration text should be the LLM summary."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("  Refactored the database module.  ")
        )

        event = _make_agent_message_event(text="I went through the codebase and refactored...")
        result = await summarizer.summarize(event)

        assert result.text == "Refactored the database module."

    async def test_summarize_session_id_carried_through(self):
        """The session_id from the source event should appear on the NarrationEvent."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary text.")
        )

        event = _make_agent_message_event(session_id="sess-42")
        result = await summarizer.summarize(event)

        assert result.session_id == "sess-42"

    async def test_summarize_source_event_id_carried_through(self):
        """The event_id from the source event should appear as source_event_id."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary text.")
        )

        event = _make_agent_message_event(event_id="evt-abc-123")
        result = await summarizer.summarize(event)

        assert result.source_event_id == "evt-abc-123"

    async def test_summarize_priority_is_normal(self):
        """agent_message narrations should have NORMAL priority."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary text.")
        )

        event = _make_agent_message_event()
        result = await summarizer.summarize(event)

        assert result.priority == NarrationPriority.NORMAL

    async def test_summarize_source_event_type_is_agent_message(self):
        """source_event_type should always be AGENT_MESSAGE."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary text.")
        )

        event = _make_agent_message_event()
        result = await summarizer.summarize(event)

        assert result.source_event_type == EventType.AGENT_MESSAGE

    async def test_ollama_empty_response_returns_empty_text(self):
        """When Ollama returns an empty string, the narration text should be empty."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("")
        )

        event = _make_agent_message_event()
        result = await summarizer.summarize(event)

        assert result.text == ""
        assert result.summarization_method == SummarizationMethod.LLM


# ---------------------------------------------------------------------------
# TestLLMSummarizerFallback — summarize() falling back to truncation
# ---------------------------------------------------------------------------


class TestLLMSummarizerFallback:
    """Tests for summarize() when Ollama is unavailable or fails."""

    async def test_summarize_unavailable_uses_truncation(self):
        """When Ollama is unavailable, should fall back to truncation."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False
        summarizer._client = None

        event = _make_agent_message_event(text="Short text.")
        result = await summarizer.summarize(event)

        assert result.summarization_method == SummarizationMethod.TRUNCATION
        assert result.text == "Short text."

    async def test_ollama_post_raises_falls_back_to_truncation(self):
        """When Ollama POST raises an exception, should fall back to truncation."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection lost")
        )

        event = _make_agent_message_event(text="Some text.")
        result = await summarizer.summarize(event)

        assert result.summarization_method == SummarizationMethod.TRUNCATION
        assert result.text == "Some text."

    async def test_ollama_http_error_falls_back_to_truncation(self):
        """When Ollama returns an HTTP error status, should fall back to truncation."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()

        error_response = httpx.Response(
            status_code=500,
            request=httpx.Request("POST", "/api/generate"),
        )
        summarizer._client.post = AsyncMock(return_value=error_response)
        # raise_for_status() will raise on the real response
        # We need to simulate the actual call flow where raise_for_status raises
        summarizer._client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "/api/generate"),
                response=error_response,
            )
        )

        event = _make_agent_message_event(text="Some text.")
        result = await summarizer.summarize(event)

        assert result.summarization_method == SummarizationMethod.TRUNCATION
        assert result.text == "Some text."


# ---------------------------------------------------------------------------
# TestTruncation — truncation logic
# ---------------------------------------------------------------------------


class TestTruncation:
    """Tests for text truncation behavior."""

    async def test_short_text_unchanged(self):
        """Text <= 150 chars should be returned as-is."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False

        short_text = "A" * _MAX_TRUNCATION_LENGTH  # exactly 150 chars
        event = _make_agent_message_event(text=short_text)
        result = await summarizer.summarize(event)

        assert result.text == short_text
        assert result.summarization_method == SummarizationMethod.TRUNCATION

    async def test_long_text_truncated_with_ellipsis(self):
        """Text > 150 chars should get first 140 chars + '...'."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False

        long_text = "B" * 300
        event = _make_agent_message_event(text=long_text)
        result = await summarizer.summarize(event)

        assert result.text == "B" * _TRUNCATED_LENGTH + "..."
        assert len(result.text) == _TRUNCATED_LENGTH + 3
        assert result.summarization_method == SummarizationMethod.TRUNCATION

    async def test_text_exactly_151_chars_is_truncated(self):
        """Text of 151 chars (just over the limit) should be truncated."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False

        text = "C" * 151
        event = _make_agent_message_event(text=text)
        result = await summarizer.summarize(event)

        assert result.text == "C" * _TRUNCATED_LENGTH + "..."

    async def test_empty_text_returns_empty_string(self):
        """When event.text is empty string, narration text should be empty."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False

        event = _make_agent_message_event(text="")
        result = await summarizer.summarize(event)

        assert result.text == ""
        assert result.summarization_method == SummarizationMethod.TRUNCATION

    async def test_none_text_returns_empty_string(self):
        """When event.text is None, narration text should be empty."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False

        event = _make_agent_message_event(text=None)
        result = await summarizer.summarize(event)

        assert result.text == ""
        assert result.summarization_method == SummarizationMethod.TRUNCATION

    async def test_truncation_strips_trailing_space(self):
        """Truncation should rstrip before appending '...'."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False

        # Build text so that char at position 139 is a space
        text = "D" * 139 + " " + "E" * 200
        event = _make_agent_message_event(text=text)
        result = await summarizer.summarize(event)

        # rstrip removes trailing spaces from the 140 slice, then appends ...
        assert result.text.endswith("...")
        assert not result.text[:-3].endswith(" ")


# ---------------------------------------------------------------------------
# TestPeriodicRecheck — health re-check interval logic
# ---------------------------------------------------------------------------


class TestPeriodicRecheck:
    """Tests for periodic health re-check behavior."""

    async def test_recheck_happens_after_interval(self):
        """When unavailable and interval has passed, health should be re-checked."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False
        summarizer._client = AsyncMock()
        summarizer._client.get = AsyncMock(return_value=_mock_health_response(200))

        # Set the last health check far in the past
        summarizer._last_health_check = time.monotonic() - 120.0

        event = _make_agent_message_event(text="Test")
        # summarize will call _maybe_recheck_health which should trigger _check_health
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary.")
        )
        result = await summarizer.summarize(event)

        # After recheck, Ollama should be available and LLM method used
        summarizer._client.get.assert_awaited_once()
        assert result.summarization_method == SummarizationMethod.LLM

    async def test_no_recheck_when_available(self):
        """When Ollama is already available, _maybe_recheck_health should not re-check."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary.")
        )

        # Even with a stale last_health_check, no re-check should happen
        summarizer._last_health_check = time.monotonic() - 120.0

        event = _make_agent_message_event(text="Test")
        await summarizer.summarize(event)

        # GET (health check) should NOT have been called
        summarizer._client.get.assert_not_awaited()

    async def test_no_recheck_before_interval_elapses(self):
        """When unavailable but interval hasn't passed, no re-check should happen."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = False
        summarizer._client = AsyncMock()
        summarizer._client.get = AsyncMock(return_value=_mock_health_response(200))

        # Set the last health check to just now
        summarizer._last_health_check = time.monotonic()

        event = _make_agent_message_event(text="Test")
        result = await summarizer.summarize(event)

        # GET (health check) should NOT have been called
        summarizer._client.get.assert_not_awaited()
        # Should have used truncation since Ollama remains unavailable
        assert result.summarization_method == SummarizationMethod.TRUNCATION


# ---------------------------------------------------------------------------
# TestConfigValues — verify config values are wired correctly
# ---------------------------------------------------------------------------


class TestConfigValues:
    """Tests that config values are passed to the HTTP client."""

    async def test_client_uses_ollama_base_url(self):
        """AsyncClient should be initialized with OLLAMA_BASE_URL."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            await summarizer.start()

            call_kwargs = MockClient.call_args
            assert "base_url" in call_kwargs.kwargs
            assert call_kwargs.kwargs["base_url"] is not None

    async def test_client_uses_ollama_timeout(self):
        """AsyncClient should be initialized with OLLAMA_TIMEOUT."""
        summarizer = LLMSummarizer()

        with patch("echo.summarizer.llm_summarizer.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=_mock_health_response(200))
            MockClient.return_value = instance

            await summarizer.start()

            call_kwargs = MockClient.call_args
            assert "timeout" in call_kwargs.kwargs

    async def test_generate_request_uses_ollama_model(self):
        """POST to /api/generate should include the configured model name."""
        summarizer = LLMSummarizer()
        summarizer._ollama_available = True
        summarizer._client = AsyncMock()
        summarizer._client.post = AsyncMock(
            return_value=_mock_generate_response("Summary.")
        )

        event = _make_agent_message_event(text="Some long text about code changes.")
        await summarizer.summarize(event)

        call_args = summarizer._client.post.call_args
        json_body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert json_body["model"] is not None
        assert json_body["stream"] is False
        assert "prompt" in json_body

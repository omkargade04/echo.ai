"""Tests for echo.stt.response_dispatcher — ResponseDispatcher."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from echo.stt.response_dispatcher import ResponseDispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_no_dispatch_env(monkeypatch):
    """Clear all environment variables that influence dispatch detection."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr("echo.stt.response_dispatcher.DISPATCH_METHOD", "")


@pytest.fixture
def dispatcher() -> ResponseDispatcher:
    """Return a fresh, un-started ResponseDispatcher."""
    return ResponseDispatcher()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_proc(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Build a mock subprocess result."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout, stderr))
    mock_proc.returncode = returncode
    return mock_proc


async def _mock_subprocess_success(*args, **kwargs):
    return _make_mock_proc(returncode=0)


async def _mock_subprocess_failure(*args, **kwargs):
    return _make_mock_proc(returncode=1, stderr=b"command failed")


async def _mock_subprocess_exception(*args, **kwargs):
    raise OSError("subprocess launch failed")


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestDetection:
    """Tests for platform/tool detection logic."""

    def test_detect_tmux(self, monkeypatch):
        """TMUX env set + tmux binary available -> 'tmux'."""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/tmux" if cmd == "tmux" else None)
        assert ResponseDispatcher._detect_method() == "tmux"

    def test_detect_applescript_on_darwin(self, monkeypatch):
        """No TMUX, platform=darwin, osascript found -> 'applescript'."""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr(
            "shutil.which",
            lambda cmd: "/usr/bin/osascript" if cmd == "osascript" else None,
        )
        assert ResponseDispatcher._detect_method() == "applescript"

    def test_detect_xdotool_on_linux(self, monkeypatch):
        """No TMUX, platform=linux, xdotool+DISPLAY -> 'xdotool'."""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr(
            "shutil.which",
            lambda cmd: "/usr/bin/xdotool" if cmd == "xdotool" else None,
        )
        assert ResponseDispatcher._detect_method() == "xdotool"

    def test_detect_none_when_nothing_available(self, monkeypatch):
        """No TMUX, no osascript, no xdotool -> None."""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        assert ResponseDispatcher._detect_method() is None

    def test_detect_tmux_priority_over_applescript(self, monkeypatch):
        """TMUX set on darwin -> 'tmux' (not applescript)."""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr(
            "shutil.which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in ("tmux", "osascript") else None,
        )
        assert ResponseDispatcher._detect_method() == "tmux"

    async def test_forced_dispatch_method(self, dispatcher, monkeypatch):
        """Set DISPATCH_METHOD='tmux' -> uses 'tmux' regardless of env."""
        monkeypatch.setattr("echo.stt.response_dispatcher.DISPATCH_METHOD", "tmux")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        await dispatcher.start()

        assert dispatcher.is_available is True
        assert dispatcher.method == "tmux"


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------


class TestDispatch:
    """Tests for the dispatch method with mocked subprocesses."""

    async def test_dispatch_tmux_success(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """Mock subprocess success for tmux -> True."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", _mock_subprocess_success
        )
        dispatcher._available = True
        dispatcher._method = "tmux"

        result = await dispatcher.dispatch("hello world")
        assert result is True

    async def test_dispatch_tmux_failure(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """Mock subprocess returncode=1 for tmux -> False."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", _mock_subprocess_failure
        )
        dispatcher._available = True
        dispatcher._method = "tmux"

        result = await dispatcher.dispatch("hello world")
        assert result is False

    async def test_dispatch_applescript_success(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """Mock subprocess success for applescript -> True."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", _mock_subprocess_success
        )
        dispatcher._available = True
        dispatcher._method = "applescript"

        result = await dispatcher.dispatch("hello world")
        assert result is True

    async def test_dispatch_xdotool_success(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """Mock subprocess success for xdotool -> True (two subprocess calls)."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", _mock_subprocess_success
        )
        dispatcher._available = True
        dispatcher._method = "xdotool"

        result = await dispatcher.dispatch("hello world")
        assert result is True

    async def test_dispatch_returns_false_when_unavailable(self, dispatcher, mock_no_dispatch_env):
        """When _available=False, dispatch returns False immediately."""
        dispatcher._available = False
        dispatcher._method = None

        result = await dispatcher.dispatch("hello world")
        assert result is False

    async def test_dispatch_handles_exception(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """Subprocess raises exception -> returns False, no crash."""
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", _mock_subprocess_exception
        )
        dispatcher._available = True
        dispatcher._method = "tmux"

        result = await dispatcher.dispatch("hello world")
        assert result is False


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for start/stop lifecycle."""

    async def test_start_sets_available_when_detected(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """start() with a detected method -> is_available True."""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/tmux" if cmd == "tmux" else None)

        await dispatcher.start()

        assert dispatcher.is_available is True
        assert dispatcher.method == "tmux"

    async def test_start_unavailable_when_no_method(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """start() with nothing available -> is_available False."""
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        await dispatcher.start()

        assert dispatcher.is_available is False
        assert dispatcher.method is None

    async def test_stop_clears_state(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """After start+stop -> not available, method None."""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
        monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/tmux" if cmd == "tmux" else None)

        await dispatcher.start()
        assert dispatcher.is_available is True
        assert dispatcher.method == "tmux"

        await dispatcher.stop()
        assert dispatcher.is_available is False
        assert dispatcher.method is None


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge-case coverage."""

    async def test_dispatch_unknown_method_returns_false(self, dispatcher, mock_no_dispatch_env):
        """Unknown method name -> False."""
        dispatcher._available = True
        dispatcher._method = "unknown_tool"

        result = await dispatcher.dispatch("hello")
        assert result is False

    async def test_xdotool_type_succeeds_but_key_return_fails(self, dispatcher, monkeypatch, mock_no_dispatch_env):
        """First xdotool call succeeds, second (key Return) fails -> False."""
        call_count = 0

        async def alternating_subprocess(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_mock_proc(returncode=0)
            return _make_mock_proc(returncode=1, stderr=b"key failed")

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", alternating_subprocess
        )
        dispatcher._available = True
        dispatcher._method = "xdotool"

        result = await dispatcher.dispatch("hello")
        assert result is False

    async def test_stop_without_start_is_safe(self, dispatcher):
        """stop() before start() should not crash."""
        await dispatcher.stop()
        assert dispatcher.is_available is False
        assert dispatcher.method is None

"""Platform-specific keystroke injection for sending responses to Claude Code terminal."""

import asyncio
import logging
import os
import shutil
import sys

from echo.config import DISPATCH_METHOD

logger = logging.getLogger(__name__)


class ResponseDispatcher:
    """Injects response text into the Claude Code terminal.

    Detection priority:
    1. tmux — Check TMUX env var. Most reliable, cross-platform.
    2. AppleScript (macOS) — Check sys.platform == 'darwin' and osascript available.
    3. xdotool (Linux) — Check xdotool available and DISPLAY env var set.
    """

    def __init__(self) -> None:
        self._available: bool = False
        self._method: str | None = None

    async def start(self) -> None:
        """Detect platform and available injection methods."""
        if DISPATCH_METHOD:
            self._method = DISPATCH_METHOD
            self._available = True
            logger.info("Response dispatch method forced: %s", DISPATCH_METHOD)
            return

        self._method = self._detect_method()
        self._available = self._method is not None
        if self._available:
            logger.info("Response dispatch method detected: %s", self._method)
        else:
            logger.warning("No response dispatch method available")

    async def stop(self) -> None:
        """Release resources."""
        self._available = False
        self._method = None

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def method(self) -> str | None:
        """Return the injection method name ('applescript', 'xdotool', 'tmux') or None."""
        return self._method

    async def dispatch(self, text: str) -> bool:
        """Inject text + Enter into the Claude Code terminal.
        Returns True if dispatch succeeded, False otherwise.
        """
        if not self._available or not self._method:
            logger.warning("Dispatch unavailable — cannot send response")
            return False

        try:
            if self._method == "tmux":
                return await self._dispatch_tmux(text)
            elif self._method == "applescript":
                return await self._dispatch_applescript(text)
            elif self._method == "xdotool":
                return await self._dispatch_xdotool(text)
            else:
                logger.warning("Unknown dispatch method: %s", self._method)
                return False
        except Exception:
            logger.warning("Dispatch failed", exc_info=True)
            return False

    async def _dispatch_tmux(self, text: str) -> bool:
        """tmux: Use tmux send-keys (works cross-platform if in tmux)."""
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", text, "Enter",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("tmux send-keys failed: %s", stderr.decode())
            return False
        return True

    async def _dispatch_applescript(self, text: str) -> bool:
        """macOS: Use osascript to send keystrokes to Terminal/iTerm2."""
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            'tell application "System Events"\n'
            f'    keystroke "{escaped}"\n'
            '    delay 0.1\n'
            '    keystroke return\n'
            'end tell'
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("AppleScript dispatch failed: %s", stderr.decode())
            return False
        return True

    async def _dispatch_xdotool(self, text: str) -> bool:
        """Linux X11: Use xdotool to type text."""
        proc = await asyncio.create_subprocess_exec(
            "xdotool", "type", "--clearmodifiers", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("xdotool type failed: %s", stderr.decode())
            return False

        proc2 = await asyncio.create_subprocess_exec(
            "xdotool", "key", "Return",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()
        return proc2.returncode == 0

    @staticmethod
    def _detect_method() -> str | None:
        """Detect the best available injection method for the current platform."""
        # Priority 1: tmux
        if os.environ.get("TMUX"):
            if shutil.which("tmux"):
                return "tmux"

        # Priority 2: AppleScript (macOS)
        if sys.platform == "darwin":
            if shutil.which("osascript"):
                return "applescript"

        # Priority 3: xdotool (Linux X11)
        if shutil.which("xdotool") and os.environ.get("DISPLAY"):
            return "xdotool"

        return None

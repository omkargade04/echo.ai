"""Install and uninstall Voice Copilot hooks in Claude Code settings."""

import json
import logging
import shutil
from pathlib import Path

from voice_copilot.config import CLAUDE_SETTINGS_PATH, HOOKS_DIR

logger = logging.getLogger(__name__)

# Path to the bundled shell script that ships with this package.
_BUNDLED_SCRIPT: Path = Path(__file__).resolve().parent.parent / "hooks" / "on_event.sh"

# The command string used in hook entries.  This doubles as the identifier
# that lets us distinguish Voice Copilot hooks from user hooks.
_HOOK_COMMAND: str = "~/.voice-copilot/hooks/on_event.sh"

# Hook configuration we inject into Claude Code's settings.json.
# Notification hooks are *synchronous* (no "async" key) so Claude Code
# waits for the script to finish before continuing — giving time for
# the developer to be alerted.  All other hooks are async.
_VOICE_COPILOT_HOOKS: dict = {
    "PostToolUse": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": _HOOK_COMMAND,
                    "async": True,
                }
            ],
        }
    ],
    "Notification": [
        {
            "matcher": "permission_prompt|idle_prompt",
            "hooks": [
                {
                    "type": "command",
                    "command": _HOOK_COMMAND,
                }
            ],
        }
    ],
    "Stop": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": _HOOK_COMMAND,
                    "async": True,
                }
            ],
        }
    ],
    "SessionStart": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": _HOOK_COMMAND,
                    "async": True,
                }
            ],
        }
    ],
    "SessionEnd": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": _HOOK_COMMAND,
                    "async": True,
                }
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_hooks() -> None:
    """Install Voice Copilot hooks into Claude Code settings.

    1. Copy ``on_event.sh`` into ``~/.voice-copilot/hooks/``.
    2. Read ``~/.claude/settings.json`` (creating it if absent).
    3. Merge Voice Copilot hook entries, preserving existing user hooks.
    4. Write the updated settings back.

    A ``.bak`` backup of the original settings file is created before
    any modification.
    """
    _deploy_hook_script()
    settings = _read_settings()
    _backup_settings()
    _merge_hooks(settings)
    _write_settings(settings)
    logger.info("Voice Copilot hooks installed successfully")


def uninstall_hooks() -> None:
    """Remove Voice Copilot hooks from Claude Code settings.

    Only removes hook entries whose ``command`` matches the Voice Copilot
    script path.  All other user hooks are preserved.  The
    ``~/.voice-copilot/hooks/`` directory is cleaned up afterwards.
    """
    if not CLAUDE_SETTINGS_PATH.exists():
        logger.info("No Claude settings file found — nothing to uninstall")
        return

    settings = _read_settings()
    _backup_settings()
    _remove_hooks(settings)
    _write_settings(settings)
    _cleanup_hook_script()
    logger.info("Voice Copilot hooks uninstalled successfully")


def are_hooks_installed() -> bool:
    """Return ``True`` if Voice Copilot hooks are present in Claude settings."""
    if not CLAUDE_SETTINGS_PATH.exists():
        return False

    try:
        settings = _read_settings()
    except Exception:
        logger.debug("Could not read settings — assuming hooks not installed")
        return False

    hooks: dict = settings.get("hooks", {})
    for _event_name, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if _entry_is_voice_copilot(entry):
                return True
    return False


# ---------------------------------------------------------------------------
# Hook script deployment
# ---------------------------------------------------------------------------


def _deploy_hook_script() -> None:
    """Copy the bundled ``on_event.sh`` to ``~/.voice-copilot/hooks/``."""
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    dest = HOOKS_DIR / "on_event.sh"

    if not _BUNDLED_SCRIPT.exists():
        raise FileNotFoundError(
            f"Bundled hook script not found at {_BUNDLED_SCRIPT}. "
            "The package may be installed incorrectly."
        )

    shutil.copy2(_BUNDLED_SCRIPT, dest)
    dest.chmod(0o755)
    logger.debug("Deployed hook script to %s", dest)


def _cleanup_hook_script() -> None:
    """Remove the deployed hook script from ``~/.voice-copilot/hooks/``."""
    script = HOOKS_DIR / "on_event.sh"
    if script.exists():
        script.unlink()
        logger.debug("Removed hook script %s", script)

    # Remove the hooks directory if it is now empty.
    try:
        if HOOKS_DIR.exists() and not any(HOOKS_DIR.iterdir()):
            HOOKS_DIR.rmdir()
            logger.debug("Removed empty hooks directory %s", HOOKS_DIR)
    except OSError as exc:
        logger.debug("Could not remove hooks directory: %s", exc)


# ---------------------------------------------------------------------------
# Settings file helpers
# ---------------------------------------------------------------------------


def _read_settings() -> dict:
    """Read and return ``~/.claude/settings.json``, or an empty dict."""
    if not CLAUDE_SETTINGS_PATH.exists():
        logger.debug("Settings file does not exist — starting fresh")
        return {}

    try:
        text = CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning(
                "settings.json root is not an object (%s) — starting fresh",
                type(data).__name__,
            )
            return {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read settings.json (%s) — starting fresh", exc)
        return {}


def _write_settings(settings: dict) -> None:
    """Write *settings* to ``~/.claude/settings.json`` with pretty formatting."""
    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        CLAUDE_SETTINGS_PATH.write_text(
            json.dumps(settings, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.debug("Wrote settings to %s", CLAUDE_SETTINGS_PATH)
    except OSError as exc:
        logger.error("Failed to write settings.json: %s", exc)
        raise


def _backup_settings() -> None:
    """Create a ``.bak`` copy of the settings file if it exists."""
    if not CLAUDE_SETTINGS_PATH.exists():
        return

    backup = CLAUDE_SETTINGS_PATH.with_suffix(".json.bak")
    try:
        shutil.copy2(CLAUDE_SETTINGS_PATH, backup)
        logger.debug("Backed up settings to %s", backup)
    except OSError as exc:
        logger.warning("Could not create settings backup: %s", exc)


# ---------------------------------------------------------------------------
# Merge / remove logic
# ---------------------------------------------------------------------------


def _merge_hooks(settings: dict) -> None:
    """Merge Voice Copilot hooks into *settings*, preserving user hooks.

    For each hook event name (e.g. ``PostToolUse``), if the event key
    already exists in settings we append our entries (avoiding
    duplicates).  If it does not exist we create it.
    """
    hooks: dict = settings.setdefault("hooks", {})

    for event_name, vc_entries in _VOICE_COPILOT_HOOKS.items():
        existing: list = hooks.get(event_name, [])
        if not isinstance(existing, list):
            logger.warning(
                "hooks.%s is not a list (%s) — overwriting with Voice Copilot hooks",
                event_name,
                type(existing).__name__,
            )
            existing = []

        # Only add entries that are not already present.
        for vc_entry in vc_entries:
            if not _list_contains_voice_copilot_entry(existing):
                existing.append(vc_entry)
                logger.debug("Added Voice Copilot hook entry for %s", event_name)
            else:
                logger.debug(
                    "Voice Copilot hook entry for %s already present — skipping",
                    event_name,
                )

        hooks[event_name] = existing


def _remove_hooks(settings: dict) -> None:
    """Remove Voice Copilot hook entries from *settings*, preserving user hooks.

    Entries are identified by the ``command`` field matching
    ``_HOOK_COMMAND``.  Empty event-name lists are removed entirely.
    """
    hooks: dict = settings.get("hooks", {})

    keys_to_delete: list[str] = []

    for event_name, entries in hooks.items():
        if not isinstance(entries, list):
            continue

        filtered = [e for e in entries if not _entry_is_voice_copilot(e)]

        if filtered:
            hooks[event_name] = filtered
        else:
            keys_to_delete.append(event_name)

    for key in keys_to_delete:
        del hooks[key]
        logger.debug("Removed empty hooks key: %s", key)

    # If the hooks dict is now empty, remove it from settings.
    if not hooks:
        settings.pop("hooks", None)
        logger.debug("Removed empty hooks object from settings")


# ---------------------------------------------------------------------------
# Entry identification
# ---------------------------------------------------------------------------


def _entry_is_voice_copilot(entry: dict) -> bool:
    """Return ``True`` if *entry* was installed by Voice Copilot.

    Identification is based on the ``command`` field of any hook in the
    entry's ``hooks`` list matching ``_HOOK_COMMAND``.
    """
    if not isinstance(entry, dict):
        return False

    inner_hooks = entry.get("hooks", [])
    if not isinstance(inner_hooks, list):
        return False

    return any(
        isinstance(h, dict) and h.get("command") == _HOOK_COMMAND for h in inner_hooks
    )


def _list_contains_voice_copilot_entry(entries: list) -> bool:
    """Return ``True`` if any entry in *entries* belongs to Voice Copilot."""
    return any(_entry_is_voice_copilot(e) for e in entries)

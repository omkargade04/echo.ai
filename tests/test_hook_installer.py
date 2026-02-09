"""Tests for echo.interceptors.hook_installer — Install/uninstall hooks.

All tests use tmp_path and monkeypatch to override config paths.
We NEVER touch the user's real ~/.claude/settings.json.
"""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Set up isolated filesystem paths for the installer under test.

    Creates:
      - A fake ~/.claude/ directory with a settings.json location
      - A fake ~/.echo-copilot/hooks/ directory
      - A fake bundled on_event.sh script

    Monkeypatches the config module constants AND the hook_installer module
    constants that were already imported.
    """
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"

    vc_dir = tmp_path / ".echo-copilot"
    hooks_dir = vc_dir / "hooks"

    # Create the fake bundled script that _deploy_hook_script() copies
    bundled_script = tmp_path / "bundled_on_event.sh"
    bundled_script.write_text("#!/bin/bash\n# fake hook script\n")

    # Patch config module paths
    import echo.config as config_mod

    monkeypatch.setattr(config_mod, "CLAUDE_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(config_mod, "ECHO_DIR", vc_dir)
    monkeypatch.setattr(config_mod, "HOOKS_DIR", hooks_dir)

    # Patch hook_installer module paths (already imported from config)
    import echo.interceptors.hook_installer as installer_mod

    monkeypatch.setattr(installer_mod, "CLAUDE_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(installer_mod, "HOOKS_DIR", hooks_dir)
    monkeypatch.setattr(installer_mod, "_BUNDLED_SCRIPT", bundled_script)

    return {
        "settings_path": settings_path,
        "hooks_dir": hooks_dir,
        "vc_dir": vc_dir,
        "bundled_script": bundled_script,
    }


def _read_settings(settings_path: Path) -> dict:
    """Read and return the settings JSON from the given path."""
    return json.loads(settings_path.read_text(encoding="utf-8"))


def _write_settings(settings_path: Path, data: dict) -> None:
    """Write a settings dict to the given path."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# install_hooks
# ---------------------------------------------------------------------------


class TestInstallHooks:
    """Tests for install_hooks()."""

    def test_install_creates_settings_when_file_does_not_exist(self, fake_env):
        from echo.interceptors.hook_installer import install_hooks

        settings_path = fake_env["settings_path"]
        assert not settings_path.exists()

        install_hooks()

        assert settings_path.exists()
        data = _read_settings(settings_path)
        assert "hooks" in data
        # All six hook event names should be present
        for event_name in [
            "PostToolUse",
            "PermissionRequest",
            "Notification",
            "Stop",
            "SessionStart",
            "SessionEnd",
        ]:
            assert event_name in data["hooks"], f"{event_name} missing from hooks"

    def test_install_merges_with_existing_settings(self, fake_env):
        """Existing user settings and hooks are preserved."""
        from echo.interceptors.hook_installer import install_hooks

        settings_path = fake_env["settings_path"]

        # Pre-existing settings with a user hook under PostToolUse
        existing = {
            "theme": "dark",
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "my-custom-logger.sh",
                            }
                        ],
                    }
                ],
            },
        }
        _write_settings(settings_path, existing)

        install_hooks()

        data = _read_settings(settings_path)
        # User setting preserved
        assert data["theme"] == "dark"
        # User hook still present
        post_tool_entries = data["hooks"]["PostToolUse"]
        user_commands = [
            h["command"]
            for entry in post_tool_entries
            for h in entry.get("hooks", [])
        ]
        assert "my-custom-logger.sh" in user_commands
        # Echo hook also present
        assert "~/.echo-copilot/hooks/on_event.sh" in user_commands

    def test_install_is_idempotent(self, fake_env):
        """Running install_hooks twice should not duplicate entries."""
        from echo.interceptors.hook_installer import install_hooks

        settings_path = fake_env["settings_path"]

        install_hooks()
        data_first = _read_settings(settings_path)

        install_hooks()
        data_second = _read_settings(settings_path)

        # The number of entries under each key should be the same
        for event_name in data_first.get("hooks", {}):
            assert len(data_second["hooks"][event_name]) == len(
                data_first["hooks"][event_name]
            ), f"Duplicate entries created for {event_name}"

    def test_install_deploys_hook_script(self, fake_env):
        """The bundled on_event.sh should be copied to hooks_dir."""
        from echo.interceptors.hook_installer import install_hooks

        hooks_dir = fake_env["hooks_dir"]

        install_hooks()

        deployed_script = hooks_dir / "on_event.sh"
        assert deployed_script.exists()
        assert deployed_script.stat().st_mode & 0o755  # executable

    def test_install_creates_backup_of_existing_settings(self, fake_env):
        """A .bak file should be created when settings already exist."""
        from echo.interceptors.hook_installer import install_hooks

        settings_path = fake_env["settings_path"]
        _write_settings(settings_path, {"existing": True})

        install_hooks()

        backup_path = settings_path.with_suffix(".json.bak")
        assert backup_path.exists()
        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        assert backup_data == {"existing": True}


# ---------------------------------------------------------------------------
# uninstall_hooks
# ---------------------------------------------------------------------------


class TestUninstallHooks:
    """Tests for uninstall_hooks()."""

    def test_uninstall_removes_echo_hooks(self, fake_env):
        from echo.interceptors.hook_installer import (
            install_hooks,
            uninstall_hooks,
        )

        settings_path = fake_env["settings_path"]

        install_hooks()
        uninstall_hooks()

        data = _read_settings(settings_path)
        # With no user hooks, the hooks key should be removed entirely
        assert "hooks" not in data or data["hooks"] == {}

    def test_uninstall_preserves_user_hooks(self, fake_env):
        from echo.interceptors.hook_installer import (
            install_hooks,
            uninstall_hooks,
        )

        settings_path = fake_env["settings_path"]

        # Set up a user hook, then install Echo hooks
        existing = {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "my-logger.sh"}
                        ],
                    }
                ],
            },
        }
        _write_settings(settings_path, existing)
        install_hooks()
        uninstall_hooks()

        data = _read_settings(settings_path)
        # User hook should still be there
        assert "PostToolUse" in data["hooks"]
        remaining_commands = [
            h["command"]
            for entry in data["hooks"]["PostToolUse"]
            for h in entry.get("hooks", [])
        ]
        assert "my-logger.sh" in remaining_commands
        assert "~/.echo-copilot/hooks/on_event.sh" not in remaining_commands

    def test_uninstall_when_no_settings_file_is_noop(self, fake_env):
        """Uninstalling when settings.json does not exist should not error."""
        from echo.interceptors.hook_installer import uninstall_hooks

        settings_path = fake_env["settings_path"]
        assert not settings_path.exists()
        # Should not raise
        uninstall_hooks()

    def test_uninstall_cleans_up_deployed_script(self, fake_env):
        from echo.interceptors.hook_installer import (
            install_hooks,
            uninstall_hooks,
        )

        hooks_dir = fake_env["hooks_dir"]
        install_hooks()
        assert (hooks_dir / "on_event.sh").exists()

        uninstall_hooks()
        assert not (hooks_dir / "on_event.sh").exists()


# ---------------------------------------------------------------------------
# are_hooks_installed
# ---------------------------------------------------------------------------


class TestAreHooksInstalled:
    """Tests for are_hooks_installed()."""

    def test_returns_false_when_no_settings_file(self, fake_env):
        from echo.interceptors.hook_installer import are_hooks_installed

        assert not fake_env["settings_path"].exists()
        assert are_hooks_installed() is False

    def test_returns_false_when_no_hooks_section(self, fake_env):
        from echo.interceptors.hook_installer import are_hooks_installed

        _write_settings(fake_env["settings_path"], {"theme": "dark"})
        assert are_hooks_installed() is False

    def test_returns_true_after_install(self, fake_env):
        from echo.interceptors.hook_installer import (
            are_hooks_installed,
            install_hooks,
        )

        install_hooks()
        assert are_hooks_installed() is True

    def test_returns_false_after_uninstall(self, fake_env):
        from echo.interceptors.hook_installer import (
            are_hooks_installed,
            install_hooks,
            uninstall_hooks,
        )

        install_hooks()
        assert are_hooks_installed() is True
        uninstall_hooks()
        assert are_hooks_installed() is False

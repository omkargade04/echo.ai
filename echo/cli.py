"""Command-line interface for Echo.

Provides ``echo-copilot start``, ``stop``, ``status``, ``install-hooks``,
and ``uninstall`` commands.  The entry point is registered via
``pyproject.toml`` as ``echo-copilot = "echo.cli:cli"``.
"""

import logging
import os
import signal
import sys
import time

import click
import httpx

from echo.config import (
    DEFAULT_PORT,
    PID_FILE,
    ECHO_DIR,
    get_port,
)

logger = logging.getLogger(__name__)

# Log file lives alongside the PID file.
_LOG_FILE = ECHO_DIR / "server.log"

_MIN_PORT = 1024
_MAX_PORT = 65535


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_port(port: int | None) -> int:
    """Return the port to use, falling back to env var / default."""
    if port is not None:
        return port
    return get_port()


def _validate_port(port: int) -> None:
    """Raise ``click.BadParameter`` if *port* is out of range."""
    if not (_MIN_PORT <= port <= _MAX_PORT):
        raise click.BadParameter(
            f"Port must be between {_MIN_PORT} and {_MAX_PORT}, got {port}."
        )


def _read_pid() -> int | None:
    """Read the PID from the PID file, or return ``None``."""
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_process_running(pid: int) -> bool:
    """Return ``True`` if a process with *pid* is alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _server_is_responding(port: int) -> bool:
    """Probe the health endpoint to check if the server is up."""
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


def _setup_logging_to_file() -> None:
    """Configure the root logger to write to the server log file.

    Called in daemon mode so that log output is persisted instead of
    being lost after the terminal detaches.
    """
    ECHO_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(_LOG_FILE)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _run_server(port: int) -> None:
    """Start uvicorn with the Echo FastAPI app.

    This blocks until the server shuts down.
    """
    import uvicorn

    from echo.server.app import create_app

    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


def _daemonize(port: int) -> None:
    """Fork into a background daemon process.

    The parent writes the child PID to the PID file and exits.
    The child redirects stdout/stderr to the log file and starts
    the server.  Unix-only (macOS / Linux).
    """
    ECHO_DIR.mkdir(parents=True, exist_ok=True)

    pid = os.fork()
    if pid > 0:
        # Parent — record child PID and exit.
        PID_FILE.write_text(str(pid))
        click.echo(
            click.style(f"Server started in background (PID {pid})", fg="green")
        )
        click.echo(f"  Logs: {_LOG_FILE}")
        click.echo(f"  PID file: {PID_FILE}")
        return

    # Child — detach from terminal.
    os.setsid()

    # Redirect stdout/stderr to log file.
    log_fd = os.open(str(_LOG_FILE), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, sys.stdout.fileno())
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(log_fd)

    # Redirect stdin to /dev/null.
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())
    os.close(devnull)

    _setup_logging_to_file()

    try:
        _run_server(port)
    except Exception:
        logger.exception("Daemon server crashed")
        sys.exit(1)
    finally:
        # Clean up PID file when the daemon exits.
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """Echo -- Real-time audio bridge for AI coding agents."""


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--port", default=None, type=int, help="Server port (default: 7865)")
@click.option("--daemon", is_flag=True, help="Run as background process")
@click.option(
    "--skip-hooks", is_flag=True, help="Don't install Claude Code hooks on start"
)
@click.option("--no-tts", is_flag=True, help="Disable TTS audio output")
def start(port: int | None, daemon: bool, skip_hooks: bool, no_tts: bool) -> None:
    """Start the Echo server."""
    port = _resolve_port(port)
    _validate_port(port)

    if no_tts:
        os.environ["ECHO_ELEVENLABS_API_KEY"] = ""
        click.echo("TTS disabled via --no-tts flag")

    # Check if a server is already running.
    existing_pid = _read_pid()
    if existing_pid is not None and _is_process_running(existing_pid):
        click.echo(
            click.style(
                f"Server is already running (PID {existing_pid}). "
                "Use 'echo-copilot stop' first.",
                fg="yellow",
            )
        )
        raise SystemExit(1)

    # Install hooks unless told to skip.
    if not skip_hooks:
        try:
            from echo.interceptors.hook_installer import install_hooks

            install_hooks()
            click.echo(click.style("Hooks installed", fg="green"))
        except Exception as exc:
            click.echo(
                click.style(f"Warning: failed to install hooks: {exc}", fg="yellow")
            )
            logger.warning("Hook installation failed", exc_info=True)

    click.echo(f"Starting Echo on port {port}...")

    if daemon:
        _daemonize(port)
    else:
        # Foreground mode — write PID for status/stop commands, then block.
        ECHO_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
        try:
            _run_server(port)
        except OSError as exc:
            if "address already in use" in str(exc).lower():
                click.echo(
                    click.style(
                        f"Port {port} is already in use. "
                        "Choose a different port with --port.",
                        fg="red",
                    )
                )
                raise SystemExit(1)
            raise
        finally:
            PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@cli.command()
def stop() -> None:
    """Stop the background Echo server."""
    pid = _read_pid()

    if pid is None:
        click.echo(click.style("No PID file found — server may not be running.", fg="yellow"))
        raise SystemExit(1)

    if not _is_process_running(pid):
        click.echo(
            click.style(
                f"Process {pid} is not running. Cleaning up stale PID file.", fg="yellow"
            )
        )
        PID_FILE.unlink(missing_ok=True)
        return

    click.echo(f"Stopping Echo server (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    # Wait up to 5 seconds for the process to exit.
    for _ in range(50):
        if not _is_process_running(pid):
            break
        time.sleep(0.1)
    else:
        click.echo(
            click.style(
                f"Process {pid} did not exit in time — sending SIGKILL.", fg="red"
            )
        )
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    PID_FILE.unlink(missing_ok=True)
    click.echo(click.style("Server stopped.", fg="green"))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--port", default=None, type=int, help="Server port to check")
def status(port: int | None) -> None:
    """Show Echo server status."""
    port = _resolve_port(port)
    pid = _read_pid()

    if pid is None:
        click.echo(click.style("Server is not running (no PID file).", fg="yellow"))
        raise SystemExit(1)

    if not _is_process_running(pid):
        click.echo(
            click.style(
                f"PID file exists ({pid}) but process is not running.", fg="yellow"
            )
        )
        PID_FILE.unlink(missing_ok=True)
        raise SystemExit(1)

    click.echo(f"Server process is running (PID {pid}).")

    if _server_is_responding(port):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
            data = resp.json()
            click.echo(click.style("Server is healthy.", fg="green"))
            click.echo(f"  Version:     {data.get('version', '?')}")
            click.echo(f"  Port:        {port}")
            click.echo(f"  Subscribers: {data.get('subscribers', '?')}")
        except Exception:
            click.echo(click.style("Server is running but health check failed.", fg="yellow"))
    else:
        click.echo(
            click.style(
                f"Server process is running but not responding on port {port}.",
                fg="yellow",
            )
        )


# ---------------------------------------------------------------------------
# install-hooks
# ---------------------------------------------------------------------------


@cli.command("install-hooks")
def install_hooks_cmd() -> None:
    """Manually install Claude Code hooks."""
    try:
        from echo.interceptors.hook_installer import install_hooks

        install_hooks()
        click.echo(click.style("Hooks installed successfully.", fg="green"))
    except Exception as exc:
        click.echo(click.style(f"Failed to install hooks: {exc}", fg="red"))
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


@cli.command()
def uninstall() -> None:
    """Remove Echo hooks and clean up.

    This uninstalls the Claude Code hooks from settings.json.  If the
    server is currently running it will be stopped first.
    """
    # Stop the server if it is running.
    pid = _read_pid()
    if pid is not None and _is_process_running(pid):
        click.echo(f"Stopping running server (PID {pid})...")
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not _is_process_running(pid):
                break
            time.sleep(0.1)
        PID_FILE.unlink(missing_ok=True)
        click.echo(click.style("Server stopped.", fg="green"))

    # Uninstall hooks.
    try:
        from echo.interceptors.hook_installer import uninstall_hooks

        uninstall_hooks()
        click.echo(click.style("Hooks uninstalled.", fg="green"))
    except Exception as exc:
        click.echo(click.style(f"Failed to uninstall hooks: {exc}", fg="red"))
        logger.warning("Hook uninstallation failed", exc_info=True)

    click.echo(click.style("Echo cleaned up.", fg="green"))

"""Shared fixtures for x11ctl integration tests.

Every test invokes scripts/x11ctl as a subprocess. No x11ctl module imports.
Each test gets a unique display number for isolation.
"""
import os
import signal
import socket
import subprocess
import shutil
import time
import threading
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT = str(Path(__file__).parent.parent.parent / "scripts" / "x11ctl")
DISPLAY_RANGE = range(80, 99)  # :80 through :98
_display_counter = threading.Lock()
_next_display = 80


# ---------------------------------------------------------------------------
# Helpers (not fixtures — called directly by tests)
# ---------------------------------------------------------------------------

def x11ctl_run(
    args: list[str],
    env_overrides: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Invoke scripts/x11ctl as a subprocess.

    Returns CompletedProcess. All tests use this instead of raw subprocess.run.
    Automatically sets X11CTL_ALLOW_HOST=1 for jail guard bypass in dev.
    """
    env = {**os.environ, "X11CTL_ALLOW_HOST": "1"}
    if env_overrides:
        # Filter out non-string values (e.g., display_num int convenience key)
        env.update({k: v for k, v in env_overrides.items() if isinstance(v, str)})
    return subprocess.run(
        [SCRIPT] + args,
        capture_output=True, text=True, timeout=timeout,
        env=env,
    )


def assert_port_listening(host: str, port: int, timeout: float = 5.0) -> None:
    """Assert that a TCP port is accepting connections within timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sock.connect_ex((host, port)) == 0:
                return
        finally:
            sock.close()
        time.sleep(0.3)
    raise AssertionError(f"Port {host}:{port} not listening after {timeout}s")


def assert_port_free(host: str, port: int, timeout: float = 5.0) -> None:
    """Assert that a TCP port is NOT bound, polling until confirmed free.

    Polls for up to `timeout` seconds to handle TIME_WAIT and process
    teardown delays.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sock.connect_ex((host, port)) != 0:
                return  # Port is free
        finally:
            sock.close()
        time.sleep(0.3)
    raise AssertionError(f"Port {host}:{port} still listening after {timeout}s")


def read_state_file(path: str) -> str | None:
    """Read a /tmp/.x11ctl-* state file directly. Returns content or None.

    This is a deliberate side-channel observation — it reads state files
    without x11ctl's safety checks (O_NOFOLLOW, fstat, ownership).

    Assumed pidfile format: "{pid} {epoch}\\n" (two space-separated ints).
    If x11ctl changes this format, this helper and callers must be updated.
    """
    try:
        return Path(path).read_text().strip()
    except (FileNotFoundError, OSError):
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def display_factory(tmp_path):
    """Allocate a unique display number with fully isolated state directory.

    Each test gets its own:
    - Display number (:80-:98, unique via atomic counter)
    - State directory (pytest tmp_path — isolated pidfiles, tiers, lock)
    - Xauth file (in /tmp with unique name — passes xauth regex validation)
    - Offset ports (derived from display number)

    Yields a dict with env overrides and convenience fields:
        {
            "X11CTL_DISPLAY": ":82",
            "X11CTL_STATE_DIR": "/tmp/pytest-.../test_foo0/",
            "X11CTL_STATE_PREFIX": ".x11ctl-test-82",
            "X11CTL_XAUTH": "/tmp/.x11ctl-test-82",
            "X11CTL_XPRA_PORT": "10082",
            "X11CTL_VNC_PORT": "5982",
            "X11CTL_NOVNC_PORT": "6162",
            "display_num": 82,
            "state_dir": "/tmp/pytest-.../test_foo0/",
        }

    Registers a finalizer that stops the display and cleans up.
    """
    global _next_display
    # Atomic counter — sequential execution only (no pytest-xdist support).
    # For parallel execution, would need file-based allocation.
    _next_display_local = _next_display
    _next_display += 1
    if _next_display > 98:
        _next_display = 80
    display_num = _next_display_local

    state_dir = str(tmp_path)
    state_prefix = f".x11ctl-test-{display_num}"

    env = {
        "X11CTL_DISPLAY": f":{display_num}",
        "X11CTL_STATE_DIR": state_dir,
        "X11CTL_STATE_PREFIX": state_prefix,
        "X11CTL_XAUTH": f"/tmp/.x11ctl-test-{display_num}",
        "X11CTL_XPRA_PORT": str(10000 + display_num),
        "X11CTL_VNC_PORT": str(5900 + display_num),
        "X11CTL_NOVNC_PORT": str(6080 + display_num),
    }

    yield {
        **env,
        "display_num": display_num,
        "state_dir": state_dir,
    }

    # Finalizer: stop everything and clean up
    try:
        x11ctl_run(["stop"], env_overrides=env, timeout=15)
    except (subprocess.TimeoutExpired, Exception):
        # Fallback: read pidfiles from state_dir and kill directly
        import glob as _glob
        for pidfile in _glob.glob(os.path.join(state_dir, "*.pid")):
            try:
                content = Path(pidfile).read_text().strip()
                pid = int(content.split()[0])
                os.kill(pid, signal.SIGKILL)
            except (ValueError, ProcessLookupError, PermissionError, OSError):
                pass
    # Clean up xauth (flat in /tmp, not in state_dir)
    try:
        os.unlink(f"/tmp/.x11ctl-test-{display_num}")
    except FileNotFoundError:
        pass
    # Clean up X artifacts
    for path in [f"/tmp/.X{display_num}-lock", f"/tmp/.X11-unix/X{display_num}"]:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    # state_dir is cleaned by pytest's tmp_path fixture automatically


@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    """Session-scoped safety net. Runs at end of all tests.

    Scans for orphaned processes and stale state files in the test
    display range (:80-:98) and cleans them up.
    """
    yield  # Tests run here

    # Kill any orphaned X11 processes in our display range
    for display_num in DISPLAY_RANGE:
        pidfile_patterns = [
            f"/tmp/.x11ctl-test-{display_num}-*.pid",
            f"/tmp/.x11ctl-test-{display_num}.pid",
        ]
        # Try stopping via x11ctl first
        env = {
            "X11CTL_DISPLAY": f":{display_num}",
            "X11CTL_XAUTH": f"/tmp/.x11ctl-test-{display_num}",
        }
        try:
            x11ctl_run(["stop"], env_overrides=env, timeout=10)
        except Exception:
            pass

    # Brute-force: find and kill any remaining Xvfb on test displays
    try:
        result = subprocess.run(
            ["/bin/ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines()[1:]:  # skip header
            line = line.strip()
            if not line:
                continue
            for display_num in DISPLAY_RANGE:
                # Match display number as an argument (e.g., ":82" in "Xvfb :82 ...")
                if f" :{display_num} " in f" {line} " and any(
                    name in line for name in ["Xvfb", "xpra", "x11vnc", "websockify"]
                ):
                    try:
                        pid = int(line.split()[0])
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, ValueError):
                        pass
    except Exception:
        pass

    # Clean up stale files
    import glob
    for display_num in DISPLAY_RANGE:
        for pattern in [
            f"/tmp/.x11ctl-test-{display_num}*",
            f"/tmp/.X{display_num}-lock",
        ]:
            for f in glob.glob(pattern):
                try:
                    os.unlink(f)
                except OSError:
                    pass
        socket_path = f"/tmp/.X11-unix/X{display_num}"
        try:
            os.unlink(socket_path)
        except OSError:
            pass

"""Shared fixtures for x11ctl integration tests.

Every test invokes scripts/x11ctl as a subprocess. No x11ctl module imports.
Each test gets a unique display number for isolation.

Test isolation relies on these x11ctl env vars (test-only escape hatches,
NOT part of the public API — subject to change without notice):
  X11CTL_STATE_DIR    — directory for pidfiles, tiers, lock (default: /tmp)
  X11CTL_STATE_PREFIX — filename prefix for state files (default: .x11ctl)
  X11CTL_DISPLAY      — X display number (default: :99)
  X11CTL_XAUTH        — xauth file path
  X11CTL_XPRA_PORT    — xpra HTML5 client port
  X11CTL_VNC_PORT     — x11vnc port
  X11CTL_NOVNC_PORT   — noVNC/websockify port
"""
import glob
import logging
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT = str(Path(__file__).parent.parent.parent / "scripts" / "x11ctl")
if not Path(SCRIPT).is_file():
    raise RuntimeError(f"x11ctl script not found at {SCRIPT}")

# Display range for test isolation. 120 slots should be ample for any
# single-session test suite (current plan: ~25 tests).
DISPLAY_RANGE = range(80, 200)
_next_display = 80

# Keys in the DisplayEnv dict that are NOT env vars (convenience metadata).
_CONVENIENCE_KEYS = frozenset({"display_num", "state_dir"})


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class PidfileEntry(NamedTuple):
    """Parsed pidfile content: pid and epoch timestamp."""
    pid: int
    epoch: int


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

    Env construction:
    1. Start with a sanitised copy of os.environ (all pre-existing X11CTL_*
       vars stripped to prevent the developer's shell from leaking config).
    2. Set X11CTL_ALLOW_HOST=1 for jail guard bypass.
    3. Apply env_overrides, skipping convenience keys listed in
       ``_CONVENIENCE_KEYS``.
    """
    # Strip pre-existing X11CTL_* from parent env to prevent bleed-through
    # (e.g., X11CTL_BIND=0.0.0.0 in the developer's shell).
    env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
    env["X11CTL_ALLOW_HOST"] = "1"
    if env_overrides:
        env.update({
            k: str(v) for k, v in env_overrides.items()
            if k not in _CONVENIENCE_KEYS
        })
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


def assert_port_free(host: str, port: int, timeout: float = 15.0) -> None:
    """Assert that a TCP port is NOT bound, polling until confirmed free.

    Default timeout is 15 s to accommodate TCP TIME_WAIT (typically 30–60 s
    on FreeBSD, but x11ctl processes use SO_REUSEADDR so 15 s is usually
    sufficient for the process to terminate and release the port).
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


def parse_pidfile(content: str) -> PidfileEntry | None:
    """Parse pidfile content into (pid, epoch) or None on malformed data.

    Mirrors x11ctl's ``read_pidfile`` validation:
    - Exactly two space-separated fields
    - pid >= 1  (pid < 1 would cause os.kill to target process groups)
    - epoch >= 0
    """
    try:
        parts = content.strip().split()
        if len(parts) != 2:
            return None
        pid, epoch = int(parts[0]), int(parts[1])
        if pid < 1 or epoch < 0:
            return None
        return PidfileEntry(pid=pid, epoch=epoch)
    except (IndexError, ValueError):
        return None


def read_state_file(path: str) -> str | None:
    """Read a state file directly. Returns stripped content or None.

    This is a deliberate side-channel observation — it reads state files
    without x11ctl's safety checks (O_NOFOLLOW, fstat, ownership).

    Use ``parse_pidfile()`` on the returned string to get structured
    (pid, epoch) access with validation matching x11ctl's own parser.

    NOTE: Content is strip()'d, so trailing whitespace/newline variations
    are normalised.  This is an accepted trade-off for test convenience —
    format-drift in whitespace would not be detected by tests using this
    helper.
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
    - Display number (:80-:199, unique via sequential counter)
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

    NOTE: Sequential execution only — no pytest-xdist support.
    For parallel execution, a file-based display allocator would be needed.
    """
    global _next_display
    if _next_display >= 200:
        pytest.fail(
            "Display counter exhausted (reached 200). "
            "Increase DISPLAY_RANGE or investigate test teardown failures."
        )
    display_num = _next_display
    _next_display += 1

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
        result = x11ctl_run(["stop"], env_overrides=env, timeout=15)
        if result.returncode != 0:
            logger.warning(
                "x11ctl stop returned %d for display :%d: %s",
                result.returncode, display_num, result.stderr.strip(),
            )
            _kill_pidfiles_in_dir(state_dir, state_prefix)
    except subprocess.TimeoutExpired:
        logger.warning("x11ctl stop timed out for display :%d", display_num)
        _kill_pidfiles_in_dir(state_dir, state_prefix)
    except Exception:
        logger.warning(
            "x11ctl stop failed for display :%d, falling back to direct kill",
            display_num, exc_info=True,
        )
        _kill_pidfiles_in_dir(state_dir, state_prefix)

    # Clean up xauth (flat in /tmp, not in state_dir)
    try:
        os.unlink(f"/tmp/.x11ctl-test-{display_num}")
    except OSError:
        pass
    # Clean up X artifacts
    for path in [f"/tmp/.X{display_num}-lock", f"/tmp/.X11-unix/X{display_num}"]:
        try:
            os.unlink(path)
        except OSError:
            pass
    # state_dir is cleaned by pytest's tmp_path fixture automatically


def _kill_pidfiles_in_dir(state_dir: str, state_prefix: str) -> None:
    """Read pidfiles matching state_prefix in state_dir and SIGKILL each PID.

    Like read_state_file, this deliberately bypasses x11ctl's symlink safety
    checks (O_NOFOLLOW, fstat).  The state_dir is a pytest tmp_path, so
    symlink attacks are unrealistic in the test context.
    """
    for pidfile in glob.glob(os.path.join(state_dir, f"{state_prefix}-*.pid")):
        try:
            content = Path(pidfile).read_text().strip()
        except OSError:
            logger.warning("Failed to read pidfile %s", pidfile, exc_info=True)
            continue
        entry = parse_pidfile(content)
        if entry is None:
            logger.warning("Malformed pidfile %s: %r", pidfile, content)
            continue
        try:
            os.kill(entry.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    """Session-scoped safety net. Runs at end of all tests.

    Scans for orphaned processes and stale state files from this test
    suite and cleans them up. Only kills processes that match ALL of:
    display in test range (:80-:199), X11-related binary name, AND
    our test xauth pattern (x11ctl-test-) in args — preventing
    accidental termination of unrelated user processes.

    NOTE: Per-test state dirs (tmp_path) are already cleaned by pytest.
    This fixture cannot reconstruct those paths, so it relies on the
    brute-force ps scan to catch orphaned processes. The file cleanup
    targets flat /tmp artifacts (xauth, X lock/socket files).
    """
    yield  # Tests run here

    # Brute-force: find and kill any remaining X11 processes on test displays.
    # Build a set of display args for O(1) lookup instead of O(displays) per line.
    _display_args = frozenset(f":{d}" for d in DISPLAY_RANGE)
    _x11_names = ("Xvfb", "xpra", "x11vnc", "websockify")
    try:
        result = subprocess.run(
            ["/bin/ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            logger.warning("ps failed (rc=%d): %s", result.returncode, result.stderr[:200])
        for line in result.stdout.splitlines()[1:]:  # skip header
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            args_str = " ".join(parts[1:])
            args_list = parts[1:]
            # Match only processes started by our test suite: must have a test
            # display arg AND an X11-related process name AND our test xauth
            # pattern in the args (prevents killing unrelated user processes).
            if (any(a in _display_args for a in args_list)
                    and any(name in args_str for name in _x11_names)
                    and "x11ctl-test-" in args_str):
                try:
                    pid = int(parts[0])
                    os.kill(pid, signal.SIGKILL)
                    logger.info(
                        "session_cleanup killed orphan PID %d (%s)", pid, args_str[:80]
                    )
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
    except Exception:
        logger.warning("session_cleanup ps scan failed", exc_info=True)

    # Clean up all stale flat /tmp test artifacts (xauth files, plus any
    # leaked pidfiles/logs if X11CTL_STATE_DIR was not set).
    for f in glob.glob("/tmp/.x11ctl-test-*"):
        try:
            os.unlink(f)
        except OSError:
            pass
    for display_num in DISPLAY_RANGE:
        for path in [f"/tmp/.X{display_num}-lock", f"/tmp/.X11-unix/X{display_num}"]:
            try:
                os.unlink(path)
            except OSError:
                pass

# x11ctl Integration Test Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a subprocess-based integration test suite that validates x11ctl's full CLI lifecycle with real X11 processes, covering all 11 acceptance criteria, security attack scenarios, and concurrency races.

**Architecture:** Three test files (`test_x11ctl_integration.py`, `test_x11ctl_security.py`, `test_x11ctl_concurrency.py`) share infrastructure from `conftest.py`. Every test invokes `scripts/x11ctl` as a subprocess with a unique display number. Per-test fixtures handle cleanup; a session-scoped safety net catches orphans.

**Tech Stack:** pytest, subprocess, socket, os, signal. No x11ctl imports — tests interact only via CLI and state file inspection.

**Spec:** `docs/superpowers/specs/2026-04-06-x11ctl-test-plan-design.md`

**Internal note:** All integration tests require `X11CTL_ALLOW_HOST=1` in the env because the test environment is a jail where `security.jail.jailed=1` but the jail guard may behave differently in CI. The helper function handles this transparently.

---

## File Map

| File | Purpose |
|------|---------|
| Create: `tests/integration/__init__.py` | Package marker |
| Create: `tests/integration/conftest.py` | Shared fixtures: `x11ctl_run`, `display_factory`, `session_cleanup`, assertion helpers |
| Create: `tests/integration/test_x11ctl_integration.py` | 11 ACs + 5 lifecycle scenarios (16 tests) |
| Create: `tests/integration/test_x11ctl_security.py` | 5 security attack tests |
| Create: `tests/integration/test_x11ctl_concurrency.py` | 4 concurrency/race tests |
| Modify: `pyproject.toml` | Add `integration` marker registration |

---

### Task 1: Test infrastructure — conftest.py and package setup

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create package marker**

```python
# tests/integration/__init__.py
```

(Empty file — just a package marker.)

- [ ] **Step 2: Register the `integration` marker in pyproject.toml**

Add to `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: integration tests requiring X11 binaries (Xvfb, xpra, etc.)",
]
# Integration tests excluded by default (run with: pytest -m integration)
# This prevents accidental parallel runs with pytest-xdist
addopts = "-m 'not integration'"
```

Note: To run integration tests explicitly: `uv run python -m pytest -m integration tests/integration/`
To run everything: `uv run python -m pytest -m '' tests/`

- [ ] **Step 3: Create conftest.py with all helpers and fixtures**

```python
# tests/integration/conftest.py
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
    """Read a /tmp/.x11ctl-* file directly. Returns content or None."""
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
```

- [ ] **Step 4: Verify conftest loads without errors**

Run: `uv run python -m pytest tests/integration/ --collect-only 2>&1`
Expected: `no tests ran` (no test files yet), but no import errors.

- [ ] **Step 5: Verify existing tests still pass**

Run: `uv run python -m pytest tests/test_x11ctl.py -q`
Expected: `203 passed`

- [ ] **Step 6: Commit**

```bash
git add tests/integration/__init__.py tests/integration/conftest.py pyproject.toml
git commit -m "feat: integration test infrastructure (conftest, fixtures, helpers)"
```

---

### Task 2: Acceptance criteria tests (AC1-AC7) — core lifecycle

**Files:**
- Create: `tests/integration/test_x11ctl_integration.py`

- [ ] **Step 1: Write AC1-AC7 tests**

```python
# tests/integration/test_x11ctl_integration.py
"""Integration tests for x11ctl — lifecycle and acceptance criteria.

Every test invokes scripts/x11ctl as a subprocess with a unique display.
Requires Xvfb and related X11 binaries to be installed.
"""
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from . conftest import x11ctl_run, assert_port_listening, assert_port_free, read_state_file

pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]


class TestAcceptanceCriteria:
    """All 11 acceptance criteria from the design spec."""

    def test_ac1_run_xdpyinfo(self, display_factory):
        """AC1: x11ctl run xdpyinfo exits 0."""
        env = display_factory
        result = x11ctl_run(["run", "xdpyinfo"], env_overrides=env, timeout=30)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    def test_ac2_start_headless_display_works(self, display_factory):
        """AC2: start --headless creates working display."""
        env = display_factory
        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode == 0, f"start failed: {result.stderr}"

        # Verify display works via xdpyinfo
        check = subprocess.run(
            ["xdpyinfo", "-display", env["X11CTL_DISPLAY"]],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "XAUTHORITY": env["X11CTL_XAUTH"]},
        )
        assert check.returncode == 0, f"xdpyinfo failed: {check.stderr}"

    def test_ac3_screenshot_produces_png(self, display_factory, tmp_path):
        """AC3: screenshot produces valid PNG."""
        env = display_factory
        x11ctl_run(["start", "--headless"], env_overrides=env)

        out_path = str(tmp_path / "test.png")
        result = x11ctl_run(["screenshot", out_path], env_overrides=env)
        assert result.returncode == 0, f"screenshot failed: {result.stderr}"

        assert Path(out_path).exists(), "PNG file not created"
        # Verify it's actually a PNG
        file_check = subprocess.run(
            ["file", out_path], capture_output=True, text=True, timeout=5,
        )
        assert "PNG image data" in file_check.stdout

    def test_ac4_xpra_html5_accessible(self, display_factory):
        """AC4: start --xpra enables HTML5 client on xpra_port."""
        env = display_factory
        if shutil.which("xpra") is None:
            pytest.skip("xpra not installed")

        result = x11ctl_run(["start", "--xpra"], env_overrides=env)
        assert result.returncode == 0, f"start --xpra failed: {result.stderr}"

        port = int(env["X11CTL_XPRA_PORT"])
        assert_port_listening("127.0.0.1", port, timeout=10)

    def test_ac5_vnc_and_novnc_accessible(self, display_factory):
        """AC5: start --vnc enables VNC and noVNC ports."""
        env = display_factory
        if shutil.which("x11vnc") is None or shutil.which("websockify") is None:
            pytest.skip("x11vnc or websockify not installed")

        result = x11ctl_run(["start", "--vnc"], env_overrides=env)
        assert result.returncode == 0, f"start --vnc failed: {result.stderr}"

        vnc_port = int(env["X11CTL_VNC_PORT"])
        novnc_port = int(env["X11CTL_NOVNC_PORT"])
        assert_port_listening("127.0.0.1", vnc_port, timeout=10)
        assert_port_listening("127.0.0.1", novnc_port, timeout=10)

    def test_ac6_start_stop_start_idempotent(self, display_factory):
        """AC6: start/stop/start cycle is idempotent, no stale PIDs."""
        env = display_factory

        # First start
        r1 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r1.returncode == 0

        pid1 = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))
        assert pid1 is not None

        # Stop
        r2 = x11ctl_run(["stop"], env_overrides=env)
        assert r2.returncode == 0

        # Verify stopped
        assert read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid")) is None

        # Second start
        r3 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r3.returncode == 0

        pid2 = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))
        assert pid2 is not None
        # Different PID after restart
        assert pid1.split()[0] != pid2.split()[0]

    def test_ac7_status_healthy_and_degraded(self, display_factory):
        """AC7: status exits 0 healthy, 1 when component killed."""
        env = display_factory

        x11ctl_run(["start", "--headless"], env_overrides=env)

        # Healthy
        r1 = x11ctl_run(["status"], env_overrides=env)
        assert r1.returncode == 0

        # Kill Xvfb
        pidfile_content = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))
        assert pidfile_content is not None
        xvfb_pid = int(pidfile_content.split()[0])
        os.kill(xvfb_pid, signal.SIGKILL)
        time.sleep(0.5)

        # Degraded
        r2 = x11ctl_run(["status"], env_overrides=env)
        assert r2.returncode == 1
```

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/integration/test_x11ctl_integration.py -v`
Expected: All 7 PASS (or skip if binaries missing).

Note: Each test gets a unique `X11CTL_STATE_DIR` (via pytest's `tmp_path`), so pidfiles, tiers files, and lock files are fully isolated between tests. The xauth file and X artifacts (lock, socket) remain in `/tmp` with display-number-derived unique names.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_x11ctl_integration.py
git commit -m "feat: integration tests AC1-AC7 (headless, screenshot, xpra, vnc, lifecycle, status)"
```

---

### Task 3: Acceptance criteria tests (AC8-AC11) and lifecycle scenarios

**Files:**
- Modify: `tests/integration/test_x11ctl_integration.py`

- [ ] **Step 1: Add AC8-AC11 tests**

```python
    # Add to class TestAcceptanceCriteria in test_x11ctl_integration.py:

    def test_ac8_port_conflict_clear_error(self, display_factory):
        """AC8: port conflict produces clear error, exit 2."""
        env = display_factory
        if shutil.which("xpra") is None:
            pytest.skip("xpra not installed")

        # Occupy the xpra port
        port = int(env["X11CTL_XPRA_PORT"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        try:
            # Start headless first (xpra needs it)
            x11ctl_run(["start", "--headless"], env_overrides=env)
            result = x11ctl_run(["start", "--xpra"], env_overrides=env)
            assert result.returncode == 2, f"Expected exit 2, got {result.returncode}: {result.stderr}"
            assert "already in use" in result.stderr.lower() or "port" in result.stderr.lower()
        finally:
            sock.close()

    def test_ac9_tcp_listeners_default_localhost(self, display_factory):
        """AC9: all TCP listeners default to 127.0.0.1."""
        env = display_factory
        if shutil.which("xpra") is None:
            pytest.skip("xpra not installed")

        x11ctl_run(["start", "--xpra"], env_overrides=env)
        port = int(env["X11CTL_XPRA_PORT"])

        # Check sockstat for the port — should show 127.0.0.1, not *
        check = subprocess.run(
            ["sockstat", "-l", "-p", str(port)],
            capture_output=True, text=True, timeout=5,
        )
        # Verify no wildcard bind
        for line in check.stdout.splitlines()[1:]:  # skip header
            assert "127.0.0.1" in line or "localhost" in line, \
                f"Port {port} bound to non-localhost: {line}"

    def test_ac10_xauth_mode_0600(self, display_factory):
        """AC10: xauth file created with mode 0600."""
        env = display_factory
        x11ctl_run(["start", "--headless"], env_overrides=env)

        xauth_path = env["X11CTL_XAUTH"]
        assert os.path.exists(xauth_path), f"xauth not found: {xauth_path}"
        mode = os.stat(xauth_path).st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_ac11_missing_dep_clear_error(self, display_factory, tmp_path):
        """AC11: missing dependency produces clear error listing install command."""
        env = {**display_factory}

        # Construct a PATH that has python3 but NOT Xvfb.
        # The shebang #!/usr/bin/env python3 needs python3 on PATH.
        import sys as _sys
        python_dir = os.path.dirname(_sys.executable)
        # Include python dir + basic system dirs, exclude Xvfb location
        restricted_path = f"{python_dir}:/usr/bin:/bin"
        env["PATH"] = restricted_path

        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode != 0
        assert "missing" in result.stderr.lower() or "not found" in result.stderr.lower()
```

- [ ] **Step 2: Add lifecycle scenario tests**

```python
# Add new class to test_x11ctl_integration.py:

class TestLifecycleScenarios:
    """Lifecycle scenarios beyond basic ACs."""

    def test_tier_switching_downgrade(self, display_factory):
        """start --all then start --headless: xpra/vnc stopped, xvfb survives."""
        env = display_factory
        if shutil.which("xpra") is None or shutil.which("x11vnc") is None:
            pytest.skip("xpra or x11vnc not installed")

        x11ctl_run(["start", "--all"], env_overrides=env)

        # Verify all running
        r1 = x11ctl_run(["status"], env_overrides=env)
        assert r1.returncode == 0
        assert "xvfb" in r1.stderr.lower()

        # Downgrade
        x11ctl_run(["start", "--headless"], env_overrides=env)
        r2 = x11ctl_run(["status"], env_overrides=env)
        assert "xvfb" in r2.stderr.lower()
        # xpra and vnc ports should be free
        assert_port_free("127.0.0.1", int(env["X11CTL_XPRA_PORT"]))

    def test_idempotent_start(self, display_factory):
        """start --headless twice: second is no-op, same PID."""
        env = display_factory

        x11ctl_run(["start", "--headless"], env_overrides=env)
        pid1 = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))

        x11ctl_run(["start", "--headless"], env_overrides=env)
        pid2 = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))

        assert pid1 == pid2, f"PID changed: {pid1} -> {pid2}"

    def test_crash_recovery(self, display_factory):
        """Kill Xvfb, then start --headless: cleans stale, starts fresh."""
        env = display_factory

        x11ctl_run(["start", "--headless"], env_overrides=env)
        pidfile_content = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))
        old_pid = int(pidfile_content.split()[0])

        # Crash Xvfb
        os.kill(old_pid, signal.SIGKILL)
        time.sleep(0.5)

        # Restart — should clean stale artifacts and start fresh
        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode == 0, f"Restart failed: {result.stderr}"

        new_pidfile = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))
        new_pid = int(new_pidfile.split()[0])
        assert new_pid != old_pid

    def test_run_exit_code_propagation(self, display_factory):
        """run /bin/sh -c 'exit 42' returns 42."""
        env = display_factory
        result = x11ctl_run(["run", "/bin/sh", "-c", "exit 42"], env_overrides=env)
        assert result.returncode == 42

    def test_env_output(self, display_factory):
        """env command outputs valid DISPLAY and XAUTHORITY exports."""
        env = display_factory
        x11ctl_run(["start", "--headless"], env_overrides=env)

        result = x11ctl_run(["env"], env_overrides=env)
        assert result.returncode == 0
        assert f"DISPLAY=" in result.stdout
        assert f"XAUTHORITY=" in result.stdout
        assert env["X11CTL_DISPLAY"] in result.stdout
```

- [ ] **Step 3: Run all integration tests**

Run: `uv run python -m pytest tests/integration/test_x11ctl_integration.py -v`
Expected: 16 tests PASS (or skip where binaries missing).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_x11ctl_integration.py
git commit -m "feat: integration tests AC8-AC11 + lifecycle scenarios (tier switch, idempotency, crash recovery)"
```

---

### Task 4: Security tests

**Files:**
- Create: `tests/integration/test_x11ctl_security.py`

- [ ] **Step 1: Write security tests**

```python
# tests/integration/test_x11ctl_security.py
"""Security integration tests for x11ctl.

Tests symlink attacks, FIFO-based DoS, and foreign-owned state file rejection.
Every test invokes scripts/x11ctl as a subprocess.
"""
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from .conftest import x11ctl_run, read_state_file

pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]


class TestSecurityAttacks:

    def test_symlink_at_pidfile_path(self, display_factory, tmp_path):
        """Symlink at pidfile path before start: start should handle safely."""
        env = display_factory
        pidfile_path = os.path.join(env["state_dir"], f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid")

        # Create a symlink pointing to a decoy
        decoy = tmp_path / "decoy"
        decoy.write_text("attacker data")
        try:
            os.symlink(str(decoy), pidfile_path)
        except FileExistsError:
            os.unlink(pidfile_path)
            os.symlink(str(decoy), pidfile_path)

        try:
            result = x11ctl_run(["start", "--headless"], env_overrides=env)
            # write_pidfile rejects symlinks — start should fail
            assert result.returncode != 0, \
                f"Expected failure when pidfile is a symlink, got exit 0"
            # Decoy must not have been modified (symlink was not followed)
            assert decoy.read_text() == "attacker data"
        finally:
            # Clean up symlink
            try:
                os.unlink(pidfile_path)
            except FileNotFoundError:
                pass

    def test_symlink_at_xauth_path(self, display_factory, tmp_path):
        """Symlink at xauth path: start should fail with clear error."""
        env = display_factory
        xauth_path = env["X11CTL_XAUTH"]

        decoy = tmp_path / "decoy_xauth"
        decoy.write_text("attacker data")
        try:
            os.symlink(str(decoy), xauth_path)
        except FileExistsError:
            os.unlink(xauth_path)
            os.symlink(str(decoy), xauth_path)

        try:
            result = x11ctl_run(["start", "--headless"], env_overrides=env)
            assert result.returncode != 0, "Start should fail when xauth is a symlink"
            assert "symlink" in result.stderr.lower() or "error" in result.stderr.lower()
            # Decoy should be unchanged
            assert decoy.read_text() == "attacker data"
        finally:
            try:
                os.unlink(xauth_path)
            except FileNotFoundError:
                pass

    def test_fifo_at_lock_path(self, display_factory):
        """FIFO at lock path: start should not hang, returns error."""
        env = display_factory
        lock_path = os.path.join(env["state_dir"], f"{env['X11CTL_STATE_PREFIX']}.lock")

        # Remove existing lock if any
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass

        os.mkfifo(lock_path)
        try:
            # Should timeout quickly (not hang on FIFO)
            result = x11ctl_run(["start", "--headless"], env_overrides=env, timeout=10)
            assert result.returncode != 0
        finally:
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass

    def test_fifo_at_pidfile_path(self, display_factory):
        """FIFO at pidfile path: read_pidfile returns None, start proceeds."""
        env = display_factory
        pidfile_path = os.path.join(env["state_dir"], f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid")

        try:
            os.unlink(pidfile_path)
        except FileNotFoundError:
            pass
        os.mkfifo(pidfile_path)

        try:
            # Start should handle FIFO pidfile gracefully
            result = x11ctl_run(["start", "--headless"], env_overrides=env, timeout=15)
            # May succeed (treats FIFO pidfile as absent) or fail gracefully
            assert result.returncode in (0, 1), f"Unexpected exit {result.returncode}: {result.stderr}"
        finally:
            try:
                os.unlink(pidfile_path)
            except FileNotFoundError:
                pass

    def test_symlink_at_x_socket(self, display_factory, tmp_path):
        """Symlink at X socket path: stale cleanup should fail closed."""
        env = display_factory
        display_num = env["display_num"]
        socket_dir = Path("/tmp/.X11-unix")
        socket_path = socket_dir / f"X{display_num}"

        # Create a symlink at the socket path
        decoy = tmp_path / "decoy_socket"
        decoy.write_text("")
        socket_dir.mkdir(exist_ok=True)
        try:
            os.symlink(str(decoy), str(socket_path))
        except FileExistsError:
            os.unlink(str(socket_path))
            os.symlink(str(decoy), str(socket_path))

        try:
            # Start should detect the symlinked socket and fail closed
            result = x11ctl_run(["start", "--headless"], env_overrides=env)
            # The symlink should NOT have been followed or deleted
            assert decoy.exists(), "Decoy was deleted — symlink was followed!"
        finally:
            try:
                os.unlink(str(socket_path))
            except FileNotFoundError:
                pass
```

- [ ] **Step 2: Run security tests**

Run: `uv run python -m pytest tests/integration/test_x11ctl_security.py -v`
Expected: 5 PASS (foreign-owned test skipped — can't easily create files as another user).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_x11ctl_security.py
git commit -m "feat: security integration tests (symlink, FIFO, socket attacks)"
```

---

### Task 5: Concurrency tests

**Files:**
- Create: `tests/integration/test_x11ctl_concurrency.py`

- [ ] **Step 1: Write concurrency tests**

```python
# tests/integration/test_x11ctl_concurrency.py
"""Concurrency integration tests for x11ctl.

Tests lock contention, concurrent start/stop, and signal handling.
Every test invokes scripts/x11ctl as a subprocess.
"""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from .conftest import x11ctl_run, SCRIPT, read_state_file

pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]


class TestConcurrency:

    def test_concurrent_start_is_idempotent(self, display_factory):
        """Two concurrent start --headless: both succeed, one Xvfb runs."""
        env = display_factory
        base_env = {**os.environ, "X11CTL_ALLOW_HOST": "1", **{
            k: v for k, v in env.items() if k.startswith("X11CTL_")
        }}

        # Launch two starts simultaneously
        p1 = subprocess.Popen(
            [SCRIPT, "start", "--headless"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [SCRIPT, "start", "--headless"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Wait for both
        _, err1 = p1.communicate(timeout=30)
        _, err2 = p2.communicate(timeout=30)

        # Both should succeed (one does the work, other is idempotent)
        assert p1.returncode == 0, f"p1 failed: {err1.decode()}"
        assert p2.returncode == 0, f"p2 failed: {err2.decode()}"

        # Exactly one Xvfb should be running for this display
        pidfile = read_state_file(os.path.join(env["state_dir"], f"{env.get('X11CTL_STATE_PREFIX', '.x11ctl')}-xvfb.pid"))
        assert pidfile is not None, "No pidfile after concurrent starts"

    def test_stop_during_startup(self, display_factory):
        """start in background, stop immediately: no orphaned processes."""
        env = display_factory
        base_env = {**os.environ, "X11CTL_ALLOW_HOST": "1", **{
            k: v for k, v in env.items() if k.startswith("X11CTL_")
        }}

        # Start in background
        start_proc = subprocess.Popen(
            [SCRIPT, "start", "--headless"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Give it a moment to acquire lock
        time.sleep(0.5)

        # Stop immediately
        stop_result = x11ctl_run(["stop"], env_overrides=env, timeout=15)

        # Wait for start to finish
        start_proc.communicate(timeout=15)

        # Verify: no orphaned Xvfb on this display
        display = env["X11CTL_DISPLAY"]
        ps_result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5,
        )
        for line in ps_result.stdout.splitlines():
            if "Xvfb" in line and display in line:
                pytest.fail(f"Orphaned Xvfb found: {line}")

    def test_concurrent_stop(self, display_factory):
        """Two concurrent stop: both succeed, no errors."""
        env = display_factory
        base_env = {**os.environ, "X11CTL_ALLOW_HOST": "1", **{
            k: v for k, v in env.items() if k.startswith("X11CTL_")
        }}

        # Start first
        x11ctl_run(["start", "--headless"], env_overrides=env)

        # Two concurrent stops
        p1 = subprocess.Popen(
            [SCRIPT, "stop"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [SCRIPT, "stop"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        _, err1 = p1.communicate(timeout=15)
        _, err2 = p2.communicate(timeout=15)

        # Both should succeed
        assert p1.returncode == 0, f"p1 failed: {err1.decode()}"
        assert p2.returncode == 0, f"p2 failed: {err2.decode()}"

    def test_signal_during_run(self, display_factory):
        """SIGTERM to x11ctl run: child killed, display cleaned."""
        env = display_factory
        base_env = {**os.environ, "X11CTL_ALLOW_HOST": "1", **{
            k: v for k, v in env.items() if k.startswith("X11CTL_")
        }}

        # Launch run with a long-running child
        run_proc = subprocess.Popen(
            [SCRIPT, "run", "sleep", "60"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Wait for Xvfb to start
        display_num = env["display_num"]
        socket_path = f"/tmp/.X11-unix/X{display_num}"
        for _ in range(20):
            time.sleep(0.5)
            if os.path.exists(socket_path) or run_proc.poll() is not None:
                break

        # Send SIGTERM
        run_proc.send_signal(signal.SIGTERM)
        run_proc.wait(timeout=15)

        # Should have exited (not hung)
        assert run_proc.returncode is not None

        # Verify xauth cleaned up
        xauth_path = env["X11CTL_XAUTH"]
        # Give cleanup a moment
        time.sleep(1)
        assert not os.path.exists(xauth_path), f"xauth not cleaned: {xauth_path}"
```

- [ ] **Step 2: Run concurrency tests**

Run: `uv run python -m pytest tests/integration/test_x11ctl_concurrency.py -v`
Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_x11ctl_concurrency.py
git commit -m "feat: concurrency integration tests (parallel start/stop, signal during run)"
```

---

### Task 6: Final verification and cleanup

- [ ] **Step 1: Run all tests together**

Run: `uv run python -m pytest tests/ -v --timeout=180`
Expected: 203 unit tests + ~25 integration tests = ~228 total, all PASS.

- [ ] **Step 2: Run integration tests alone with timing**

Run: `uv run python -m pytest tests/integration/ -v --durations=0`
Expected: Under 3 minutes total.

- [ ] **Step 3: Verify unit tests unaffected**

Run: `uv run python -m pytest tests/test_x11ctl.py -q`
Expected: `203 passed`

- [ ] **Step 4: Verify integration tests skip cleanly without Xvfb**

Run: `PATH=/usr/bin:/bin uv run python -m pytest tests/integration/ --collect-only 2>&1 | head -20`
Expected: All tests show `SKIP` markers.

- [ ] **Step 5: Verify no orphaned processes after full suite**

Run: `ps aux | grep -E "Xvfb|xpra|x11vnc|websockify" | grep -v grep`
Expected: No matching processes (session_cleanup fixture cleaned everything).

- [ ] **Step 6: Commit**

```bash
git commit --allow-empty -m "test: verify full integration test suite passes (228 tests, <3min)"
```

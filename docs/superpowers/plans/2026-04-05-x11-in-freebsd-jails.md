# X11 in FreeBSD Jails — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/x11ctl`, a standalone Python script for managing X11 inside FreeBSD jails (Xvfb + Xpra + VNC), plus a comprehensive runbook.

**Architecture:** Single-file Python script (stdlib only, `>=3.10`) managing a 3-tier X11 stack: Xvfb headless display (Tier 1), Xpra remote viewing (Tier 2), x11vnc/noVNC fallback (Tier 3). Declarative `start` replaces desired tier set; subtractive `stop` removes tiers. `fcntl.flock` serializes all mutations. PID identity validated via `ps etimes` + creation epoch.

**Tech Stack:** Python 3.10+ stdlib (`subprocess`, `signal`, `os`, `socket`, `argparse`, `fcntl`, `time`, `pathlib`), FreeBSD `pkg` packages (xorg-vfbserver, xpra, x11vnc, novnc, ImageMagick7-nox11)

**Spec:** `docs/superpowers/specs/2026-04-05-x11-in-freebsd-jails-design.md`

---

## File Map

| File | Purpose |
|------|---------|
| Create: `scripts/x11ctl` | Main script — all subcommands, process management, state |
| Create: `tests/test_x11ctl.py` | Unit tests for pure logic (config, pidfile parsing, tier sets, CLI args) |
| Create: `docs/x11-in-freebsd-jails.md` | Runbook — operator guide with all tiers, recipes, troubleshooting |

The script is intentionally a single file (no package structure) to match the `scripts/claudechic-remote` pattern and enable copy-into-jail deployment. Tests exercise the pure-logic functions by importing the script as a module.

---

### Task 1: Script skeleton with config and CLI parsing

**Files:**
- Create: `scripts/x11ctl`
- Create: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for config defaults and CLI parsing**

```python
# tests/test_x11ctl.py
"""Unit tests for x11ctl pure logic. Import the script as a module."""
import importlib.util
import sys
from pathlib import Path

# Import x11ctl as a module (it's a script, not a package)
def _import_x11ctl():
    spec = importlib.util.spec_from_file_location(
        "x11ctl", Path(__file__).parent.parent / "scripts" / "x11ctl"
    )
    mod = importlib.util.module_from_spec(spec)
    # Prevent argparse from consuming pytest's args
    old_argv = sys.argv
    sys.argv = ["x11ctl"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
    return mod

x11ctl = _import_x11ctl()


class TestConfig:
    def test_default_display(self):
        cfg = x11ctl.Config()
        assert cfg.display == ":99"

    def test_default_screen(self):
        cfg = x11ctl.Config()
        assert cfg.screen == "1920x1080x24"

    def test_default_bind(self):
        cfg = x11ctl.Config()
        assert cfg.bind == "127.0.0.1"

    def test_default_ports(self):
        cfg = x11ctl.Config()
        assert cfg.xpra_port == 10000
        assert cfg.vnc_port == 5900
        assert cfg.novnc_port == 6080

    def test_xauth_path(self):
        cfg = x11ctl.Config()
        assert cfg.xauth == "/tmp/.x11ctl-xauth"

    def test_env_override_display(self, monkeypatch):
        monkeypatch.setenv("X11CTL_DISPLAY", ":42")
        cfg = x11ctl.Config()
        assert cfg.display == ":42"

    def test_env_override_bind(self, monkeypatch):
        monkeypatch.setenv("X11CTL_BIND", "0.0.0.0")
        cfg = x11ctl.Config()
        assert cfg.bind == "0.0.0.0"

    def test_display_number(self):
        cfg = x11ctl.Config()
        assert cfg.display_number == 99

    def test_pidfile_path(self):
        cfg = x11ctl.Config()
        assert cfg.pidfile("xvfb") == "/tmp/.x11ctl-xvfb.pid"

    def test_logfile_path(self):
        cfg = x11ctl.Config()
        assert cfg.logfile("xpra") == "/tmp/.x11ctl-xpra.log"

    def test_tiers_file(self):
        cfg = x11ctl.Config()
        assert cfg.tiers_file == "/tmp/.x11ctl-tiers"

    def test_lock_file(self):
        cfg = x11ctl.Config()
        assert cfg.lock_file == "/tmp/.x11ctl.lock"


class TestTierSets:
    def test_headless_implies(self):
        assert x11ctl.desired_tiers("headless") == {"headless"}

    def test_xpra_implies_headless(self):
        assert x11ctl.desired_tiers("xpra") == {"headless", "xpra"}

    def test_vnc_implies_headless(self):
        assert x11ctl.desired_tiers("vnc") == {"headless", "vnc"}

    def test_all_tiers(self):
        assert x11ctl.desired_tiers("all") == {"headless", "xpra", "vnc"}


class TestCLIParsing:
    def test_start_headless(self):
        args = x11ctl.parse_args(["start", "--headless"])
        assert args.command == "start"
        assert args.headless is True

    def test_start_all(self):
        args = x11ctl.parse_args(["start", "--all"])
        assert args.command == "start"
        assert args.all is True

    def test_start_bind_all(self):
        args = x11ctl.parse_args(["start", "--headless", "--bind-all"])
        assert args.bind_all is True

    def test_stop_vnc(self):
        args = x11ctl.parse_args(["stop", "--vnc"])
        assert args.command == "stop"
        assert args.vnc is True

    def test_status(self):
        args = x11ctl.parse_args(["status"])
        assert args.command == "status"

    def test_env(self):
        args = x11ctl.parse_args(["env"])
        assert args.command == "env"

    def test_run_with_command(self):
        args = x11ctl.parse_args(["run", "echo", "hello"])
        assert args.command == "run"
        assert args.run_command == ["echo", "hello"]

    def test_screenshot(self):
        args = x11ctl.parse_args(["screenshot", "/tmp/out.png"])
        assert args.command == "screenshot"
        assert args.path == "/tmp/out.png"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: FAIL — `scripts/x11ctl` does not exist yet.

- [ ] **Step 3: Create x11ctl with Config, tier logic, and CLI parsing**

```python
#!/usr/local/bin/python3
"""x11ctl — Manage X11 display stack inside FreeBSD jails.

Single-file, stdlib-only. See docs/superpowers/specs/2026-04-05-x11-in-freebsd-jails-design.md
"""
from __future__ import annotations

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class Config:
    """Runtime configuration from env vars with sensible defaults."""

    def __init__(self) -> None:
        self.display = os.environ.get("X11CTL_DISPLAY", ":99")
        self.screen = os.environ.get("X11CTL_SCREEN", "1920x1080x24")
        self.xauth = os.environ.get("X11CTL_XAUTH", "/tmp/.x11ctl-xauth")
        self.xpra_port = int(os.environ.get("X11CTL_XPRA_PORT", "10000"))
        self.vnc_port = int(os.environ.get("X11CTL_VNC_PORT", "5900"))
        self.novnc_port = int(os.environ.get("X11CTL_NOVNC_PORT", "6080"))
        self.bind = os.environ.get("X11CTL_BIND", "127.0.0.1")

    @property
    def display_number(self) -> int:
        return int(self.display.lstrip(":"))

    def pidfile(self, component: str) -> str:
        return f"/tmp/.x11ctl-{component}.pid"

    def logfile(self, component: str) -> str:
        return f"/tmp/.x11ctl-{component}.log"

    @property
    def tiers_file(self) -> str:
        return "/tmp/.x11ctl-tiers"

    @property
    def lock_file(self) -> str:
        return "/tmp/.x11ctl.lock"


# ---------------------------------------------------------------------------
# Tier logic
# ---------------------------------------------------------------------------

TIER_IMPLIES: dict[str, set[str]] = {
    "headless": {"headless"},
    "xpra": {"headless", "xpra"},
    "vnc": {"headless", "vnc"},
    "all": {"headless", "xpra", "vnc"},
}

def desired_tiers(flag: str) -> set[str]:
    """Return the set of tiers implied by a CLI flag."""
    return TIER_IMPLIES[flag]


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="x11ctl",
        description="Manage X11 display stack inside FreeBSD jails.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = sub.add_parser("start", help="Start X11 components")
    p_start.add_argument("--headless", action="store_true")
    p_start.add_argument("--xpra", action="store_true")
    p_start.add_argument("--vnc", action="store_true")
    p_start.add_argument("--all", action="store_true")
    p_start.add_argument("--bind-all", action="store_true")

    # stop
    p_stop = sub.add_parser("stop", help="Stop X11 components")
    p_stop.add_argument("--headless", action="store_true")
    p_stop.add_argument("--xpra", action="store_true")
    p_stop.add_argument("--vnc", action="store_true")
    p_stop.add_argument("--all", action="store_true")

    # status
    sub.add_parser("status", help="Show component status")

    # env
    sub.add_parser("env", help="Print DISPLAY/XAUTHORITY exports")

    # run
    p_run = sub.add_parser("run", help="Run command with temporary Xvfb")
    p_run.add_argument("run_command", nargs=argparse.REMAINDER)

    # screenshot
    p_ss = sub.add_parser("screenshot", help="Capture display to PNG")
    p_ss.add_argument("path", help="Output PNG path")

    # setup
    p_setup = sub.add_parser("setup", help="Install packages")
    p_setup.add_argument("--tier1", action="store_true")
    p_setup.add_argument("--tier2", action="store_true")
    p_setup.add_argument("--tier3", action="store_true")
    p_setup.add_argument("--all", action="store_true")

    # self-test (diagnostic)
    p_test = sub.add_parser("self-test", help="Run self-test (diagnostic)")
    p_test.add_argument("--tier1", action="store_true")
    p_test.add_argument("--tier2", action="store_true")
    p_test.add_argument("--tier3", action="store_true")
    p_test.add_argument("--all", action="store_true")

    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Main (guard prevents execution on import)
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    print(f"x11ctl: {args.command} not yet implemented", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make script executable**

Run: `chmod +x scripts/x11ctl`

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl skeleton with config, tier logic, CLI parsing"
```

---

### Task 2: Pidfile read/write and identity validation

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for pidfile operations**

```python
# Add to tests/test_x11ctl.py

class TestPidfile:
    def test_write_and_read(self, tmp_path):
        path = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(path, 1234, 1712345678)
        pid, epoch = x11ctl.read_pidfile(path)
        assert pid == 1234
        assert epoch == 1712345678

    def test_read_missing(self, tmp_path):
        path = str(tmp_path / "missing.pid")
        assert x11ctl.read_pidfile(path) is None

    def test_read_corrupt(self, tmp_path):
        path = str(tmp_path / "bad.pid")
        Path(path).write_text("garbage\n")
        assert x11ctl.read_pidfile(path) is None

    def test_format_two_integers(self, tmp_path):
        path = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(path, 42, 9999999999)
        content = Path(path).read_text()
        assert content == "42 9999999999\n"

    def test_write_creates_with_excl(self, tmp_path):
        path = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(path, 1, 1)
        # Second write should remove old file first, not fail
        x11ctl.write_pidfile(path, 2, 2)
        pid, _ = x11ctl.read_pidfile(path)
        assert pid == 2

    def test_symlink_rejected(self, tmp_path):
        target = tmp_path / "target"
        target.write_text("fake")
        link = tmp_path / "link.pid"
        link.symlink_to(target)
        # write_pidfile should reject symlinks
        import pytest
        with pytest.raises(OSError):
            x11ctl.write_pidfile(str(link), 1, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestPidfile -v`
Expected: FAIL — `write_pidfile` / `read_pidfile` not defined.

- [ ] **Step 3: Implement pidfile functions**

Add to `scripts/x11ctl` after the Config class:

```python
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Pidfile I/O
# ---------------------------------------------------------------------------

def write_pidfile(path: str, pid: int, created_epoch: int) -> None:
    """Write pidfile atomically. Rejects symlinks via O_NOFOLLOW."""
    # Remove existing file if it's a regular file
    p = Path(path)
    if p.exists():
        if p.is_symlink():
            raise OSError(f"Refusing to write pidfile: {path} is a symlink")
        p.unlink()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, f"{pid} {created_epoch}\n".encode())
    finally:
        os.close(fd)


def read_pidfile(path: str) -> tuple[int, int] | None:
    """Read pidfile. Returns (pid, created_epoch) or None if missing/corrupt."""
    try:
        content = Path(path).read_text().strip()
        parts = content.split()
        if len(parts) != 2:
            return None
        return int(parts[0]), int(parts[1])
    except (FileNotFoundError, ValueError, OSError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestPidfile -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl pidfile read/write with symlink rejection"
```

---

### Task 3: Tiers file I/O and set operations

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for tiers file operations**

```python
# Add to tests/test_x11ctl.py

class TestTiersFile:
    def test_write_and_read(self, tmp_path):
        path = str(tmp_path / "tiers")
        x11ctl.write_tiers(path, {"headless", "xpra"})
        result = x11ctl.read_tiers(path)
        assert result == {"headless", "xpra"}

    def test_read_missing(self, tmp_path):
        path = str(tmp_path / "missing")
        assert x11ctl.read_tiers(path) is None

    def test_delete(self, tmp_path):
        path = str(tmp_path / "tiers")
        x11ctl.write_tiers(path, {"headless"})
        x11ctl.delete_tiers(path)
        assert x11ctl.read_tiers(path) is None

    def test_delete_missing_is_safe(self, tmp_path):
        path = str(tmp_path / "missing")
        x11ctl.delete_tiers(path)  # should not raise

    def test_reconcile_start_declarative(self):
        """start --xpra when vnc was running: stop vnc, keep headless+xpra."""
        old = {"headless", "xpra", "vnc"}
        new = x11ctl.desired_tiers("xpra")
        to_stop = old - new
        to_start = new - old
        assert to_stop == {"vnc"}
        assert to_start == set()  # headless+xpra already running

    def test_reconcile_stop_subtractive(self):
        """stop --xpra when all running: remove xpra, keep headless+vnc."""
        old = {"headless", "xpra", "vnc"}
        to_remove = {"xpra"}
        remaining = old - to_remove
        assert remaining == {"headless", "vnc"}

    def test_reconcile_stop_headless_cascades(self):
        """stop --headless cascades: everything stops."""
        old = {"headless", "xpra", "vnc"}
        # headless cascade means stop everything
        remaining = set()  # cascade rule
        assert remaining == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestTiersFile -v`
Expected: FAIL — `write_tiers` / `read_tiers` / `delete_tiers` not defined.

- [ ] **Step 3: Implement tiers file functions**

Add to `scripts/x11ctl`:

```python
# ---------------------------------------------------------------------------
# Tiers file I/O
# ---------------------------------------------------------------------------

def write_tiers(path: str, tiers: set[str]) -> None:
    """Write active tiers set. Uses O_NOFOLLOW for symlink safety."""
    p = Path(path)
    if p.is_symlink():
        raise OSError(f"Refusing to write tiers file: {path} is a symlink")
    if p.exists():
        p.unlink()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, " ".join(sorted(tiers)).encode() + b"\n")
    finally:
        os.close(fd)


def read_tiers(path: str) -> set[str] | None:
    """Read active tiers set. Returns None if missing."""
    try:
        content = Path(path).read_text().strip()
        if not content:
            return None
        return set(content.split())
    except (FileNotFoundError, OSError):
        return None


def delete_tiers(path: str) -> None:
    """Delete tiers file if it exists."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestTiersFile -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl tiers file I/O with set operations"
```

---

### Task 4: Locking, preflight checks, and env subcommand

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for preflight and env**

```python
# Add to tests/test_x11ctl.py
import subprocess

class TestPreflight:
    def test_find_binary_existing(self):
        # python3 should always exist
        assert x11ctl.find_binary("python3") is not None

    def test_find_binary_missing(self):
        assert x11ctl.find_binary("nonexistent_binary_xyz") is None

    def test_check_binaries_all_present(self):
        missing = x11ctl.check_binaries(["python3", "sh"])
        assert missing == []

    def test_check_binaries_some_missing(self):
        missing = x11ctl.check_binaries(["python3", "nonexistent_xyz"])
        assert missing == ["nonexistent_xyz"]


class TestEnvOutput:
    def test_env_output(self):
        cfg = x11ctl.Config()
        output = x11ctl.format_env(cfg)
        assert 'export DISPLAY=":99"' in output
        assert "export XAUTHORITY=" in output


class TestLocking:
    def test_lock_acquire_release(self, tmp_path):
        lock_path = str(tmp_path / "test.lock")
        lock_fd = x11ctl.acquire_lock(lock_path, exclusive=True)
        assert lock_fd is not None
        x11ctl.release_lock(lock_fd)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestPreflight tests/test_x11ctl.py::TestEnvOutput tests/test_x11ctl.py::TestLocking -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement preflight, env, and locking**

Add to `scripts/x11ctl`:

```python
import fcntl
import shutil

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

TIER_BINARIES: dict[str, list[str]] = {
    "headless": ["Xvfb", "xauth", "xdpyinfo"],
    "xpra": ["xpra"],
    "vnc": ["x11vnc", "websockify"],
}

def find_binary(name: str) -> str | None:
    """Find a binary on PATH. Returns full path or None."""
    return shutil.which(name)


def check_binaries(names: list[str]) -> list[str]:
    """Return list of missing binaries."""
    return [n for n in names if find_binary(n) is None]


# ---------------------------------------------------------------------------
# Env output
# ---------------------------------------------------------------------------

def format_env(cfg: Config) -> str:
    """Format environment exports for shell eval."""
    return (
        f'export DISPLAY="{cfg.display}"\n'
        f'export XAUTHORITY="{cfg.xauth}"\n'
    )


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------

def acquire_lock(path: str, exclusive: bool = True) -> int:
    """Acquire flock on path. Returns fd. Uses O_NOFOLLOW."""
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, op)
    except Exception:
        os.close(fd)
        raise
    return fd


def release_lock(fd: int) -> None:
    """Release flock and close fd."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestPreflight tests/test_x11ctl.py::TestEnvOutput tests/test_x11ctl.py::TestLocking -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl preflight checks, env output, flock locking"
```

---

### Task 5: Port-conflict detection

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for port checking**

```python
# Add to tests/test_x11ctl.py
import socket as _socket

class TestPortCheck:
    def test_available_port(self):
        # Use a high ephemeral port that's almost certainly free
        assert x11ctl.check_port_available("127.0.0.1", 59999) is True

    def test_occupied_port(self):
        # Bind a port, then check it
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 59998))
        sock.listen(1)
        try:
            assert x11ctl.check_port_available("127.0.0.1", 59998) is False
        finally:
            sock.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestPortCheck -v`
Expected: FAIL — `check_port_available` not defined.

- [ ] **Step 3: Implement port checking**

Add to `scripts/x11ctl`:

```python
import socket
import subprocess

# ---------------------------------------------------------------------------
# Port-conflict detection
# ---------------------------------------------------------------------------

def check_port_available(host: str, port: int) -> bool:
    """Check if a port is available for binding. Returns True if free."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def identify_port_user(port: int) -> str:
    """Use sockstat to identify what's using a port. Returns description string."""
    try:
        result = subprocess.run(
            ["sockstat", "-l", "-p", str(port)],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) > 1:
            return lines[1]  # First data line after header
        return "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown (sockstat unavailable)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestPortCheck -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl port-conflict detection via socket.bind"
```

---

### Task 6: Process lifecycle — start headless (Tier 1)

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

This is the core task — wiring up `start --headless` to actually launch Xvfb with readiness probes, pidfile management, and log rotation. Since it requires actual Xvfb binaries, the integration test checks are conditional on binary availability.

- [ ] **Step 1: Write failing tests for process helpers**

```python
# Add to tests/test_x11ctl.py

class TestLogRotation:
    def test_rotate_creates_prev(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("old content")
        x11ctl.rotate_log(str(log))
        prev = tmp_path / "test.log.prev"
        assert prev.read_text() == "old content"
        assert not log.exists()

    def test_rotate_missing_is_safe(self, tmp_path):
        x11ctl.rotate_log(str(tmp_path / "missing.log"))  # no error


class TestXauthCreation:
    def test_creates_with_0600(self, tmp_path):
        path = str(tmp_path / "xauth")
        x11ctl.create_xauth_file(path)
        import stat
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_rejects_symlink(self, tmp_path):
        target = tmp_path / "target"
        target.write_text("x")
        link = tmp_path / "xauth"
        link.symlink_to(target)
        import pytest
        with pytest.raises(OSError):
            x11ctl.create_xauth_file(str(link))


class TestReadinessProbe:
    def test_probe_with_immediate_success(self):
        """Test the probe loop logic with a callable that succeeds immediately."""
        call_count = 0
        def check():
            nonlocal call_count
            call_count += 1
            return True
        assert x11ctl.wait_ready(check, retries=3, delay=0.01) is True
        assert call_count == 1

    def test_probe_with_eventual_success(self):
        """Test probe that fails twice then succeeds."""
        attempts = []
        def check():
            attempts.append(1)
            return len(attempts) >= 3
        assert x11ctl.wait_ready(check, retries=5, delay=0.01) is True
        assert len(attempts) == 3

    def test_probe_timeout(self):
        """Test probe that never succeeds."""
        assert x11ctl.wait_ready(lambda: False, retries=3, delay=0.01) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestLogRotation tests/test_x11ctl.py::TestXauthCreation tests/test_x11ctl.py::TestReadinessProbe -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement process helpers**

Add to `scripts/x11ctl`:

```python
# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------

def rotate_log(path: str) -> None:
    """Rename log to .prev if it exists."""
    p = Path(path)
    if p.exists() and not p.is_symlink():
        prev = Path(path + ".prev")
        if prev.exists():
            prev.unlink()
        p.rename(prev)


# ---------------------------------------------------------------------------
# Xauth
# ---------------------------------------------------------------------------

def create_xauth_file(path: str) -> None:
    """Create empty xauth file with mode 0600. Rejects symlinks."""
    p = Path(path)
    if p.is_symlink():
        raise OSError(f"Refusing to create xauth: {path} is a symlink")
    if p.exists():
        p.unlink()
    fd = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
        0o600,
    )
    os.close(fd)


# ---------------------------------------------------------------------------
# Readiness probes
# ---------------------------------------------------------------------------

def wait_ready(
    check: callable,
    retries: int = 10,
    delay: float = 0.5,
) -> bool:
    """Poll check() up to retries times with delay between. Returns True on success."""
    for _ in range(retries):
        if check():
            return True
        time.sleep(delay)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestLogRotation tests/test_x11ctl.py::TestXauthCreation tests/test_x11ctl.py::TestReadinessProbe -v`
Expected: All PASS.

- [ ] **Step 5: Implement start_headless, stop_component, and wire up main**

Add to `scripts/x11ctl` the full `start_headless()`, `stop_component()`, `start_command()`, `stop_command()`, and update `main()`. This is the integration point — too large for inline code in the plan. The implementation should:

1. `start_headless(cfg, bind_all)`:
   - Clean stale X artifacts (`/tmp/.X{N}-lock`, `/tmp/.X11-unix/X{N}`)
   - Check if Xvfb pidfile already valid (idempotent skip)
   - Preflight check for `Xvfb`, `xauth`, `xdpyinfo` binaries
   - Create xauth file, generate cookie via `xauth generate`
   - Rotate log, launch `Xvfb` via `subprocess.Popen`, write pidfile
   - Readiness probe via `xdpyinfo -display`
   - On failure: rollback (kill Xvfb, remove pidfile/xauth)

2. `stop_component(cfg, name)`:
   - Read pidfile, validate PID identity
   - SIGTERM → 3s wait → SIGKILL
   - Remove pidfile

3. `start_command(args, cfg)`:
   - Determine desired tiers from flags
   - Acquire exclusive lock
   - Read current tiers, compute diff (stop excess, start missing)
   - Write new tiers file
   - Call `start_headless`, `start_xpra`, `start_vnc` as needed

4. `stop_command(args, cfg)`:
   - Acquire exclusive lock
   - Read current tiers
   - If `--headless`: cascade stop everything
   - Else: remove named tier, stop its components
   - Update or delete tiers file

5. Wire `main()` to dispatch to `start_command`, `stop_command`, `status_command`, `env_command`, `run_command`, `screenshot_command`, `setup_command`.

- [ ] **Step 6: Manual integration test (if Xvfb is available)**

Run: `scripts/x11ctl start --headless && scripts/x11ctl status && scripts/x11ctl stop --headless`
Expected: Xvfb starts, status shows healthy, stop tears down cleanly.

- [ ] **Step 7: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl start/stop headless with readiness probes and rollback"
```

---

### Task 7: Tier 2 (Xpra) and Tier 3 (VNC) start/stop

**Files:**
- Modify: `scripts/x11ctl`

- [ ] **Step 1: Implement start_xpra(cfg, bind_all)**

The function should:
- Preflight check for `xpra` binary
- Check port available for Xpra TCP
- Rotate log, launch `xpra shadow :N --bind-tcp=HOST:PORT --html=on --daemon=no` with `XAUTHORITY` env var
- Write pidfile
- No readiness probe needed (Xpra logs to its own file; failure is visible in status)

- [ ] **Step 2: Implement start_vnc(cfg, bind_all)**

The function should:
- Preflight check for `x11vnc` and `websockify` binaries
- Check ports available for VNC and noVNC
- Rotate logs for both x11vnc and websockify
- Launch `x11vnc -display :N -rfbport PORT -shared -forever -noxdamage -localhost -auth XAUTH`
- Readiness probe on VNC port via `socket.connect_ex`
- Launch `websockify --listen HOST:PORT localhost:VNC_PORT --web=/usr/local/libexec/novnc`
- Write pidfiles for both

- [ ] **Step 3: Update start_command to handle all tiers**

Wire `start_xpra` and `start_vnc` into the tier reconciliation logic in `start_command`.

- [ ] **Step 4: Manual integration test (if packages available)**

Run: `scripts/x11ctl start --all && scripts/x11ctl status && scripts/x11ctl stop --all`
Expected: All 4 components start, status shows healthy, stop tears down cleanly.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl
git commit -m "feat: x11ctl Tier 2 (Xpra) and Tier 3 (VNC) start/stop"
```

---

### Task 8: status, run, screenshot, setup subcommands

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Implement status_command(cfg)**

- Acquire shared lock
- Read tiers file → expected components
- For each expected component: validate pidfile, check process alive
- For non-expected components: show "not started" (informational)
- Print last 5 lines of each log
- Exit 0 if all expected healthy, 1 if any expected down

- [ ] **Step 2: Implement run_command(args, cfg)**

- Create temporary display (`:$$` like the shell function)
- Create temp xauth, launch Xvfb, readiness probe
- Install SIGINT/SIGTERM handlers that forward to child and teardown
- Run user command with `DISPLAY` and `XAUTHORITY` set
- Capture exit code, teardown Xvfb, return exit code

- [ ] **Step 3: Implement screenshot_command(args, cfg)**

- Verify `import` binary exists (from ImageMagick)
- Run `import -window root -display :N <path>`
- Report success/failure

- [ ] **Step 4: Implement setup_command(args, cfg)**

- Check `os.geteuid() == 0`, fail if not root
- Map tier flags to package lists
- Run `pkg install -y <packages>`

- [ ] **Step 5: Write test for run_command signal forwarding**

```python
class TestRunCommand:
    def test_format_env_for_run(self):
        cfg = x11ctl.Config()
        env = x11ctl.format_env(cfg)
        assert "DISPLAY" in env
        assert "XAUTHORITY" in env
```

- [ ] **Step 6: Run all tests**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl status, run, screenshot, setup subcommands"
```

---

### Task 9: self-test subcommand

**Files:**
- Modify: `scripts/x11ctl`

- [ ] **Step 1: Implement self_test_command(args, cfg)**

The self-test exercises acceptance criteria programmatically:

- `--tier1`: start headless → xdpyinfo → screenshot → stop → verify cleanup
- `--tier2`: start xpra → check port 10000 responds → stop
- `--tier3`: start vnc → check port 5900 responds → stop
- `--all`: all above + idempotency (start/stop/start) + failure paths:
  - Stale PID recovery
  - Partial-start rollback
  - Xauth symlink rejection
  - Status with partial tiers

Each test prints PASS/FAIL with description. Overall exit 0 if all pass, 1 if any fail.

- [ ] **Step 2: Manual run**

Run: `scripts/x11ctl self-test --tier1`
Expected: All checks pass (if Xvfb is installed).

- [ ] **Step 3: Commit**

```bash
git add scripts/x11ctl
git commit -m "feat: x11ctl self-test diagnostic subcommand"
```

---

### Task 10: Runbook

**Files:**
- Create: `docs/x11-in-freebsd-jails.md`

- [ ] **Step 1: Write the runbook**

Follow the structure defined in the spec (sections 1-10): Overview, Prerequisites, Simplest Path, Tier 1-3, x11ctl Reference, Recipes, Verification, Troubleshooting. Include the `xvfb_run` shell function from the spec. Include complete command examples for every recipe.

- [ ] **Step 2: Verify all code blocks are copy-pasteable**

Read through every code block and verify the commands match the actual `x11ctl` interface implemented in Tasks 1-9.

- [ ] **Step 3: Commit**

```bash
git add docs/x11-in-freebsd-jails.md
git commit -m "docs: X11 in FreeBSD jails runbook with all tiers, recipes, troubleshooting"
```

---

### Task 11: Final integration and cleanup

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 2: Run self-test if in jail with packages**

Run: `scripts/x11ctl self-test --all`
Expected: All checks pass.

- [ ] **Step 3: Verify script is executable and has correct shebang**

Run: `head -1 scripts/x11ctl && ls -la scripts/x11ctl`
Expected: `#!/usr/local/bin/python3` and `-rwxr-xr-x`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: x11ctl final integration and cleanup"
```

# X11 in FreeBSD Jails — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/x11ctl`, a standalone Python script for managing X11 inside FreeBSD jails (Xvfb + Xpra + VNC), plus a comprehensive runbook.

**Architecture:** Single-file Python script (stdlib only, `>=3.10`) managing a 3-tier X11 stack: Xvfb headless display (Tier 1), Xpra remote viewing (Tier 2), x11vnc/noVNC fallback (Tier 3). Declarative `start` replaces desired tier set; subtractive `stop` removes tiers. `fcntl.flock` serializes all mutations. PID identity validated via `ps etimes` + creation epoch.

**Tech Stack:** Python 3.10+ stdlib (`subprocess`, `signal`, `os`, `socket`, `argparse`, `fcntl`, `time`, `pathlib`), FreeBSD `pkg` packages (xorg-vfbserver, xpra, x11vnc, novnc, ImageMagick7-nox11)

**Spec:** `docs/superpowers/specs/2026-04-05-x11-in-freebsd-jails-design.md`

**Internal structure target:** The single file should not exceed ~800 lines. Code is organized in comment-delimited sections: Config, Tier Logic, Pidfile I/O, Tiers File I/O, PID Validation, Process Lifecycle, Locking, Preflight, Port Detection, Xauth, Log Rotation, Readiness Probes, Subcommands, Main.

**Safety-critical acceptance criteria (in addition to spec AC):**
- Xauth: `xauth add` failure/timeout aborts startup with clear error; xauth file cleaned up on failure
- Stale cleanup: live Xvfb artifacts preserved; dead-PID artifacts cleaned; malformed lock cleaned; `ps` timeout → fail closed with manual remediation guidance
- Cookie: offline generation via `os.urandom` + `xauth add`, no live display required

---

## File Map

| File | Purpose |
|------|---------|
| Create: `scripts/x11ctl` | Main script — all subcommands, process management, state |
| Create: `tests/test_x11ctl.py` | Unit + integration tests (pure logic, PID validation, lifecycle, cascade) |
| Create: `docs/x11-in-freebsd-jails.md` | Runbook — operator guide with all tiers, recipes, troubleshooting |

The script is intentionally a single file (no package structure) to match the `scripts/claudechic-remote` pattern and enable copy-into-jail deployment. Tests exercise both pure-logic functions and process-lifecycle logic by importing the script as a module. Integration tests that require real binaries (Xvfb, xpra, x11vnc) are marked with `@pytest.mark.skipif` and skipped when binaries are absent.

---

### Task 1: Script skeleton — Config, CLI parsing, tier logic, all pure I/O functions

This task creates the script and all pure, independently testable functions. These are low-risk but form the foundation for everything else.

**Files:**
- Create: `scripts/x11ctl`
- Create: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for Config, CLI parsing, tier logic, pidfile I/O, tiers file I/O, locking, preflight, port detection, env output, log rotation, xauth creation, and readiness probes**

The test file starts with a module importer that loads the extensionless script:

```python
# tests/test_x11ctl.py
"""Unit tests for x11ctl pure logic."""
import importlib.machinery
import importlib.util
import os
import sys
import socket as _socket
from pathlib import Path

import pytest


def _import_x11ctl():
    """Import the extensionless scripts/x11ctl as a module.

    Uses SourceFileLoader directly because spec_from_file_location returns
    None for files without a recognized Python suffix.
    """
    script_path = str(Path(__file__).parent.parent / "scripts" / "x11ctl")
    loader = importlib.machinery.SourceFileLoader("x11ctl", script_path)
    mod = loader.load_module("x11ctl")
    return mod

x11ctl = _import_x11ctl()


# --- Config ---

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


# --- Tier logic ---

class TestTierSets:
    def test_headless_implies(self):
        assert x11ctl.desired_tiers("headless") == {"headless"}

    def test_xpra_implies_headless(self):
        assert x11ctl.desired_tiers("xpra") == {"headless", "xpra"}

    def test_vnc_implies_headless(self):
        assert x11ctl.desired_tiers("vnc") == {"headless", "vnc"}

    def test_all_tiers(self):
        assert x11ctl.desired_tiers("all") == {"headless", "xpra", "vnc"}

    def test_reconcile_start_declarative(self):
        """start --xpra when vnc was running: stop vnc, keep headless+xpra."""
        old = {"headless", "xpra", "vnc"}
        new = x11ctl.desired_tiers("xpra")
        assert old - new == {"vnc"}  # to stop
        assert new - old == set()    # to start (already running)

    def test_reconcile_stop_subtractive(self):
        """stop --xpra when all running: remove xpra, keep headless+vnc."""
        old = {"headless", "xpra", "vnc"}
        assert old - {"xpra"} == {"headless", "vnc"}

    def test_reconcile_stop_headless_cascades(self):
        """stop --headless: everything stops (cascade rule)."""
        # Cascade is a policy decision, not a set op — tested in Task 4


# --- CLI parsing ---

class TestCLIParsing:
    def test_start_headless(self):
        args = x11ctl.parse_args(["start", "--headless"])
        assert args.command == "start"
        assert args.headless is True

    def test_start_all_bind_all(self):
        args = x11ctl.parse_args(["start", "--all", "--bind-all"])
        assert args.all is True
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


# --- Pidfile I/O ---

class TestPidfile:
    def test_write_and_read(self, tmp_path):
        path = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(path, 1234, 1712345678)
        pid, epoch = x11ctl.read_pidfile(path)
        assert pid == 1234
        assert epoch == 1712345678

    def test_read_missing(self, tmp_path):
        assert x11ctl.read_pidfile(str(tmp_path / "missing.pid")) is None

    def test_read_corrupt(self, tmp_path):
        path = str(tmp_path / "bad.pid")
        Path(path).write_text("garbage\n")
        assert x11ctl.read_pidfile(path) is None

    def test_format_two_integers(self, tmp_path):
        path = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(path, 42, 9999999999)
        assert Path(path).read_text() == "42 9999999999\n"

    def test_write_overwrites(self, tmp_path):
        path = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(path, 1, 1)
        x11ctl.write_pidfile(path, 2, 2)
        pid, _ = x11ctl.read_pidfile(path)
        assert pid == 2

    def test_write_rejects_symlink(self, tmp_path):
        target = tmp_path / "target"
        target.write_text("fake")
        link = tmp_path / "link.pid"
        link.symlink_to(target)
        with pytest.raises(OSError):
            x11ctl.write_pidfile(str(link), 1, 1)

    def test_read_rejects_symlink(self, tmp_path):
        """read_pidfile should also reject symlinks per spec O_NOFOLLOW."""
        target = tmp_path / "target"
        target.write_text("1234 1712345678\n")
        link = tmp_path / "link.pid"
        link.symlink_to(target)
        assert x11ctl.read_pidfile(str(link)) is None


# --- Tiers file I/O ---

class TestTiersFile:
    def test_write_and_read(self, tmp_path):
        path = str(tmp_path / "tiers")
        x11ctl.write_tiers(path, {"headless", "xpra"})
        assert x11ctl.read_tiers(path) == {"headless", "xpra"}

    def test_read_missing(self, tmp_path):
        assert x11ctl.read_tiers(str(tmp_path / "missing")) is None

    def test_delete(self, tmp_path):
        path = str(tmp_path / "tiers")
        x11ctl.write_tiers(path, {"headless"})
        x11ctl.delete_tiers(path)
        assert x11ctl.read_tiers(path) is None

    def test_delete_missing_is_safe(self, tmp_path):
        x11ctl.delete_tiers(str(tmp_path / "missing"))


# --- Locking ---

class TestLocking:
    def test_lock_acquire_release(self, tmp_path):
        lock_path = str(tmp_path / "test.lock")
        lock_fd = x11ctl.acquire_lock(lock_path, exclusive=True)
        assert lock_fd is not None
        x11ctl.release_lock(lock_fd)


# --- Preflight ---

class TestPreflight:
    def test_find_binary_existing(self):
        assert x11ctl.find_binary("python3") is not None

    def test_find_binary_missing(self):
        assert x11ctl.find_binary("nonexistent_binary_xyz") is None

    def test_check_binaries_some_missing(self):
        missing = x11ctl.check_binaries(["python3", "nonexistent_xyz"])
        assert missing == ["nonexistent_xyz"]


# --- Port detection ---

class TestPortCheck:
    def test_available_port(self):
        assert x11ctl.check_port_available("127.0.0.1", 59999) is True

    def test_occupied_port(self):
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 59998))
        sock.listen(1)
        try:
            assert x11ctl.check_port_available("127.0.0.1", 59998) is False
        finally:
            sock.close()


# --- Env output ---

class TestEnvOutput:
    def test_env_output(self):
        cfg = x11ctl.Config()
        output = x11ctl.format_env(cfg)
        assert 'export DISPLAY=":99"' in output
        assert "export XAUTHORITY=" in output


# --- Log rotation ---

class TestLogRotation:
    def test_rotate_creates_prev(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("old content")
        x11ctl.rotate_log(str(log))
        assert (tmp_path / "test.log.prev").read_text() == "old content"
        assert not log.exists()

    def test_rotate_missing_is_safe(self, tmp_path):
        x11ctl.rotate_log(str(tmp_path / "missing.log"))


# --- Xauth ---

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
        with pytest.raises(OSError):
            x11ctl.create_xauth_file(str(link))


# --- Readiness probes ---

class TestReadinessProbe:
    def test_immediate_success(self):
        assert x11ctl.wait_ready(lambda: True, retries=3, delay=0.01) is True

    def test_eventual_success(self):
        attempts = []
        def check():
            attempts.append(1)
            return len(attempts) >= 3
        assert x11ctl.wait_ready(check, retries=5, delay=0.01) is True

    def test_timeout(self):
        assert x11ctl.wait_ready(lambda: False, retries=3, delay=0.01) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: FAIL — `scripts/x11ctl` does not exist yet.

- [ ] **Step 3: Create x11ctl with ALL pure functions**

Create `scripts/x11ctl` containing: `Config`, `desired_tiers`, `parse_args`, `write_pidfile`, `read_pidfile`, `write_tiers`, `read_tiers`, `delete_tiers`, `acquire_lock`, `release_lock`, `find_binary`, `check_binaries`, `check_port_available`, `identify_port_user`, `format_env`, `rotate_log`, `create_xauth_file`, `wait_ready`, and a guarded `main()` stub. Exact implementations as specified in the spec (see spec sections: Display Configuration, PID Management, Tiers File, Port-in-use detection, Xauth, Log rotation). `read_pidfile` must reject symlinks (check `Path(path).is_symlink()` before reading).

- [ ] **Step 4: Make executable and run tests**

Run: `chmod +x scripts/x11ctl && uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl skeleton with all pure functions and tests"
```

---

### Task 2: PID identity validation (`validate_pid`)

The spec's primary defense against PID reuse. This MUST be implemented and tested before any process lifecycle code.

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for validate_pid**

```python
# Add to tests/test_x11ctl.py
import subprocess

class TestValidatePid:
    def test_own_process_is_valid(self):
        """Current process should validate against its own name and recent epoch."""
        pid = os.getpid()
        created = int(x11ctl.time.time())
        comm = "python"  # ps -o comm= for python3 shows "python" or "python3"
        # Get actual comm for this process
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True,
        )
        actual_comm = result.stdout.strip()
        assert x11ctl.validate_pid(pid, created, actual_comm) is True

    def test_dead_pid_is_invalid(self):
        """A PID that doesn't exist should be invalid."""
        assert x11ctl.validate_pid(99999999, int(x11ctl.time.time()), "fake") is False

    def test_wrong_comm_is_invalid(self):
        """Current PID but wrong process name should be invalid."""
        pid = os.getpid()
        created = int(x11ctl.time.time())
        assert x11ctl.validate_pid(pid, created, "definitely_not_this") is False

    def test_wrong_epoch_is_invalid(self):
        """Current PID but epoch from a year ago should be invalid (age mismatch)."""
        pid = os.getpid()
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True,
        )
        comm = result.stdout.strip()
        ancient_epoch = int(x11ctl.time.time()) - 365 * 86400  # 1 year ago
        assert x11ctl.validate_pid(pid, ancient_epoch, comm) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestValidatePid -v`
Expected: FAIL — `validate_pid` not defined.

- [ ] **Step 3: Implement validate_pid**

Add to `scripts/x11ctl`:

```python
# ---------------------------------------------------------------------------
# PID identity validation
# ---------------------------------------------------------------------------

def validate_pid(pid: int, created_epoch: int, expected_comm: str) -> bool:
    """Validate that a PID is alive, has the expected process name, and age matches.

    Uses 3-factor check:
    1. os.kill(pid, 0) — is the process alive?
    2. ps -p <pid> -o comm= — does the process name match?
    3. ps -p <pid> -o etimes= vs time.time() - created_epoch — age match within ±5s?
    """
    # Check 1: alive
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False

    # Check 2: process name
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=5,
        )
        actual_comm = result.stdout.strip()
        if not actual_comm or actual_comm != expected_comm:
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    # Check 3: age match
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etimes="],
            capture_output=True, text=True, timeout=5,
        )
        etimes = int(result.stdout.strip())
        expected_age = int(time.time()) - created_epoch
        if abs(etimes - expected_age) > 5:
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return False

    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestValidatePid -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl validate_pid with 3-factor PID identity check"
```

---

### Task 3: stop_component — kill escalation with PID validation

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for stop_component**

```python
# Add to tests/test_x11ctl.py
import signal

class TestStopComponent:
    def test_stop_running_process(self, tmp_path):
        """Start a sleep process, write its pidfile, then stop it."""
        proc = subprocess.Popen(["sleep", "60"])
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, proc.pid, int(x11ctl.time.time()))
        result = x11ctl.stop_component(pidfile, "sleep")
        assert result is True
        assert proc.poll() is not None  # process is dead
        assert not Path(pidfile).exists()  # pidfile cleaned up

    def test_stop_already_dead(self, tmp_path):
        """Stop with pidfile pointing to dead process: just clean up."""
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, 99999999, int(x11ctl.time.time()))
        result = x11ctl.stop_component(pidfile, "fake")
        assert result is True  # cleaned up successfully
        assert not Path(pidfile).exists()

    def test_stop_no_pidfile(self, tmp_path):
        """Stop when no pidfile exists: nothing to do."""
        pidfile = str(tmp_path / "missing.pid")
        result = x11ctl.stop_component(pidfile, "fake")
        assert result is True

    def test_stop_stale_pid_different_process(self, tmp_path):
        """Pidfile points to a PID that's alive but is a different process."""
        # Use our own PID (python) but claim it should be "Xvfb"
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, os.getpid(), int(x11ctl.time.time()))
        result = x11ctl.stop_component(pidfile, "Xvfb")
        assert result is True  # pidfile cleaned up (stale)
        assert not Path(pidfile).exists()
        # But our process (python) should NOT have been killed
        assert os.getpid() > 0  # we're still alive
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestStopComponent -v`
Expected: FAIL — `stop_component` not defined.

- [ ] **Step 3: Implement stop_component**

```python
# ---------------------------------------------------------------------------
# Process lifecycle — stop
# ---------------------------------------------------------------------------

def stop_component(pidfile_path: str, expected_comm: str) -> bool:
    """Stop a component by reading its pidfile, validating identity, and killing.

    Returns True on success (or nothing to do). False on unexpected error.
    """
    data = read_pidfile(pidfile_path)
    if data is None:
        return True  # no pidfile = nothing to stop

    pid, created_epoch = data

    # Validate identity before killing
    if not validate_pid(pid, created_epoch, expected_comm):
        # Stale pidfile — just clean up
        try:
            os.unlink(pidfile_path)
        except FileNotFoundError:
            pass
        return True

    # Kill: SIGTERM → wait → SIGKILL
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Died between validate and kill
        try:
            os.unlink(pidfile_path)
        except FileNotFoundError:
            pass
        return True

    # Poll until dead (up to 3s)
    for _ in range(30):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    else:
        # Still alive after 3s — SIGKILL
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.1)
        except ProcessLookupError:
            pass

    # Clean up pidfile
    try:
        os.unlink(pidfile_path)
    except FileNotFoundError:
        pass
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestStopComponent -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl stop_component with kill escalation and PID validation"
```

---

### Task 4: Tier reconciliation — start_command and stop_command core logic

The declarative start / subtractive stop dispatcher. Tests use mock components to verify reconciliation logic without real binaries.

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for tier reconciliation**

```python
# Add to tests/test_x11ctl.py

class TestTierReconciliation:
    def test_start_xpra_computes_correct_diff(self):
        """start --xpra when vnc was running: stop vnc, start nothing new (headless+xpra exist)."""
        old_tiers = {"headless", "xpra", "vnc"}
        new_tiers = x11ctl.desired_tiers("xpra")
        to_stop, to_start = x11ctl.compute_tier_diff(old_tiers, new_tiers)
        assert to_stop == {"vnc"}
        assert to_start == set()

    def test_start_all_from_headless(self):
        """start --all when only headless: start xpra + vnc."""
        old_tiers = {"headless"}
        new_tiers = x11ctl.desired_tiers("all")
        to_stop, to_start = x11ctl.compute_tier_diff(old_tiers, new_tiers)
        assert to_stop == set()
        assert to_start == {"xpra", "vnc"}

    def test_start_headless_from_all(self):
        """start --headless when all running: stop xpra + vnc."""
        old_tiers = {"headless", "xpra", "vnc"}
        new_tiers = x11ctl.desired_tiers("headless")
        to_stop, to_start = x11ctl.compute_tier_diff(old_tiers, new_tiers)
        assert to_stop == {"xpra", "vnc"}
        assert to_start == set()

    def test_start_from_nothing(self):
        """start --xpra with nothing running."""
        old_tiers = set()
        new_tiers = x11ctl.desired_tiers("xpra")
        to_stop, to_start = x11ctl.compute_tier_diff(old_tiers, new_tiers)
        assert to_stop == set()
        assert to_start == {"headless", "xpra"}

    def test_cascade_stop_headless(self):
        """stop --headless cascades: returns all current tiers as to_stop."""
        current = {"headless", "xpra", "vnc"}
        to_stop = x11ctl.compute_cascade_stop("headless", current)
        assert to_stop == {"headless", "xpra", "vnc"}

    def test_cascade_stop_vnc(self):
        """stop --vnc: only vnc stops."""
        current = {"headless", "xpra", "vnc"}
        to_stop = x11ctl.compute_cascade_stop("vnc", current)
        assert to_stop == {"vnc"}

    def test_cascade_stop_xpra(self):
        """stop --xpra: only xpra stops."""
        current = {"headless", "xpra", "vnc"}
        to_stop = x11ctl.compute_cascade_stop("xpra", current)
        assert to_stop == {"xpra"}

    def test_tier_to_components(self):
        """Map tier names to process component names for stop."""
        assert x11ctl.tier_components("headless") == ["xvfb"]
        assert x11ctl.tier_components("xpra") == ["xpra"]
        assert set(x11ctl.tier_components("vnc")) == {"x11vnc", "websockify"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestTierReconciliation -v`
Expected: FAIL — `compute_tier_diff`, `compute_cascade_stop`, `tier_components` not defined.

- [ ] **Step 3: Implement reconciliation functions**

```python
# ---------------------------------------------------------------------------
# Tier reconciliation
# ---------------------------------------------------------------------------

TIER_COMPONENTS: dict[str, list[str]] = {
    "headless": ["xvfb"],
    "xpra": ["xpra"],
    "vnc": ["x11vnc", "websockify"],
}

# Stop order: viewers first, then display
STOP_ORDER = ["websockify", "x11vnc", "xpra", "xvfb"]


def compute_tier_diff(
    old_tiers: set[str], new_tiers: set[str]
) -> tuple[set[str], set[str]]:
    """Compute which tiers to stop and start for a declarative start.

    Returns (to_stop, to_start).
    """
    return old_tiers - new_tiers, new_tiers - old_tiers


def compute_cascade_stop(tier: str, current: set[str]) -> set[str]:
    """Compute which tiers to stop. Headless cascades everything."""
    if tier == "headless" or tier == "all":
        return set(current)
    return {tier} & current


def tier_components(tier: str) -> list[str]:
    """Map a tier name to its component process names."""
    return TIER_COMPONENTS.get(tier, [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestTierReconciliation -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl tier reconciliation (declarative start, cascade stop)"
```

---

### Task 5: start_headless — launch Xvfb with rollback

Wire up the actual process launch for Tier 1. Integration test conditional on Xvfb being available.

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write test for stale artifact cleanup**

```python
# Add to tests/test_x11ctl.py

class TestStaleCleanup:
    def test_clean_dead_pid_artifacts(self, tmp_path):
        """Stale lock with dead PID should be removed."""
        lock = tmp_path / ".X99-lock"
        lock.write_text("99999999\n")  # PID that doesn't exist
        socket_dir = tmp_path / ".X11-unix"
        socket_dir.mkdir()
        socket_file = socket_dir / "X99"
        socket_file.write_text("")

        result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
        assert result is True
        assert not lock.exists()
        assert not socket_file.exists()

    def test_clean_no_artifacts(self, tmp_path):
        """No artifacts = returns True (nothing to do)."""
        assert x11ctl.clean_stale_x_artifacts(99, str(tmp_path)) is True

    def test_clean_live_xvfb_preserved(self, tmp_path):
        """Lock owned by a live Xvfb should NOT be deleted."""
        lock = tmp_path / ".X99-lock"
        socket_dir = tmp_path / ".X11-unix"
        socket_dir.mkdir()
        socket_file = socket_dir / "X99"
        socket_file.write_text("")
        lock.write_text("12345\n")

        # Mock ps to report this PID is a live Xvfb
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="Xvfb\n", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
            assert result is False  # live Xvfb — don't touch
            assert lock.exists()  # preserved
            assert socket_file.exists()  # preserved

    def test_clean_live_non_xvfb_cleaned(self, tmp_path):
        """Lock owned by a live non-Xvfb process should be cleaned (stale)."""
        lock = tmp_path / ".X99-lock"
        lock.write_text(f"{os.getpid()}\n")
        # Our process is python, not Xvfb — stale, safe to clean
        result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
        assert result is True

    def test_clean_socket_only_fails_closed(self, tmp_path):
        """Socket exists but lock missing — can't determine owner, fail closed."""
        socket_dir = tmp_path / ".X11-unix"
        socket_dir.mkdir()
        socket_file = socket_dir / "X99"
        socket_file.write_text("")
        # No lock file
        result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
        assert result is False  # fail closed
        assert socket_file.exists()  # not deleted

    def test_clean_malformed_lock(self, tmp_path):
        """Malformed lock file (not a PID) should be cleaned."""
        lock = tmp_path / ".X99-lock"
        lock.write_text("not_a_pid\n")
        result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
        assert result is True
        assert not lock.exists()

    def test_clean_returns_false_on_timeout(self, tmp_path):
        """If ps times out, should fail closed (return False)."""
        lock = tmp_path / ".X99-lock"
        lock.write_text("1\n")  # PID 1 (init) — will exist

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
            assert result is False  # fail closed
            assert lock.exists()  # not deleted


class TestXauthAdd:
    def test_xauth_add_failure_aborts_startup(self):
        """If xauth add returns non-zero, start_headless should return False."""
        cfg = x11ctl.Config()
        started = []

        with patch.object(x11ctl, "check_binaries", return_value=[]), \
             patch.object(x11ctl, "read_pidfile", return_value=None), \
             patch.object(x11ctl, "clean_stale_x_artifacts", return_value=True), \
             patch.object(x11ctl, "create_xauth_file"), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=1, stdout="", stderr="auth error",
             )):
            result = x11ctl.start_headless(cfg, started)
            assert result is False
            assert "xvfb" not in started

    def test_xauth_add_timeout_aborts_startup(self):
        """If xauth add times out, start_headless should return False."""
        cfg = x11ctl.Config()
        started = []

        with patch.object(x11ctl, "check_binaries", return_value=[]), \
             patch.object(x11ctl, "read_pidfile", return_value=None), \
             patch.object(x11ctl, "clean_stale_x_artifacts", return_value=True), \
             patch.object(x11ctl, "create_xauth_file"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("xauth", 5)):
            result = x11ctl.start_headless(cfg, started)
            assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestStaleCleanup -v`
Expected: FAIL — `clean_stale_x_artifacts` not defined.

- [ ] **Step 3: Implement clean_stale_x_artifacts and start_headless**

```python
# ---------------------------------------------------------------------------
# Stale artifact cleanup
# ---------------------------------------------------------------------------

def clean_stale_x_artifacts(display_num: int, tmp_dir: str = "/tmp") -> bool:
    """Remove stale X server lock and socket files ONLY if no matching Xvfb is running.

    Reads PID from the lock file and checks if that PID is a live Xvfb process.
    If it is, artifacts are left untouched (another Xvfb owns them).

    Returns True if artifacts were cleaned or didn't exist.
    Returns False if cleanup was blocked (live Xvfb or couldn't determine).
    Caller should print guidance when False is returned.
    """
    lock_path = Path(tmp_dir) / f".X{display_num}-lock"
    socket_path = Path(tmp_dir) / ".X11-unix" / f"X{display_num}"

    # No artifacts = nothing to do
    if not lock_path.exists() and not socket_path.exists():
        return True

    # Socket exists but lock is missing — can't determine owner, fail closed
    if not lock_path.exists() and socket_path.exists():
        return False

    # Check if a live Xvfb owns these artifacts — FAIL CLOSED
    # Only delete if we have positive evidence the owning PID is gone or not Xvfb.
    if lock_path.exists() and not lock_path.is_symlink():
        try:
            pid_str = lock_path.read_text().strip()
            pid = int(pid_str)
            # Check if this PID is alive and is actually Xvfb
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=5,
            )
            comm = result.stdout.strip()
            if comm == "Xvfb":
                return False  # Live Xvfb owns these — caller distinguishes via lock PID
            if comm:
                pass  # PID alive but not Xvfb — stale, safe to clean
            # If comm is empty, process is dead — stale, safe to clean
        except (ValueError, FileNotFoundError):
            pass  # Lock file unreadable/missing — safe to clean (no owner)
        except subprocess.TimeoutExpired:
            return False  # Can't determine owner — fail closed, don't touch

    for p in (lock_path, socket_path):
        if p.exists() and not p.is_symlink():
            p.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Start headless (Tier 1)
# ---------------------------------------------------------------------------

def start_headless(cfg: Config, started: list[str]) -> bool:
    """Start Xvfb. On failure, returns False (caller handles rollback).

    Appends 'xvfb' to `started` list on success for rollback tracking.
    """
    pidfile = cfg.pidfile("xvfb")

    # Idempotent: check if already running
    data = read_pidfile(pidfile)
    if data is not None:
        pid, epoch = data
        if validate_pid(pid, epoch, "Xvfb"):
            return True  # already running

    # Preflight
    missing = check_binaries(["Xvfb", "xauth", "xdpyinfo"])
    if missing:
        print(f"Error: missing required commands: {', '.join(missing)}", file=sys.stderr)
        print(f"  Run: pkg install {' '.join(missing)}", file=sys.stderr)
        return False

    # Clean stale artifacts (fail-closed: won't delete if a live Xvfb or unknown owner)
    if not clean_stale_x_artifacts(cfg.display_number):
        # Distinguish "live Xvfb" from "unknown owner" for operator guidance
        lock_path = Path(f"/tmp/.X{cfg.display_number}-lock")
        if lock_path.exists():
            try:
                lock_pid = int(lock_path.read_text().strip())
                r = subprocess.run(["ps", "-p", str(lock_pid), "-o", "comm="],
                                   capture_output=True, text=True, timeout=2)
                if r.stdout.strip() == "Xvfb":
                    print(
                        f"Error: display {cfg.display} is already owned by a live Xvfb (PID {lock_pid}).\n"
                        f"  Stop it first: kill {lock_pid}",
                        file=sys.stderr,
                    )
                    return False
            except Exception:
                pass
        print(
            f"Error: stale X artifacts exist for display {cfg.display} but ownership could not be determined.\n"
            f"  This may be due to a crashed Xvfb or a ps timeout.\n"
            f"  Manual fix: verify no Xvfb is running on {cfg.display}, then:\n"
            f"    rm -f /tmp/.X{cfg.display_number}-lock /tmp/.X11-unix/X{cfg.display_number}",
            file=sys.stderr,
        )
        return False

    # Create xauth file and add a random cookie (display-independent — no live X server needed)
    create_xauth_file(cfg.xauth)
    cookie = os.urandom(16).hex()  # 32-char hex MIT-MAGIC-COOKIE
    try:
        result = subprocess.run(
            ["xauth", "-f", cfg.xauth, "add", cfg.display, ".", cookie],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            print(f"Error: xauth add failed (exit {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
            try:
                os.unlink(cfg.xauth)
            except FileNotFoundError:
                pass
            return False
    except subprocess.TimeoutExpired:
        print("Error: xauth add timed out after 5s", file=sys.stderr)
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        return False
    except FileNotFoundError:
        print("Error: xauth binary not found (should have been caught by preflight)", file=sys.stderr)
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        return False

    # Rotate log
    logfile = cfg.logfile("xvfb")
    rotate_log(logfile)

    # Launch Xvfb
    log_fd = os.open(logfile, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    proc = subprocess.Popen(
        ["Xvfb", cfg.display, "-screen", "0", cfg.screen, "-auth", cfg.xauth],
        stdout=log_fd, stderr=log_fd,
    )
    os.close(log_fd)

    # Write pidfile
    write_pidfile(pidfile, proc.pid, int(time.time()))
    started.append("xvfb")

    # Readiness probe
    def check_display():
        r = subprocess.run(
            ["xdpyinfo", "-display", cfg.display],
            capture_output=True, env={**os.environ, "XAUTHORITY": cfg.xauth},
            timeout=5,
        )
        return r.returncode == 0

    if not wait_ready(check_display, retries=10, delay=0.5):
        print(f"Error: Xvfb failed to start. See {logfile}", file=sys.stderr)
        # Self-rollback: kill the Xvfb we just started, clean up state
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            os.unlink(pidfile)
        except FileNotFoundError:
            pass
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        started.remove("xvfb") if "xvfb" in started else None
        return False

    return True
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 5: Integration test (conditional)**

Run: `which Xvfb && scripts/x11ctl start --headless && scripts/x11ctl status && scripts/x11ctl stop --headless || echo "Xvfb not available, skipping"`

- [ ] **Step 6: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl start_headless with readiness probes and rollback"
```

---

### Task 6: Wire up start_command, stop_command, status_command with full dispatch

Connect tier reconciliation to actual process lifecycle. This is the integration glue.

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for command wiring**

```python
# Add to tests/test_x11ctl.py
from unittest.mock import patch, MagicMock

class TestStartCommand:
    def test_start_stops_excess_before_starting_missing(self):
        """start --headless when all running: stops xpra+vnc components."""
        cfg = x11ctl.Config()
        stopped = []
        started = []

        def mock_stop(pidfile, comm):
            stopped.append(comm)
            return True

        def mock_start_headless(c, s):
            started.append("xvfb")
            return True

        with patch.object(x11ctl, "stop_component", side_effect=mock_stop), \
             patch.object(x11ctl, "start_headless", side_effect=mock_start_headless), \
             patch.object(x11ctl, "read_tiers", return_value={"headless", "xpra", "vnc"}), \
             patch.object(x11ctl, "write_tiers"), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            # Simulate start --headless (desired = {headless}, excess = {xpra, vnc})
            # The command should stop excess BEFORE starting
            result = x11ctl.start_command_impl(cfg, desired={"headless"}, bind_all=False)
            assert result == 0
            # Excess components stopped (in STOP_ORDER)
            assert "websockify" in stopped or "x11vnc" in stopped or "xpra" in stopped


class TestStopCommand:
    def test_stop_vnc_updates_tiers_file(self):
        """stop --vnc should update tiers file to remove vnc."""
        cfg = x11ctl.Config()
        written_tiers = []

        with patch.object(x11ctl, "stop_component", return_value=True), \
             patch.object(x11ctl, "read_tiers", return_value={"headless", "xpra", "vnc"}), \
             patch.object(x11ctl, "write_tiers", side_effect=lambda p, t: written_tiers.append(t)), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            result = x11ctl.stop_command_impl(cfg, tier="vnc")
            assert result == 0
            assert written_tiers[-1] == {"headless", "xpra"}

    def test_stop_all_deletes_tiers_file(self):
        """stop --all should delete tiers file."""
        cfg = x11ctl.Config()
        deleted = []

        with patch.object(x11ctl, "stop_component", return_value=True), \
             patch.object(x11ctl, "read_tiers", return_value={"headless", "xpra", "vnc"}), \
             patch.object(x11ctl, "delete_tiers", side_effect=lambda p: deleted.append(p)), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            result = x11ctl.stop_command_impl(cfg, tier="all")
            assert result == 0
            assert len(deleted) == 1


class TestStatusCommand:
    def test_status_healthy_returns_0(self):
        """status with all expected components running returns 0."""
        cfg = x11ctl.Config()

        with patch.object(x11ctl, "read_tiers", return_value={"headless"}), \
             patch.object(x11ctl, "read_pidfile", return_value=(1234, 1712345678)), \
             patch.object(x11ctl, "validate_pid", return_value=True), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            result = x11ctl.status_command_impl(cfg)
            assert result == 0

    def test_status_unhealthy_returns_1(self):
        """status with expected component down returns 1."""
        cfg = x11ctl.Config()

        with patch.object(x11ctl, "read_tiers", return_value={"headless"}), \
             patch.object(x11ctl, "read_pidfile", return_value=None), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            result = x11ctl.status_command_impl(cfg)
            assert result == 1

    def test_status_no_tiers_file_returns_0(self):
        """status with no tiers file (nothing started) returns 0."""
        cfg = x11ctl.Config()

        with patch.object(x11ctl, "read_tiers", return_value=None), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            result = x11ctl.status_command_impl(cfg)
            assert result == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestStartCommand tests/test_x11ctl.py::TestStopCommand tests/test_x11ctl.py::TestStatusCommand -v`
Expected: FAIL — `start_command_impl`, `stop_command_impl`, `status_command_impl` not defined.

- [ ] **Step 3: Implement start_command_impl, stop_command_impl, status_command_impl**

These are the testable inner functions. The top-level `start_command(args, cfg)` is a thin wrapper that extracts flags, determines bind_all, and calls `start_command_impl`. Note: `bind_all` is passed as a parameter, NOT mutated on `cfg` (immutability rule).

The functions should:
- `start_command_impl(cfg, desired, bind_all)`: lock, read tiers, compute diff, stop excess (STOP_ORDER), start missing (headless first), write tiers, rollback on failure
- `stop_command_impl(cfg, tier)`: lock, read tiers, compute cascade, stop components (STOP_ORDER), update/delete tiers
- `status_command_impl(cfg)`: shared lock, read tiers, validate each expected component, return 0/1

- [ ] **Step 4: Wire main() to dispatch all subcommands**

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 6: Integration test (conditional)**

Run (only if Xvfb available):
```bash
which Xvfb && {
    scripts/x11ctl start --headless
    scripts/x11ctl status
    scripts/x11ctl stop --headless
    scripts/x11ctl status
} || echo "Xvfb not installed — integration test skipped (this is expected in CI without X11 packages)"
```

- [ ] **Step 7: Commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl start/stop/status with tier reconciliation and rollback"
```

---

### Task 7: Tier 2 — Xpra start/stop

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write test for Xpra command assembly**

```python
class TestXpraCommand:
    def test_xpra_command_localhost(self):
        cfg = x11ctl.Config()
        cmd = x11ctl.build_xpra_command(cfg)
        assert cmd[0] == "xpra"
        assert "shadow" in cmd
        assert "--daemon=no" in cmd
        assert f"--bind-tcp=127.0.0.1:{cfg.xpra_port}" in cmd
        assert "--html=on" in cmd

    def test_xpra_command_bind_all(self):
        cfg = x11ctl.Config()
        cmd = x11ctl.build_xpra_command(cfg, bind="0.0.0.0")
        assert f"--bind-tcp=0.0.0.0:{cfg.xpra_port}" in cmd

    def test_xpra_env_has_xauthority(self):
        cfg = x11ctl.Config()
        env = x11ctl.build_xpra_env(cfg)
        assert env["XAUTHORITY"] == cfg.xauth

    def test_xpra_port_conflict_returns_2(self):
        """start_xpra should return exit code 2 when port is occupied."""
        cfg = x11ctl.Config()
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", cfg.xpra_port))
        sock.listen(1)
        try:
            result = x11ctl.start_xpra(cfg, bind="127.0.0.1", started=[])
            assert result == 2
        finally:
            sock.close()
```

- [ ] **Step 2: Implement build_xpra_command, build_xpra_env, start_xpra**

- [ ] **Step 3: Run tests, commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl Tier 2 Xpra start/stop"
```

---

### Task 8: Tier 3 — VNC start/stop

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write test for VNC command assembly**

```python
class TestVncCommand:
    def test_x11vnc_command(self):
        cfg = x11ctl.Config()
        cmd = x11ctl.build_x11vnc_command(cfg)
        assert "x11vnc" in cmd
        assert "-noxdamage" in cmd
        assert "-localhost" in cmd
        assert f"-rfbport" in cmd

    def test_websockify_command(self):
        cfg = x11ctl.Config()
        cmd = x11ctl.build_websockify_command(cfg)
        assert "websockify" in cmd
        assert "--web=/usr/local/libexec/novnc" in cmd

    def test_x11vnc_no_localhost_with_bind_all(self):
        cfg = x11ctl.Config()
        cmd = x11ctl.build_x11vnc_command(cfg, bind="0.0.0.0")
        assert "-localhost" not in cmd

    def test_vnc_port_conflict_returns_2(self):
        """start_vnc should return exit code 2 when VNC port is occupied."""
        cfg = x11ctl.Config()
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", cfg.vnc_port))
        sock.listen(1)
        try:
            result = x11ctl.start_vnc(cfg, bind="127.0.0.1", started=[])
            assert result == 2
        finally:
            sock.close()
```

- [ ] **Step 2: Implement build_x11vnc_command, build_websockify_command, start_vnc**

- [ ] **Step 3: Run tests, commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl Tier 3 VNC/noVNC start/stop"
```

---

### Task 9: run subcommand with signal forwarding

**Files:**
- Modify: `scripts/x11ctl`
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write test for run_command cleanup**

```python
class TestRunCommand:
    @pytest.mark.skipif(
        x11ctl.find_binary("Xvfb") is None,
        reason="Xvfb not installed",
    )
    def test_run_returns_child_exit_code(self, tmp_path):
        """run should propagate the child's exit code."""
        cfg = x11ctl.Config()
        cfg.display = f":{os.getpid()}"
        cfg.xauth = str(tmp_path / "xauth")
        rc = x11ctl.run_with_temp_display(cfg, ["/bin/sh", "-c", "exit 42"])
        assert rc == 42

    @pytest.mark.skipif(
        x11ctl.find_binary("Xvfb") is None,
        reason="Xvfb not installed",
    )
    def test_run_creates_and_tears_down_display(self, tmp_path):
        """run should create a temp display and tear it down after."""
        cfg = x11ctl.Config()
        cfg.display = f":{os.getpid()}"
        cfg.xauth = str(tmp_path / "xauth")
        rc = x11ctl.run_with_temp_display(cfg, ["xdpyinfo"])
        assert rc == 0
        assert not Path(cfg.xauth).exists()

    @pytest.mark.skipif(
        x11ctl.find_binary("Xvfb") is None,
        reason="Xvfb not installed",
    )
    def test_run_signal_forwarding(self, tmp_path):
        """SIGTERM to x11ctl run should kill child and Xvfb."""
        cfg = x11ctl.Config()
        cfg.display = f":{os.getpid()}"
        cfg.xauth = str(tmp_path / "xauth")
        # Launch run in a subprocess so we can signal it
        proc = subprocess.Popen(
            [sys.executable, "-c", f"""
import sys; sys.path.insert(0, '.')
from importlib.machinery import SourceFileLoader
x = SourceFileLoader('x11ctl', 'scripts/x11ctl').load_module()
cfg = x.Config()
cfg.display = '{cfg.display}'
cfg.xauth = '{cfg.xauth}'
sys.exit(x.run_with_temp_display(cfg, ['sleep', '60']))
"""],
            cwd=str(Path(__file__).parent.parent),
        )
        import time as _time
        _time.sleep(2)  # Give Xvfb time to start
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        # Process should have exited (not hung)
        assert proc.returncode is not None
```

- [ ] **Step 2: Implement run_with_temp_display with signal forwarding**

The function creates a temp Xvfb, registers SIGINT/SIGTERM handlers that forward to child and teardown, runs the user command, and returns its exit code.

- [ ] **Step 3: Run tests, commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
git commit -m "feat: x11ctl run subcommand with signal forwarding"
```

---

### Task 10: screenshot, setup, self-test subcommands

**Files:**
- Modify: `scripts/x11ctl`

- [ ] **Step 1: Implement screenshot_command**

Verify `import` binary exists, run `import -window root -display :N <path>`, report success/failure.

- [ ] **Step 2: Implement setup_command**

Check `os.geteuid() == 0`, map tier flags to package lists, run `pkg install -y`.

- [ ] **Step 3: Implement self_test_command**

Exercise acceptance criteria programmatically. `--tier1`: start/xdpyinfo/screenshot/stop. `--all`: adds idempotency, stale PID recovery, partial rollback, symlink rejection, partial-tier status.

- [ ] **Step 4: Commit**

```bash
git add scripts/x11ctl
git commit -m "feat: x11ctl screenshot, setup, self-test subcommands"
```

---

### Task 11: Runbook

**Files:**
- Create: `docs/x11-in-freebsd-jails.md`

- [ ] **Step 1: Write the runbook**

Follow the spec's 10-section structure: Overview, Prerequisites, Simplest Path (xvfb_run shell function), Tier 1-3, x11ctl Reference, Recipes, Verification, Troubleshooting. All code blocks must be copy-pasteable and match the actual x11ctl interface.

- [ ] **Step 2: Commit**

```bash
git add docs/x11-in-freebsd-jails.md
git commit -m "docs: X11 in FreeBSD jails runbook"
```

---

### Task 12: Final integration and cleanup

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/test_x11ctl.py -v`
Expected: All PASS.

- [ ] **Step 2: Run self-test if binaries available**

Run: `scripts/x11ctl self-test --all`

- [ ] **Step 3: Verify executable permissions and shebang**

Run: `head -1 scripts/x11ctl && ls -la scripts/x11ctl`
Expected: `#!/usr/local/bin/python3` and `-rwxr-xr-x`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: x11ctl final integration and cleanup"
```

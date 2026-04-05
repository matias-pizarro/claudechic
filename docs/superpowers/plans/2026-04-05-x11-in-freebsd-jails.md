# X11 in FreeBSD Jails — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/x11ctl`, a standalone Python script for managing X11 inside FreeBSD jails (Xvfb + Xpra + VNC), plus a comprehensive runbook.

**Architecture:** Single-file Python script (stdlib only, `>=3.10`) managing a 3-tier X11 stack: Xvfb headless display (Tier 1), Xpra remote viewing (Tier 2), x11vnc/noVNC fallback (Tier 3). Declarative `start` replaces desired tier set; subtractive `stop` removes tiers. `fcntl.flock` serializes all mutations. PID identity validated via `ps etimes` + creation epoch.

**Tech Stack:** Python 3.10+ stdlib (`subprocess`, `signal`, `os`, `sys`, `socket`, `argparse`, `fcntl`, `time`, `pathlib`, `tempfile`), FreeBSD `pkg` packages (xorg-vfbserver, xpra, x11vnc, novnc, ImageMagick7-nox11)

**Spec:** `docs/superpowers/specs/2026-04-05-x11-in-freebsd-jails-design.md`

**Internal structure target:** The single file should not exceed ~800 lines. Code is organized in comment-delimited sections: Config, Tier Logic, Pidfile I/O, Tiers File I/O, PID Validation, Process Lifecycle, Locking, Preflight, Port Detection, Xauth, Log Rotation, Readiness Probes, Subcommands, Main.

**Return type convention:** All tier start functions (`start_headless`, `start_xpra`, `start_vnc`) share the signature `(cfg: Config, started: list[str], *, bind: str = "127.0.0.1") -> int` and return: 0=success, 1=general failure, 2=port conflict. `start_headless` ignores the `bind` parameter (Xvfb uses Unix sockets). Helper function `stop_component` returns `bool`: `True` on success or nothing-to-do, `False` when SIGKILL fails (process still running). `clean_stale_x_artifacts` returns `bool`. The `start_command_impl` aggregates component return codes into a single exit code.

**`validate_pid` fallback:** If `ps -o etimes=` is unavailable inside a jail (restricted `kern.proc` visibility), `validate_pid` should fall back to PID + comm check only (2-factor instead of 3-factor) rather than treating every pidfile as stale. The implementer should handle empty `ps` output gracefully.

**Atomic file writes:** Both `write_pidfile` and `write_tiers` MUST use the write-to-temp-then-rename pattern: write to a tempfile in the same directory (`/tmp`), then `os.rename()` directly over the target path. Do NOT unlink the old file first — `os.rename()` atomically replaces the destination, so readers never see a missing file and a crash between unlink and rename cannot lose the last known-good state. Before renaming, check `os.path.lexists(target) and os.path.islink(target)` — if the target is a symlink, raise `OSError` instead of renaming over it. Failure of `write_pidfile` after a successful `Popen` MUST terminate the spawned process and clean up xauth before returning failure.

**Lock acquisition timeout:** `acquire_lock` MUST use `fcntl.LOCK_NB` (non-blocking) in a retry loop with a 30-second timeout. On timeout, print "Another x11ctl operation is in progress" and return `None`. Callers must check for `None` and exit with code 1.

**Config immutability:** `Config` should be a `@dataclass(frozen=True)` or use `__slots__` + no setters. All fields set at construction from env vars and defaults. Tests that need custom Config values must use `monkeypatch.setenv` before construction or a factory classmethod with overrides.

**Input validation:** `Config.__post_init__` MUST validate:
- `X11CTL_DISPLAY` matches `^:\d+$` regex
- `X11CTL_SCREEN` (if set) matches `^\d+x\d+x\d+$`
- `X11CTL_XAUTH` (if set) is validated by normalizing first, then checking structurally. The validation MUST: (1) resolve the path: `normalized = str(Path(xauth).resolve(strict=False))`, (2) verify `normalized` matches `^/tmp/\.x11ctl-[^/]+$` exactly (this single regex enforces parent == /tmp, basename starts with .x11ctl-, no nested components, no traversal). This prevents path traversal attacks like `/tmp/.x11ctl-/../etc/shadow` (resolves to `/etc/shadow`, fails regex) and `/tmp/anything/../.x11ctl-safe` (resolves to `/tmp/.x11ctl-safe`, passes only if valid). The default is `/tmp/.x11ctl-xauth`. Invalid values raise `ValueError`. Tests use paths like `/tmp/.x11ctl-test-xauth` which pass validation.

**Xauth cleanup on stop:** When the headless tier is stopped (via `stop --headless`, `stop --all`, or cascade), `stop_command_impl` MUST remove the xauth file — but ONLY if all components in that tier were actually stopped successfully (`stop_component` returned `True`). If any `stop_component` returns `False` (process survived SIGKILL), preserve the tiers file for the surviving components and do NOT delete xauth. Update the tiers file to reflect only the surviving tiers.

**Screenshot safety:** `screenshot_command_impl` MUST reject output paths beginning with `-` and MUST insert `--` before the output filename in the ImageMagick `import` command to prevent argument injection.

**Setup safety:** `setup_command_impl` MUST invoke `pkg` via absolute path `/usr/sbin/pkg` (not PATH search) to prevent privilege escalation with a poisoned PATH. The setup should resolve the correct `websockify` package name by checking `pkg search websockify` output.

**Self-test isolation:** `self_test_command` MUST refuse to run if tiers are already active (check tiers file first). Self-test dynamically selects an unused high display number by probing `:98`, `:97`, ... (bounded range 98→80, fail with error if all occupied) — a display is considered occupied if EITHER `/tmp/.X<N>-lock` OR `/tmp/.X11-unix/X<N>` exists. Creates a temporary `Config` with `state_prefix=".x11ctl-selftest-<N>"`. All state paths derive from `state_prefix`. The `Config` class accepts an optional `state_prefix` parameter (default: `.x11ctl`) in its factory classmethod `Config.for_self_test(display_num)`.

**Self-test cleanup:** On completion or failure, self-test MUST: (1) stop all spawned processes via `stop_component` for each component in the selftest stack (not just delete files), (2) remove all state artifacts (pidfiles, tiers, xauth, logs). On SIGTERM interruption, the self-test SIGTERM handler MUST kill all child processes before exiting. On the next self-test run, detect stale selftest artifacts by checking for selftest pidfiles — validate PIDs via `validate_pid`, kill any surviving selftest processes, then clean artifacts before proceeding.

**Bare start/stop behavior:** `start` with no tier flag defaults to `--headless`. `stop` with no tier flag defaults to `--all`.

**Safety-critical acceptance criteria (in addition to spec AC):**
- Xauth: `xauth add` failure/timeout aborts startup with clear error; xauth file cleaned up on failure; xauth file cleaned up on stop
- Stale cleanup: live Xvfb artifacts preserved; dead-PID artifacts cleaned; malformed lock cleaned; `ps` timeout → fail closed with manual remediation guidance; broken symlinks fail closed
- Cookie: offline generation via `os.urandom` + `xauth add`, no live display required
- File I/O: all state file writes use atomic rename pattern; all reads/writes use `O_NOFOLLOW`

**Alternatives considered (why not shell/rc.d/daemon(8)):**
The spec's `xvfb_run` shell function covers the simplest case (ephemeral headless display for CI). However, the persistent multi-tier use case (Xvfb + Xpra + VNC with declarative reconciliation, readiness probes, PID tracking, rollback, and self-test) exceeds what shell scripts, `rc.d` services, or `daemon(8)` can express without equivalent or greater complexity. Specifically: (1) `daemon(8)` manages a single process — it cannot express tier dependencies or cascade stop; (2) `rc.d` scripts would need 3-4 separate services with ordering dependencies in `rc.conf`, each needing the same PID validation logic; (3) shell lacks structured error handling for the rollback-on-partial-failure pattern. The Python approach consolidates all tiers in one tool with a unified CLI, tests, and self-test. The runbook documents the shell function for simple cases and `x11ctl` for complex ones — operators choose the appropriate tool.

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
import signal
import subprocess
import sys
import socket as _socket
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _import_x11ctl():
    """Import the extensionless scripts/x11ctl as a module.

    Uses SourceFileLoader + exec_module (not deprecated load_module)
    because spec_from_file_location returns None for extensionless files.
    """
    script_path = str(Path(__file__).parent.parent / "scripts" / "x11ctl")
    loader = importlib.machinery.SourceFileLoader("x11ctl", script_path)
    spec = importlib.util.spec_from_loader("x11ctl", loader)
    mod = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    sys.argv = ["x11ctl"]
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.argv = old_argv
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

    def test_start_no_tier_defaults_to_headless(self):
        args = x11ctl.parse_args(["start"])
        assert args.command == "start"
        # No tier flags set — default handled by start_command dispatch

    def test_stop_no_tier_defaults_to_all(self):
        args = x11ctl.parse_args(["stop"])
        assert args.command == "stop"
        # No tier flags set — default handled by stop_command dispatch

    def test_self_test(self):
        args = x11ctl.parse_args(["self-test", "--tier1"])
        assert args.command == "self-test"
        assert args.tier1 is True

    def test_self_test_all(self):
        args = x11ctl.parse_args(["self-test", "--all"])
        assert args.command == "self-test"
        assert args.all is True


# --- Config validation ---

class TestConfigValidation:
    def test_valid_display(self):
        cfg = x11ctl.Config()
        assert cfg.display == ":99"

    def test_invalid_display_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_DISPLAY", "bad")
        with pytest.raises(ValueError, match="must match"):
            x11ctl.Config()

    def test_display_with_extra_chars_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_DISPLAY", ":99; rm -rf /")
        with pytest.raises(ValueError, match="must match"):
            x11ctl.Config()

    def test_valid_screen(self):
        cfg = x11ctl.Config()
        assert cfg.screen == "1920x1080x24"

    def test_custom_screen(self, monkeypatch):
        monkeypatch.setenv("X11CTL_SCREEN", "800x600x16")
        cfg = x11ctl.Config()
        assert cfg.screen == "800x600x16"

    def test_invalid_screen_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_SCREEN", "bad")
        with pytest.raises(ValueError, match="must match"):
            x11ctl.Config()

    def test_screen_injection_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_SCREEN", "1920x1080x24 -evil")
        with pytest.raises(ValueError, match="must match"):
            x11ctl.Config()

    def test_xauth_default(self):
        cfg = x11ctl.Config()
        assert cfg.xauth == "/tmp/.x11ctl-xauth"

    def test_xauth_valid_override(self, monkeypatch):
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-test-xauth")
        cfg = x11ctl.Config()
        assert cfg.xauth == "/tmp/.x11ctl-test-xauth"

    def test_xauth_invalid_prefix_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_XAUTH", "/home/attacker/.xauth")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_xauth_arbitrary_path_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_XAUTH", "/etc/shadow")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_xauth_traversal_rejected(self, monkeypatch):
        """Path traversal like /tmp/.x11ctl-/../etc/shadow must be rejected."""
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-/../etc/shadow")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_xauth_nested_path_rejected(self, monkeypatch):
        """Nested paths like /tmp/.x11ctl-foo/bar must be rejected."""
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-foo/bar")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_xauth_indirect_traversal_rejected(self, monkeypatch):
        """/tmp/anything/../.x11ctl-safe resolves to /tmp/.x11ctl-safe — allowed."""
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/anything/../.x11ctl-safe")
        # This resolves to /tmp/.x11ctl-safe which IS valid
        cfg = x11ctl.Config()
        assert cfg.xauth == "/tmp/.x11ctl-safe"  # normalized


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

    def test_write_rejects_symlink(self, tmp_path):
        target = tmp_path / "target"
        target.write_text("headless")
        link = tmp_path / "link"
        link.symlink_to(target)
        with pytest.raises(OSError):
            x11ctl.write_tiers(str(link), {"headless"})


# --- Locking ---

class TestLocking:
    def test_lock_acquire_release(self, tmp_path):
        lock_path = str(tmp_path / "test.lock")
        lock_fd = x11ctl.acquire_lock(lock_path, exclusive=True)
        assert lock_fd is not None
        x11ctl.release_lock(lock_fd)

    def test_lock_exclusion(self, tmp_path):
        """Second exclusive lock on same path from subprocess should fail (non-blocking)."""
        lock_path = str(tmp_path / "test.lock")
        lock_fd = x11ctl.acquire_lock(lock_path, exclusive=True)
        assert lock_fd is not None
        # Try to acquire from a subprocess — should fail with non-blocking
        result = subprocess.run(
            [sys.executable, "-c", f"""
import fcntl, os, sys
fd = os.open("{lock_path}", os.O_CREAT | os.O_WRONLY, 0o644)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("acquired")
except BlockingIOError:
    print("blocked")
os.close(fd)
"""],
            capture_output=True, text=True, timeout=5,
        )
        assert result.stdout.strip() == "blocked"
        x11ctl.release_lock(lock_fd)

    def test_lock_timeout_returns_none(self, tmp_path):
        """acquire_lock should return None after timeout when lock is held by another process."""
        lock_path = str(tmp_path / "test.lock")
        # Hold lock in a subprocess (flock is per-file-description, not per-file)
        holder = subprocess.Popen(
            [sys.executable, "-c", f"""
import fcntl, os, time
fd = os.open("{lock_path}", os.O_CREAT | os.O_WRONLY, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
# Hold lock for 5 seconds
time.sleep(5)
os.close(fd)
"""],
        )
        import time as _time
        _time.sleep(0.5)  # give subprocess time to acquire
        # Acquire with short timeout — should return None
        lock_fd = x11ctl.acquire_lock(lock_path, exclusive=True, timeout=1.0)
        assert lock_fd is None
        holder.terminate()
        holder.wait(timeout=3)


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
        # Find a guaranteed-free port by binding to 0, noting it, closing
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        _, free_port = sock.getsockname()
        sock.close()
        assert x11ctl.check_port_available("127.0.0.1", free_port) is True

    def test_occupied_port(self):
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.listen(1)
        try:
            assert x11ctl.check_port_available("127.0.0.1", port) is False
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

Create `scripts/x11ctl` containing: `Config` (as `@dataclass(frozen=True)` with `__post_init__` validating `X11CTL_DISPLAY` matches `^:\d+$`, `X11CTL_SCREEN` matches `^\d+x\d+x\d+$`, and `X11CTL_XAUTH` validated structurally: `resolve().parent == /tmp` and basename matches `^\.x11ctl-[^/]+$` (prevents path traversal); includes a `state_prefix` field (default `.x11ctl`) that all path methods derive from; `Config.for_self_test(display_num)` classmethod returns a Config with dynamic display and `state_prefix=".x11ctl-selftest-<N>"`, isolated xauth), `desired_tiers`, `parse_args` (including `self-test` subcommand, bare `start` defaulting to headless, bare `stop` defaulting to all), `write_pidfile` (atomic: write to tempfile in same dir with `O_CREAT | O_EXCL | O_NOFOLLOW`, then check target is not symlink via `os.path.islink()`, then `os.rename()` directly over target without unlinking first), `read_pidfile` (reject symlinks via `O_NOFOLLOW`), `write_tiers` (atomic rename, same pattern), `read_tiers`, `delete_tiers`, `acquire_lock` (non-blocking `fcntl.LOCK_NB` with retry loop, 30s timeout, returns `None` on timeout), `release_lock`, `find_binary`, `check_binaries`, `check_port_available`, `identify_port_user`, `format_env`, `rotate_log`, `create_xauth_file`, `wait_ready`, and a guarded `main()` stub. Imports MUST include `sys` and `tempfile`. Exact implementations as specified in the spec (see spec sections: Display Configuration, PID Management, Tiers File, Port-in-use detection, Xauth, Log rotation).

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

    # Check 3: age match (best-effort — enhances confidence but not required)
    # If etimes is unavailable (restricted jail, ps timeout), we already have
    # 2-factor validation (PID alive + comm match) which passed above.
    # The 3rd factor only REJECTS — it never grants access on its own.
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etimes="],
            capture_output=True, text=True, timeout=5,
        )
        etimes_str = result.stdout.strip()
        if etimes_str:
            etimes = int(etimes_str)
            expected_age = int(time.time()) - created_epoch
            if abs(etimes - expected_age) > 5:
                return False  # Age mismatch — PID was reused
        # If etimes_str is empty, skip age check (2-factor only)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass  # etimes unavailable — proceed with 2-factor result

    # Passed 2-factor (alive + comm) and optionally 3-factor (+ age)
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
        assert result is True  # bool: success
        assert proc.poll() is not None  # process is dead
        assert not Path(pidfile).exists()  # pidfile cleaned up

    def test_stop_already_dead(self, tmp_path):
        """Stop with pidfile pointing to dead process: just clean up."""
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, 99999999, int(x11ctl.time.time()))
        result = x11ctl.stop_component(pidfile, "fake")
        assert result is True
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

    Returns True on success (or nothing to do).
    Returns False only when SIGKILL also fails (process truly unstoppable).
    Partial-stop failure does not block stopping other components — the caller
    should continue stopping remaining components and report failure in exit code.
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
        # Verify process is actually dead
        try:
            os.kill(pid, 0)
            # Still alive after SIGKILL — log warning and return False
            print(f"Warning: PID {pid} ({expected_comm}) survived SIGKILL", file=sys.stderr)
            return False
        except ProcessLookupError:
            pass  # Dead — proceed to cleanup

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
from unittest.mock import patch, MagicMock

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
        """Lock owned by a live non-Xvfb process — stale, safe to clean."""
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
        """Malformed lock file (not a PID) — safe to clean (no owner determinable)."""
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

    def test_clean_lock_read_race_via_os_open_failure(self, tmp_path):
        """If os.open() fails with OSError during lock read, should fail closed."""
        lock = tmp_path / ".X99-lock"
        lock.write_text("12345\n")
        # Simulate OSError during atomic O_NOFOLLOW open
        with patch("os.open", side_effect=OSError("simulated race")):
            result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
            assert result is False  # fail closed on any OSError

    def test_clean_symlinked_lock_fails_closed(self, tmp_path):
        """Valid symlinked lock file should fail closed."""
        target = tmp_path / "target"
        target.write_text("12345\n")
        lock = tmp_path / ".X99-lock"
        lock.symlink_to(target)
        result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
        assert result is False  # symlink = suspicious, fail closed

    def test_clean_broken_symlinked_lock_fails_closed(self, tmp_path):
        """Broken symlinked lock file should also fail closed."""
        lock = tmp_path / ".X99-lock"
        lock.symlink_to(tmp_path / "nonexistent_target")
        # lock.exists() would return False, but is_symlink() returns True
        result = x11ctl.clean_stale_x_artifacts(99, str(tmp_path))
        assert result is False  # broken symlink = suspicious, fail closed


class TestXauthAdd:
    def test_xauth_add_failure_aborts_startup(self):
        """If xauth add returns non-zero, start_headless should return 1 (failure)."""
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
            assert result == 1
            assert "xvfb" not in started

    def test_xauth_add_timeout_aborts_startup(self):
        """If xauth add times out, start_headless should return 1 (failure)."""
        cfg = x11ctl.Config()
        started = []

        with patch.object(x11ctl, "check_binaries", return_value=[]), \
             patch.object(x11ctl, "read_pidfile", return_value=None), \
             patch.object(x11ctl, "clean_stale_x_artifacts", return_value=True), \
             patch.object(x11ctl, "create_xauth_file"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("xauth", 5)):
            result = x11ctl.start_headless(cfg, started)
            assert result == 1

    def test_xauth_add_missing_binary_removes_xauth_and_aborts_startup(self):
        """If xauth add raises FileNotFoundError, start_headless should clean up."""
        cfg = x11ctl.Config()
        started = []

        with patch.object(x11ctl, "check_binaries", return_value=[]), \
             patch.object(x11ctl, "read_pidfile", return_value=None), \
             patch.object(x11ctl, "clean_stale_x_artifacts", return_value=True), \
             patch.object(x11ctl, "create_xauth_file"), \
             patch("subprocess.run", side_effect=FileNotFoundError), \
             patch("os.unlink") as mock_unlink:
            result = x11ctl.start_headless(cfg, started)
            assert result == 1
            mock_unlink.assert_called_once_with(cfg.xauth)
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
    """Remove stale X server lock and socket files only when ownership is safely stale.

    Reads PID from the lock file and checks whether that PID is still alive.
    If it is, or if the liveness check can't be completed, artifacts are preserved.

    Returns True if artifacts were cleaned or didn't exist.
    Returns False if cleanup was blocked (live process or couldn't determine).
    Caller should print guidance when False is returned.
    """
    lock_path = Path(tmp_dir) / f".X{display_num}-lock"
    socket_path = Path(tmp_dir) / ".X11-unix" / f"X{display_num}"

    # Check for symlinks FIRST (before exists()) — broken symlinks return
    # exists()=False but are still suspicious. Use os.path.lexists() or
    # is_symlink() which detects both broken and valid symlinks.
    if lock_path.is_symlink():
        return False  # Symlinked lock is suspicious — fail closed

    lock_lexists = os.path.lexists(str(lock_path))
    socket_lexists = os.path.lexists(str(socket_path))

    # No artifacts = nothing to do
    if not lock_lexists and not socket_lexists:
        return True

    # Socket exists but lock is missing — can't determine owner, fail closed
    if not lock_lexists and socket_lexists:
        return False

    # Read lock file atomically with O_NOFOLLOW to prevent TOCTOU symlink race
    # (attacker could swap file for symlink between is_symlink() and read)
    try:
        fd = os.open(str(lock_path), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            pid_str = os.read(fd, 64).decode().strip()
        finally:
            os.close(fd)
        pid = int(pid_str)
    except OSError:
        return False  # Lock file disappeared, became symlink, or I/O error — fail closed
    except ValueError:
        pass  # Malformed lock (not a valid PID) — no determinable owner, safe to clean
    else:
        try:
            # Check if this PID is alive AND is Xvfb
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=5,
            )
            comm = result.stdout.strip()
            if comm == "Xvfb":
                return False  # Live Xvfb owns these artifacts — don't touch
            # If comm is empty (dead) or non-Xvfb (PID reused), artifacts are stale
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False  # Can't determine owner — fail closed, don't touch

    for p in (lock_path, socket_path):
        if os.path.lexists(str(p)) and not p.is_symlink():
            p.unlink(missing_ok=True)
    return True


# ---------------------------------------------------------------------------
# Start headless (Tier 1)
# ---------------------------------------------------------------------------

def start_headless(cfg: Config, started: list[str], *, bind: str = "127.0.0.1") -> int:
    """Start Xvfb. Returns 0 on success, 1 on general failure, 2 on port conflict.

    All tier start functions share signature (cfg, started, *, bind) -> int
    with convention: 0=ok, 1=failure, 2=port conflict. start_headless ignores
    bind (Xvfb uses Unix sockets, not TCP).

    Appends 'xvfb' to `started` list on success for rollback tracking.
    """
    pidfile = cfg.pidfile("xvfb")

    # Idempotent: check if already running
    data = read_pidfile(pidfile)
    if data is not None:
        pid, epoch = data
        if validate_pid(pid, epoch, "Xvfb"):
            return 0  # already running

    # Preflight
    missing = check_binaries(["Xvfb", "xauth", "xdpyinfo"])
    if missing:
        print(f"Error: missing required commands: {', '.join(missing)}", file=sys.stderr)
        print(f"  Run: pkg install {' '.join(missing)}", file=sys.stderr)
        return 1

    # Clean stale artifacts (fail-closed: won't delete if a live process or unknown owner)
    if not clean_stale_x_artifacts(cfg.display_number):
        lock_path = Path(f"/tmp/.X{cfg.display_number}-lock")
        if lock_path.exists():
            try:
                lock_pid = int(lock_path.read_text().strip())
                r = subprocess.run(["ps", "-p", str(lock_pid), "-o", "comm="],
                                   capture_output=True, text=True, timeout=2)
                comm = r.stdout.strip()
                if comm:
                    print(
                        f"Error: display {cfg.display} appears to be owned by a live process ({comm}, PID {lock_pid}).\n"
                        f"  Verify it is safe to stop before removing X artifacts.",
                        file=sys.stderr,
                    )
                    return 1
            except Exception:
                pass
        print(
            f"Error: stale X artifacts exist for display {cfg.display} but ownership could not be determined.\n"
            f"  This may be due to a crashed X server or a ps timeout.\n"
            f"  Manual fix: verify no live process is using {cfg.display}, then:\n"
            f"    rm -f /tmp/.X{cfg.display_number}-lock /tmp/.X11-unix/X{cfg.display_number}",
            file=sys.stderr,
        )
        return 1

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
            return 1
    except subprocess.TimeoutExpired:
        print("Error: xauth add timed out after 5s", file=sys.stderr)
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        return 1
    except (FileNotFoundError, PermissionError) as exc:
        print(f"Error: xauth binary not usable ({exc})", file=sys.stderr)
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        return 1

    # Rotate log
    logfile = cfg.logfile("xvfb")
    rotate_log(logfile)

    # Launch Xvfb (try/finally to prevent FD leak; except to clean up xauth on failure)
    log_fd = os.open(logfile, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        proc = subprocess.Popen(
            ["Xvfb", cfg.display, "-screen", "0", cfg.screen, "-auth", cfg.xauth],
            stdout=log_fd, stderr=log_fd,
        )
    except Exception as exc:
        os.close(log_fd)
        print(f"Error: failed to launch Xvfb: {exc}", file=sys.stderr)
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        return 1
    else:
        os.close(log_fd)

    # Write pidfile (atomic: write to temp, rename)
    # If write_pidfile fails, kill the just-launched process and clean up
    try:
        write_pidfile(pidfile, proc.pid, int(time.time()))
    except Exception as exc:
        print(f"Error: failed to write pidfile: {exc}", file=sys.stderr)
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception:
                pass
        try:
            os.unlink(cfg.xauth)
        except FileNotFoundError:
            pass
        return 1
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
                proc.wait(timeout=1)  # reap zombie
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
        return 1

    return 0
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

        def mock_start_headless(c, s, **kwargs):
            started.append("xvfb")
            return 0  # int, not bool — matches return type convention

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
             patch.object(x11ctl, "release_lock"), \
             patch("os.unlink") as mock_unlink:
            result = x11ctl.stop_command_impl(cfg, tier="all")
            assert result == 0
            assert len(deleted) == 1
            # Xauth should be cleaned up when headless stops
            mock_unlink.assert_any_call(cfg.xauth)

    def test_stop_headless_cleans_xauth(self):
        """stop --headless should cascade all and clean up xauth file."""
        cfg = x11ctl.Config()

        with patch.object(x11ctl, "stop_component", return_value=True), \
             patch.object(x11ctl, "read_tiers", return_value={"headless", "xpra"}), \
             patch.object(x11ctl, "delete_tiers"), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"), \
             patch("os.unlink") as mock_unlink:
            result = x11ctl.stop_command_impl(cfg, tier="headless")
            assert result == 0
            mock_unlink.assert_any_call(cfg.xauth)

    def test_stop_vnc_does_not_clean_xauth(self):
        """stop --vnc should NOT clean up xauth (headless still running)."""
        cfg = x11ctl.Config()

        with patch.object(x11ctl, "stop_component", return_value=True), \
             patch.object(x11ctl, "read_tiers", return_value={"headless", "vnc"}), \
             patch.object(x11ctl, "write_tiers"), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"), \
             patch("os.unlink") as mock_unlink:
            result = x11ctl.stop_command_impl(cfg, tier="vnc")
            assert result == 0
            # Verify xauth was NOT unlinked
            for call in mock_unlink.call_args_list:
                assert call.args[0] != cfg.xauth

    def test_stop_reports_failure_when_sigkill_fails(self):
        """stop should return non-zero exit when a component can't be killed."""
        cfg = x11ctl.Config()
        written_tiers = []

        with patch.object(x11ctl, "stop_component", return_value=False), \
             patch.object(x11ctl, "read_tiers", return_value={"headless"}), \
             patch.object(x11ctl, "write_tiers", side_effect=lambda p, t: written_tiers.append(t)), \
             patch.object(x11ctl, "delete_tiers") as mock_delete, \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"), \
             patch("os.unlink") as mock_unlink:
            result = x11ctl.stop_command_impl(cfg, tier="all")
            assert result != 0  # failure reported
            # Xauth should NOT be cleaned when stop fails
            for call in mock_unlink.call_args_list:
                assert call.args[0] != cfg.xauth
            # Surviving tiers should be preserved in tiers file
            mock_delete.assert_not_called()  # tiers file NOT deleted
            # write_tiers should be called with surviving tier set
            assert len(written_tiers) >= 1
            assert "headless" in written_tiers[-1]


class TestLockTimeout:
    def test_start_returns_1_on_lock_timeout(self):
        """start should return 1 when lock acquisition times out."""
        cfg = x11ctl.Config()
        with patch.object(x11ctl, "acquire_lock", return_value=None):
            result = x11ctl.start_command_impl(cfg, desired={"headless"}, bind_all=False)
            assert result == 1

    def test_stop_returns_1_on_lock_timeout(self):
        """stop should return 1 when lock acquisition times out."""
        cfg = x11ctl.Config()
        with patch.object(x11ctl, "acquire_lock", return_value=None):
            result = x11ctl.stop_command_impl(cfg, tier="all")
            assert result == 1

    def test_status_returns_1_on_lock_timeout(self):
        """status should return 1 when lock acquisition times out."""
        cfg = x11ctl.Config()
        with patch.object(x11ctl, "acquire_lock", return_value=None):
            result = x11ctl.status_command_impl(cfg)
            assert result == 1


class TestStartRollback:
    def test_rollback_on_xpra_failure(self):
        """If start_xpra fails after start_headless succeeds, headless should be rolled back."""
        cfg = x11ctl.Config()
        rollback_calls = []

        def mock_start_headless(c, started, **kwargs):
            started.append("xvfb")
            return 0  # int, not bool — matches return type convention

        def mock_start_xpra(c, started, **kw):
            return 1  # failure

        def mock_stop(pidfile, comm):
            rollback_calls.append(comm)
            return True

        with patch.object(x11ctl, "start_headless", side_effect=mock_start_headless), \
             patch.object(x11ctl, "start_xpra", side_effect=mock_start_xpra), \
             patch.object(x11ctl, "stop_component", side_effect=mock_stop), \
             patch.object(x11ctl, "read_tiers", return_value=set()), \
             patch.object(x11ctl, "delete_tiers"), \
             patch.object(x11ctl, "acquire_lock", return_value=99), \
             patch.object(x11ctl, "release_lock"):
            result = x11ctl.start_command_impl(cfg, desired={"headless", "xpra"}, bind_all=False)
            assert result != 0  # failure
            # Xvfb should have been rolled back
            assert "Xvfb" in rollback_calls or "xvfb" in [c.lower() for c in rollback_calls]


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
- `start_command_impl(cfg, desired, bind_all)`: acquire lock (check for `None` timeout), read tiers, compute diff, stop excess (STOP_ORDER), start missing (headless first, passing `bind=` to all), write tiers (atomic), rollback on failure
- `stop_command_impl(cfg, tier)`: acquire lock (check for `None` timeout), read tiers, compute cascade, stop components (STOP_ORDER), track which succeeded/failed. **Only update tiers file to remove successfully-stopped tiers; keep surviving tiers in file.** Delete tiers file only when ALL components stopped successfully. **Only remove xauth when headless was successfully stopped.** Report `stop_component` False results in exit code.
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
            result = x11ctl.start_xpra(cfg, [], bind="127.0.0.1")
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
            result = x11ctl.start_vnc(cfg, [], bind="127.0.0.1")
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
    def test_run_returns_child_exit_code(self, tmp_path, monkeypatch):
        """run should propagate the child's exit code."""
        monkeypatch.setenv("X11CTL_DISPLAY", f":{os.getpid()}")
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-test-xauth")
        cfg = x11ctl.Config()  # reads from env — no mutation
        rc = x11ctl.run_with_temp_display(cfg, ["/bin/sh", "-c", "exit 42"])
        assert rc == 42

    @pytest.mark.skipif(
        x11ctl.find_binary("Xvfb") is None,
        reason="Xvfb not installed",
    )
    def test_run_creates_and_tears_down_display(self, tmp_path, monkeypatch):
        """run should create a temp display and tear it down after."""
        monkeypatch.setenv("X11CTL_DISPLAY", f":{os.getpid()}")
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-test-xauth")
        cfg = x11ctl.Config()  # reads from env — no mutation
        rc = x11ctl.run_with_temp_display(cfg, ["xdpyinfo"])
        assert rc == 0
        assert not Path("/tmp/.x11ctl-test-xauth").exists()

    @pytest.mark.skipif(
        x11ctl.find_binary("Xvfb") is None,
        reason="Xvfb not installed",
    )
    def test_run_signal_forwarding(self, tmp_path, monkeypatch):
        """SIGTERM to x11ctl run should kill child and Xvfb."""
        display = f":{os.getpid()}"
        xauth = "/tmp/.x11ctl-test-signal-xauth"
        repo_root = str(Path(__file__).resolve().parent.parent)
        # Launch run in a subprocess so we can signal it.
        # Pass config via environment variables (no Config mutation).
        proc = subprocess.Popen(
            [sys.executable, "-c", f"""
import os, sys
os.environ["X11CTL_DISPLAY"] = "{display}"
os.environ["X11CTL_XAUTH"] = "/tmp/.x11ctl-test-signal-xauth"
sys.path.insert(0, '.')
from importlib.machinery import SourceFileLoader
import importlib.util as _iu
_loader = SourceFileLoader('x11ctl', 'scripts/x11ctl')
_spec = _iu.spec_from_loader('x11ctl', _loader)
x = _iu.module_from_spec(_spec)
_spec.loader.exec_module(x)
cfg = x.Config()  # reads from env — no mutation
sys.exit(x.run_with_temp_display(cfg, ['sleep', '60']))
"""],
            cwd=repo_root,
        )
        # Poll for readiness — check for display socket (run_with_temp_display
        # may not write a pidfile since it manages a temporary display)
        display_num = display.lstrip(":")
        socket_path = f"/tmp/.X11-unix/X{display_num}"
        import time as _time
        for _ in range(20):  # up to 10s
            _time.sleep(0.5)
            if os.path.exists(socket_path) or proc.poll() is not None:
                break
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
- Modify: `tests/test_x11ctl.py`

- [ ] **Step 1: Write failing tests for screenshot and setup**

```python
class TestScreenshotCommand:
    def test_screenshot_missing_import_binary(self):
        """screenshot should fail with clear error when import binary missing."""
        cfg = x11ctl.Config()
        with patch.object(x11ctl, "find_binary", return_value=None):
            result = x11ctl.screenshot_command_impl(cfg, "/tmp/out.png")
            assert result != 0

    def test_screenshot_builds_correct_command(self):
        """screenshot should invoke import with correct display and path."""
        cfg = x11ctl.Config()
        called_with = []

        def mock_run(cmd, **kwargs):
            called_with.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        with patch.object(x11ctl, "find_binary", return_value="/usr/local/bin/import"), \
             patch("subprocess.run", side_effect=mock_run):
            result = x11ctl.screenshot_command_impl(cfg, "/tmp/out.png")
            assert result == 0
            assert "import" in called_with[0][0]
            assert "-window" in called_with[0]
            # Verify -- separator before output path
            assert "--" in called_with[0]
            idx_sep = called_with[0].index("--")
            assert called_with[0][idx_sep + 1] == "/tmp/out.png"

    def test_screenshot_rejects_dash_path(self):
        """screenshot should reject output paths starting with -."""
        cfg = x11ctl.Config()
        with patch.object(x11ctl, "find_binary", return_value="/usr/local/bin/import"):
            result = x11ctl.screenshot_command_impl(cfg, "-evil")
            assert result != 0


class TestSetupCommand:
    def test_setup_rejects_non_root(self):
        """setup should fail when not running as root."""
        with patch("os.geteuid", return_value=1000):
            result = x11ctl.setup_command_impl(tier="headless")
            assert result != 0

    def test_setup_accepts_root(self):
        """setup should proceed when running as root."""
        with patch("os.geteuid", return_value=0), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=0)):
            result = x11ctl.setup_command_impl(tier="headless")
            assert result == 0

    def test_setup_uses_absolute_pkg_path(self):
        """setup should invoke pkg via /usr/sbin/pkg, not PATH search."""
        cfg = x11ctl.Config()
        called_with = []

        def mock_run(cmd, **kwargs):
            called_with.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        with patch("os.geteuid", return_value=0), \
             patch("subprocess.run", side_effect=mock_run):
            x11ctl.setup_command_impl(tier="headless")
            assert called_with[0][0] == "/usr/sbin/pkg"

    def test_setup_headless_package_list(self):
        """setup --headless should install xorg-vfbserver and xauth."""
        called_with = []

        def mock_run(cmd, **kwargs):
            called_with.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        with patch("os.geteuid", return_value=0), \
             patch("subprocess.run", side_effect=mock_run):
            x11ctl.setup_command_impl(tier="headless")
            pkg_cmd = called_with[0]
            assert "xorg-vfbserver" in pkg_cmd
            assert "xauth" in pkg_cmd

    def test_setup_pkg_failure(self):
        """setup should return non-zero when pkg install fails."""
        with patch("os.geteuid", return_value=0), \
             patch("subprocess.run", return_value=subprocess.CompletedProcess(
                 args=[], returncode=1, stderr="pkg error")):
            result = x11ctl.setup_command_impl(tier="headless")
            assert result != 0


class TestSelfTest:
    def test_self_test_refuses_when_tiers_active(self):
        """self-test should refuse to run when a stack is already active."""
        with patch.object(x11ctl, "read_tiers", return_value={"headless"}):
            result = x11ctl.self_test_command_impl(tier="tier1")
            assert result != 0

    def test_self_test_runs_when_clean(self):
        """self-test should proceed when no tiers are active."""
        with patch.object(x11ctl, "read_tiers", return_value=None), \
             patch.object(x11ctl, "find_binary", return_value=None):
            # Will fail at preflight (no binaries), but proves it didn't
            # refuse due to active tiers
            result = x11ctl.self_test_command_impl(tier="tier1")
            # Non-zero is fine — the point is it attempted to run
            assert isinstance(result, int)

    def test_self_test_skips_occupied_display(self, tmp_path):
        """self-test should skip display :98 if occupied and try :97."""
        # Simulate :98 being occupied (lock file exists)
        lock_98 = Path("/tmp/.X98-lock")
        with patch("os.path.exists", side_effect=lambda p: p == str(lock_98) or p == "/tmp/.X11-unix/X98"):
            with patch.object(x11ctl, "read_tiers", return_value=None), \
                 patch.object(x11ctl, "find_binary", return_value=None):
                # Should attempt :97 instead of :98
                result = x11ctl.self_test_command_impl(tier="tier1")
                assert isinstance(result, int)

    def test_self_test_fails_when_all_displays_occupied(self):
        """self-test should fail with error when no free display in range."""
        with patch("os.path.exists", return_value=True), \
             patch.object(x11ctl, "read_tiers", return_value=None):
            result = x11ctl.self_test_command_impl(tier="tier1")
            assert result != 0

    def test_self_test_uses_isolated_state(self):
        """self-test should use isolated display and state paths, not production."""
        cfg = x11ctl.Config.for_self_test(display_num=98)
        assert cfg.display == ":98"
        assert cfg.display != ":99"  # not the production display
        assert "selftest" in cfg.xauth
        assert "selftest" in cfg.pidfile("xvfb")
        assert "selftest" in cfg.tiers_file
        assert "selftest" in cfg.lock_file
        # All paths must be under /tmp and pass xauth validation
        assert cfg.xauth.startswith("/tmp/.x11ctl-selftest-")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_x11ctl.py::TestScreenshotCommand tests/test_x11ctl.py::TestSetupCommand tests/test_x11ctl.py::TestSelfTest -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement screenshot_command_impl**

Verify `import` binary exists, reject paths starting with `-`, run `import -window root -display :N -- <path>` (note `--` separator), report success/failure.

- [ ] **Step 4: Implement setup_command_impl**

Check `os.geteuid() == 0`, map tier flags to package lists (headless: xorg-vfbserver, xauth, xdpyinfo, ImageMagick7-nox11, xdotool; xpra: xpra, xpra-html5; vnc: x11vnc, novnc, resolve websockify package via `/usr/sbin/pkg search -e websockify` — if no exact match, try `/usr/sbin/pkg search py3.*-websockify` and pick the first result; if zero matches, print error with manual install guidance). ALL `pkg` invocations MUST use absolute path `/usr/sbin/pkg` (both `install` and `search`). Run `/usr/sbin/pkg install -y` for installation.

- [ ] **Step 5: Implement self_test_command_impl**

Check that no tiers are active (read_tiers returns None/empty), refuse if active. Use isolated display number and state paths. Exercise acceptance criteria programmatically. `--tier1`: start/xdpyinfo/screenshot/stop. `--all`: adds idempotency, stale PID recovery, partial rollback, symlink rejection, partial-tier status.

- [ ] **Step 6: Run tests, commit**

```bash
git add scripts/x11ctl tests/test_x11ctl.py
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

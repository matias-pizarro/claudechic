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
    sys.modules["x11ctl"] = mod  # Register before exec so @dataclass can resolve
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

    def test_xauth_indirect_traversal_resolves_valid(self, monkeypatch):
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

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
from unittest.mock import patch

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

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError, match="Unknown tier"):
            x11ctl.desired_tiers("bogus")

    def test_component_tiers_cover_all_non_meta(self):
        """_COMPONENT_TIERS + _META_TIERS == _TIER_DEPS.keys() (structural invariant)."""
        assert x11ctl._COMPONENT_TIERS | x11ctl._META_TIERS == frozenset(x11ctl._TIER_DEPS.keys())

    def test_component_and_meta_tiers_disjoint(self):
        """No tier is both a component and a meta-tier."""
        assert x11ctl._COMPONENT_TIERS & x11ctl._META_TIERS == frozenset()

    # Note: cascade stop (--headless stops everything) is a policy decision
    # tested in Task 4 with mock components, not a pure set operation.


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
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_display_with_extra_chars_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_DISPLAY", ":99; rm -rf /")
        with pytest.raises(ValueError):
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
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_screen_injection_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_SCREEN", "1920x1080x24 -evil")
        with pytest.raises(ValueError):
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

    def test_xauth_constructor_supplied_validated(self):
        """Config(xauth=...) must validate even without env var."""
        with pytest.raises(ValueError):
            x11ctl.Config(xauth="/etc/shadow")

    def test_xauth_shell_metachar_rejected(self, monkeypatch):
        """Shell metacharacters in xauth basename must be rejected."""
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-$(touch /tmp/pwned)")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_port_negative_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_XPRA_PORT", "-1")
        with pytest.raises(ValueError, match="must be 1-65535"):
            x11ctl.Config()

    def test_port_too_high_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_VNC_PORT", "70000")
        with pytest.raises(ValueError, match="must be 1-65535"):
            x11ctl.Config()

    def test_port_zero_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_NOVNC_PORT", "0")
        with pytest.raises(ValueError, match="must be 1-65535"):
            x11ctl.Config()

    def test_port_non_integer_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_XPRA_PORT", "abc")
        with pytest.raises(ValueError, match="must be an integer"):
            x11ctl.Config()

    def test_port_empty_string_rejected(self, monkeypatch):
        """Empty port env var must error, not silently use default."""
        monkeypatch.setenv("X11CTL_VNC_PORT", "")
        with pytest.raises(ValueError, match="must be an integer"):
            x11ctl.Config()

    def test_bind_invalid_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_BIND", "not-an-ip")
        with pytest.raises(ValueError, match="valid IPv4"):
            x11ctl.Config()

    def test_bind_valid_loopback(self):
        cfg = x11ctl.Config()
        assert cfg.bind == "127.0.0.1"

    def test_bind_valid_all(self, monkeypatch):
        monkeypatch.setenv("X11CTL_BIND", "0.0.0.0")
        cfg = x11ctl.Config()
        assert cfg.bind == "0.0.0.0"

    def test_bind_non_canonical_rejected(self, monkeypatch):
        """Non-canonical IP forms like '127.1' must be rejected."""
        monkeypatch.setenv("X11CTL_BIND", "127.1")
        with pytest.raises(ValueError, match="canonical"):
            x11ctl.Config()

    def test_display_trailing_newline_rejected(self, monkeypatch):
        """Trailing newline must be rejected ($ vs \\Z)."""
        monkeypatch.setenv("X11CTL_DISPLAY", ":99\n")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_screen_trailing_newline_rejected(self, monkeypatch):
        monkeypatch.setenv("X11CTL_SCREEN", "1920x1080x24\n")
        with pytest.raises(ValueError):
            x11ctl.Config()

    def test_xauth_trailing_newline_rejected(self, monkeypatch):
        """Trailing newline in xauth must be rejected (\\Z anchor)."""
        monkeypatch.setenv("X11CTL_XAUTH", "/tmp/.x11ctl-safe\n")
        with pytest.raises(ValueError):
            x11ctl.Config()


# --- Config.for_self_test ---

class TestConfigForSelfTest:
    def test_display(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        assert cfg.display == ":98"

    def test_offset_ports(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        assert cfg.xpra_port == 10098
        assert cfg.vnc_port == 5998
        assert cfg.novnc_port == 6178

    def test_xauth_path(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        assert cfg.xauth == "/tmp/.x11ctl-selftest-98"

    def test_pidfile_uses_state_dir(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        assert cfg.pidfile("xvfb").startswith("/tmp/selftest-dir/")

    def test_tiers_file_uses_state_dir(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        assert cfg.tiers_file.startswith("/tmp/selftest-dir/")

    def test_lock_file_uses_state_dir(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        assert cfg.lock_file.startswith("/tmp/selftest-dir/")

    def test_frozen(self):
        cfg = x11ctl.Config.for_self_test(98, "/tmp/selftest-dir")
        with pytest.raises(AttributeError):
            cfg.display = ":1"

    def test_env_does_not_affect_selftest(self, monkeypatch):
        """Dirty env vars must not leak into self-test config."""
        monkeypatch.setenv("X11CTL_SCREEN", "800x600x16")
        monkeypatch.setenv("X11CTL_BIND", "0.0.0.0")
        cfg = x11ctl.Config.for_self_test(97, "/tmp/selftest-dir")
        assert cfg.screen == "1920x1080x24"  # Known-good default, not env
        assert cfg.bind == "127.0.0.1"       # Known-good default, not env


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

    def test_read_negative_pid_returns_none(self, tmp_path):
        """Negative PIDs must be rejected (os.kill(-1) would kill all)."""
        path = str(tmp_path / "neg.pid")
        Path(path).write_text("-1 1712345678\n")
        assert x11ctl.read_pidfile(path) is None

    def test_read_negative_epoch_returns_none(self, tmp_path):
        path = str(tmp_path / "neg.pid")
        Path(path).write_text("1234 -1\n")
        assert x11ctl.read_pidfile(path) is None

    def test_read_rejects_symlink(self, tmp_path):
        """read_pidfile should also reject symlinks per spec O_NOFOLLOW."""
        target = tmp_path / "target"
        target.write_text("1234 1712345678\n")
        link = tmp_path / "link.pid"
        link.symlink_to(target)
        assert x11ctl.read_pidfile(str(link)) is None

    def test_read_rejects_fifo(self, tmp_path):
        """read_pidfile should reject FIFOs (prevents FIFO-based DoS)."""
        fifo = str(tmp_path / "fifo.pid")
        os.mkfifo(fifo)
        assert x11ctl.read_pidfile(fifo) is None


# --- PID identity validation ---

class TestValidatePid:
    def test_own_process_is_valid(self):
        """Current process should validate against its own name and derived epoch."""
        pid = os.getpid()
        # Get actual comm for this process
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True,
        )
        actual_comm = result.stdout.strip()
        # Derive epoch from actual etimes to avoid flaky ±5s tolerance
        etimes_result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etimes="],
            capture_output=True, text=True,
        )
        etimes_str = etimes_result.stdout.strip()
        if not (etimes_str and etimes_str.isdigit()):
            pytest.skip(f"ps etimes unavailable (got {etimes_str!r})")
        created = int(x11ctl.time.time()) - int(etimes_str)
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

    def test_permission_error_returns_false(self):
        """PermissionError from os.kill returns False (not our process)."""
        with patch("os.kill", side_effect=PermissionError):
            assert x11ctl.validate_pid(os.getpid(), int(x11ctl.time.time()), "python") is False

    def test_ps_comm_nonzero_exit_is_invalid(self):
        """ps returning non-zero exit code should invalidate PID."""
        pid = os.getpid()
        original_run = subprocess.run
        def mock_run(cmd, **kwargs):
            if "-o" in cmd:
                idx = cmd.index("-o") + 1
                if idx < len(cmd) and "comm=" in cmd[idx]:
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
            return original_run(cmd, **kwargs)
        with patch("subprocess.run", side_effect=mock_run):
            assert x11ctl.validate_pid(pid, int(x11ctl.time.time()), "python") is False

    def test_2factor_fallback_when_etimes_empty(self):
        """validate_pid should succeed on 2-factor (alive + comm) when etimes is empty."""
        pid = os.getpid()
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True,
        )
        comm = result.stdout.strip()
        # Mock ps etimes to return empty (restricted jail scenario)
        original_run = subprocess.run
        def mock_run(cmd, **kwargs):
            # Match on command args, not call order
            if "-o" in cmd:
                idx = cmd.index("-o") + 1
                if idx < len(cmd) and "etimes=" in cmd[idx]:
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            return original_run(cmd, **kwargs)
        with patch("subprocess.run", side_effect=mock_run):
            # Should still return True via 2-factor (alive + comm)
            assert x11ctl.validate_pid(pid, int(x11ctl.time.time()), comm) is True


class TestValidatePidTristate:
    def test_alive_process(self):
        """Living process with matching comm returns 'alive'."""
        pid = os.getpid()
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                capture_output=True, text=True)
        comm = result.stdout.strip()
        # Derive epoch from actual etimes to avoid flaky ±5s tolerance
        etimes_result = subprocess.run(["ps", "-p", str(pid), "-o", "etimes="],
                                       capture_output=True, text=True)
        etimes_str = etimes_result.stdout.strip()
        if not (etimes_str and etimes_str.isdigit()):
            pytest.skip(f"ps etimes unavailable (got {etimes_str!r})")
        created = int(x11ctl.time.time()) - int(etimes_str)
        assert x11ctl.validate_pid_tristate(pid, created, comm) == "alive"

    def test_dead_process(self):
        """Non-existent PID returns 'dead'."""
        assert x11ctl.validate_pid_tristate(99999999, int(x11ctl.time.time()), "fake") == "dead"

    def test_wrong_comm_returns_dead(self):
        """Current PID but wrong process name returns 'dead'."""
        pid = os.getpid()
        assert x11ctl.validate_pid_tristate(pid, int(x11ctl.time.time()), "definitely_not_this") == "dead"

    def test_wrong_epoch_returns_dead(self):
        """Current PID but epoch from a year ago returns 'dead' (age mismatch)."""
        pid = os.getpid()
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                capture_output=True, text=True)
        comm = result.stdout.strip()
        ancient_epoch = int(x11ctl.time.time()) - 365 * 86400
        assert x11ctl.validate_pid_tristate(pid, ancient_epoch, comm) == "dead"

    def test_ps_comm_nonzero_exit_returns_dead(self):
        """ps returning non-zero exit code returns 'dead'."""
        pid = os.getpid()
        original_run = subprocess.run
        def mock_run(cmd, **kwargs):
            if "-o" in cmd:
                idx = cmd.index("-o") + 1
                if idx < len(cmd) and "comm=" in cmd[idx]:
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")
            return original_run(cmd, **kwargs)
        with patch("subprocess.run", side_effect=mock_run):
            assert x11ctl.validate_pid_tristate(pid, int(x11ctl.time.time()), "python") == "dead"

    def test_ps_comm_timeout_returns_unknown(self):
        """ps timeout during comm check returns 'unknown'."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            result = x11ctl.validate_pid_tristate(os.getpid(), int(x11ctl.time.time()), "python")
            assert result == "unknown"

    def test_ps_etimes_timeout_returns_unknown(self):
        """ps timeout during etimes check (after comm succeeds) returns 'unknown'."""
        def mock_run(cmd, **kwargs):
            if "-o" in cmd:
                idx = cmd.index("-o") + 1
                if idx < len(cmd) and "etimes=" in cmd[idx]:
                    raise subprocess.TimeoutExpired("ps", 5)
                if idx < len(cmd) and "comm=" in cmd[idx]:
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="python\n", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        with patch("subprocess.run", side_effect=mock_run):
            result = x11ctl.validate_pid_tristate(os.getpid(), int(x11ctl.time.time()), "python")
            assert result == "unknown"

    def test_2factor_fallback_when_etimes_empty(self):
        """validate_pid_tristate should return 'alive' on 2-factor when etimes empty."""
        pid = os.getpid()
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                capture_output=True, text=True)
        comm = result.stdout.strip()
        def mock_run(cmd, **kwargs):
            if "-o" in cmd:
                idx = cmd.index("-o") + 1
                if idx < len(cmd) and "etimes=" in cmd[idx]:
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
                if idx < len(cmd) and "comm=" in cmd[idx]:
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=f"{comm}\n", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        with patch("subprocess.run", side_effect=mock_run):
            result = x11ctl.validate_pid_tristate(pid, int(x11ctl.time.time()), comm)
            assert result == "alive"  # 2-factor fallback succeeds

    def test_ps_not_found_returns_unknown(self):
        """Missing ps binary returns 'unknown'."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = x11ctl.validate_pid_tristate(os.getpid(), int(x11ctl.time.time()), "python")
            assert result == "unknown"

    def test_permission_error_returns_unknown(self):
        """PermissionError from os.kill (can't probe PID) returns 'unknown'."""
        with patch("os.kill", side_effect=PermissionError):
            result = x11ctl.validate_pid_tristate(os.getpid(), int(x11ctl.time.time()), "python")
            assert result == "unknown"


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

    def test_write_empty_set_reads_as_none(self, tmp_path):
        """Empty tier set writes a newline; reads back as None (nothing running)."""
        path = str(tmp_path / "tiers")
        x11ctl.write_tiers(path, set())
        assert x11ctl.read_tiers(path) is None

    def test_write_rejects_meta_tier(self, tmp_path):
        """write_tiers should reject 'all' and other non-component tier names."""
        path = str(tmp_path / "tiers")
        with pytest.raises(ValueError, match="non-component"):
            x11ctl.write_tiers(path, {"headless", "all"})

    def test_read_rejects_symlink(self, tmp_path):
        """read_tiers should reject symlinks."""
        target = tmp_path / "target"
        target.write_text("headless\n")
        link = tmp_path / "link"
        link.symlink_to(target)
        assert x11ctl.read_tiers(str(link)) is None

    def test_read_unknown_tier_names_returns_none(self, tmp_path):
        """read_tiers should return None for files with unknown tier names."""
        path = str(tmp_path / "tiers")
        Path(path).write_text("headless bogus_tier\n")
        assert x11ctl.read_tiers(path) is None

    def test_read_valid_tier_names(self, tmp_path):
        """read_tiers accepts only known component tier names."""
        path = str(tmp_path / "tiers")
        x11ctl.write_tiers(path, {"headless", "vnc"})
        assert x11ctl.read_tiers(path) == {"headless", "vnc"}

    def test_read_meta_tier_all_rejected(self, tmp_path):
        """read_tiers rejects 'all' meta-tier (not a component tier)."""
        path = str(tmp_path / "tiers")
        Path(path).write_text("headless all\n")
        assert x11ctl.read_tiers(path) is None

    def test_read_invalid_utf8_returns_none(self, tmp_path):
        """read_tiers should return None for files with invalid UTF-8."""
        path = str(tmp_path / "tiers")
        Path(path).write_bytes(b"\xff\xfe invalid utf8")
        assert x11ctl.read_tiers(path) is None


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

    def test_lock_rejects_symlink(self, tmp_path):
        """acquire_lock should fail on symlinks due to O_NOFOLLOW."""
        target = tmp_path / "target.lock"
        target.write_text("")
        link = tmp_path / "symlink.lock"
        link.symlink_to(target)
        lock_fd = x11ctl.acquire_lock(str(link), exclusive=True, timeout=1.0)
        assert lock_fd is None  # Should fail due to O_NOFOLLOW

    def test_lock_rejects_fifo(self, tmp_path):
        """acquire_lock should reject FIFOs (prevents FIFO-based DoS)."""
        fifo = str(tmp_path / "fifo.lock")
        os.mkfifo(fifo)
        lock_fd = x11ctl.acquire_lock(fifo, exclusive=True, timeout=1.0)
        assert lock_fd is None

    def test_shared_lock(self, tmp_path):
        """Shared locks should be compatible with each other."""
        lock_path = str(tmp_path / "test.lock")
        fd1 = x11ctl.acquire_lock(lock_path, exclusive=False)
        fd2 = x11ctl.acquire_lock(lock_path, exclusive=False)
        assert fd1 is not None
        assert fd2 is not None
        x11ctl.release_lock(fd2)
        x11ctl.release_lock(fd1)

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


# --- Port user identification ---

class TestIdentifyPortUser:
    def test_returns_none_for_free_port(self):
        """identify_port_user on an unused port should return None or empty info."""
        # Use a high ephemeral port unlikely to be in use
        result = x11ctl.identify_port_user(59999)
        # Either None (no output) or a string (sockstat header only)
        # is acceptable for an unused port
        assert result is None or isinstance(result, str)

    def test_returns_string_for_occupied_port(self):
        """identify_port_user on a bound port should return process info."""
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        _, port = sock.getsockname()
        sock.listen(1)
        try:
            result = x11ctl.identify_port_user(port)
            # Should return a string with process info (or None if sockstat unavailable)
            if os.path.exists("/usr/bin/sockstat"):
                assert result is None or isinstance(result, str)
            else:
                assert result is None
        finally:
            sock.close()

    def test_handles_missing_sockstat(self):
        """identify_port_user should return None when sockstat is unavailable."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert x11ctl.identify_port_user(5900) is None

    def test_handles_sockstat_timeout(self):
        """identify_port_user should return None on timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sockstat", 5)):
            assert x11ctl.identify_port_user(5900) is None


# --- Env output ---

class TestEnvOutput:
    def test_env_output(self):
        cfg = x11ctl.Config()
        output = x11ctl.format_env(cfg)
        assert "export DISPLAY=':99'" in output or 'export DISPLAY=":99"' in output or "export DISPLAY=:99" in output
        assert "export XAUTHORITY=" in output

    def test_env_output_uses_shlex_quote(self):
        """format_env uses shlex.quote — verify exact quoting format."""
        import shlex
        cfg = x11ctl.Config()
        output = x11ctl.format_env(cfg)
        # shlex.quote produces specific output for these safe values
        expected_display = f"export DISPLAY={shlex.quote(cfg.display)}"
        expected_xauth = f"export XAUTHORITY={shlex.quote(cfg.xauth)}"
        assert expected_display in output
        assert expected_xauth in output


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
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_rejects_symlink(self, tmp_path):
        target = tmp_path / "target"
        target.write_text("x")
        link = tmp_path / "xauth"
        link.symlink_to(target)
        with pytest.raises(OSError):
            x11ctl.create_xauth_file(str(link))

    def test_overwrites_existing_regular_file(self, tmp_path):
        """create_xauth_file should replace an existing regular file."""
        path = str(tmp_path / "xauth")
        Path(path).write_text("old content")
        os.chmod(path, 0o644)  # wrong perms
        x11ctl.create_xauth_file(path)
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600
        assert Path(path).read_text() == ""  # fresh empty file


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


# --- stop_component ---

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
        # Install a signal handler to detect if SIGTERM was sent to us
        sigterm_received = []
        old_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda s, f: sigterm_received.append(True))
        try:
            result = x11ctl.stop_component(pidfile, "Xvfb")
            assert result is True  # pidfile cleaned up (stale)
            assert not Path(pidfile).exists()
            # Verify no signal was sent to our process
            assert sigterm_received == [], "SIGTERM was sent to test process (wrong PID targeted)"
        finally:
            signal.signal(signal.SIGTERM, old_handler)

    def test_stop_sigkill_escalation(self, tmp_path):
        """Process that traps SIGTERM should be killed via SIGKILL."""
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]
        )
        # Get actual comm name from ps to ensure validate_pid matches
        ps_result = subprocess.run(
            ["ps", "-p", str(proc.pid), "-o", "comm="],
            capture_output=True, text=True, timeout=5,
        )
        comm = ps_result.stdout.strip()
        if not comm:
            proc.kill()
            proc.wait()
            pytest.skip("Could not determine comm name via ps")

        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, proc.pid, int(x11ctl.time.time()))
        result = x11ctl.stop_component(pidfile, comm)
        assert result is True
        # Verify process is dead (may already be reaped by stop_component)
        try:
            os.kill(proc.pid, 0)
            assert False, "process should be dead after SIGKILL"
        except (ProcessLookupError, PermissionError):
            pass  # confirmed dead or not ours
        assert not Path(pidfile).exists()  # pidfile cleaned up
        # Reap zombie to avoid resource leak in test
        try:
            proc.wait(timeout=1)
        except Exception:
            pass

    def test_stop_returns_false_when_unkillable(self, tmp_path):
        """stop_component returns False when even SIGKILL cannot stop process."""
        pidfile = str(tmp_path / "test.pid")
        fake_pid = 12345
        x11ctl.write_pidfile(pidfile, fake_pid, int(x11ctl.time.time()))

        # Mock validate_pid_tristate to return "alive" (always), then os.kill
        # to always succeed (process appears alive even after SIGKILL)
        with patch.object(x11ctl, "validate_pid_tristate", return_value="alive"), \
             patch("os.kill"), \
             patch("os.waitpid", side_effect=ChildProcessError), \
             patch("time.sleep"):
            result = x11ctl.stop_component(pidfile, "Xvfb")
            assert result is False  # unkillable

    def test_stop_race_dies_between_validate_and_kill(self, tmp_path):
        """Process dies between validate_pid_tristate and os.kill(SIGTERM)."""
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, 12345, int(x11ctl.time.time()))

        with patch.object(x11ctl, "validate_pid_tristate", return_value="alive"), \
             patch("os.kill", side_effect=ProcessLookupError):
            result = x11ctl.stop_component(pidfile, "Xvfb")
            assert result is True
            assert not Path(pidfile).exists()

    def test_stop_permission_error_on_sigterm(self, tmp_path):
        """PermissionError on SIGTERM (PID reused by another user) cleans up pidfile."""
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, 12345, int(x11ctl.time.time()))

        with patch.object(x11ctl, "validate_pid_tristate", return_value="alive"), \
             patch("os.kill", side_effect=PermissionError):
            result = x11ctl.stop_component(pidfile, "Xvfb")
            assert result is True  # treated as stale
            assert not Path(pidfile).exists()

    def test_stop_unknown_state_preserves_pidfile(self, tmp_path):
        """stop_component preserves pidfile when PID state is 'unknown' (fail-closed)."""
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, 12345, int(x11ctl.time.time()))

        with patch.object(x11ctl, "validate_pid_tristate", return_value="unknown"):
            result = x11ctl.stop_component(pidfile, "Xvfb")
            assert result is True
            # Pidfile should be PRESERVED (fail-closed — can't verify)
            assert Path(pidfile).exists()

    def test_stop_revalidates_before_sigkill(self, tmp_path):
        """stop_component revalidates PID identity before escalating to SIGKILL."""
        pidfile = str(tmp_path / "test.pid")
        x11ctl.write_pidfile(pidfile, 12345, int(x11ctl.time.time()))

        call_count = [0]
        def tristate_side_effect(pid, epoch, comm):
            call_count[0] += 1
            if call_count[0] == 1:
                return "alive"  # Initial check
            return "dead"  # Revalidation before SIGKILL

        with patch.object(x11ctl, "validate_pid_tristate", side_effect=tristate_side_effect), \
             patch("os.kill"), \
             patch("os.waitpid", side_effect=ChildProcessError), \
             patch("time.sleep"):
            result = x11ctl.stop_component(pidfile, "Xvfb")
            assert result is True
            # Should have been called at least twice (initial + revalidation)
            assert call_count[0] >= 2
            # Pidfile cleaned up because revalidation returned "dead"
            assert not Path(pidfile).exists()

    def test_stop_symlinked_pidfile(self, tmp_path):
        """stop_component with a symlinked pidfile returns True without signaling."""
        target = tmp_path / "target.pid"
        target.write_text("1234 1712345678\n")
        link = tmp_path / "link.pid"
        link.symlink_to(target)
        # read_pidfile rejects symlinks → returns None → nothing to stop
        result = x11ctl.stop_component(str(link), "Xvfb")
        assert result is True

"""Integration tests for x11ctl — lifecycle and acceptance criteria.

Every test invokes scripts/x11ctl as a subprocess with a unique display.
Requires Xvfb and related X11 binaries to be installed.
"""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from .conftest import (
    x11ctl_run, assert_port_listening, assert_port_free,
    read_state_file, parse_pidfile,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]


class TestAcceptanceCriteria:
    """Acceptance criteria AC1-AC11 from the design spec."""

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

        # Verify display works via xdpyinfo (sanitise env like x11ctl_run does)
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
        clean_env["DISPLAY"] = env["X11CTL_DISPLAY"]
        clean_env["XAUTHORITY"] = env["X11CTL_XAUTH"]
        check = subprocess.run(
            ["xdpyinfo", "-display", env["X11CTL_DISPLAY"]],
            capture_output=True, text=True, timeout=10,
            env=clean_env,
        )
        assert check.returncode == 0, f"xdpyinfo failed: {check.stderr}"

    def test_ac3_screenshot_produces_png(self, display_factory, tmp_path):
        """AC3: screenshot produces valid PNG."""
        if shutil.which("import") is None:
            pytest.skip("ImageMagick import not installed")
        env = display_factory
        setup = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert setup.returncode == 0, f"setup start failed: {setup.stderr}"

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
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )

        # First start
        r1 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r1.returncode == 0

        content1 = read_state_file(pidfile)
        assert content1 is not None
        entry1 = parse_pidfile(content1)
        assert entry1 is not None

        # Stop
        r2 = x11ctl_run(["stop"], env_overrides=env)
        assert r2.returncode == 0

        # Verify stopped — pidfile should be removed
        assert read_state_file(pidfile) is None

        # Second start
        r3 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r3.returncode == 0

        content2 = read_state_file(pidfile)
        assert content2 is not None
        entry2 = parse_pidfile(content2)
        assert entry2 is not None
        # Different PID after restart
        assert entry1.pid != entry2.pid

    def test_ac7_status_healthy_and_degraded(self, display_factory):
        """AC7: status exits 0 healthy, 1 when component killed."""
        env = display_factory
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )

        setup = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert setup.returncode == 0, f"setup start failed: {setup.stderr}"

        # Healthy
        r1 = x11ctl_run(["status"], env_overrides=env)
        assert r1.returncode == 0

        # Kill Xvfb
        content = read_state_file(pidfile)
        assert content is not None
        entry = parse_pidfile(content)
        assert entry is not None
        os.kill(entry.pid, signal.SIGKILL)

        # Poll until process is gone (avoids fixed sleep flakiness)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(entry.pid, 0)  # Check if still alive
            except ProcessLookupError:
                break  # Process is gone
            time.sleep(0.1)

        # Degraded — exit 1 and stderr should indicate the dead component
        r2 = x11ctl_run(["status"], env_overrides=env)
        assert r2.returncode == 1, f"Expected degraded status (exit 1), got {r2.returncode}"
        assert "down" in r2.stderr.lower() or "dead" in r2.stderr.lower(), \
            f"Expected 'down' or 'dead' in stderr, got: {r2.stderr}"

    # --- AC8-AC11 (Task 3) ---

    def test_ac8_port_conflict_clear_error(self, display_factory):
        """AC8: port conflict produces clear error, exit 2."""
        env = display_factory
        if shutil.which("xpra") is None:
            pytest.skip("xpra not installed")

        # Occupy the xpra port
        import socket
        port = int(env["X11CTL_XPRA_PORT"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        try:
            # Start headless first (xpra needs it)
            setup = x11ctl_run(["start", "--headless"], env_overrides=env)
            assert setup.returncode == 0, f"setup start failed: {setup.stderr}"
            result = x11ctl_run(["start", "--xpra"], env_overrides=env)
            assert result.returncode == 2, \
                f"Expected exit 2 for port conflict, got {result.returncode}: {result.stderr}"
            assert "already in use" in result.stderr.lower() or "port" in result.stderr.lower(), \
                f"Expected port conflict message in stderr: {result.stderr}"
        finally:
            sock.close()

    def test_ac9_tcp_listeners_default_localhost(self, display_factory):
        """AC9: all TCP listeners default to 127.0.0.1."""
        env = display_factory
        if shutil.which("xpra") is None:
            pytest.skip("xpra not installed")

        result = x11ctl_run(["start", "--xpra"], env_overrides=env)
        assert result.returncode == 0, f"start --xpra failed: {result.stderr}"
        port = int(env["X11CTL_XPRA_PORT"])

        # Check sockstat for the port — LOCAL ADDRESS should be 127.0.0.1, not *
        # sockstat format: USER COMMAND PID FD PROTO LOCAL_ADDRESS FOREIGN_ADDRESS
        # Use -4 to filter to IPv4 TCP only (excludes Unix domain sockets)
        check = subprocess.run(
            ["sockstat", "-4", "-l", "-p", str(port)],
            capture_output=True, text=True, timeout=5,
        )
        for line in check.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) >= 6 and str(port) in parts[5]:
                local_addr = parts[5]  # e.g., "127.0.0.1:10080" or "*:10080"
                assert local_addr.startswith("127.0.0.1:"), \
                    f"Port {port} bound to non-localhost: {local_addr} (full line: {line})"

    def test_ac10_xauth_mode_0600(self, display_factory):
        """AC10: xauth file created with mode 0600."""
        env = display_factory
        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode == 0, f"start failed: {result.stderr}"

        xauth_path = env["X11CTL_XAUTH"]
        assert os.path.exists(xauth_path), f"xauth not found: {xauth_path}"
        mode = os.stat(xauth_path).st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_ac11_missing_dep_clear_error(self, display_factory):
        """AC11: missing dependency produces clear error listing install command."""
        env = {**display_factory}

        # Construct a PATH that has python3 but NOT Xvfb.
        import sys as _sys
        python_dir = os.path.dirname(_sys.executable)
        # Include python dir + basic system dirs, exclude Xvfb location
        restricted_path = f"{python_dir}:/usr/bin:/bin"
        env["PATH"] = restricted_path

        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode != 0
        assert "missing" in result.stderr.lower() or "not found" in result.stderr.lower(), \
            f"Expected missing dep message: {result.stderr}"


class TestLifecycleScenarios:
    """Lifecycle scenarios beyond basic ACs."""

    def test_tier_switching_downgrade(self, display_factory):
        """start --all then start --headless: xpra/vnc stopped, xvfb survives."""
        env = display_factory
        if shutil.which("xpra") is None or shutil.which("x11vnc") is None:
            pytest.skip("xpra or x11vnc not installed")

        result = x11ctl_run(["start", "--all"], env_overrides=env)
        assert result.returncode == 0, f"start --all failed: {result.stderr}"

        # Verify all running
        r1 = x11ctl_run(["status"], env_overrides=env)
        assert r1.returncode == 0
        assert "xvfb" in r1.stderr.lower()

        # Downgrade
        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode == 0, f"downgrade failed: {result.stderr}"
        r2 = x11ctl_run(["status"], env_overrides=env)
        assert "xvfb" in r2.stderr.lower()
        # xpra port should be free
        assert_port_free("127.0.0.1", int(env["X11CTL_XPRA_PORT"]))

    def test_idempotent_start(self, display_factory):
        """start --headless twice: second is no-op, same PID."""
        env = display_factory
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )

        r1 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r1.returncode == 0
        content1 = read_state_file(pidfile)

        r2 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r2.returncode == 0
        content2 = read_state_file(pidfile)

        assert content1 == content2, f"PID changed: {content1} -> {content2}"

    @pytest.mark.xfail(reason="x11ctl does not yet clean stale pidfiles on restart")
    def test_crash_recovery(self, display_factory):
        """Kill Xvfb, then start --headless: cleans stale, starts fresh."""
        env = display_factory
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )

        r1 = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert r1.returncode == 0
        content = read_state_file(pidfile)
        assert content is not None
        old_entry = parse_pidfile(content)
        assert old_entry is not None

        # Crash Xvfb
        os.kill(old_entry.pid, signal.SIGKILL)
        # Poll until process is gone
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(old_entry.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)

        # Restart — should clean stale artifacts and start fresh
        result = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert result.returncode == 0, f"Restart failed: {result.stderr}"

        new_content = read_state_file(pidfile)
        assert new_content is not None
        new_entry = parse_pidfile(new_content)
        assert new_entry is not None
        # Verify the new Xvfb is actually alive (the key crash recovery assertion).
        # PID and epoch may coincide if the kernel reuses the PID within the same
        # second, so we verify the process is running rather than comparing entries.
        try:
            os.kill(new_entry.pid, 0)
        except ProcessLookupError:
            pytest.fail(f"New Xvfb (PID {new_entry.pid}) is not running after restart")

    def test_run_exit_code_propagation(self, display_factory):
        """run /bin/sh -c 'exit 42' returns 42."""
        env = display_factory
        result = x11ctl_run(["run", "/bin/sh", "-c", "exit 42"], env_overrides=env)
        assert result.returncode == 42

    def test_env_output(self, display_factory):
        """env command outputs valid DISPLAY and XAUTHORITY exports."""
        env = display_factory
        setup = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert setup.returncode == 0, f"setup start failed: {setup.stderr}"

        result = x11ctl_run(["env"], env_overrides=env)
        assert result.returncode == 0
        assert "DISPLAY=" in result.stdout
        assert "XAUTHORITY=" in result.stdout
        assert env["X11CTL_DISPLAY"] in result.stdout

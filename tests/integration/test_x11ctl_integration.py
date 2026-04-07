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

from .conftest import x11ctl_run, assert_port_listening, assert_port_free, read_state_file, parse_pidfile

pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]


class TestAcceptanceCriteria:
    """Acceptance criteria AC1-AC7 from the design spec.

    AC8-AC11 are in Task 3 (same file, added later).
    """

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
        if shutil.which("import") is None:
            pytest.skip("ImageMagick import not installed")
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

        x11ctl_run(["start", "--headless"], env_overrides=env)

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

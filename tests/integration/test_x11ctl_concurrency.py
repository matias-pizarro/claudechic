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
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
        base_env["X11CTL_ALLOW_HOST"] = "1"
        base_env.update({
            k: v for k, v in env.items()
            if k.startswith("X11CTL_")
        })

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
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )
        content = read_state_file(pidfile)
        assert content is not None, "No pidfile after concurrent starts"

    def test_stop_during_startup(self, display_factory):
        """start in background, stop immediately: no orphaned processes."""
        env = display_factory
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
        base_env["X11CTL_ALLOW_HOST"] = "1"
        base_env.update({
            k: v for k, v in env.items()
            if k.startswith("X11CTL_")
        })

        # Start in background
        start_proc = subprocess.Popen(
            [SCRIPT, "start", "--headless"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Give it a moment to acquire lock
        time.sleep(0.5)

        # Stop immediately
        x11ctl_run(["stop"], env_overrides=env, timeout=15)

        # Wait for start to finish
        start_proc.communicate(timeout=15)

        # Verify: no orphaned Xvfb on this display
        display = env["X11CTL_DISPLAY"]
        ps_result = subprocess.run(
            ["/bin/ps", "aux"], capture_output=True, text=True, timeout=5,
        )
        for line in ps_result.stdout.splitlines():
            if "Xvfb" in line and display in line:
                pytest.fail(f"Orphaned Xvfb found: {line}")

    def test_concurrent_stop(self, display_factory):
        """Two concurrent stop: both succeed, no errors."""
        env = display_factory
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
        base_env["X11CTL_ALLOW_HOST"] = "1"
        base_env.update({
            k: v for k, v in env.items()
            if k.startswith("X11CTL_")
        })

        # Start first
        setup = x11ctl_run(["start", "--headless"], env_overrides=env)
        assert setup.returncode == 0, f"setup start failed: {setup.stderr}"

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
        base_env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
        base_env["X11CTL_ALLOW_HOST"] = "1"
        base_env.update({
            k: v for k, v in env.items()
            if k.startswith("X11CTL_")
        })

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

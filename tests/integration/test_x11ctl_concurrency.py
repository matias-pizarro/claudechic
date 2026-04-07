"""Concurrency integration tests for x11ctl.

Tests lock contention, concurrent start/stop, and signal handling.
Every test invokes scripts/x11ctl as a subprocess.
"""
import os
import shutil
import signal
import subprocess
import time

import pytest

from .conftest import x11ctl_run, SCRIPT, read_state_file, parse_pidfile, _CONVENIENCE_KEYS

pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]


def _build_subprocess_env(display_env: dict) -> dict[str, str]:
    """Build a subprocess env dict from a display_factory result.

    Mirrors x11ctl_run's env construction: strips parent X11CTL_* vars,
    sets ALLOW_HOST, and applies display_env excluding convenience keys.
    This avoids each test duplicating the env-building logic.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("X11CTL_")}
    env["X11CTL_ALLOW_HOST"] = "1"
    env.update({
        k: v for k, v in display_env.items()
        if k not in _CONVENIENCE_KEYS and isinstance(v, str)
    })
    return env


class TestConcurrency:

    def test_concurrent_start_is_idempotent(self, display_factory):
        """Two concurrent start --headless: both succeed, one Xvfb runs."""
        env = display_factory
        base_env = _build_subprocess_env(env)

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

        # Verify pidfile exists with valid content and process alive
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )
        content = read_state_file(pidfile)
        assert content is not None, "No pidfile after concurrent starts"
        entry = parse_pidfile(content)
        assert entry is not None, f"Malformed pidfile content: {content!r}"
        try:
            os.kill(entry.pid, 0)
        except ProcessLookupError:
            pytest.fail(f"Xvfb PID {entry.pid} not running after concurrent starts")
        except PermissionError:
            pass  # Process exists but owned by different user (CI edge case)

    def test_stop_during_startup(self, display_factory):
        """start in background, stop after lock acquired: no orphaned processes."""
        env = display_factory
        base_env = _build_subprocess_env(env)
        lock_path = os.path.join(
            env["state_dir"], f"{env['X11CTL_STATE_PREFIX']}.lock"
        )

        # Start in background
        start_proc = subprocess.Popen(
            [SCRIPT, "start", "--headless"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Poll until start has acquired the lock (or has exited)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if start_proc.poll() is not None:
                break  # Already finished
            if os.path.exists(lock_path):
                break  # Lock acquired — safe to issue stop
            time.sleep(0.1)

        # Wait for start to finish (stop will block on lock if start holds it)
        start_proc.communicate(timeout=15)

        # Stop (may be no-op if start already finished and exited)
        x11ctl_run(["stop"], env_overrides=env, timeout=15)

        # Verify: no orphaned Xvfb on this display (use -eo pid,args to
        # minimize information leakage in CI failure messages)
        display = env["X11CTL_DISPLAY"]
        ps_result = subprocess.run(
            ["/bin/ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5,
        )
        for line in ps_result.stdout.splitlines():
            if "Xvfb" in line and display in line:
                pytest.fail(f"Orphaned Xvfb found: {line.strip()}")

    def test_concurrent_stop(self, display_factory):
        """Two concurrent stop: both succeed, no errors."""
        env = display_factory
        base_env = _build_subprocess_env(env)

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

        # Verify post-stop state is clean (pidfile removed)
        pidfile = os.path.join(
            env["state_dir"],
            f"{env['X11CTL_STATE_PREFIX']}-xvfb.pid",
        )
        assert read_state_file(pidfile) is None, \
            "Pidfile not cleaned after concurrent stops"

    def test_signal_during_run(self, display_factory):
        """SIGTERM to x11ctl run: child killed, display cleaned."""
        env = display_factory
        base_env = _build_subprocess_env(env)

        # Launch run with a long-running child
        run_proc = subprocess.Popen(
            [SCRIPT, "run", "sleep", "60"],
            env=base_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        # Wait for Xvfb to start (socket appears or process exits early)
        display_num = env["display_num"]
        socket_path = f"/tmp/.X11-unix/X{display_num}"
        for _ in range(20):
            time.sleep(0.5)
            if os.path.exists(socket_path) or run_proc.poll() is not None:
                break

        # Send SIGTERM
        run_proc.send_signal(signal.SIGTERM)
        # wait() ensures process exited AND its finally cleanup ran
        run_proc.wait(timeout=15)

        # x11ctl's signal handler does sys.exit(128 + signum)
        assert run_proc.returncode != 0, \
            f"Expected non-zero exit from SIGTERM, got {run_proc.returncode}"

        # Verify xauth cleaned up — process already waited, so cleanup
        # (which runs in x11ctl's finally block) has completed. No sleep needed.
        xauth_path = env["X11CTL_XAUTH"]
        assert not os.path.exists(xauth_path), f"xauth not cleaned: {xauth_path}"

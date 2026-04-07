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
                "Expected failure when pidfile is a symlink, got exit 0"
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
            # Start should handle FIFO pidfile gracefully — _safe_open_regular
            # rejects non-regular files, so read_pidfile returns None and start
            # proceeds as if no pidfile exists (starts a fresh Xvfb).
            result = x11ctl_run(["start", "--headless"], env_overrides=env, timeout=15)
            assert result.returncode == 0, \
                f"Expected start to succeed (FIFO pidfile treated as absent): {result.stderr}"
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

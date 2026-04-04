"""Integration tests for claudechic.shell_runner PTY execution.

These tests verify that real PTY execution works on the current platform,
complementing the UI tests in test_app_ui.py which mock the PTY layer.

Note: Login shell (bash -lc) produces empty PTY output inside Textual's
headless run_test() context on FreeBSD due to terminal initialization
timing. These integration tests run outside Textual to verify the real
PTY path works correctly.
"""

import asyncio
import os
import sys

import pytest

# PTY support is Unix-only
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY not available on Windows")


class TestRunInPty:
    """Tests for the synchronous run_in_pty function."""

    def test_captures_stdout(self):
        """PTY captures stdout from a simple echo command."""
        from claudechic.shell_runner import run_in_pty

        shell = os.environ.get("SHELL", "/bin/sh")
        output, rc = run_in_pty("echo hello", shell, None, dict(os.environ))
        assert "hello" in output
        assert rc == 0

    def test_captures_stderr_via_pty(self):
        """PTY merges stderr into stdout since both use the slave fd."""
        from claudechic.shell_runner import run_in_pty

        shell = os.environ.get("SHELL", "/bin/sh")
        output, rc = run_in_pty("echo error >&2", shell, None, dict(os.environ))
        assert "error" in output
        assert rc == 0

    def test_captures_exit_code(self):
        """PTY captures non-zero exit codes."""
        from claudechic.shell_runner import run_in_pty

        shell = os.environ.get("SHELL", "/bin/sh")
        output, rc = run_in_pty("exit 42", shell, None, dict(os.environ))
        assert rc == 42

    def test_respects_cwd(self):
        """PTY runs command in specified working directory."""
        from claudechic.shell_runner import run_in_pty

        shell = os.environ.get("SHELL", "/bin/sh")
        output, rc = run_in_pty("pwd", shell, "/tmp", dict(os.environ))
        assert "/tmp" in output
        assert rc == 0


class TestRunInPtyCancellable:
    """Tests for the async run_in_pty_cancellable function."""

    @pytest.mark.asyncio
    async def test_captures_output(self):
        """Async PTY wrapper captures output correctly."""
        from claudechic.shell_runner import run_in_pty_cancellable

        shell = os.environ.get("SHELL", "/bin/sh")
        cancel = asyncio.Event()
        output, rc, cancelled = await run_in_pty_cancellable(
            "echo async_hello", shell, None, dict(os.environ), cancel
        )
        assert "async_hello" in output
        assert rc == 0
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_cancellation(self):
        """Async PTY wrapper respects cancellation event."""
        from claudechic.shell_runner import run_in_pty_cancellable

        shell = os.environ.get("SHELL", "/bin/sh")
        cancel = asyncio.Event()
        cancel.set()  # Pre-cancel
        output, rc, cancelled = await run_in_pty_cancellable(
            "sleep 10", shell, None, dict(os.environ), cancel
        )
        assert cancelled is True

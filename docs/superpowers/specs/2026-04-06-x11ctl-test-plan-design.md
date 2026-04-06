# x11ctl Integration Test Plan — Design Spec

**Date:** 2026-04-06
**Status:** Final
**Scope:** Comprehensive integration test plan for `scripts/x11ctl` covering lifecycle, security, and concurrency scenarios.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Environment | Both CI + local, clearly separated | Unit tests always run; integration skipped when binaries absent |
| Time budget | 2-3 minutes total | Allows thorough lifecycle, concurrency, repeated cycles |
| Isolation | Subprocess per test | Most realistic (tests real CLI), crash-proof, zero poisoning risk |
| File structure | Split by category | Integration, security, concurrency in separate files |
| Test interaction | CLI + state file inspection | No x11ctl imports; invoke CLI, read `/tmp/.x11ctl-*` for assertions |
| Missing binaries | Skip entire file | Module-level `skipif`; clean CI output |
| Cleanup | Per-test fixture + session safety net | Belt and suspenders; fixture for orderly cleanup, session scan for crashes |
| Acceptance criteria | All 11 from spec | Redundant coverage is fine; integration tests serve as acceptance gate |

## Priorities

1. Most realistic (test the real CLI, not internals)
2. Most robust (crash-proof, no poisoning)
3. Minimal cross-test leakage
4. Speed is not a concern

## Structure

```
tests/
    test_x11ctl.py                    # Existing 203 unit tests (unchanged)
    integration/
        conftest.py                   # Shared fixtures + helpers
        test_x11ctl_integration.py    # Lifecycle & acceptance (11 ACs + 5 scenarios)
        test_x11ctl_security.py       # Attack scenarios (6 tests)
        test_x11ctl_concurrency.py    # Race conditions (4 tests)
```

### Module-Level Skip

Each integration file begins with:

```python
pytestmark = [
    pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed"),
    pytest.mark.integration,
]
```

CI can run `pytest -m "not integration"` for fast feedback or `pytest` for the full suite.

## Test Infrastructure (`conftest.py`)

### Helpers

1. **`x11ctl_run(args, env_overrides=None, timeout=30)`** — invokes `scripts/x11ctl` as a subprocess, returns `CompletedProcess`. All tests use this instead of raw `subprocess.run`.

2. **`display_factory` fixture (function-scoped)** — allocates a unique display number from `:80`-`:98` (atomic counter), returns a dict of env vars (`X11CTL_DISPLAY`, `X11CTL_XAUTH`, `X11CTL_XPRA_PORT`, etc.) with offset ports derived from display number. Registers a per-test finalizer that runs `x11ctl stop` and cleans up state files.

3. **`session_cleanup` fixture (session-scoped, autouse)** — runs at session end: scans for any process with `Xvfb`, `xpra`, `x11vnc`, `websockify` in comm whose PID matches a `/tmp/.x11ctl-*` pidfile in the test display range (`:80`-`:98`). Kills orphans, removes stale state files. Safety net for test crashes.

4. **`assert_port_listening(host, port, timeout=5)`** — polls `socket.connect_ex` until success or timeout.

5. **`assert_port_free(host, port)`** — verifies port is not bound.

6. **`read_state_file(path)`** — reads a `/tmp/.x11ctl-*` file directly (no x11ctl imports), returns content string or None.

### Timeouts

- Per-subprocess call: 30 seconds (via `subprocess.run(timeout=30)`)
- Session safety net: 60-second total budget for orphan cleanup

## Test Scenarios

### `test_x11ctl_integration.py` — Lifecycle & Acceptance

#### All 11 Acceptance Criteria

| AC | Test | Verification |
|----|------|-------------|
| AC1 | `x11ctl run xdpyinfo` exits 0 | Exit code |
| AC2 | `start --headless` then `xdpyinfo -display :<N>` | Subprocess exit 0 |
| AC3 | `screenshot /tmp/test.png` produces valid PNG | File exists, `file` command reports PNG |
| AC4 | `start --xpra` then HTTP on xpra_port | `curl` or `socket.connect` to port |
| AC5 | `start --vnc` then VNC port + noVNC port listening | `sockstat` or `socket.connect` |
| AC6 | start/stop/start cycle — no stale PIDs | Pidfiles valid after restart, no orphan processes |
| AC7 | `status` exits 0 healthy, 1 when component killed | Kill xvfb, check status exit code |
| AC8 | Port conflict then clear error, exit 2 | Bind port, attempt start, check stderr + exit code |
| AC9 | TCP listeners default to 127.0.0.1 | `sockstat` output shows `127.0.0.1` not `*` |
| AC10 | Xauth file mode 0600 | `os.stat` after real start |
| AC11 | Missing dep then clear error listing install command | Rename binary, attempt start, check stderr |

#### Lifecycle Scenarios

| Scenario | Steps | Assertion |
|----------|-------|-----------|
| Tier switching | `start --all` then `start --headless` | xpra/vnc stopped, xvfb still running, tiers file = `headless` |
| Idempotency | `start --headless` twice | Second is no-op, same PID in pidfile |
| Crash recovery | `start --headless`, `kill -9 <xvfb_pid>`, `start --headless` | Stale artifacts cleaned, new Xvfb starts |
| Run subcommand | `run /bin/sh -c "exit 42"` | Exit code 42, xauth cleaned up |
| Env output | `eval $(x11ctl env)` | DISPLAY and XAUTHORITY set correctly |

### `test_x11ctl_security.py` — Attack Scenarios

| Scenario | Setup | Assertion |
|----------|-------|-----------|
| Symlink pidfile | Create symlink at pidfile path before start | Start rejects or overwrites safely |
| Symlink xauth | Create symlink at xauth path | Start fails with clear error |
| FIFO at lock path | Create FIFO at lock path | Start times out (doesn't hang), returns error |
| FIFO at pidfile path | Create FIFO at pidfile path | read_pidfile returns None, start proceeds |
| Foreign-owned state | Create state files as different user (if testable) | Rejected |
| Symlink X socket | Place symlink at `/tmp/.X11-unix/X<N>` | Stale cleanup fails closed |

### `test_x11ctl_concurrency.py` — Race Conditions

| Scenario | Setup | Assertion |
|----------|-------|-----------|
| Concurrent start | Two `x11ctl start --headless` simultaneously | One succeeds, one waits on lock then succeeds (idempotent) |
| Stop during start | `start --headless` in background, `stop` immediately | No orphaned processes |
| Concurrent stop | Two `x11ctl stop` simultaneously | Both succeed, no errors |
| Signal during run | SIGTERM to `x11ctl run sleep 60` | Child killed, Xvfb cleaned, exit 128+15 |

## Relationship to Existing Tests

The 203 existing unit tests in `tests/test_x11ctl.py` are **unchanged**. They test pure logic, mocked I/O, and isolated components. The integration tests complement them by verifying the full CLI path with real processes.

```
pytest tests/test_x11ctl.py                  # Unit only (~6s)
pytest tests/integration/                    # Integration only (~2-3min)
pytest                                       # Everything
pytest -m "not integration"                  # Skip integration (CI fast path)
```

## Acceptance Criteria for the Test Plan Itself

1. All 203 existing unit tests continue to pass
2. All 11 spec ACs have corresponding integration tests
3. Integration tests run in under 3 minutes on FreeBSD 15
4. Integration tests skip cleanly on systems without X11 binaries
5. No test leaves orphaned processes or stale state after completion or crash
6. Tests can run in parallel without interfering with each other

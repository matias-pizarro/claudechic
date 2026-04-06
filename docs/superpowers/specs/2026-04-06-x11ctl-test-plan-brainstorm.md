# x11ctl Test Plan — Brainstorming

**Date:** 2026-04-06
**Status:** Brainstorming
**Context:** `scripts/x11ctl` (2002 lines), `tests/test_x11ctl.py` (1788 lines, 203 unit tests), FreeBSD 15 jail with all X11 binaries installed

## Current Test Coverage

### What exists (203 unit tests)

| Area | Tests | Approach |
|------|-------|----------|
| Config validation | 25+ | Real construction + monkeypatch env |
| Pidfile I/O | 10 | Real filesystem (tmp_path), symlink/FIFO rejection |
| PID validation | 14 | Real `ps` + mocked edge cases (timeout, PermissionError, 2-factor fallback) |
| Tiers file I/O | 12 | Real filesystem, symlink/UTF-8/meta-tier rejection |
| Locking | 6 | Real flock with subprocess contention |
| stop_component | 11 | Real process kill (sleep), SIGKILL escalation, mocked races |
| Tier reconciliation | 12 | Pure logic + structural invariants |
| Stale X cleanup | 11 | Real filesystem + mocked ps |
| Command dispatch | 13 | Mocked start/stop/read_tiers |
| Xpra/VNC commands | 9 | Command assembly + real port binding |
| run subcommand | 3 | Real Xvfb (integration) + signal forwarding |
| screenshot | 3 | Mocked import binary |
| setup | 5 | Mocked pkg/geteuid |

### What's NOT tested

- Full start→status→stop lifecycle with real processes
- Multi-tier start (headless + xpra, headless + vnc, all)
- Tier switching (start --xpra then start --headless)
- Idempotency (start twice, stop twice)
- Stale PID recovery across invocations
- Port conflict with real bound ports during start
- Screenshot with real display
- Concurrent x11ctl invocations (lock contention)
- Signal forwarding in run (SIGINT behavior)
- Crash recovery (kill -9 xvfb mid-stack, then restart)
- self-test subcommand (not implemented)

---

## Open Question

**What's the primary testing environment to target?**

1. **This FreeBSD jail** (all X11 binaries installed) — full integration testing with real Xvfb/Xpra/x11vnc processes
2. **CI (GitHub Actions / Linux)** — X11 binaries may/may not be available, focused on unit tests + conditional integration
3. **Both** — test plan works in CI (unit tests always, integration when binaries present) AND locally for full-stack validation
4. **Something else** — dedicated FreeBSD test jail, matrix of jail configurations

---

## Candidate Test Categories (to discuss)

### A. Unit Tests (current — 203 tests)
Pure logic, mocked I/O, fast (~6s). Already comprehensive.

### B. Component Integration Tests
Real process lifecycle but isolated per component:
- Start/stop Xvfb with real readiness probe
- Start/stop Xpra shadow against real Xvfb
- Start/stop x11vnc+websockify against real Xvfb
- Port conflict detection with real bound sockets

### C. Full-Stack Integration Tests
End-to-end lifecycle scenarios:
- `start --all` → `status` → `screenshot` → `stop`
- Tier switching: `start --xpra` → `start --headless` (downgrades)
- Idempotency: `start --all` twice
- Crash recovery: kill Xvfb, then `start --headless`

### D. Concurrency / Race Tests
- Two `x11ctl start` invocations simultaneously (lock contention)
- `stop` during `start` readiness probe
- Signal during `run` at various points

### E. Security Tests
- Symlink attacks on /tmp state files during operation
- FIFO substitution during operation
- Foreign-owned state file rejection

### F. Acceptance Tests (from spec AC)
1. `x11ctl start --headless` → `xdpyinfo -display :99` succeeds
2. `x11ctl screenshot /tmp/test.png` produces valid PNG
3. `x11ctl start --xpra` → HTML5 client at http://127.0.0.1:10000
4. `x11ctl start --vnc` → VNC on 5900, noVNC on 6080
5. Start/stop/start idempotent — no stale PIDs
6. `x11ctl status` exits 0 when healthy, 1 when degraded
7. Port conflict produces clear error naming conflicting process
8. All TCP listeners default to 127.0.0.1
9. Xauth file mode 0600
10. Missing dependencies produce clear error listing what to install

---

## Notes for Discussion

- Should integration tests use a non-default display (e.g., `:98`) to avoid conflicting with any existing `:99`?
- Should integration tests be in a separate file (`tests/test_x11ctl_integration.py`)?
- What timeout is acceptable for integration tests? (Each tier start takes 1-5s for readiness)
- Should we test with `--bind-all` or only loopback?
- How do we handle test cleanup if a test crashes mid-lifecycle?

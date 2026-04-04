# X11 in FreeBSD Jails — Design Spec

**Date:** 2026-04-05
**Status:** Draft (rev 3 — post round-2 review: roborev + code-reviewer + architect + contrarian)
**Scope:** Running X11 applications inside any FreeBSD jail — headless testing, remote viewing, GUI development, and window automation.

## Problem

FreeBSD jails used for agent development (and CI) often set `DISPLAY=:0` with no backing X server. GUI-dependent tools (Playwright, Selenium, GUI apps, screenshot utilities) fail silently or crash. There is no standard approach for provisioning X11 inside jails.

## Goals

1. **Headless testing** — run GUI apps (browsers, Playwright, Selenium) without a physical display
2. **Remote viewing** — view the virtual display from a browser or native VNC client for debugging
3. **GUI development** — render and interact with GUI applications inside the jail
4. **Window automation** — `xdotool`, screenshots, visual regression testing

## Acceptance Criteria

1. Headless wrapper one-liner runs a Playwright/Selenium test to completion (see Simplest Path section)
2. `x11ctl start --headless` creates a working display; `xdpyinfo -display :99` succeeds
3. `x11ctl screenshot /tmp/test.png` produces a valid PNG file
4. `x11ctl start --xpra` enables remote viewing via `http://jail-ip:10000` (HTML5 client)
5. `x11ctl start --vnc` enables remote viewing via VNC client on port 5900 and noVNC on port 6080
6. `x11ctl start` / `stop` / `start` cycle is idempotent — no stale PIDs, no orphaned processes
7. `x11ctl status` correctly reports running/stopped for each component, exits 0 when healthy, exits 1 when some down (never exits 2 — that code is reserved for `start`)
8. Port-in-use conflicts at start time produce a clear error naming the conflicting process
9. All TCP listeners default to `127.0.0.1`; `0.0.0.0` requires explicit `--bind-all` flag
10. Xauth file is created with mode `0600`; not world-readable
11. Missing runtime dependencies (python3, Xvfb, xdpyinfo, etc.) produce a clear error at startup listing what to install

## Non-Goals

- GPU passthrough / hardware-accelerated rendering
- Linux container support (Docker, Podman, systemd-nspawn) — FreeBSD jails only
- Full desktop environment provisioning (documented as a recipe, not a default)
- Wayland-native workflows (X11 only)

## Simplest Path: Headless One-Liner (CI-Only)

For headless CI testing where no remote viewing is needed, a simple wrapper creates a temporary Xvfb display, runs a command, and tears it down automatically.

**Note:** `xvfb-run` is a Debian/Ubuntu convenience script that is **not available on FreeBSD**. The equivalent on FreeBSD is a short shell function or the `x11ctl` script's `--headless` mode.

**FreeBSD equivalent of `xvfb-run` (shell function):**

```sh
# Add to ~/.profile or use inline
xvfb_run() {
    _display=":$$"  # use PID as unique display number
    _xauth=$(mktemp /tmp/.xauth.XXXXXX)
    trap 'kill $! 2>/dev/null; rm -f "$_xauth"' EXIT
    xauth -f "$_xauth" generate "$_display" . trusted 2>/dev/null
    Xvfb "$_display" -screen 0 1920x1080x24 -auth "$_xauth" >/dev/null 2>&1 &
    DISPLAY="$_display" XAUTHORITY="$_xauth" "$@"
    _rc=$?
    kill $! 2>/dev/null; rm -f "$_xauth"
    trap - EXIT
    return $_rc
}

# Usage:
xvfb_run playwright test
xvfb_run import -window root screenshot.png
```

The runbook includes this function and documents it as the simplest headless path. The `x11ctl` script also ships an `x11ctl run <command>` subcommand that does the same thing.

**When the one-liner is sufficient:**
- Automated test suites that just need a `DISPLAY` (Playwright, Selenium, pytest-qt)
- CI pipelines where no human ever needs to see the screen
- One-off screenshot captures

**When you need the full x11ctl stack instead:**
- You want to view the display remotely (debugging, manual testing)
- You need a persistent display across multiple commands (not just wrapping one command)
- You want attach/detach semantics (Xpra, like tmux for X11)
- You're running a desktop environment or long-lived GUI app

The rest of this spec covers the full stack. If the one-liner meets your needs, stop here.

## Architecture

### Approach: Hybrid — Xvfb baseline + Xpra overlay + VNC fallback

Three opt-in tiers, each building on the previous:

```
                          ┌─────────────────────────┐
                          │   User / Browser / CLI   │
                          └────┬────────┬────────┬───┘
                               │        │        │
                     Xpra HTML5│   VNC  │  noVNC │
                      :10000   │  :5900 │  :6080 │
                          ┌────┴───┐ ┌──┴──┐ ┌───┴──────┐
               Tier 2 →   │  xpra  │ │x11  │ │websockify│  ← Tier 3
                          │ shadow │ │vnc  │ │ + noVNC  │
                          └────┬───┘ └──┬──┘ └───┬──────┘
                               │        │        │
                               └────────┼────────┘
                                        │
                                   ┌────┴────┐
                    Tier 1 →       │  Xvfb   │
                                   │  :99    │
                                   └─────────┘
```

**Tier 1 (Headless):** Xvfb provides a virtual framebuffer. Sufficient for CI, automated testing, and any app that just needs a `DISPLAY`.

**Tier 2 (Xpra):** Shadows the Xvfb display (attaches as a viewer to the existing framebuffer) and provides attach/detach semantics (like tmux for X11), per-window forwarding, and a built-in HTML5 browser client.

**Tier 3 (VNC fallback):** x11vnc exposes the Xvfb display as VNC. noVNC + websockify provides browser-based access. Independent of Xpra — works if Xpra has issues.

### Why three tiers (not just Xpra standalone)

Xpra can embed its own virtual X server, but using Xvfb as the single source of truth means:
- Tier 1 works with zero Xpra dependency (minimal headless — the most common use case in CI)
- Multiple viewers (Xpra, x11vnc) can attach to the same display simultaneously
- If Xpra breaks, VNC still works against the same Xvfb
- Xvfb is simpler to debug and more battle-tested on FreeBSD

**Why keep Tier 3 (VNC) alongside Tier 2 (Xpra)?** Defense in depth. Xpra is the better remote viewing experience (attach/detach, per-window), but it has more moving parts. VNC (x11vnc + noVNC) is simpler and provides a fallback if Xpra's FreeBSD port has issues. The maintenance cost is low: Tier 3 is 4 additional packages and ~20 lines in the script. Operators who don't need the fallback simply never pass `--vnc`.

The runbook also documents Xpra-standalone mode for operators who prefer a single-process setup.

## Package Dependencies

### Tier 1 — Headless (required baseline)

```
xorg-vfbserver     # Xvfb virtual framebuffer
xauth              # X authority file management
xdpyinfo           # Display info for health checks and readiness probes
xdotool            # Window automation, key/mouse simulation
ImageMagick7-nox11 # Screenshot capture via `import -window root`
```

**Note on ImageMagick7-nox11:** The "nox11" variant avoids pulling in heavier X11 display/window manager dependencies, but still depends on `libX11`, `libXext`, `libXt` (needed by `import`). This is a conscious trade-off — `import` is the most capable and well-documented screenshot tool. Lighter alternatives (`xwd` + `convert`, `scrot`) exist but are less portable and less featureful. The runbook documents alternatives.

### Tier 2 — Xpra remote viewing

```
xpra              # Attach/detach remote viewer + HTML5 client (v6.4 on FreeBSD 15)
xpra-html5        # Browser-based client assets
```

### Tier 3 — VNC fallback

```
x11vnc            # Exposes any X display over VNC protocol
novnc             # HTML5 VNC client (installs to /usr/local/libexec/novnc)
py311-websockify  # WebSocket-to-TCP proxy for noVNC (version may vary)
```

Note: `tigervnc-server` is intentionally omitted from the default install. It provides an alternative VNC server with its own built-in Xvfb mode, but overlaps with x11vnc + Xvfb. The runbook documents it as an alternative for operators who prefer TigerVNC's integrated approach.

## Runtime Requirements

**Python:** `>=3.11` (FreeBSD 15 ships 3.11 as the default `python3`). The script uses stdlib only — no pip packages required.

**Shebang:** `#!/usr/local/bin/python3` (FreeBSD convention — `/usr/local/bin` is where `pkg` installs Python). The runbook notes that `#!/usr/bin/env python3` also works if `python3` is on `PATH`.

**Preflight checks:** On startup, `x11ctl` verifies that required binaries exist for the requested tier before attempting to launch anything:

```
$ x11ctl start --xpra
Error: missing required commands for --xpra: xpra, xdpyinfo
  Run: pkg install xpra xdpyinfo
```

## Display Configuration

| Setting | Default | Env Override |
|---------|---------|-------------|
| Display number | `:99` | `X11CTL_DISPLAY` |
| Screen geometry | `1920x1080x24` | `X11CTL_SCREEN` |
| Xauth file | `/tmp/.x11ctl-xauth` | `X11CTL_XAUTH` |
| Xpra TCP port | `10000` | `X11CTL_XPRA_PORT` |
| VNC port | `5900` | `X11CTL_VNC_PORT` |
| noVNC HTTP port | `6080` | `X11CTL_NOVNC_PORT` |
| Bind address | `127.0.0.1` | `X11CTL_BIND` (or `--bind-all` flag for `0.0.0.0`) |

**Why `:99` not `:0`:** Many jails inherit `DISPLAY=:0` as a phantom env var with no backing socket. Using `:99` avoids ambiguity and collision.

**Xauth:** A random MIT-MAGIC-COOKIE is generated per session and written to the Xauth file. The file is created with mode `0600` (via `os.open()` with `O_CREAT | O_WRONLY`, mode `0o600`, followed by `os.fdopen()` — symlink-safe). All components and client apps reference this file via the `XAUTHORITY` env var or explicit `-auth` flags.

## Process Tree

```
x11ctl start --all
├── Xvfb :99 -screen 0 1920x1080x24 -auth /tmp/.x11ctl-xauth
├── env XAUTHORITY=/tmp/.x11ctl-xauth xpra shadow :99 --bind-tcp=127.0.0.1:10000 --html=on --daemon=no
├── x11vnc -display :99 -rfbport 5900 -shared -forever -noxdamage -localhost -auth /tmp/.x11ctl-xauth
└── websockify --listen 127.0.0.1:6080 localhost:5900 --web=/usr/local/libexec/novnc
```

**Key flags explained:**
- `xpra shadow :99` — attaches to the **existing** Xvfb display as a viewer (not `xpra start :99`, which would create a new display and conflict with Xvfb). Xpra 6.x `shadow` subcommand is the canonical way to proxy an existing X display.
- `env XAUTHORITY=/tmp/.x11ctl-xauth` — Xpra reads the `XAUTHORITY` environment variable to authenticate to the Xvfb display. There is no `--xauth` command-line flag in Xpra. The script sets this env var via `subprocess.Popen(env={...})`.
- `--bind-tcp=127.0.0.1:10000` — the current, non-deprecated TCP binding option in Xpra 6.4 (verified against `xpra/scripts/parsing.py`). The `parse_bind_ip()` function expects `HOST:PORT` format. Defaults to localhost; `--bind-tcp=0.0.0.0:10000` with `--bind-all`.
- `--html=on` — enables Xpra's built-in HTML5 web client, served on the same TCP port.
- `--daemon=no` — prevents Xpra from self-daemonizing so the script manages the process lifecycle consistently with all other components (via `subprocess.Popen`). The script captures Xpra's PID directly from `Popen.pid` rather than reading Xpra's native `~/.xpra/` pidfile. This avoids split-brain PID management.
- `-noxdamage` on x11vnc — Xvfb is a software framebuffer with no meaningful DAMAGE extension support. Without this flag, VNC shows a black or frozen screen.
- `-localhost` on x11vnc — restricts VNC to loopback only. Websockify connects locally and proxies to browsers.
- `--web=/usr/local/libexec/novnc` — correct FreeBSD path for noVNC web assets (the port installs to `${PREFIX}/libexec/novnc`, not `share/novnc`).

**Startup sequence with readiness probes:**

Each component waits for its dependency to be ready before launching:

1. Start Xvfb in background via `subprocess.Popen()`
2. **Readiness probe:** poll `xdpyinfo -display :99` (retry up to 10 times, 0.5s apart) until success
3. Start Xpra shadow (only after Xvfb is confirmed ready)
4. Start x11vnc (only after Xvfb is confirmed ready)
5. **Readiness probe:** poll x11vnc port with `socket.connect_ex(("127.0.0.1", 5900))` until it returns 0
6. Start websockify (only after x11vnc port is listening)

If any readiness probe times out (5s default), the script prints the component's log file path and exits with an error.

**PID management:**

All four components are launched via `subprocess.Popen()` with `--daemon=no` for Xpra (see above). This means all processes are direct children of the `x11ctl` Python process, and the script captures PIDs from `Popen.pid`.

Each PID is written to `/tmp/.x11ctl-<component>.pid`. Pidfiles are created atomically via `os.open()` with `O_CREAT | O_EXCL` (prevents races and symlink attacks). The pidfile also stores a creation-time nonce (random 8-char hex string) as a secondary validation against PID reuse.

Format: `<pid> <nonce>\n` (e.g., `1234 a3f8b2c1\n`)

- **On start:** check if pidfile exists. If it does, read PID and nonce. Verify the PID is alive AND belongs to the expected process using `subprocess.run(["ps", "-p", str(pid), "-o", "comm="])` (not `/proc`, which is unavailable in FreeBSD jails by default). If the PID is stale (dead or wrong process), remove the pidfile and proceed. If the PID is live and correct, skip (idempotent).
- **On stop (same invocation):** for child processes, use `Popen.terminate()` (SIGTERM), then `Popen.wait(timeout=3)`, then `Popen.kill()` (SIGKILL) if still alive. Remove pidfile.
- **On stop (different invocation):** read PID from pidfile, validate via `ps -p <pid> -o comm=`, then send `os.kill(pid, signal.SIGTERM)`, poll with `os.kill(pid, 0)` in a loop (up to 3s, 0.1s interval), then `os.kill(pid, signal.SIGKILL)` if still alive. `os.waitpid()` is NOT used here because the processes are not children of this invocation. Remove pidfile.

**Log file management:**

Each component's stdout/stderr is redirected to `/tmp/.x11ctl-<component>.log`:
- `/tmp/.x11ctl-xvfb.log`
- `/tmp/.x11ctl-xpra.log`
- `/tmp/.x11ctl-x11vnc.log`
- `/tmp/.x11ctl-websockify.log`

On each `start`, the previous log is preserved as `.log.prev` (renamed, not deleted) before the new log is created. This ensures crash evidence survives a restart cycle. Only one generation of `.prev` is kept — unbounded growth is prevented while preserving the most recent crash log. The `status` subcommand prints the last 5 lines of each current log for quick diagnostics. Troubleshooting guidance in the runbook points users to both `.log` and `.log.prev` files.

## Port Summary

| Port | Protocol | Component | Default Bind | Purpose |
|------|----------|-----------|-------------|---------|
| — | Unix socket | Xvfb | n/a | `/tmp/.X11-unix/X99` |
| 10000 | TCP/HTTP | Xpra | `127.0.0.1` | HTML5 client + native xpra attach |
| 5900 | TCP | x11vnc | `127.0.0.1` | Native VNC client connections |
| 6080 | HTTP | websockify | `127.0.0.1` | noVNC browser-based VNC |

**Security:** All TCP listeners default to `127.0.0.1` (loopback only). To expose to the network, pass `--bind-all` to `x11ctl start`, which switches to `0.0.0.0`. The runbook documents additional hardening for network-exposed setups:
- x11vnc: use `-passwd <password>` or `-passwdfile /tmp/.x11ctl-vnc-passwd`
- Xpra: use `--tcp-auth=file:filename=/tmp/.x11ctl-xpra-passwd`
- Or: use SSH tunnels (`ssh -L 10000:localhost:10000 jail-host`) to keep ports on loopback and authenticate via SSH

**Port-in-use detection:** Before starting each component, the script checks whether the target port is already bound. The authoritative check is an explicit bind attempt (`socket.bind()` on the target address/port, then immediately close). This correctly detects all conflict scenarios including interface/family mismatches and bound-but-not-accepting states. If the bind fails (EADDRINUSE), the script runs `sockstat -l -p <port>` to identify the conflicting process for the error message:

```
Error: port 5900 is already in use by pid 1234 (x11vnc)
  Run: x11ctl stop --vnc   (to stop the existing instance)
  Or:  X11CTL_VNC_PORT=5901 x11ctl start --vnc   (to use a different port)
```

The script exits with status 2 on port conflicts (distinct from status 1 for "component down"). The `status` subcommand never returns 2.

## Jail Configuration Requirements

Jail configuration is split into host-side and in-jail responsibilities.

### Host-side: `jail.conf` settings

These changes are made by the jail operator on the **host** system, not inside the jail:

```conf
myjail {
    # Standard — devfs for /dev
    mount.devfs;

    # Required for MIT-SHM (shared memory) — X clients like browsers need this
    sysvshm = new;        # Give jail its own SHM namespace
    sysvsem = new;        # Semaphores (some apps need this too)
    sysvmsg = new;        # Message queues (rarely needed, but cheap)

    # Optional but recommended — faster /tmp
    allow.mount.tmpfs;

    # If remote viewing ports need to be accessible from outside the jail,
    # ensure the jail's IP address / network config allows inbound TCP on
    # the configured ports (10000, 5900, 6080).
}
```

### In-jail: package installation and x11ctl

These steps are performed **inside** the jail:

```sh
# Tier 1 (headless baseline)
pkg install xorg-vfbserver xauth xdpyinfo xdotool ImageMagick7-nox11

# Tier 2 (Xpra remote viewing) — optional
pkg install xpra xpra-html5

# Tier 3 (VNC fallback) — optional
pkg install x11vnc novnc py311-websockify
```

### What's NOT needed (host or jail)

- `allow.raw_sockets` — X11 uses Unix domain sockets and TCP
- Linux compat — all native FreeBSD packages
- GPU passthrough — Xvfb is software-rendered

### SHM verification (run inside the jail)

```sh
ipcs -m 2>/dev/null && echo "SHM available" || echo "SHM not available — add sysvshm=new to jail.conf on the HOST"
```

## `x11ctl` Script Design

Python script (stdlib only — no third-party dependencies). Single file, copy-and-run.

**Runtime:** Python `>=3.11` with `#!/usr/local/bin/python3` shebang (FreeBSD convention). Uses only stdlib modules: `subprocess`, `signal`, `os`, `socket`, `argparse`, `time`, `json`, `pathlib`.

**Why Python, not POSIX shell:** The script needs process management with PID validation, readiness probes with retries and timeouts, port-conflict detection via socket bind attempts, cascading stop with dependency ordering, and structured log output. These requirements exceed what POSIX shell handles reliably — PID races, missing arrays, no proper error handling. Python's stdlib modules make all of this correct, testable, and readable. Python is already a hard dependency of the project environment.

### Subcommands

| Command | Description |
|---------|-------------|
| `x11ctl setup [--tier1\|--tier2\|--tier3\|--all]` | Install packages for specified tiers |
| `x11ctl start [--headless\|--xpra\|--vnc\|--all] [--bind-all]` | Start components (each flag implies tiers below) |
| `x11ctl stop [--headless\|--xpra\|--vnc\|--all]` | Stop components by tier |
| `x11ctl status` | Show running state, PIDs, ports, last 5 log lines |
| `x11ctl env` | Print `export DISPLAY=...; export XAUTHORITY=...` for eval |
| `x11ctl run <command> [args...]` | Run a command with a temporary Xvfb (FreeBSD equivalent of `xvfb-run`) |
| `x11ctl screenshot <path>` | Capture display to PNG via `import -window root` (ImageMagick) |

### Behavior

- **Idempotent** — `start` validates pidfiles (PID alive + process name match via `ps` + nonce) before launching; `stop` is safe when nothing is running
- **Layered** — `--xpra` implies `--headless`; `--vnc` implies `--headless`; `--all` starts everything
- **Readiness-gated** — each component waits for its dependency to be ready before starting (see Startup sequence above)
- **Port-conflict detection** — authoritative `socket.bind()` attempt before launching; `sockstat` for diagnostics on conflict (exit code 2)
- **Preflight checks** — verifies required binaries exist for the requested tier before attempting anything
- **Localhost by default** — all TCP listeners bind to `127.0.0.1`. Pass `--bind-all` to bind to `0.0.0.0` for network access
- **Clean shutdown** — SIGTERM, 3s grace, SIGKILL; removes pidfiles and Xauth
- **Dependency-aware stop** — `stop --headless` warns and stops Xpra/VNC first if they're running (they depend on Xvfb). `stop --all` stops top-down: websockify → x11vnc → Xpra → Xvfb.
- **Status exit codes** — 0 = all requested components running, 1 = some down. Exit code 2 (port conflict) is only returned by `start`, never by `status` or `stop`.

### Start/Stop Asymmetry

Start implies dependencies **downward** (start Xvfb first, then viewers). Stop cascades **upward** (stop viewers first, then Xvfb):

```
Start --xpra  →  starts Xvfb, then Xpra
Stop  --headless  →  stops Xpra and VNC first (if running), then Xvfb
```

This asymmetry is intentional — starting a viewer without a display is an error, but stopping the display while viewers are attached would leave orphaned processes.

## Verification Plan

### Automated (in x11ctl itself)

The `x11ctl` script includes a `self-test` subcommand (not listed in the user-facing subcommands table) that exercises the acceptance criteria programmatically:

```sh
x11ctl self-test --tier1    # Start headless, verify xdpyinfo, take screenshot, stop, verify cleanup
x11ctl self-test --tier2    # Start Xpra, verify HTML5 port responds, stop
x11ctl self-test --tier3    # Start VNC, verify port responds, stop
x11ctl self-test --all      # All of the above + idempotency test (start/stop/start cycle)
```

### Manual (runbook checklist)

The runbook includes a verification checklist for operators:

1. Run `x11ctl start --headless` and verify `xdpyinfo` output
2. Run `x11ctl screenshot /tmp/test.png` and verify PNG with `file /tmp/test.png`
3. Run `x11ctl start --xpra` and open `http://jail-ip:10000` in a browser (requires `--bind-all` or SSH tunnel)
4. Run `x11ctl start --vnc` and connect with a VNC client to port 5900 (requires `--bind-all` or SSH tunnel)
5. Kill Xvfb with `kill -9`, then run `x11ctl start --headless` — verify it recovers (stale PID handling)
6. Run `x11ctl status` and verify output matches expectations

## Runbook Structure

Primary deliverable: `docs/x11-in-freebsd-jails.md`

```
1. Overview
   - Purpose, architecture diagram
   - Quick decision: headless one-liner vs full x11ctl stack

2. Prerequisites
   - Host-side: jail.conf settings (sysvshm, devfs)
   - In-jail: SHM verification check, Python >=3.11
   - Network: port/firewall considerations, localhost vs bind-all

3. Simplest Path: Headless One-Liner
   - xvfb_run shell function (FreeBSD equivalent of Debian's xvfb-run)
   - x11ctl run <command> alternative
   - When this is all you need

4. Tier 1: Headless Display (Xvfb)
   - Package install
   - Starting Xvfb manually
   - Setting DISPLAY and XAUTHORITY
   - Readiness verification: xdpyinfo
   - Screenshot: import -window root
   - Alternative screenshot tools (xwd, scrot)
   - Use case examples: Playwright, Selenium, any GUI app in CI

5. Tier 2: Remote Viewing with Xpra
   - Package install
   - Shadowing the Xvfb display (xpra shadow :99 with XAUTHORITY env var)
   - Running Xpra standalone (no Xvfb needed)
   - Connecting: browser (HTML5), native xpra client, VNC mode
   - Attach/detach workflow (like tmux for X11)
   - Per-window vs full-desktop forwarding
   - Authentication: --tcp-auth for network-exposed setups

6. Tier 3: VNC Fallback (x11vnc + noVNC)
   - Package install
   - Starting x11vnc with -noxdamage against Xvfb
   - Starting noVNC/websockify (correct path: /usr/local/libexec/novnc)
   - Connecting from browser and native VNC clients
   - Authentication: -passwd/-passwdfile for network-exposed setups
   - Alternative: tigervnc-server (integrated Xvfb+VNC)

7. x11ctl Script Reference
   - Installation (copy script, make executable, verify Python >=3.11)
   - All subcommands with examples
   - Environment variable overrides
   - --bind-all flag for network exposure
   - Integrating into jail startup scripts (exec.start hook example)

8. Recipes
   - "Run Playwright tests headlessly" (xvfb_run function or x11ctl run)
   - "Run Playwright with a persistent display" (x11ctl start --headless)
   - "Debug a GUI app from your laptop browser" (x11ctl start --xpra + SSH tunnel)
   - "Xpra attach/detach across sessions"
   - "Take automated screenshots for visual regression"
   - "Run a full desktop environment (fluxbox) for manual testing"

9. Verification
   - x11ctl self-test walkthrough
   - Manual operator checklist

10. Troubleshooting
    - "Connection refused" — port/firewall checklist, port-in-use detection
    - "Cannot open display" — DISPLAY/XAUTHORITY mismatch, readiness probe tips
    - "MIT-SHM error" — sysvshm not enabled on host
    - "VNC shows black screen" — missing -noxdamage
    - "Xpra won't start" — check XAUTHORITY env var, fallback to VNC stack
    - Stale pidfiles / orphaned processes — how x11ctl handles PID validation + nonce
    - Log file locations (.log and .log.prev) for each component
```

## Deliverables

1. **Runbook** — `docs/x11-in-freebsd-jails.md` — comprehensive guide with all tiers, recipes, verification, troubleshooting
2. **Script** — `scripts/x11ctl` — Python (stdlib only, `>=3.11`), single file, all subcommands documented above

## Out of Scope (Future Work)

- Persistent Xpra sessions across jail restarts (requires jail hook integration)
- Audio forwarding (PulseAudio over Xpra — possible but not needed now)
- Multi-display support (multiple Xvfb instances)
- Integration with ClaudeChic's remote testing server
- Automatic port allocation for multi-jail hosts (env-var override is sufficient for now)

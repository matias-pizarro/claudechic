# X11 in FreeBSD Jails — Design Spec

**Date:** 2026-04-05
**Status:** Draft (rev 2 — post review: roborev + code-reviewer + architect + contrarian)
**Scope:** Running X11 applications inside any FreeBSD jail — headless testing, remote viewing, GUI development, and window automation.

## Problem

FreeBSD jails used for agent development (and CI) often set `DISPLAY=:0` with no backing X server. GUI-dependent tools (Playwright, Selenium, GUI apps, screenshot utilities) fail silently or crash. There is no standard approach for provisioning X11 inside jails.

## Goals

1. **Headless testing** — run GUI apps (browsers, Playwright, Selenium) without a physical display
2. **Remote viewing** — view the virtual display from a browser or native VNC client for debugging
3. **GUI development** — render and interact with GUI applications inside the jail
4. **Window automation** — `xdotool`, screenshots, visual regression testing

## Acceptance Criteria

1. `xvfb-run` one-liner runs a headless Playwright/Selenium test to completion
2. `x11ctl start --headless` creates a working display; `xdpyinfo -display :99` succeeds
3. `x11ctl screenshot /tmp/test.png` produces a valid PNG file
4. `x11ctl start --xpra` enables remote viewing via `http://jail-ip:10000` (HTML5 client)
5. `x11ctl start --vnc` enables remote viewing via VNC client on port 5900 and noVNC on port 6080
6. `x11ctl start` / `stop` / `start` cycle is idempotent — no stale PIDs, no orphaned processes
7. `x11ctl status` correctly reports running/stopped for each component, exits 0 when healthy
8. Port-in-use conflicts at start time produce a clear error naming the conflicting process
9. All TCP listeners default to `127.0.0.1`; `0.0.0.0` requires explicit `--bind-all` flag
10. Xauth file is created with mode `0600`; not world-readable

## Non-Goals

- GPU passthrough / hardware-accelerated rendering
- Linux container support (Docker, Podman, systemd-nspawn) — FreeBSD jails only
- Full desktop environment provisioning (documented as a recipe, not a default)
- Wayland-native workflows (X11 only)

## Simplest Path: `xvfb-run` (CI-Only)

For headless CI testing where no remote viewing is needed, `xvfb-run` is a one-liner wrapper that creates a temporary Xvfb display, runs a command, and tears it down automatically:

```sh
pkg install xorg-vfbserver xauth
xvfb-run playwright test
xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" my-gui-app --headless
```

**When `xvfb-run` is sufficient:**
- Automated test suites that just need a `DISPLAY` (Playwright, Selenium, pytest-qt)
- CI pipelines where no human ever needs to see the screen
- One-off screenshot captures (`xvfb-run import -window root screenshot.png`)

**When you need the full x11ctl stack instead:**
- You want to view the display remotely (debugging, manual testing)
- You need a persistent display across multiple commands (not just wrapping one command)
- You want attach/detach semantics (Xpra, like tmux for X11)
- You're running a desktop environment or long-lived GUI app

The rest of this spec covers the full stack. If `xvfb-run` meets your needs, stop here.

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

### Why Xvfb as the foundation (not Xpra standalone)

Xpra can embed its own virtual X server, but using Xvfb as the single source of truth means:
- Tier 1 works with zero Xpra dependency (minimal headless)
- Multiple viewers (Xpra, x11vnc) can attach to the same display
- If Xpra breaks, VNC still works against the same Xvfb
- Xvfb is simpler to debug and more battle-tested on FreeBSD

The runbook also documents Xpra-standalone mode for operators who prefer a single-process setup.

## Package Dependencies

### Tier 1 — Headless (required baseline)

```
xorg-vfbserver    # Xvfb virtual framebuffer
xauth             # X authority file management
xdpyinfo          # Display info for health checks and readiness probes
xdotool           # Window automation, key/mouse simulation
ImageMagick7-nox11 # Screenshot capture via `import -window root` (nox11 variant avoids pulling in X client libs)
```

### Tier 2 — Xpra remote viewing

```
xpra              # Attach/detach remote viewer + HTML5 client (v6.4 on FreeBSD 15)
xpra-html5        # Browser-based client assets
```

### Tier 3 — VNC fallback

```
x11vnc            # Exposes any X display over VNC protocol
tigervnc-server   # Alternative VNC server (also includes Xvfb mode)
novnc             # HTML5 VNC client (installs to /usr/local/libexec/novnc)
py311-websockify  # WebSocket-to-TCP proxy for noVNC (version may vary)
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

**Xauth:** A random MIT-MAGIC-COOKIE is generated per session and written to the Xauth file. The file is created with mode `0600` (via `umask 077` before `xauth generate`) to prevent other jail users from reading the cookie. All components and client apps reference this file via the `XAUTHORITY` env var or explicit `-auth`/`--xauth` flags.

## Process Tree

```
x11ctl start --all
├── Xvfb :99 -screen 0 1920x1080x24 -auth /tmp/.x11ctl-xauth
├── xpra shadow :99 --bind=tcp://127.0.0.1:10000/ --html=on --xauth=/tmp/.x11ctl-xauth
├── x11vnc -display :99 -rfbport 5900 -shared -forever -noxdamage -localhost -auth /tmp/.x11ctl-xauth
└── websockify --listen 127.0.0.1:6080 localhost:5900 --web=/usr/local/libexec/novnc
```

**Key flags explained:**
- `xpra shadow :99` — attaches to the **existing** Xvfb display as a viewer (not `xpra start :99`, which would create a new display and conflict with Xvfb). Xpra 6.x `shadow` subcommand is the canonical way to proxy an existing X display.
- `--bind=tcp://127.0.0.1:10000/` — Xpra 6.x syntax (replaces deprecated `--bind-tcp`). Defaults to localhost; use `--bind=tcp://0.0.0.0:10000/` only with `--bind-all`.
- `--xauth=/tmp/.x11ctl-xauth` — tells Xpra how to authenticate to the Xvfb display. Without this, Xpra fails with "cannot open display".
- `-noxdamage` on x11vnc — Xvfb is a software framebuffer with no meaningful DAMAGE extension support. Without this flag, VNC shows a black or frozen screen.
- `-localhost` on x11vnc — restricts VNC to loopback only. Websockify connects locally and proxies to browsers.
- `--web=/usr/local/libexec/novnc` — correct FreeBSD path for noVNC web assets (the port installs to `${PREFIX}/libexec/novnc`, not `share/novnc`).

**Startup sequence with readiness probes:**

Each component waits for its dependency to be ready before launching:

1. Start Xvfb in background
2. **Readiness probe:** poll `xdpyinfo -display :99` (retry up to 10 times, 0.5s apart) until success
3. Start Xpra shadow (only after Xvfb is confirmed ready)
4. Start x11vnc (only after Xvfb is confirmed ready)
5. **Readiness probe:** poll x11vnc port with `socket.connect_ex(("127.0.0.1", 5900))` until it returns 0
6. Start websockify (only after x11vnc port is listening)

If any readiness probe times out (5s default), the script prints the component's log file path and exits with an error.

**PID management:**

Each process writes a pidfile to `/tmp/.x11ctl-<component>.pid`. The script validates pidfiles before acting:

- **On start:** check if pidfile exists. If it does, verify the PID is alive AND belongs to the expected process using `subprocess.run(["ps", "-p", str(pid), "-o", "comm="])` (not `/proc`, which is unavailable in FreeBSD jails by default). If the PID is stale (dead or wrong process), remove the pidfile and proceed. If the PID is live and correct, skip (idempotent). Pidfiles are created atomically via `os.open()` with `O_CREAT | O_EXCL` to avoid races.
- **On stop:** read PID from file, validate it's the right process via `ps`, send `SIGTERM` via `os.kill()`, wait up to 3s with `os.waitpid()` / polling, send `SIGKILL` if still alive. Remove pidfile. If the process is already gone, just clean up the pidfile.
- **Daemonization note:** Xvfb and x11vnc are launched via `subprocess.Popen()` with stdout/stderr redirected to log files; the script captures `.pid`. Xpra daemonizes itself by default and writes its own PID to a file; the script reads Xpra's native pidfile location (`~/.xpra/`) rather than managing it separately.

**Log file management:**

Each component's stdout/stderr is redirected to `/tmp/.x11ctl-<component>.log`:
- `/tmp/.x11ctl-xvfb.log`
- `/tmp/.x11ctl-xpra.log`
- `/tmp/.x11ctl-x11vnc.log`
- `/tmp/.x11ctl-websockify.log`

Logs are overwritten on each `start` (not appended) to avoid unbounded growth. The `status` subcommand prints the last 5 lines of each log for quick diagnostics. Troubleshooting guidance in the runbook points users to these files.

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

**Port-in-use detection:** Before starting each component, the script checks whether the target port is already bound. It first attempts a quick `socket.connect_ex()` probe, and if the port is occupied, runs `sockstat -l -p <port>` to identify the conflicting process. If a conflict is detected, the script prints a clear error:

```
Error: port 5900 is already in use by pid 1234 (x11vnc)
  Run: x11ctl stop --vnc   (to stop the existing instance)
  Or:  X11CTL_VNC_PORT=5901 x11ctl start --vnc   (to use a different port)
```

The script exits with status 2 on port conflicts (distinct from status 1 for "component down").

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
pkg install x11vnc tigervnc-server novnc py311-websockify
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

**Why Python, not POSIX shell:** The script needs process management with PID validation, readiness probes with retries and timeouts, port-conflict detection via subprocess parsing, cascading stop with dependency ordering, and structured log output. These requirements exceed what POSIX shell handles reliably — PID races, missing arrays, no proper error handling. Python's `subprocess`, `signal`, `os`, and `socket` modules make all of this correct, testable, and readable. Python is already a hard dependency of the project environment.

### Subcommands

| Command | Description |
|---------|-------------|
| `x11ctl setup [--tier1\|--tier2\|--tier3\|--all]` | Install packages for specified tiers |
| `x11ctl start [--headless\|--xpra\|--vnc\|--all] [--bind-all]` | Start components (each flag implies tiers below) |
| `x11ctl stop [--headless\|--xpra\|--vnc\|--all]` | Stop components by tier |
| `x11ctl status` | Show running state, PIDs, ports, last 5 log lines |
| `x11ctl env` | Print `export DISPLAY=...; export XAUTHORITY=...` for eval |
| `x11ctl screenshot <path>` | Capture display to PNG via `import -window root` (ImageMagick) |

### Behavior

- **Idempotent** — `start` validates pidfiles (PID alive + process name match via `ps`) before launching; `stop` is safe when nothing is running
- **Layered** — `--xpra` implies `--headless`; `--vnc` implies `--headless`; `--all` starts everything
- **Readiness-gated** — each component waits for its dependency to be ready before starting (see Startup sequence above)
- **Port-conflict detection** — checks `sockstat -l -p <port>` before binding; fails with actionable error on conflict (exit code 2)
- **Localhost by default** — all TCP listeners bind to `127.0.0.1`. Pass `--bind-all` to bind to `0.0.0.0` for network access
- **Clean shutdown** — SIGTERM, 3s grace, SIGKILL; removes pidfiles and Xauth
- **Dependency-aware stop** — `stop --headless` warns and stops Xpra/VNC first if they're running (they depend on Xvfb). `stop --all` stops top-down: websockify → x11vnc → Xpra → Xvfb.
- **Status exit codes** — 0 = all requested components running, 1 = some down, 2 = port conflict

### Start/Stop Asymmetry

Start implies dependencies **downward** (start Xvfb first, then viewers). Stop cascades **upward** (stop viewers first, then Xvfb):

```
Start --xpra  →  starts Xvfb, then Xpra
Stop  --headless  →  stops Xpra and VNC first (if running), then Xvfb
```

This asymmetry is intentional — starting a viewer without a display is an error, but stopping the display while viewers are attached would leave orphaned processes.

## Runbook Structure

Primary deliverable: `docs/x11-in-freebsd-jails.md`

```
1. Overview
   - Purpose, architecture diagram
   - Quick decision: xvfb-run vs full x11ctl stack

2. Prerequisites
   - Host-side: jail.conf settings (sysvshm, devfs)
   - In-jail: SHM verification check
   - Network: port/firewall considerations, localhost vs bind-all

3. Simplest Path: xvfb-run
   - One-liner examples for Playwright, Selenium, pytest-qt
   - When this is all you need

4. Tier 1: Headless Display (Xvfb)
   - Package install
   - Starting Xvfb manually
   - Setting DISPLAY and XAUTHORITY
   - Readiness verification: xdpyinfo
   - Screenshot: import -window root
   - Use case examples: Playwright, Selenium, any GUI app in CI

5. Tier 2: Remote Viewing with Xpra
   - Package install
   - Shadowing the Xvfb display (xpra shadow :99)
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

7. x11ctl Script Reference
   - Installation (copy script, make executable)
   - All subcommands with examples
   - Environment variable overrides
   - --bind-all flag for network exposure
   - Integrating into jail startup scripts (exec.start hook example)

8. Recipes
   - "Run Playwright tests headlessly" (xvfb-run one-liner)
   - "Run Playwright with a persistent display" (x11ctl start --headless)
   - "Debug a GUI app from your laptop browser" (x11ctl start --xpra + SSH tunnel)
   - "Xpra attach/detach across sessions"
   - "Take automated screenshots for visual regression"
   - "Run a full desktop environment (fluxbox) for manual testing"

9. Troubleshooting
   - "Connection refused" — port/firewall checklist, port-in-use detection
   - "Cannot open display" — DISPLAY/XAUTHORITY mismatch, readiness probe tips
   - "MIT-SHM error" — sysvshm not enabled on host
   - "VNC shows black screen" — missing -noxdamage
   - "Xpra won't start" — fallback to VNC stack
   - Stale pidfiles / orphaned processes — how x11ctl handles PID validation
   - Log file locations for each component
```

## Deliverables

1. **Runbook** — `docs/x11-in-freebsd-jails.md` — comprehensive guide with all tiers, recipes, troubleshooting
2. **Script** — `scripts/x11ctl` — Python (stdlib only), single file, all subcommands documented above

## Out of Scope (Future Work)

- Persistent Xpra sessions across jail restarts (requires jail hook integration)
- Audio forwarding (PulseAudio over Xpra — possible but not needed now)
- Multi-display support (multiple Xvfb instances)
- Integration with ClaudeChic's remote testing server
- Automatic port allocation for multi-jail hosts (env-var override is sufficient for now)

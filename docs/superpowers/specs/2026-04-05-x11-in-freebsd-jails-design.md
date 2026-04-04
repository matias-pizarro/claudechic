# X11 in FreeBSD Jails — Design Spec

**Date:** 2026-04-05
**Status:** Draft
**Scope:** Running X11 applications inside any FreeBSD jail — headless testing, remote viewing, GUI development, and window automation.

## Problem

FreeBSD jails used for agent development (and CI) often set `DISPLAY=:0` with no backing X server. GUI-dependent tools (Playwright, Selenium, GUI apps, screenshot utilities) fail silently or crash. There is no standard approach for provisioning X11 inside jails.

## Goals

1. **Headless testing** — run GUI apps (browsers, Playwright, Selenium) without a physical display
2. **Remote viewing** — view the virtual display from a browser or native VNC client for debugging
3. **GUI development** — render and interact with GUI applications inside the jail
4. **Window automation** — `xdotool`, screenshots, visual regression testing

## Non-Goals

- GPU passthrough / hardware-accelerated rendering
- Linux container support (Docker, Podman, systemd-nspawn) — FreeBSD jails only
- Full desktop environment provisioning (documented as a recipe, not a default)
- Wayland-native workflows (X11 only)

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
                          │ server │ │vnc  │ │ + noVNC  │
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

**Tier 2 (Xpra):** Attaches to the Xvfb display and provides attach/detach semantics (like tmux for X11), per-window forwarding, and a built-in HTML5 browser client.

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
xdpyinfo          # Display info for health checks
xdotool           # Window automation, key/mouse simulation
```

### Tier 2 — Xpra remote viewing

```
xpra              # Virtual display + attach/detach + HTML5 viewer
xpra-html5        # Browser-based client assets
```

### Tier 3 — VNC fallback

```
x11vnc            # Exposes any X display over VNC protocol
tigervnc-server   # Alternative VNC server (also includes Xvfb mode)
novnc             # HTML5 VNC client
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

**Why `:99` not `:0`:** Many jails inherit `DISPLAY=:0` as a phantom env var with no backing socket. Using `:99` avoids ambiguity and collision.

**Xauth:** A random MIT-MAGIC-COOKIE is generated per session and written to the Xauth file. All components and client apps reference this file.

## Process Tree

```
x11ctl start --all
├── Xvfb :99 -screen 0 1920x1080x24 -auth /tmp/.x11ctl-xauth
├── xpra start :99 --bind-tcp=0.0.0.0:10000 --html=on
├── x11vnc -display :99 -rfbport 5900 -shared -forever -auth /tmp/.x11ctl-xauth
└── websockify 6080 localhost:5900 --web=/usr/local/share/novnc
```

**PID management:** Each process writes a pidfile to `/tmp/.x11ctl-<component>.pid`. The script checks these for idempotent start/stop.

## Port Summary

| Port | Protocol | Component | Purpose |
|------|----------|-----------|---------|
| — | Unix socket | Xvfb | `/tmp/.X11-unix/X99` |
| 10000 | TCP/HTTP | Xpra | HTML5 client + native xpra attach |
| 5900 | TCP | x11vnc | Native VNC client connections |
| 6080 | HTTP | noVNC | Browser-based VNC via websockify |

## Jail Configuration Requirements

### Required `jail.conf` settings

```conf
myjail {
    # Standard — devfs for /dev
    mount.devfs;

    # Required for MIT-SHM (shared memory) — X clients like browsers need this
    sysvshm = new;
    sysvsem = new;
    sysvmsg = new;

    # Optional but recommended — faster /tmp
    allow.mount.tmpfs;
}
```

### What's NOT needed

- `allow.raw_sockets` — X11 uses Unix domain sockets and TCP
- Linux compat — all native FreeBSD packages
- GPU passthrough — Xvfb is software-rendered

### SHM verification

```sh
ipcs -m 2>/dev/null && echo "SHM available" || echo "SHM not available — add sysvshm=new to jail.conf"
```

## `x11ctl` Script Design

POSIX shell script, no bash or python dependency. Single file, copy-and-run.

### Subcommands

| Command | Description |
|---------|-------------|
| `x11ctl setup [--tier1\|--tier2\|--tier3\|--all]` | Install packages for specified tiers |
| `x11ctl start [--headless\|--xpra\|--vnc\|--all]` | Start components (each flag implies tiers below) |
| `x11ctl stop [--headless\|--xpra\|--vnc\|--all]` | Stop components by tier |
| `x11ctl status` | Show running state, PIDs, ports |
| `x11ctl env` | Print `export DISPLAY=...; export XAUTHORITY=...` for eval |
| `x11ctl screenshot <path>` | Capture display to PNG |

### Behavior

- **Idempotent** — `start` checks pidfiles before launching; `stop` is safe when nothing is running
- **Layered** — `--xpra` implies `--headless`; `--vnc` implies `--headless`; `--all` starts everything
- **Clean shutdown** — SIGTERM, 3s grace, SIGKILL; removes pidfiles and Xauth
- **Dependency-aware stop** — `stop --headless` warns and stops Xpra/VNC first if they're running (they depend on Xvfb). `stop --all` stops top-down: noVNC → x11vnc → Xpra → Xvfb.
- **Status exit codes** — 0 = all requested components running, 1 = some down

## Runbook Structure

Primary deliverable: `docs/x11-in-freebsd-jails.md`

```
1. Overview
   - Purpose, architecture diagram

2. Prerequisites
   - Jail config (sysvshm, devfs)
   - SHM check
   - Port/firewall considerations

3. Tier 1: Headless Display (Xvfb)
   - Install, start, verify
   - DISPLAY/XAUTHORITY setup
   - Use cases: Playwright, Selenium, GUI apps in CI

4. Tier 2: Remote Viewing with Xpra
   - Install, attach to Xvfb, standalone mode
   - Connect: HTML5 browser, native client, VNC mode
   - Attach/detach workflow
   - Per-window vs full-desktop

5. Tier 3: VNC Fallback
   - Install x11vnc, noVNC, websockify
   - Connect from browser and native clients

6. x11ctl Script Reference
   - Install, subcommands, env overrides
   - Jail startup integration

7. Recipes
   - Headless Playwright tests
   - Debug GUI app from laptop browser
   - Xpra attach/detach across sessions
   - Automated screenshots for visual regression
   - Full desktop (fluxbox/xfce4) for manual testing

8. Troubleshooting
   - "Connection refused" — ports/firewall
   - "Cannot open display" — DISPLAY/XAUTHORITY mismatch
   - "MIT-SHM error" — sysvshm not enabled
   - "Xpra won't start" — fallback to VNC
   - Stale pidfiles / orphaned processes
```

## Deliverables

1. **Runbook** — `docs/x11-in-freebsd-jails.md` — comprehensive guide with all tiers, recipes, troubleshooting
2. **Script** — `scripts/x11ctl` — POSIX shell, single file, all subcommands documented above

## Out of Scope (Future Work)

- Persistent Xpra sessions across jail restarts (requires jail hook integration)
- Audio forwarding (PulseAudio over Xpra — possible but not needed now)
- Multi-display support (multiple Xvfb instances)
- Integration with ClaudeChic's remote testing server

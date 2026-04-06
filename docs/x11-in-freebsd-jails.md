# X11 in FreeBSD Jails — Operator Runbook

## Overview

This runbook covers running X11 applications inside FreeBSD jails for headless testing, remote viewing, GUI development, and window automation. It documents both the simple one-liner approach and the full `x11ctl` management tool.

## Prerequisites

- FreeBSD 15+ jail with Python 3.10+
- `pkg` package manager available
- For headless only: `xorg-vfbserver`, `xauth`, `xdpyinfo`, `ImageMagick7` (not `-nox11` — needs X11 delegate for screenshots)
- For remote viewing: additionally `xpra`, `xpra-html5` (Tier 2) or `x11vnc`, `novnc` (Tier 3)

Install all dependencies:

```sh
# As root inside the jail:
x11ctl setup --all
# Or tier-by-tier:
x11ctl setup --tier1   # Headless (Xvfb, xauth, xdpyinfo, ImageMagick)
x11ctl setup --tier2   # Xpra remote viewing
x11ctl setup --tier3   # VNC/noVNC fallback
```

## Simplest Path: Headless One-Liner (CI-Only)

For CI pipelines that just need a `DISPLAY`:

```sh
# Shell function — add to ~/.profile or use inline
xvfb_run() {
    _display=":$$"
    _xauth=$(mktemp /tmp/.xauth.XXXXXX)
    chmod 600 "$_xauth"
    trap 'kill $! 2>/dev/null; rm -f "$_xauth"' EXIT
    Xvfb "$_display" -screen 0 1920x1080x24 -auth "$_xauth" >/dev/null 2>&1 &
    _xvfb_pid=$!
    xauth -f "$_xauth" generate "$_display" . trusted 2>/dev/null
    _tries=0
    while [ $_tries -lt 10 ]; do
        if DISPLAY="$_display" XAUTHORITY="$_xauth" xdpyinfo >/dev/null 2>&1; then
            break
        fi
        _tries=$((_tries + 1))
        sleep 0.5
    done
    DISPLAY="$_display" XAUTHORITY="$_xauth" "$@"
    _rc=$?
    kill $_xvfb_pid 2>/dev/null; rm -f "$_xauth"
    trap - EXIT
    return $_rc
}

# Usage:
xvfb_run playwright test
xvfb_run import -window root screenshot.png
```

Or use `x11ctl run`:

```sh
x11ctl run playwright test
x11ctl run import -window root /tmp/screenshot.png
```

## Tier 1: Headless Display (Xvfb)

```sh
x11ctl start --headless        # Start Xvfb on :99
eval $(x11ctl env)             # Export DISPLAY and XAUTHORITY
x11ctl status                  # Check health
x11ctl screenshot /tmp/out.png # Capture display
x11ctl stop --headless         # Tear down
```

## Tier 2: Remote Viewing (Xpra)

```sh
x11ctl start --xpra            # Start Xvfb + Xpra shadow
# Access: http://127.0.0.1:10000 (HTML5 client)
# Or: xpra attach tcp://127.0.0.1:10000
x11ctl stop --xpra             # Stop Xpra (keeps Xvfb)
```

## Tier 3: VNC Fallback

```sh
x11ctl start --vnc             # Start Xvfb + x11vnc + noVNC
# VNC: connect to 127.0.0.1:5900
# noVNC: http://127.0.0.1:6080/vnc.html
x11ctl stop --vnc              # Stop VNC (keeps Xvfb)
```

## All Tiers

```sh
x11ctl start --all             # Start everything
x11ctl status                  # Show all components
x11ctl stop                    # Stop everything (default: --all)
```

## Network Exposure

By default, all TCP listeners bind to `127.0.0.1` (loopback only).

To expose to the network (e.g., for remote debugging):

```sh
x11ctl start --all --bind-all  # Binds to 0.0.0.0
```

**Security hardening for network-exposed setups:**

```sh
# VNC password:
x11vnc -passwd <password>
# Or: x11vnc -passwdfile /tmp/.x11ctl-vnc-passwd

# Xpra auth:
xpra shadow :99 --tcp-auth=file:filename=/tmp/.x11ctl-xpra-passwd

# Better: use SSH tunnels (keeps ports on loopback):
ssh -L 10000:localhost:10000 jail-host
```

## Configuration

All settings via environment variables:

| Setting | Default | Env Variable |
|---------|---------|-------------|
| Display | `:99` | `X11CTL_DISPLAY` |
| Screen | `1920x1080x24` | `X11CTL_SCREEN` |
| Bind address | `127.0.0.1` | `X11CTL_BIND` |
| Xpra port | `10000` | `X11CTL_XPRA_PORT` |
| VNC port | `5900` | `X11CTL_VNC_PORT` |
| noVNC port | `6080` | `X11CTL_NOVNC_PORT` |
| Xauth file | `/tmp/.x11ctl-xauth` | `X11CTL_XAUTH` |

## Troubleshooting

### Display not working

```sh
x11ctl status              # Check component health
cat /tmp/.x11ctl-xvfb.log  # Xvfb logs
cat /tmp/.x11ctl-xpra.log  # Xpra logs (if using Tier 2)
```

### Port conflicts

```sh
sockstat -l -p 10000       # Who's using the port?
X11CTL_XPRA_PORT=10001 x11ctl start --xpra  # Use alternate port
```

### Stale artifacts after crash

```sh
x11ctl start --headless    # Auto-cleans stale artifacts
# If auto-clean fails:
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
x11ctl start --headless
```

### Previous log files

Each component keeps one generation of `.log.prev`:

```sh
cat /tmp/.x11ctl-xvfb.log.prev  # Previous Xvfb log (crash evidence)
```

## Jail Configuration

### Host-side: jail.conf

```
allow.sysvipc = 1;          # Required for X11 shared memory
mount.devfs;                 # /dev access
devfs_ruleset = 4;           # Standard devfs rules
```

### In-jail: /etc/sysctl.conf (optional)

```
kern.ipc.shmmax=67108864     # 64MB shared memory (for large displays)
kern.ipc.shmall=32768
```

## x11ctl Reference

```
x11ctl start [--headless|--xpra|--vnc|--all] [--bind-all]
x11ctl stop [--headless|--xpra|--vnc|--all]
x11ctl status
x11ctl env
x11ctl run <command> [args...]
x11ctl screenshot <path.png>
x11ctl setup [--tier1|--tier2|--tier3|--all]
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General failure |
| 2 | Port conflict (start only) |

# FreeBSD Jail Setup Notes

Setup notes for X11 testing, Playwright/Chromium, and shared memory (SHM) in FreeBSD jails.

## 1. Playwright with FreeBSD Chromium

Playwright downloads **Linux** Chromium binaries by default, which require the Linuxulator (Linux binary compatibility layer) and various Linux filesystem mounts. This is fragile in jails.

**Better approach:** Use the native FreeBSD Chromium package and tell Playwright to use it.

### Install FreeBSD Chromium

```sh
# Inside the jail (as root or via sudo):
pkg install chromium
```

This installs `/usr/local/bin/chrome` (native FreeBSD binary — no Linuxulator needed).

### Configure Playwright to use it

```sh
# Set in ~/.profile, ~/.bashrc, or your CI environment:
export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/local/bin/chrome

# Or per-invocation:
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/local/bin/chrome playwright test
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/local/bin/chrome playwright open https://example.com
```

For programmatic use in test code:

```python
# playwright.config.ts / test code
browser = playwright.chromium.launch(
    executable_path="/usr/local/bin/chrome",
    args=["--no-sandbox"],  # Required in jails (no user namespaces)
)
```

### Why not the Linux binary?

| Approach | Pros | Cons |
|----------|------|------|
| FreeBSD Chromium (`pkg install chromium`) | Native binary, no compat layer, works in any jail | Version may lag behind Playwright's expected version |
| Linux Chromium (Playwright's download) | Exact version match with Playwright | Requires Linuxulator, linprocfs, linsysfs, /dev/shm, Linux base libraries |

**Recommendation:** Use FreeBSD Chromium for jail-based testing. The minor version mismatch rarely causes issues with Playwright's protocol.

### Verify it works

```sh
# With x11ctl display running:
eval $(scripts/x11ctl env)
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/local/bin/chrome playwright open https://example.com

# Or headless (no display needed):
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/local/bin/chrome playwright test
```

---

## 2. Linux Jails for X11 Testing

If you must run Linux binaries (not just FreeBSD-native ones), the jail needs Linux binary compatibility.

### Host-side requirements

**Kernel modules** (load on the host):

```sh
# /boot/loader.conf (persistent):
linux_load="YES"
linux64_load="YES"
linprocfs_load="YES"
linsysfs_load="YES"
tmpfs_load="YES"

# Or load at runtime:
kldload linux linux64 linprocfs linsysfs tmpfs
```

**Jail configuration** (`jail.conf`):

```
myjail {
    # Standard jail settings
    host.hostname = "myjail";
    path = "/jails/myjail";
    
    # Linux binary compat
    allow.mount;
    allow.mount.tmpfs;
    allow.mount.linprocfs;
    allow.mount.linsysfs;
    allow.mount.fdescfs;
    enforce_statfs = 1;
    
    # SHM for Chromium and PostgreSQL
    allow.sysvipc = 1;
    # Or per-jail SHM (FreeBSD 13+):
    # sysvshm = new;
    # sysvsem = new;
    # sysvmsg = new;
    
    # Linux filesystem mounts (done at jail start)
    exec.start += "/bin/sh /etc/rc";
    exec.start += "mkdir -p /compat/linux/proc && mount -t linprocfs linproc /compat/linux/proc";
    exec.start += "mkdir -p /compat/linux/sys && mount -t linsysfs linsys /compat/linux/sys";
    
    exec.stop = "/bin/sh /etc/rc.shutdown jail";
    exec.stop += "umount /compat/linux/proc 2>/dev/null || true";
    exec.stop += "umount /compat/linux/sys 2>/dev/null || true";
}
```

### Inside the jail — install Linux base

```sh
pkg install linux_base-c7    # CentOS 7 base (minimum for most Linux binaries)
pkg install linux-c7-gtk3    # GTK3 for GUI apps (Chromium needs this)
pkg install linux-c7-nss     # NSS certificates
```

### Verify Linux compat

```sh
# Inside the jail:
sysctl kern.features.linux        # Should print: 1
/compat/linux/bin/ls /proc/self/  # Should show Linux procfs entries
brandelf -t Linux /compat/linux/bin/ls && echo "Linuxulator works"
```

---

## 3. Shared Memory (SHM) in Jails

Both Chromium and PostgreSQL require POSIX or SysV shared memory. FreeBSD jails have restricted SHM by default.

### The problem

```
# Chromium:
[1:1:0101/000000.000000:ERROR:...] Failed to reserve virtual memory for shared memory
# → Crashes with SIGTRAP or SIGABRT

# PostgreSQL:
FATAL: could not create shared memory segment: Function not implemented
```

### Option A: SysV SHM (simplest — works for PostgreSQL)

In `jail.conf` (host-side):

```
myjail {
    allow.sysvipc = 1;       # Share host's SHM namespace (simple but less isolated)
}
```

Or for per-jail isolation (FreeBSD 13+):

```
myjail {
    sysvshm = new;            # Jail gets its own SHM namespace
    sysvsem = new;
    sysvmsg = new;
}
```

Tune SHM limits inside the jail:

```sh
# /etc/sysctl.conf inside the jail:
kern.ipc.shmmax=536870912    # 512MB (default on this system)
kern.ipc.shmall=131072       # Pages (512MB / 4KB page = 131072)
```

### Option B: tmpfs on /dev/shm (needed for Chromium POSIX SHM)

Chromium uses POSIX shared memory (`shm_open`), which requires `/dev/shm` as a tmpfs mount. This **cannot be created from inside the jail** because `/dev` is a devfs mount managed by the host.

**From the HOST:**

```sh
# Find the jail's root path:
JAIL_ROOT=$(jls -j <jailname> path)

# Create and mount before jail starts:
mkdir -p ${JAIL_ROOT}/dev/shm
mount -t tmpfs -o rw,mode=1777 tmpfs ${JAIL_ROOT}/dev/shm
```

**In `jail.conf`:**

```
myjail {
    # Mount /dev/shm before the jail starts
    exec.prestart += "mkdir -p /jails/myjail/dev/shm";
    exec.prestart += "mount -t tmpfs -o rw,mode=1777 tmpfs /jails/myjail/dev/shm";
    
    exec.poststop += "umount /jails/myjail/dev/shm 2>/dev/null || true";
}
```

**Why `exec.prestart` not `exec.start`?**

- `exec.prestart` runs on the **host** before the jail starts — it can manipulate the jail's filesystem tree
- `exec.start` runs **inside** the jail — it cannot create directories in `/dev` (devfs is read-only for directory creation)

### Option C: Chromium without /dev/shm

Chromium's `--disable-dev-shm-usage` flag tells it to use `/tmp` instead of `/dev/shm` for shared memory. Playwright passes this flag by default. However, it doesn't work reliably on all Chromium versions on FreeBSD.

```sh
# Test if Chromium works without /dev/shm:
DISPLAY=:99 /usr/local/bin/chrome --no-sandbox --disable-dev-shm-usage --headless=new --dump-dom https://example.com 2>/dev/null | head -5
```

### Verification checklist

```sh
# SysV SHM available:
ipcs -m                        # Should not error out

# POSIX SHM (/dev/shm) available:
ls /dev/shm/                   # Should be empty tmpfs (not "No such file")

# PostgreSQL can start:
pg_isready                     # Should report "accepting connections"

# Chromium can launch:
DISPLAY=:99 /usr/local/bin/chrome --no-sandbox --headless=new --dump-dom https://example.com 2>/dev/null | head -1
# Should print "<!DOCTYPE html>"
```

---

## Quick Reference: Minimal jail.conf for X11 + Chromium + PostgreSQL

```
myjail {
    host.hostname = "myjail";
    path = "/jails/myjail";
    ip4.addr = "lo0|127.0.1.1";
    
    # Basic
    allow.raw_sockets;
    mount.devfs;
    devfs_ruleset = 4;
    
    # SHM (PostgreSQL + Chromium)
    sysvshm = new;
    sysvsem = new;
    sysvmsg = new;
    
    # Mount permissions (for Linux compat + /dev/shm)
    allow.mount;
    allow.mount.tmpfs;
    allow.mount.linprocfs;
    allow.mount.linsysfs;
    allow.mount.fdescfs;
    enforce_statfs = 1;
    
    # Pre-start mounts (HOST-side, before jail starts)
    exec.prestart += "mkdir -p /jails/myjail/dev/shm";
    exec.prestart += "mount -t tmpfs -o rw,mode=1777 tmpfs /jails/myjail/dev/shm";
    
    # Start
    exec.start = "/bin/sh /etc/rc";
    exec.start += "mkdir -p /compat/linux/proc && mount -t linprocfs linproc /compat/linux/proc || true";
    exec.start += "mkdir -p /compat/linux/sys && mount -t linsysfs linsys /compat/linux/sys || true";
    
    # Stop
    exec.stop = "/bin/sh /etc/rc.shutdown jail";
    exec.poststop += "umount /jails/myjail/dev/shm 2>/dev/null || true";
    exec.poststop += "umount /compat/linux/proc 2>/dev/null || true";
    exec.poststop += "umount /compat/linux/sys 2>/dev/null || true";
}
```

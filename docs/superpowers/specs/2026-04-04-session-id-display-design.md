# Session ID Capture & Display

**Date:** 2026-04-04
**Status:** Draft (rev 5 — post round-3 review: roborev/Codex + code-reviewer + architect + contrarian)
**Branch:** `claudechic-session-id`

## Problem

The Claude session_id is captured internally but never surfaced to the user. Users need to see which session they're in at a glance (visual confirmation) and copy the full ID quickly (for resuming via `claudechic -s <id>` or sharing).

## Goals

1. Display the session_id in the StatusFooter with adaptive truncation
2. Add a `/session-id` slash command that prints the full ID and copies to clipboard

## Acceptance Criteria

1. Footer shows the session_id (truncated) before the first response completes (via init message). Requires mocked agent init message in integration test.
2. After `/clear`, footer hides session label immediately and `/session-id` prints "No active session" (session_id cleared before reconnect). Footer remains hidden until new init message arrives.
3. After `_reconnect_sdk()` without resume, footer hides and `/session-id` prints "No active session"
4. After `_reconnect_sdk()` with auto-resume, footer shows the resumed session_id once `_load_and_display_history` completes
5. When a background agent receives an init message or completes a response, the active agent's footer session label does not change
6. Footer hides the session indicator when budget < `MIN_SESSION_LENGTH`
7. `/session-id` prints the full ID even when clipboard copy fails (best-effort copy, guaranteed print). The full ID is printed into the chat stream and is visible in screenshots/screen shares — this is intentional (the value is not sensitive).
8. The `compact-height` footer-hidden mode still allows `/session-id` command as the only access path

## Non-Goals

- Sidebar display (explicitly descoped to keep sidebar clean)
- Changes to the existing agent.py capture logic (already captures from both SDK init and ResultMessage)
- New SDK integration (reads existing `agent.session_id`)
- Click-to-copy on the footer widget (deferred — user-reported click handling bug, see Known Issues for scope)
- ECC session data file persistence (extracted to its own future spec — requires defined consumers, lifecycle, format, and concurrency semantics)
- Session IDs are not sensitive — they require an already-authenticated Claude Code session to use, so no special handling is needed. Existing analytics coupling (`agent.analytics_id` resolves to `session_id`) is unchanged.

## Existing Data Flow (No Changes)

The session_id is already captured in two places in `agent.py`:

```python
# Early capture from SDK init message
if message.subtype == "init" and not self.session_id:
    if isinstance(message.data, dict) and "session_id" in message.data:
        self.session_id = message.data["session_id"]

# Authoritative capture from ResultMessage
self.session_id = message.session_id
```

Both feed `agent.session_id`. The init message provides the value early (before first response); `ResultMessage` overwrites authoritatively after each response. In practice they match. Note: the authoritative assignment happens inside `agent._handle_message` — the redundant defensive set at `app.py` `on_response_complete` (`agent.session_id = event.result.session_id`) merely re-writes the same value. Footer push reads `agent.session_id`, not `event.result.session_id`.

Additionally, `agent.connect(options, resume=session_id)` sets `self.session_id = resume` inside `connect()`. The explicit set in `resume_session` at app.py is for clarity/guarantee, not because `connect()` fails to do it.

Exposed to the app via:

```python
@property
def session_id(self) -> str | None:
    return self._agent.session_id if self._agent else None
```

## Design

### Component Overview

| Component | File | Purpose |
|-----------|------|---------|
| `SessionIndicator` class | `widgets/layout/footer.py` (add to existing) | Passive render target for session_id |
| `StatusFooter.set_session_id()` | `widgets/layout/footer.py` (add method) | State owner — stores `_session_id`, triggers re-render |
| `format_session_id()` | `formatting.py` (add function) | Adaptive truncation |
| `/session-id` command | `commands.py` (add handler + registry entry) | Print full ID + copy to clipboard |

### 1. State Ownership

**`StatusFooter` owns the session_id state**, matching how it owns cwd state today:

```python
# Existing pattern (cwd):
StatusFooter._cwd: str        # state
StatusFooter.set_cwd(cwd)     # public API, stores + triggers re-render
Static("#cwd-label")           # passive render target

# New pattern (session_id):
StatusFooter._session_id: str | None   # state
StatusFooter.set_session_id(id)        # public API, stores + triggers re-render
SessionIndicator("#session-label")     # passive render target
```

`SessionIndicator` is a minimal `Static` subclass — it has no `set_session_id()` method of its own. It is a passive label that `_render_cwd_label` writes to, exactly like the cwd `Static` label.

### 2. SessionIndicator Widget

**File:** `claudechic/widgets/layout/footer.py` (colocated with `PermissionModeLabel`, `ModelLabel`, `ViModeLabel`)

A `Static` subclass. Minimal — only exists for CSS targeting (`#session-label`) and type-safe `query_one_optional`. No methods beyond what `Static` provides.

```python
class SessionIndicator(Static):
    """Passive session ID label in the footer. Content set by StatusFooter._render_cwd_label."""
    pass
```

**CSS ID:** `#session-label`
**Styling:** Inherits from `.footer-label` class (which provides `padding: 0 1`, `color: $text-muted`, `width: auto`). A dedicated `#session-label` CSS rule is optional — only needed if styling diverges from `.footer-label` in the future. No pointer cursor (no click interaction).

### 3. Footer Layout

The actual `StatusFooter.compose()` order is:

```
ViModeLabel | ModelLabel | · | PermissionModeLabel | spacer | ProcessIndicator | ContextBar | CPUBar | cwd-label | session-label | branch-label
```

The `SessionIndicator` is placed between `cwd-label` and `branch-label`:

```python
yield Static("", id="cwd-label", classes="footer-label hidden")
yield SessionIndicator("", id="session-label", classes="footer-label hidden")
yield Static("", id="branch-label", classes="footer-label")
```

**`StatusFooter.set_session_id()` method:**

```python
def set_session_id(self, session_id: str | None) -> None:
    """Update session_id value and schedule re-render."""
    self._session_id = session_id
    self.call_after_refresh(self._render_cwd_label)
```

This mirrors `set_cwd()` exactly.

**Budget computation:**

The existing `_render_cwd_label()` method is **kept as-is** (no rename — avoids blast radius across 6+ call sites, public API, and tests). Session label budget logic is added inside it with a comment: `# Also renders session label (budget split).`

**New constant** in `footer.py` alongside `CWD_PADDING`:

```python
SESSION_PADDING = 2  # CSS "padding: 0 1" on .footer-label = 1 left + 1 right
```

**Budget exclusion set** — the `used` sum must exclude `session-label` in addition to `cwd-label` and `footer-spacer`:

```python
used = sum(
    child.outer_size.width
    for child in footer_content.children
    if child.id not in ("cwd-label", "session-label", "footer-spacer")
)
```

**Session label gets a fixed budget of 12 characters** (enough for 11 hex chars + `…`, collision-free for any realistic session count). Cwd gets the remainder. `SESSION_PADDING` is only subtracted when session_id is present:

```python
# Only subtract SESSION_PADDING when session_id is present
has_session = bool(self._session_id)
total_budget = app_width - used - CWD_PADDING - (SESSION_PADDING if has_session else 0)

if not self._cwd and not has_session:
    # both hidden
elif not self._cwd:
    session_budget = min(12, total_budget)
elif not has_session:
    cwd_budget = total_budget
    session_budget = 0
else:
    session_budget = min(12, total_budget)
    cwd_budget = total_budget - session_budget
```

**Widget query safety:** `query_one_optional("#session-label", SessionIndicator)` may return `None` during early mount (before `compose()` completes). If `None`, skip session rendering and give all budget to cwd. This matches how the existing code handles `query_one_optional("#cwd-label")`.

**Hiding thresholds:**
- `MIN_SESSION_LENGTH = 8` (imported from `formatting.py`) — below this, hide the session indicator
- `MIN_CWD_LENGTH = 10` — existing threshold, unchanged
- When `compact-height` CSS class is active, the entire footer is hidden (existing behavior). The `/session-id` command remains the only access path.

### 4. format_session_id()

**File:** `claudechic/formatting.py`

**New constant:**

```python
MIN_SESSION_LENGTH = 8   # Below this budget, hide session_id entirely
```

**Function:**

```python
def format_session_id(session_id: str, budget: int) -> str:
    """Format session ID with adaptive truncation.

    Truncates from the end (suffix removed, prefix preserved).
    This is the OPPOSITE direction from format_cwd(), which truncates
    from the front (preserving the rightmost path segments). For UUIDs,
    the prefix is preserved because no segment is inherently more
    meaningful, and prefix-matching is the common convention (like git
    short hashes).

    Uses U+2026 (…) for the ellipsis character, consistent with
    format_cwd().

    Precondition: session_id is non-empty. Caller handles None/empty
    by hiding the widget before calling this function.

    Rules:
        budget >= len(session_id)  -> full ID (format-agnostic, not hardcoded to 36)
        budget >= 2                -> first (budget - 1) chars + "…"
        budget < 2                 -> "…" (defensive only — MIN_SESSION_LENGTH prevents this in practice)
    """
```

### 5. Update Triggers

**Active-agent gating rule.** The footer-push call (`status_footer.set_session_id()`) within per-agent event handlers MUST check `event.agent_id == self.agent_mgr.active_id` before executing. This does NOT gate the entire handler — only the footer update line. Background agent events still update `agent.session_id` on the agent model; the footer is refreshed when that agent becomes active via `on_agent_switched`.

**Clear before reconnect rule.** All session-reset paths MUST set `agent.session_id = None` before disconnecting and reconnecting. This includes `_start_new_session` (`/clear`) AND `_reconnect_sdk()` when reconnecting without a resume ID. The init message early-capture path (`if not self.session_id`) is guarded by falsiness — if the old ID lingers, the new init won't overwrite it, leaving a stale ID visible until the first `ResultMessage`.

The footer is updated via `status_footer.set_session_id()` at these app.py locations:

| Trigger | Location | What happens | Code change | Active-agent gate? |
|---------|----------|--------------|-------------|-------------------|
| **`on_response_complete`** | Adjacent to `refresh_context()` | Push `agent.session_id` to footer. Note: `agent.session_id` is already set by the agent layer — the app-level set is redundant/defensive. | Add footer push line | Yes — only if `event.agent_id == active_id` |
| **`on_system_notification` (init subtype)** | **New `elif subtype == "init":` branch.** Also add `"init"` to the suppression set at the catch-all guard to prevent duplicate display. | Push `agent.session_id` on first connect. Prevents blank state until first response. | Add new branch + footer push | Yes — only if `event.agent_id == active_id` |
| **`on_agent_switched`** | In the existing footer-update block | Push `new_agent.session_id` to footer. | Add footer push line | N/A — this IS the switch event |
| **`resume_session()`** | After `_reconnect_agent()` completes | Push session_id to footer. This is async — user can switch agents before it completes. Guard must compare **captured** `agent.id` against **live** `self.agent_mgr.active_id` (not `self._agent.id`, which may have changed). Note: init handler may also fire, producing an idempotent double-push. | Add guarded footer push | Yes — required because async |
| **`_start_new_session` (`/clear`)** | Before `disconnect()` | **Required code change:** add `agent.session_id = None` (does NOT exist today — `disconnect()` and `connect()` without resume do not clear it). Then push `None` to footer. | Add `session_id = None` + footer push | N/A — explicit user action |
| **`_reconnect_sdk()` (no resume)** | In the `else` branch | `agent.session_id = None` already exists at app.py:1831. Push `None` to footer. | Add footer push line | N/A — explicit user action |
| **`_reconnect_sdk()` (with resume)** | In the `if resume_id:` branch, after `_load_and_display_history` | Push `agent.session_id` to footer. Guard with `if agent is self._agent` (existing pattern at app.py:1810). Without this, footer shows stale/no session_id until first response. | Add guarded footer push | Yes — guard with `agent is self._agent` |

### 6. /session-id Command

**File:** `claudechic/commands.py`

**Registry entry** (added to `COMMANDS` list for autocomplete + help):

```python
("/session-id", "Show and copy session ID", [])
```

**Handler behavior:**
1. Prints the full session_id as a system message in the chat view (not sent to Claude). This is the **guaranteed** output — always printed regardless of clipboard state.
2. Attempts clipboard copy via `app.copy_to_clipboard()` (best-effort — `copy_to_clipboard()` returns no status and swallows errors on Linux fallback)
3. Uses `app.notify("Copied to clipboard")` for feedback. Note: this is best-effort notification — the underlying copy may silently fail in SSH/headless sessions. The printed chat message ensures the user always has access to the full ID.
4. Multi-agent clarity — includes agent name:
   ```
   Session ID (main): a1b2c3d4-e5f6-7890-abcd-ef1234567890
   ```
5. If no session_id available: prints `"No active session"`

## Data Flow Diagram

```
SDK init message -------> agent.session_id (early)
SDK ResultMessage ------> agent.session_id (authoritative, also set defensively in app.py)
/clear -----------------> agent.session_id = None  [NEW CODE — does not exist today]
_reconnect_sdk(no resume) -> agent.session_id = None  [existing]
_reconnect_sdk(with resume) -> agent.session_id set via _load_and_display_history
agent switch ------------> footer reads new_agent.session_id
                              |
                              +---> StatusFooter.set_session_id()
                              |       stores _session_id, calls _render_cwd_label
                              |       _render_cwd_label -> format_session_id(id, budget)
                              |       SessionIndicator hidden when None/empty or budget < MIN_SESSION_LENGTH
                              |       [gated: only if agent is active]
                              |
                              +---> /session-id command
                                      prints full ID + app.notify("Copied to clipboard")
```

## Files Modified

| File | Change |
|------|--------|
| `claudechic/formatting.py` | Add `MIN_SESSION_LENGTH` constant, `format_session_id()` function |
| `claudechic/widgets/layout/footer.py` | Add `SessionIndicator` class (passive), `SESSION_PADDING` constant, `_session_id` field, `set_session_id()` method, yield in `compose()`, add session budget logic inside `_render_cwd_label` |
| `claudechic/commands.py` | Add `/session-id` to `COMMANDS` registry, add handler function |
| `claudechic/app.py` | Add `agent.session_id = None` in `_start_new_session` (bug fix), call `status_footer.set_session_id()` at 7 trigger points, add `elif subtype == "init":` branch + suppression in `on_system_notification` |
| `claudechic/styles.tcss` | Add `#session-label` rule if needed (`.footer-label` class may suffice) |
| `tests/test_widgets.py` | Add `SessionIndicator` widget tests, `format_session_id` unit tests |
| `CLAUDE.md` | Add `/session-id` to Commands section, `SessionIndicator` to Widget Hierarchy |

## Known Issues

- **Click-to-copy deferred due to user-reported click handling bug.** The user reports that clickable labels do not work in their environment. Note: `ClickableLabel` subclasses (`PermissionModeLabel`, `ModelLabel`) and `IndicatorWidget` subclasses (`ContextBar`, `CPUBar`) all implement `on_click()` successfully elsewhere in the footer. The bug may be environment-specific (terminal emulator, tmux, SSH) rather than a systemic Textual issue. Investigation should start with CSS `pointer-events`, layout containment, or mouse event propagation. Until diagnosed, click-to-copy is not implemented — the `/session-id` command is the reliable copy path.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `/session-id` before init message arrives | Prints "No active session" |
| `/session-id` during reconnect window | Prints last known session_id if available, "No active session" if None |
| `/clear` then immediate `/session-id` | Prints "No active session" (session_id cleared before reconnect) |
| `_reconnect_sdk()` without resume then `/session-id` | Prints "No active session" (session_id already None at app.py:1831) |
| Reconnect failure after `/clear` | session_id remains None, footer hidden, `/session-id` prints "No active session". User must use `/resume` to recover old session. |
| Footer render with None or empty session_id | Widget hidden, no error |
| `format_session_id` called with empty string | Precondition violation — caller must not call with empty string (treat as None, hide widget) |
| `_render_cwd_label` runs before `SessionIndicator` mounted | `query_one_optional` returns None, skip session rendering, give all budget to cwd |

## Implementation Stages

Ordered by dependency and risk — **lifecycle correctness first**, then UI, then wiring:

1. **Lifecycle fixes + `format_session_id()`** — Add `agent.session_id = None` in `_start_new_session` before `disconnect()` (bug fix). Add `format_session_id()` pure function + unit tests. No UI changes yet.
2. **`SessionIndicator` widget + footer layout + budget** — Add passive `SessionIndicator` class, `_session_id` field, `set_session_id()` method, `SESSION_PADDING` constant, budget logic inside `_render_cwd_label`, yield in `compose()`. Widget tests.
3. **Event wiring + `/session-id` command** — 7 trigger points with active-agent gating. Command handler + COMMANDS registry. Split into reviewable sub-PRs if needed:
   - 3a: Event wiring (triggers 1-7) + integration tests
   - 3b: `/session-id` command + command tests + CLAUDE.md update

## Future Work

- **ECC session data file persistence** — session_id, per-agent mapping, and timestamps persisted to `~/.claude/session-data/`. Requires its own spec covering: data format (JSON/YAML, not markdown), consumers, write triggers, debouncing, concurrency, cleanup/GC, and integration with existing `/save-session` and `/resume-session` skills.
- **Click-to-copy** — add `on_click()` to `SessionIndicator` once clickable labels are fixed.
- **Human-readable aliases** — optionally derive a two-word name from the UUID (like Docker container names) for easier recognition. Could complement or replace the truncated hex display.

## Testing Strategy

1. **Unit tests for `format_session_id()`** — budget edge cases (0, 1, 2, 8, 35, 36, 100), various ID lengths, ellipsis character (`…`) correctness
2. **Widget tests for `SessionIndicator`** — render states (None hides, empty string hides, short ID, full UUID), `set_session_id()` on StatusFooter triggers re-render
3. **Command test for `/session-id`** — output format with agent name, None case, COMMANDS registry entry exists
4. **Footer layout test** — session indicator participates in budget computation, hides at narrow widths, shows at wide widths, conditional budget when one label absent, medium-width regression (12-char session reservation doesn't suppress cwd prematurely). Use ChatApp-level test (not WidgetTestApp) for reliable width-budget verification.
5. **Integration test** — session_id flows from agent through to footer display after `on_response_complete`
6. **Failure-prone flow tests:**
   - `/clear` — session_id is None, footer hidden immediately, stays hidden until new init
   - `_reconnect_sdk()` without resume — session_id is None, footer hidden
   - `_reconnect_sdk()` with auto-resume — footer shows resumed session_id after `_load_and_display_history`
   - Init-event display after reconnect — footer shows new session_id immediately
   - Background agent completes response while foreground agent is active — foreground footer must not change
   - Background agent receives init message while foreground agent is active — foreground footer must not change
   - `/session-id` invoked before init message arrives — prints "No active session"
   - Rapid agent switching — footer shows correct session_id for the landed-on agent
   - `resume_session()` completes after user has already switched to another agent — stale resume does not overwrite active footer

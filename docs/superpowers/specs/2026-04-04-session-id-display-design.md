# Session ID Capture & Display

**Date:** 2026-04-04
**Status:** Draft (rev 4 — post round-2 review: roborev/Codex + code-reviewer + architect + contrarian)
**Branch:** `claudechic-session-id`

## Problem

The Claude session_id is captured internally but never surfaced to the user. Users need to see which session they're in at a glance (visual confirmation) and copy the full ID quickly (for resuming via `claudechic -s <id>` or sharing).

## Goals

1. Display the session_id in the StatusFooter with adaptive truncation
2. Add a `/session-id` slash command that prints the full ID and copies to clipboard

## Acceptance Criteria

1. Footer shows the session_id (truncated) before the first response completes (via init message). Requires mocked agent init message in integration test.
2. After `/clear`, `/session-id` prints "No active session" (session_id cleared before reconnect)
3. After `_reconnect_sdk()` without resume, `/session-id` prints "No active session"
4. When a background agent receives an init message or completes a response, the active agent's footer session label does not change
5. Footer hides the session indicator when budget < `MIN_SESSION_LENGTH`
6. `/session-id` prints the full ID even when clipboard copy fails (best-effort copy, guaranteed print)
7. The `compact-height` footer-hidden mode still allows `/session-id` command as the only access path

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

Both feed `agent.session_id`. The init message provides the value early (before first response); `ResultMessage` overwrites authoritatively after each response. In practice they match.

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
| `SessionIndicator` class | `widgets/layout/footer.py` (add to existing) | Footer display |
| `format_session_id()` | `formatting.py` (add function) | Adaptive truncation |
| `/session-id` command | `commands.py` (add handler + registry entry) | Print full ID + copy to clipboard |

### 1. SessionIndicator Widget

**File:** `claudechic/widgets/layout/footer.py` (colocated with `PermissionModeLabel`, `ModelLabel`, `ViModeLabel`)

A `Static` subclass that displays the session_id with adaptive truncation. Minimal — roughly 20-30 lines, consistent with the existing small label classes in this file.

**Rendering:**
- Active state: `"a1b2c3d4…"` (adaptive truncation based on available width, no prefix — position and dim styling provide context)
- Empty state: hidden entirely via `add_class("hidden")` (consistent with how `cwd-label` handles the empty case)
- The caller treats both `None` and empty string `""` identically → hide the widget

**Public API:**

```python
class SessionIndicator(Static):
    """Display session ID in the footer."""

    def set_session_id(self, session_id: str | None) -> None:
        """Update the displayed session_id and schedule footer re-render.

        None or empty string → hide widget.
        Non-empty string → format and display.
        """
```

No `on_click()` handler — click-to-copy is deferred until clickable labels are debugged. The `/session-id` command provides the copy-to-clipboard path.

**CSS ID:** `#session-label`
**Styling:** Dim text by default, matching the cwd label style. No pointer cursor (no click interaction).

### 2. Footer Layout

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

**Budget computation:**

The existing `_render_cwd_label()` method is **kept as-is** (no rename — avoids blast radius across 6+ call sites, public API, and tests). Session label budget logic is added inside it with a comment: `# Also renders session label (budget split).`

**New constant** in `footer.py` alongside `CWD_PADDING`:

```python
SESSION_PADDING = 2  # CSS "padding: 0 1" on #session-label = 1 left + 1 right
```

**Budget exclusion set** — the `used` sum must exclude `session-label` in addition to `cwd-label` and `footer-spacer`:

```python
used = sum(
    child.outer_size.width
    for child in footer_content.children
    if child.id not in ("cwd-label", "session-label", "footer-spacer")
)
```

**Session label gets a fixed budget of 12 characters** (enough for 11 hex chars + `…`, collision-free for any realistic session count). Cwd gets the remainder:

```python
total_budget = app_width - used - CWD_PADDING - SESSION_PADDING

# Session label: fixed 12-char budget (or full ID if shorter)
# Cwd: gets the remainder
if no cwd and no session_id:
    both hidden
elif no cwd:
    session_budget = min(12, total_budget)
elif no session_id:
    cwd_budget = total_budget
    session_budget = 0
else:
    session_budget = min(12, total_budget)
    cwd_budget = total_budget - session_budget
```

**Hiding thresholds:**
- `MIN_SESSION_LENGTH = 8` — below this, hide the session indicator (too truncated to be useful)
- `MIN_CWD_LENGTH = 10` — existing threshold, unchanged
- When `compact-height` CSS class is active, the entire footer is hidden (existing behavior, no change needed). The `/session-id` command remains the only access path in this mode.

### 3. format_session_id()

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
        budget < 2                 -> "…"
    """
```

### 4. Update Triggers

**Active-agent gating rule.** The footer-push call (`set_session_id()`) within per-agent event handlers MUST check `event.agent_id == self.agent_mgr.active_id` before executing. This does NOT gate the entire handler — only the footer update line. Background agent events still update `agent.session_id` on the agent model; the footer is refreshed when that agent becomes active via `on_agent_switched`.

**Clear before reconnect rule.** All session-reset paths MUST set `agent.session_id = None` before disconnecting and reconnecting. This includes `_start_new_session` (`/clear`) AND `_reconnect_sdk()` when reconnecting without a resume ID. The init message early-capture path (`if not self.session_id`) is guarded by falsiness — if the old ID lingers, the new init won't overwrite it, leaving a stale ID visible until the first `ResultMessage`.

The `SessionIndicator` is updated in these app.py locations:

| Trigger | Location | What happens | Active-agent gate? |
|---------|----------|--------------|-------------------|
| **`on_response_complete`** | Textual message handler, adjacent to `refresh_context()` | Push `agent.session_id` to widget. Authoritative source. | Yes — only push to footer if `event.agent_id == active_id` |
| **`on_system_notification` (init subtype)** | Textual message handler. **New `elif subtype == "init":` branch** (does not exist today). | Push `agent.session_id` on first connect. Prevents blank state until first response. | Yes — only push to footer if `event.agent_id == active_id` |
| **`on_agent_switched`** | In the existing footer-update block | Push `new_agent.session_id` to widget. | N/A — this IS the switch event |
| **`resume_session()`** | After reconnection completes | Push session_id to widget. Note: `resume_session()` is async — user can switch agents before it completes. Guard the footer push with `agent.id == active_id` check before writing. | Yes — guard needed because async |
| **`_start_new_session` (`/clear`)** | Before `disconnect()` | Set `agent.session_id = None` BEFORE reconnect, then push `None` to widget (resets to hidden). | N/A — explicit user action on active agent |
| **`_reconnect_sdk()` (no resume)** | When reconnecting SDK state without resuming | `agent.session_id = None` is already set at app.py:1831. Push `None` to widget. | N/A — explicit user action on active agent |

All update paths call `set_session_id()` which internally calls `call_after_refresh(self._render_cwd_label)` per the existing footer convention.

### 5. /session-id Command

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
SDK ResultMessage ------> agent.session_id (authoritative)
/clear -----------------> agent.session_id = None
_reconnect_sdk(no resume) -> agent.session_id = None
agent switch ------------> footer reads new_agent.session_id
                              |
                              +---> StatusFooter.SessionIndicator
                              |       set_session_id() -> format_session_id(id, budget)
                              |       hidden when None/empty or budget < MIN_SESSION_LENGTH
                              |       [gated: only if agent is active]
                              |
                              +---> /session-id command
                                      prints full ID + app.notify("Copied to clipboard")
```

## Files Modified

| File | Change |
|------|--------|
| `claudechic/formatting.py` | Add `MIN_SESSION_LENGTH` constant, `format_session_id()` function |
| `claudechic/widgets/layout/footer.py` | Add `SessionIndicator` class, `SESSION_PADDING` constant, yield in `compose()`, add session budget logic inside `_render_cwd_label` |
| `claudechic/commands.py` | Add `/session-id` to `COMMANDS` registry, add handler function |
| `claudechic/app.py` | Call `set_session_id()` at 6 trigger points (see Section 4), add `elif subtype == "init":` branch in `on_system_notification` |
| `claudechic/styles.tcss` | Add `#session-label` styling (dim, padding) |
| `tests/test_widgets.py` | Add `SessionIndicator` widget tests, `format_session_id` tests |
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

## Implementation Stages

Ordered by dependency — session lifecycle correctness before UI:

1. **`format_session_id()` + unit tests** — pure function, no dependencies
2. **`SessionIndicator` widget + footer layout + budget** — colocate in `footer.py`, add session budget logic inside `_render_cwd_label`, widget tests. Ensure `_reconnect_sdk` already sets `session_id = None` (it does at app.py:1831).
3. **Event wiring + `/session-id` command + all tests** — 6 triggers with active-agent gating, command handler, COMMANDS registry, integration tests, failure-prone flow tests

## Future Work

- **ECC session data file persistence** — session_id, per-agent mapping, and timestamps persisted to `~/.claude/session-data/`. Requires its own spec covering: data format (JSON/YAML, not markdown), consumers, write triggers, debouncing, concurrency, cleanup/GC, and integration with existing `/save-session` and `/resume-session` skills.
- **Click-to-copy** — add `on_click()` to `SessionIndicator` once clickable labels are fixed.
- **Human-readable aliases** — optionally derive a two-word name from the UUID (like Docker container names) for easier recognition. Could complement or replace the truncated hex display.

## Testing Strategy

1. **Unit tests for `format_session_id()`** — budget edge cases (0, 1, 2, 8, 35, 36, 100), various ID lengths, ellipsis character (`…`) correctness
2. **Widget tests for `SessionIndicator`** — render states (None hides, empty string hides, short ID, full UUID), `set_session_id()` triggers re-render
3. **Command test for `/session-id`** — output format with agent name, None case, COMMANDS registry entry exists
4. **Footer layout test** — session indicator participates in budget computation, hides at narrow widths, shows at wide widths, conditional budget when one label absent. Use ChatApp-level test (not WidgetTestApp) for reliable width-budget verification.
5. **Integration test** — session_id flows from agent through to footer display after `on_response_complete`
6. **Failure-prone flow tests:**
   - `/clear` before first response — session_id must be None, footer hidden
   - `_reconnect_sdk()` without resume — session_id must be None, footer hidden
   - Init-event display after reconnect — footer shows new session_id immediately
   - Background agent completes response while foreground agent is active — foreground footer must not change
   - Background agent receives init message while foreground agent is active — foreground footer must not change
   - `/session-id` invoked before init message arrives — prints "No active session"
   - Rapid agent switching — footer shows correct session_id for the landed-on agent
   - `resume_session()` completes after user has already switched to another agent — stale resume does not overwrite active footer

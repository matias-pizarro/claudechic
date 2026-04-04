# Session ID Capture & Display

**Date:** 2026-04-04
**Status:** Draft (rev 3 — post-roborev/Codex review)
**Branch:** `claudechic-session-id`

## Problem

The Claude session_id is captured internally but never surfaced to the user. Users need to see which session they're in at a glance (visual confirmation) and copy the full ID quickly (for resuming via `claudechic -s <id>` or sharing).

## Goals

1. Display the session_id in the StatusFooter with adaptive truncation
2. Add a `/session-id` slash command that prints the full ID and copies to clipboard
3. Persist session_id to the ECC session data file (separate spec — see Future Work)

## Acceptance Criteria

1. Footer shows the session_id (truncated) before the first response completes (via init message)
2. `/session-id` never shows a stale ID after `/clear` — the underlying `agent.session_id` is set to `None` before reconnect
3. Background agent events never update the active agent's footer — all update triggers gate on `event.agent_id == active_id`
4. Footer hides the session indicator gracefully at narrow widths (< `MIN_SESSION_LENGTH` budget)
5. `/session-id` prints the full ID even when clipboard copy fails (best-effort copy, guaranteed print)
6. The `compact-height` footer-hidden mode still allows `/session-id` command as the only access path

## Non-Goals

- Sidebar display (explicitly descoped to keep sidebar clean)
- Changes to the existing agent.py capture logic (already captures from both SDK init and ResultMessage)
- New SDK integration (reads existing `agent.session_id`)
- Click-to-copy on the footer widget (deferred — user-reported click handling bug, see Known Issues for scope)
- ECC session data file persistence (extracted to its own future spec — requires defined consumers, lifecycle, format, and concurrency semantics)

## Existing Data Flow (No Changes)

The session_id is already captured in two places in `agent.py`:

```python
# Early capture from SDK init message (agent.py:589-592)
if message.subtype == "init" and not self.session_id:
    if isinstance(message.data, dict) and "session_id" in message.data:
        self.session_id = message.data["session_id"]

# Authoritative capture from ResultMessage (agent.py:598)
self.session_id = message.session_id
```

Both feed `agent.session_id`. The init message provides the value early (before first response); `ResultMessage` overwrites authoritatively after each response. In practice they match.

Exposed to the app via:

```python
# app.py:258-264
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

**Public API:**

```python
class SessionIndicator(Static):
    """Display session ID in the footer."""

    def set_session_id(self, session_id: str | None) -> None:
        """Update the displayed session_id and schedule footer re-render."""
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

The existing `_render_cwd_label()` computes a budget by summing all non-cwd, non-spacer sibling widths and subtracting from `app_width`. This method becomes `_render_variable_labels()` and splits the remaining budget between cwd and session:

```
total_budget = app_width - used_by_fixed_widgets - CWD_PADDING - SESSION_PADDING

# Conditional split: if one label has no content, the other gets the full budget
if no cwd and no session_id:
    both hidden
elif no cwd:
    session_budget = total_budget
    cwd_budget = 0
elif no session_id:
    cwd_budget = total_budget
    session_budget = 0
else:
    session_budget = total_budget // 3          (integer division, session gets the smaller share)
    cwd_budget = total_budget - session_budget  (cwd gets the remainder — more informative)
```

**Hiding thresholds:**
- `MIN_SESSION_LENGTH = 8` — below this, hide the session indicator (too truncated to be useful)
- `MIN_CWD_LENGTH = 10` — existing threshold, unchanged
- When both are present but total budget is too small for either minimum, hide both (prefer clean over cramped)
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

    Rules:
        budget >= len(session_id)  -> full ID (format-agnostic, not hardcoded to 36)
        budget >= 2                -> first (budget - 1) chars + "…"
        budget < 2                 -> "…"
    """
```

The function accepts `str` (not `str | None`). The caller (`SessionIndicator.set_session_id`) handles `None` by hiding the widget.

### 4. Update Triggers

**Critical rule: active-agent gating.** All per-agent event handlers (`on_response_complete`, `on_system_notification`) MUST check `event.agent_id == self.agent_mgr.active_id` before updating the footer. Background agent events update only `agent.session_id` on the agent model — the footer is only refreshed when that agent becomes active via `on_agent_switched`. This prevents a background agent's init/response from overwriting the visible footer.

**Critical rule: clear before reconnect.** `_start_new_session` MUST set `agent.session_id = None` before calling `disconnect()` and reconnecting. The init message early-capture path (`if not self.session_id`) is guarded by falsiness — if the old ID lingers, the new init won't overwrite it, leaving a stale ID visible until the first `ResultMessage`.

The `SessionIndicator` is updated in these app.py locations:

| Trigger | Location | What happens | Active-agent gate? |
|---------|----------|--------------|-------------------|
| **`on_response_complete`** | Textual message handler (~line 1363), adjacent to `refresh_context()` | Push `agent.session_id` to widget. Authoritative source. | Yes — only update footer if `event.agent_id == active_id` |
| **`on_system_notification` (init subtype)** | Textual message handler (~line 1139) | Push `agent.session_id` on first connect. Prevents blank state until first response. | Yes — only update footer if `event.agent_id == active_id` |
| **`on_agent_switched`** | (~line 2383), in the existing footer-update block | Push `new_agent.session_id` to widget. | N/A — this IS the switch event |
| **Session resume** | `resume_session()` (~line 1416) | Push session_id after reconnection. | N/A — explicit user action on active agent |
| **`_start_new_session` (`/clear`)** | (~line 2002) | Set `agent.session_id = None` BEFORE reconnect, then push `None` to widget (resets to hidden). | N/A — explicit user action on active agent |

All update paths call `set_session_id()` which internally calls `call_after_refresh(self._render_variable_labels)` per the existing footer convention.

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
                              |
                              +---> StatusFooter.SessionIndicator
                              |       set_session_id() -> format_session_id(id, budget)
                              |       hidden when None or budget < MIN_SESSION_LENGTH
                              |
                              +---> /session-id command
                                      prints full ID + app.notify("Copied to clipboard")
```

## Files Modified

| File | Change |
|------|--------|
| `claudechic/formatting.py` | Add `MIN_SESSION_LENGTH` constant, `format_session_id()` function |
| `claudechic/widgets/layout/footer.py` | Add `SessionIndicator` class, yield in `compose()`, update `_render_cwd_label` -> `_render_variable_labels` with budget split |
| `claudechic/commands.py` | Add `/session-id` to `COMMANDS` registry, add handler function |
| `claudechic/app.py` | Call `set_session_id()` at 5 trigger points (see Section 4) |
| `claudechic/styles.tcss` | Add `#session-label` styling (dim, padding) |
| `claudechic/widgets/__init__.py` | Re-export `SessionIndicator` if needed |

## Known Issues

- **Click-to-copy deferred due to user-reported click handling bug.** The user reports that clickable labels do not work in their environment. Note: `ClickableLabel` subclasses (`PermissionModeLabel`, `ModelLabel`) and `IndicatorWidget` subclasses (`ContextBar`, `CPUBar`) all implement `on_click()` successfully elsewhere in the footer. The bug may be environment-specific (terminal emulator, tmux, SSH) rather than a systemic Textual issue. Investigation should start with CSS `pointer-events`, layout containment, or mouse event propagation. Until diagnosed, click-to-copy is not implemented — the `/session-id` command is the reliable copy path.

## Security & Privacy

Session IDs are opaque UUIDs that can be used to resume sessions (`claudechic -s <id>`). They are not authentication tokens — resuming requires an already-authenticated Claude Code session. However, they should be treated with reasonable care:
- The footer display is truncated by default, reducing exposure in screenshots and screen shares
- The `/session-id` command prints the full ID only when explicitly requested
- No session IDs are written to log files by this feature (existing SDK logging is unchanged)

## Error Handling

| Scenario | Behavior |
|----------|----------|
| `/session-id` before init message arrives | Prints "No active session" |
| `/session-id` during reconnect window | Prints last known session_id if available, "No active session" if None |
| `/clear` then immediate `/session-id` | Prints "No active session" (session_id cleared before reconnect) |
| Footer render with None session_id | Widget hidden, no error |
| `format_session_id` called with empty string | Returns `"…"` (budget < 2 path) |

## Implementation Stages

Ordered by dependency — session lifecycle correctness before UI:

1. **State reset semantics** — ensure `agent.session_id = None` in `_start_new_session` before reconnect
2. **`format_session_id()` + unit tests** — pure function, no dependencies
3. **`SessionIndicator` widget + widget tests** — colocate in `footer.py`, render states
4. **Footer layout + budget split** — `_render_variable_labels`, conditional budget, hiding thresholds
5. **Event wiring in `app.py`** — all 5 triggers with active-agent gating
6. **`/session-id` command + registry** — handler, COMMANDS entry, command tests
7. **Integration tests** — end-to-end flow, failure-prone paths

## Future Work

- **ECC session data file persistence** — session_id, per-agent mapping, and timestamps persisted to `~/.claude/session-data/`. Requires its own spec covering: data format (JSON/YAML, not markdown), consumers, write triggers, debouncing, concurrency, cleanup/GC, and integration with existing `/save-session` and `/resume-session` skills.
- **Click-to-copy** — add `on_click()` to `SessionIndicator` once clickable labels are fixed.
- **Human-readable aliases** — optionally derive a two-word name from the UUID (like Docker container names) for easier recognition. Could complement or replace the truncated hex display.

## Testing Strategy

1. **Unit tests for `format_session_id()`** — budget edge cases (0, 1, 2, 8, 35, 36, 100), various ID lengths, ellipsis character (`…`) correctness, empty string input
2. **Widget tests for `SessionIndicator`** — render states (None hides, short ID, full UUID), `set_session_id()` triggers re-render
3. **Command test for `/session-id`** — output format with agent name, None case, COMMANDS registry entry exists
4. **Footer layout test** — session indicator participates in budget computation, hides at narrow widths, shows at wide widths, conditional budget when one label absent
5. **Integration test** — session_id flows from agent through to footer display after `on_response_complete`
6. **Failure-prone flow tests:**
   - `/clear` before first response — session_id must be None, footer hidden
   - Init-event display after reconnect — footer shows new session_id immediately
   - Background agent events while foreground agent is active — footer must not change
   - `/session-id` invoked before init message arrives — prints "No active session"
   - Rapid agent switching — footer shows correct session_id for the landed-on agent

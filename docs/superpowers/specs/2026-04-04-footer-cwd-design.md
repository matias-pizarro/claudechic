# Footer CWD Display

**Date:** 2026-04-04
**Status:** Revised (v12)
**Revision:** v10 + roborev/codex eighth review fixes

## Goal

Display the active agent's current working directory in the StatusFooter, dynamically sized to the terminal width. Hidden when computed budget < `MIN_CWD_LENGTH` or cwd is empty.

## Non-Goals

- Does not replace the sidebar's per-agent cwd display.
- Does not change sidebar's `max_cwd_length = 20`.
- Does not add cwd editing or navigation — display only.
- POSIX paths only. Claude Code requires Unix (macOS, Linux, FreeBSD). No Windows support exists in the project.

## Success Criteria

- Cwd label visible when computed budget >= `MIN_CWD_LENGTH` (10). Hidden otherwise.
- Cwd never causes overflow: the cwd label's `outer_size.width` + sum of sibling `outer_size.width` <= `app.size.width`. (Branch/model labels are unbounded by existing design — this feature only controls cwd.) Verified by worst-case acceptance test at widths 60/120/200.
- Sidebar cwd unchanged except improved segment truncation.
- All existing tests pass.
- Non-git directory: cwd displays normally; branch label shows "detached" (existing `get_git_branch()` behavior).
- Detached HEAD: same as non-git — branch shows "detached", cwd unaffected.

## Key Design Decisions

### 1. Centralized invalidation via deferred `_render_cwd_label()`

Every footer method that mutates a widget's content or visibility appends `self.call_after_refresh(self._render_cwd_label)`:

| Footer method | What changes |
|---|---|
| `watch_branch()` | `#branch-label` |
| `watch_model()` | `#model-label` |
| `watch_permission_mode()` | `#permission-mode-label` |
| `update_processes()` | `ProcessIndicator` |
| `update_vi_mode()` | `ViModeLabel` |
| `set_cwd()` | `_cwd` value |

**All calls are deferred via `call_after_refresh`.** This ensures `outer_size.width` reflects post-layout sibling dimensions, not stale pre-mutation values. Textual coalesces multiple deferred callbacks within a single frame, so rapid successive calls (e.g., model + branch changing in one tick) result in one render.

**Exhaustive analysis of footer children width variability:**

| Widget | Width behavior | Invalidation needed? |
|---|---|---|
| `ViModeLabel` | Toggles hidden/visible (0 or ~8 chars) | Yes — `update_vi_mode()` |
| `ModelLabel` | Variable text ("Sonnet 4.6", "connecting...") | Yes — `watch_model()` |
| `.footer-sep` | Fixed "·" (1 char + padding) | No — never changes |
| `PermissionModeLabel` | Variable text ("Auto-edit: off/on", "Plan mode") | Yes — `watch_permission_mode()` |
| `#footer-spacer` | `1fr` flex — absorbs remaining space | Excluded from budget calc |
| `ProcessIndicator` | Toggles hidden/visible; width varies with digit count ("⚙ 1" vs "⚙ 12") | Yes — `update_processes()` |
| `ContextBar` | **Fixed 10-char bar** (always renders exactly `bar_width=10`) | **No** — constant width |
| `CPUBar` | **Fixed "CPU XXX%"** (always 8 chars) | **No** — constant width |
| `#cwd-label` | Our new widget | Excluded from budget calc |
| `#branch-label` | Variable text ("⎇ main", "⎇ feat/...") | Yes — `watch_branch()` |

`ContextBar` and `CPUBar` have constant rendered widths (`bar_width=10` and `"CPU %3.0f%%"=8` respectively). Their content changes (different percentages, colors) but their character width does not. No invalidation hook is needed for them.

**Safeguard:** Behavioral integration tests (Step 3b) verify that after each variable-width mutator fires, the cwd label reflects the correct budget after `await pilot.pause()`. A docstring on `_render_cwd_label` documents the convention.

### 2. Clean footer-update ownership

`agent.cwd` only changes in two places: `on_agent_switched` (new agent) and `_reconnect_sdk` (same agent, new directory). Footer cwd is updated from these two paths only.

`_reconnect_sdk` takes `new_cwd: Path` and operates on `self._agent` (always the active agent). No background-agent reconnect exists in the current codebase.

```python
# on_agent_switched:
self.status_footer.set_cwd(str(new_agent.cwd))

# _reconnect_sdk, after agent.cwd = new_cwd (agent is self._agent, always active):
self.status_footer.set_cwd(str(new_cwd))
self._update_sidebar_agent_context(agent)  # keep sidebar in sync
create_safe_task(self.status_footer.refresh_branch(str(new_cwd)), name="refresh-branch")
```

`_update_sidebar_agent_context` is NOT modified — it remains sidebar-only, matching its name.

### 3. POSIX paths only

`format_cwd` uses `os.path.expanduser("~")` for home substitution and splits on `/` for segments.

### 4. Post-layout width measurement

All `_render_cwd_label()` calls are deferred via `call_after_refresh`. `_render_cwd_label` itself degrades safely if called during transient lifecycle states: `query_one_optional` returns `None` if `#cwd-label` isn't mounted, and `query_one("#footer-content")` is wrapped in a try/except for the same reason.

### 5. No debouncing needed

Multiple deferred `_render_cwd_label()` calls in the same frame are coalesced by Textual. If coalescing doesn't happen, duplicate renders are harmless — the method is idempotent and produces the same result each time.

### 6. Transient budget on agent switch/reconnect

On switch/reconnect, `set_cwd()` runs synchronously but `refresh_branch()` is async. The cwd budget is briefly computed against the OLD branch width. This is self-correcting: when `refresh_branch` completes, `watch_branch` fires `_render_cwd_label` with the correct budget. The transient error is at most a few characters for one frame. No mitigation needed.

## Design

### Pure function: `format_cwd` (in `formatting.py`)

```python
def format_cwd(path: str, max_length: int) -> str
```

- Replace home prefix with `~` (via `os.path.expanduser("~")`)
- If fits in `max_length`, return as-is
- **Segment-truncate**: split on `/`, walk from right, build `…/seg_n/...` until budget exceeded
- **Last-segment fallback**: if last segment alone exceeds `max_length`, char-truncate: `…tatusline`
- Return `""` if `max_length < 4` or `path` is empty

Sidebar refactored to call `format_cwd(self._cwd, self.max_cwd_length)`.

### StatusFooter changes

**New attribute:** `self._cwd: str = ""`

**New widget in `compose()`:**
```python
yield ContextBar(id="context-bar")
yield CPUBar(id="cpu-bar")
yield Static("", id="cwd-label", classes="footer-label hidden")  # NEW
yield Static("", id="branch-label", classes="footer-label")
```

**`set_cwd(cwd: str)`:** Stores cwd, defers `refresh_cwd_label` via `call_after_refresh`.

**`refresh_cwd_label()`** (public) and **`_render_cwd_label()`** (private):

`refresh_cwd_label()` is the public API for the app. It simply calls `_render_cwd_label()`. The private method does the actual work:
```python
CWD_PADDING = 2  # #cwd-label CSS "padding: 0 1" = 1 left + 1 right = 2 horizontal cells

def _render_cwd_label(self) -> None:
    """Recompute cwd budget from sibling widths and render.

    CONVENTION: Every method that changes a footer widget's content or
    visibility MUST defer a call to this method via call_after_refresh.
    Integration tests in test_app_ui.py verify this behaviorally.
    """
    label = self.query_one_optional("#cwd-label", Static)
    if not label:
        return
    if not self._cwd:
        label.add_class("hidden")
        return
    try:
        app_width = self.app.size.width
        footer_content = self.query_one("#footer-content")
    except Exception:
        # Widget not fully mounted or in transient lifecycle state
        label.add_class("hidden")
        log.debug("_render_cwd_label: footer not ready", exc_info=True)
        return
    used = sum(
        child.outer_size.width
        for child in footer_content.children
        if child.id not in ("cwd-label", "footer-spacer")
    )
    budget = min(max(app_width - used - CWD_PADDING, 0), MAX_CWD_LENGTH)
    if budget < MIN_CWD_LENGTH:
        label.add_class("hidden")
    else:
        label.update(format_cwd(self._cwd, budget))
        label.remove_class("hidden")
```

**Updated existing methods:** Each appends `self.call_after_refresh(self._render_cwd_label)`.

### Styling (styles.tcss)

```css
#cwd-label {
    color: $text-muted;
    padding: 0 1;
    width: auto;
}
```

### App wiring (app.py)

Three touch points (footer cwd only changes when active agent's cwd changes):

1. **`on_resize`**: Uses a wrapper that checks `self._status_footer is not None` before deferring:
   ```python
   def _refresh_footer_cwd(self) -> None:
       if self._status_footer is not None:
           self._status_footer.refresh_cwd_label()
   # In on_resize:
   self.call_after_refresh(self._refresh_footer_cwd)
   ```
   Safe before mount: `_status_footer` is `None` until first `query_one(StatusFooter)` succeeds. After mount, `refresh_cwd_label()` itself also guards against missing `#cwd-label` via `query_one_optional`.

2. **`on_agent_switched`**: `self.status_footer.set_cwd(str(new_agent.cwd))` — new active agent may have different cwd. Also serves as first-layout trigger.

3. **`_reconnect_sdk`** (after `agent.cwd = new_cwd`): `set_cwd` + `refresh_branch` + `_update_sidebar_agent_context`. No guard needed — `_reconnect_sdk` operates on `self._agent` which is always the active agent.

`_update_sidebar_agent_context` is NOT modified — it remains sidebar-only, matching its name and responsibility.

### Constants

`formatting.py`: `MIN_CWD_LENGTH = 10`, `MAX_CWD_LENGTH = 40`
`footer.py`: `CWD_PADDING = 2`

## Implementation Plan

### Step 1: `format_cwd` + unit tests
**Files:** `claudechic/formatting.py`, `tests/test_formatting.py`
- Add `format_cwd()`, `MIN_CWD_LENGTH`, `MAX_CWD_LENGTH`
- Tests: home sub, segment truncation at 15/25/35, last-segment fallback, empty, budget<4, budget=4, exact-fit, single segment, root `/`, no-home-prefix
- GREEN

### Step 2: Footer widget + widget tests (incl. budget assertion)
**Files:** `claudechic/widgets/layout/footer.py`, `claudechic/styles.tcss`, `tests/test_widgets.py`
- Add `_cwd`, `#cwd-label` (hidden default), `set_cwd()`, `_render_cwd_label()`, `CWD_PADDING`
- All calls deferred via `call_after_refresh`
- Append deferred call to 5 existing methods
- Widget tests (all use `run_test(size=(120, 24))`, assertions after `await pilot.pause()`):
  - `set_cwd` visible/hidden/empty/truncation/raw-storage
  - Budget assertion: after `set_cwd` with known cwd, verify `sum(child.outer_size.width) <= 120`
- GREEN

### Step 3a: App wiring + resize-safe wrapper
**Files:** `claudechic/app.py`
- Add `_refresh_footer_cwd()` wrapper: checks `_status_footer is not None`, then calls `refresh_cwd_label()`
- Wire `on_resize`: `call_after_refresh(self._refresh_footer_cwd)`
- Wire `on_agent_switched`: `status_footer.set_cwd(str(new_agent.cwd))`
- Wire `_reconnect_sdk`: `status_footer.set_cwd(str(new_cwd))` + sidebar + branch
- GREEN

### Step 3b: Integration tests — footer update triggers
**Files:** `tests/test_app_ui.py`
All tests use `run_test(size=(120, 24))` with `await pilot.pause()` settle step after each trigger:
- Mount + agent switch → cwd appears
- Model change → cwd text reflects new budget
- Branch change → cwd text reflects new budget
- Permission mode change → cwd text reflects new budget
- Process indicator visibility toggle → cwd text reflects new budget
- Vi mode toggle → cwd text reflects new budget
- Resize narrow → cwd hidden; resize wide → cwd visible
- Early resize before mount → no exception (lifecycle safety)
- GREEN

### Step 3c: Reconnect + edge cases + overflow acceptance
**Files:** `tests/test_app_ui.py`
- Reconnect (always active agent) → footer cwd, sidebar cwd, branch all update
- Non-git directory → cwd visible, branch shows "detached"
- Detached HEAD → cwd visible, branch shows "detached"
- **Worst-case acceptance at width matrix (60, 120, 200)**: model="Opus 4.5 Extended", branch="feat/my-very-long-branch-name", permission_mode="planSwarm", process indicator visible, vi mode enabled, cwd="~/code/projects/claudechic/claudechic-statusline":
  - Width 60: cwd hidden (budget < MIN_CWD_LENGTH)
  - Width 120: cwd visible or gracefully hidden, no cwd-caused overflow
  - Width 200: cwd visible
- GREEN

### Step 4: Sidebar refactor
**Files:** `claudechic/widgets/layout/sidebar.py`, `tests/test_sidebar_context.py`
- Refactor `_render_cwd_label` to use `format_cwd(self._cwd, self.max_cwd_length)`
- Update tests: explicit segment-truncation assertions
- Full suite: GREEN

## Files Changed

| File | Change |
|------|--------|
| `claudechic/formatting.py` | `format_cwd()`, `MIN_CWD_LENGTH`, `MAX_CWD_LENGTH` |
| `claudechic/widgets/layout/footer.py` | `_cwd`, `#cwd-label`, `set_cwd()`, `_render_cwd_label()`, `CWD_PADDING`; update 5 methods |
| `claudechic/widgets/layout/sidebar.py` | Refactor `_render_cwd_label` to use `format_cwd()` |
| `claudechic/styles.tcss` | `#cwd-label` style |
| `claudechic/app.py` | 3 touch points (resize, agent switch, reconnect) |
| `tests/test_formatting.py` | `format_cwd` unit tests |
| `tests/test_widgets.py` | `set_cwd` widget tests with budget assertion |
| `tests/test_app_ui.py` | Behavioral invalidation, reconnect, worst-case overflow acceptance |
| `tests/test_sidebar_context.py` | Segment-truncation assertions |

# Fix Auto-Copy Selection Crash

**Date:** 2026-04-05
**Status:** Revised after 4-agent review
**Revision:** 4 (rev 3: test coverage, UX, debounce framing; rev 4: fix timer handle-clobbering race, accurate debounce criterion)

## Problem

claudechic crashes several times a day with an `IndexError` in `_check_and_copy_selection`. The traceback:

```
File ".../claudechic/app.py", line 1587, in _check_and_copy_selection
    selected = self.screen.get_selected_text()
File ".../textual/screen.py", line 971, in get_selected_text
    selected_text_in_widget = widget.get_selection(selection)
File ".../textual/widget.py", line 4190, in get_selection
    return selection.extract(text), "\n"
File ".../textual/selection.py", line 66, in extract
    return lines[start_line][start_offset:end_offset]
IndexError: list index out of range
```

## Root Cause

Race condition between widget content mutation and selection extraction:

1. **User selects text** with the mouse. Textual records selection coordinates (line number + character offset) relative to the widget's rendered content at that moment.
2. **Mouse-up fires** in `on_mouse_up` (app.py:1500). A 50ms timer calls `_check_and_copy_selection`.
3. **During that 50ms window** (or during `get_selected_text()` itself), the widget's content changes -- streaming chat messages, tool results arriving, collapsibles toggling, ThinkingIndicator animating.
4. **`get_selection()` re-renders the widget** (`self._render()` at widget.py:4185) to get the *current* text, but the `Selection` object still holds coordinates from the *old* text.
5. **`Selection.extract()`** splits the *new* (shorter) text into lines, then tries to index with `start_line` from the *old* (longer) text -- **`IndexError`**.

The bug is **not in claudechic** -- it's an upstream issue in Textual's `Selection.extract()` which doesn't bounds-check `start_line` against `len(lines)`. Verified against Textual 8.2.2 source: `end_line` is clamped with `min(len(lines), end_line)` but `start_line` is never clamped. However, claudechic triggers it more often than most Textual apps because:

- Widgets stream content in real-time (chat messages, tool output)
- Widgets collapse/expand (tool uses auto-collapse)
- The ThinkingIndicator animates continuously
- Selection coordinates go stale very fast

## Call Chain

```
on_mouse_up (app.py:1500)
  +-- set_timer(0.05, _check_and_copy_selection)
       +-- _check_and_copy_selection (app.py:1586)
            +-- screen.get_selected_text() (screen.py:960)
                 +-- widget.get_selection(selection) (widget.py:4176)
                      +-- self._render()  <- re-renders widget, gets NEW text
                      +-- selection.extract(text) (selection.py:30)
                           +-- lines[start_line]  <- OLD coordinates, NEW text -> IndexError
```

## Approaches Considered

### Approach A: try/except in `_check_and_copy_selection` (CHOSEN, then revised)

Wrap the `get_selected_text()` call in a `try/except IndexError` and return early on failure.

**Pros:**
- Minimal change, zero risk of side effects
- Catches the exact failure at the exact call site
- Correct UX: stale selection that can't be resolved is not worth surfacing -- user just re-selects

**Cons:**
- Swallows a symptom -- if a *different* IndexError appears in the same path, it won't be visible
- Doesn't fix the underlying Textual bug

### Approach B: Override `get_selection` on streaming widgets

Override `get_selection()` on `ChatMessage`, `ToolUseWidget`, and other claudechic widgets that stream content. Add bounds checks before calling `selection.extract()`.

**Pros:**
- More targeted -- only guards the widgets that actually mutate during selection
- Doesn't mask unrelated errors in other code paths

**Cons:**
- More code to maintain across multiple widget classes
- The crash can still happen on *any* widget Textual iterates over in `get_selected_text()`, including widgets we don't override
- Fragile: new widgets added later might miss the override

### Approach C: Monkey-patch `Selection.extract` at startup

Patch `Selection.extract` to clamp `start_line` and `end_line` to `len(lines) - 1`, fixing it globally.

**Pros:**
- Fixes the root cause at the Textual layer for the entire app
- All widgets protected automatically, including future ones

**Cons:**
- Monkey-patching a framework is fragile -- a Textual upgrade could break it or silently revert it
- Harder to discover and debug if something goes wrong
- Might mask real bugs in selection logic during development

## Decision

**Approach A** chosen for its simplicity and correctness of semantics, then **revised** after a 4-agent review (code reviewer, architect, contrarian, roborev) that identified gaps in the original implementation.

## 4-Agent Review Findings

The original implementation (a 3-line try/except in `_check_and_copy_selection` only) was reviewed by four independent agents. Key findings that drove the revision:

1. **`action_copy_selection` unprotected (HIGH):** The keybinding-triggered manual copy at app.py:1453 calls the same `screen.get_selected_text()` with no guard. Both call sites share the same vulnerability. (Roborev, Contrarian)
2. **No regression tests (HIGH):** Zero tests for any selection-copy path. (All 4 agents)
3. **No observability (MEDIUM):** The except block did a bare `return` with only a comment. No logging means you can't measure race frequency or distinguish harmless races from new bugs. (Contrarian, Roborev)
4. **KeyError is speculative (MEDIUM):** The original spec claimed `KeyError` could arise because `get_selected_text` "traverses widget trees by ID." The contrarian agent read the actual code and showed it iterates `self.selections.items()` -- there is no dict-by-key lookup. `KeyError` was removed from the except clause. (Contrarian)
5. **No acceptance criteria (MEDIUM):** The original spec never defined what "done" means. (Roborev)
6. **Timer debounce missing (LOW):** Every `on_mouse_up` fires a new timer with no cancellation of the previous one. Rapid mouse-ups accumulate overlapping timers. Already flagged as M13 in the existing code review. (Contrarian, Roborev)

## Revised Implementation

*Note: This implementation replaces the initial quick-fix (commit `f3516ff`) which caught `(IndexError, KeyError)` in `_check_and_copy_selection` only.*

### Shared safe helper

A private method that wraps `screen.get_selected_text()` with try/except and logging:

```python
def _safe_get_selected_text(self) -> str | None:
    """Get selected text, returning None if selection coords are stale."""
    try:
        return self.screen.get_selected_text()
    except IndexError:
        log.debug("Stale selection coordinates, skipping copy")
        return None
```

Key decisions:
- Catches **only `IndexError`** -- the single evidenced exception from `Selection.extract()` line 66.
- Returns `None` on failure -- callers already check truthiness of the result.
- `log.debug()` not `log.warning()` -- this is expected behavior during streaming, not an anomaly.

### Both call sites use the helper

```python
def action_copy_selection(self) -> None:
    selected = self._safe_get_selected_text()
    if selected:
        success = self.copy_to_clipboard(selected)
        if success:
            self.notify("Copied to clipboard")
        else:
            self.notify("Copy failed", severity="warning", timeout=2)

def _check_and_copy_selection(self) -> None:
    selected = self._safe_get_selected_text()
    if selected and len(selected.strip()) > 0:
        success = self.copy_to_clipboard(selected)
        if success:
            self._copy_failed_notified = False
            self.notify("Copied", timeout=1)
        elif not self._copy_failed_notified:
            self._copy_failed_notified = True
            self.notify("Copy failed", severity="warning", timeout=2)
```

### Timer debounce

Store the timer handle and cancel the previous one before scheduling new:

```python
_copy_timer: Timer | None = None

def on_mouse_up(self, event: MouseUp) -> None:
    # ... sidebar overlay logic unchanged ...
    if self._copy_timer is not None:
        self._copy_timer.stop()
    self._copy_timer = self.set_timer(0.05, self._check_and_copy_selection)
```

Prevents timer accumulation on rapid mouse-ups (scrolling, drag-selecting, clicking). `Timer` is already imported under `TYPE_CHECKING` in app.py (line 18); the `_copy_timer: Timer | None` annotation is safe at runtime because `from __future__ import annotations` (line 3) makes all annotations lazy strings.

The callback does **not** clear `_copy_timer`. Doing so would introduce a handle-clobbering race: if an old callback fires after a newer timer is stored, setting `_copy_timer = None` would lose the new timer reference, preventing cancellation on the next mouse-up. Calling `stop()` on an already-fired timer is a no-op in Textual, so a stale reference is harmless.

**Important framing:** `Timer.stop()` cancels the timer's asyncio Task but cannot retract a callback already pushed to the message queue via `call_next`. This means two `_check_and_copy_selection` calls could briefly overlap in a narrow race. This is benign — the function is idempotent (get selection, copy, notify) and the try/except in `_safe_get_selected_text` protects both invocations. The debounce is an **optimization** to reduce redundant clipboard writes; the **correctness mechanism** is the try/except.

This follows the existing timer-management pattern in the codebase (see `_review_poll_timer` at app.py:898).

### Tests (6 tests in test_app_ui.py)

1. **`test_check_and_copy_selection_handles_index_error`** -- mock `screen.get_selected_text` to raise `IndexError`, assert method returns without crash, assert no "Copied" notification.
2. **`test_action_copy_selection_handles_index_error`** -- same for the keybinding path.
3. **`test_check_and_copy_selection_copies_on_success`** -- mock `screen.get_selected_text` to return text, mock `copy_to_clipboard` to return `True`, assert `copy_to_clipboard` is called and "Copied" notification is shown.
4. **`test_mouse_up_debounce_cancels_previous_timer`** -- call `on_mouse_up` twice, assert the first timer's `stop()` was called before the second `set_timer`.
5. **`test_safe_get_selected_text_logs_on_stale_selection`** -- mock `screen.get_selected_text` to raise `IndexError`, assert `log.debug` is called with the expected message.
6. **`test_check_and_copy_selection_ignores_whitespace`** -- mock `screen.get_selected_text` to return `"\n  \n"`, assert `copy_to_clipboard` is NOT called.

## Goals and Non-Goals

**Goals:**
- Contain the `IndexError` crash in all selection-copy paths (crash-containment, not root-cause fix)
- Add observability so race frequency is measurable
- Prevent timer accumulation on rapid mouse events

**Non-goals:**
- Fix the upstream Textual bug in `Selection.extract()` (backlog item)
- Redesign the timer-based auto-copy mechanism
- Add retry or recovery logic for stale selections

**Edge cases considered:**
- Whitespace-only selections (`"\n \n"`) -- auto-copy skips these via `.strip()` guard (existing behavior, now covered by test). Manual copy (`action_copy_selection`) copies any truthy string including whitespace-only — this is intentional for backwards compatibility (a user who explicitly triggers copy may want whitespace)
- Widget destroyed between selection and extraction -- `get_selected_text()` checks `widget.is_attached` upstream
- Repeated stale selections during active streaming -- each produces a `log.debug()`, no accumulation risk due to debounce
- Screen detached when timer fires -- Textual does not fire timer callbacks after app teardown

## Design Choices

**Silent failure UX (both auto-copy and manual copy):** Auto-copy is a convenience feature. A failed auto-copy producing no toast is intentional -- the "Copied" toast's *absence* is sufficient signal. For manual copy (`action_copy_selection`), the same logic applies: the user expects "Copied to clipboard" on success; its absence signals something went wrong. Showing a "Selection lost" toast was considered and rejected for both paths: it would fire during rapid scrolling and streaming, creating noise. The user re-selects naturally.

**Method placement:** `_safe_get_selected_text` is a private method on `ChatApp` rather than a standalone function because it accesses `self.screen`, a Textual framework attribute. Extracting it would require passing the screen reference, adding coupling for no benefit. It belongs with the other clipboard methods on ChatApp.

## Acceptance Criteria

**Functional:**
- [ ] `_check_and_copy_selection` does not crash when `screen.get_selected_text()` raises `IndexError`
- [ ] `action_copy_selection` does not crash when `screen.get_selected_text()` raises `IndexError`
- [ ] Successful selections still copy and show the "Copied" toast
- [ ] Stale selections produce a `log.debug()` entry (visible in `~/claudechic-dev.log` where debug logging is enabled by default)
- [ ] New mouse-up events cancel the previously tracked timer when possible; stale callbacks do not crash (debounce is best-effort optimization, not a hard guarantee)

**Non-functional:**
- [ ] All 6 new tests pass
- [ ] Pre-commit hooks pass (ruff, ruff-format, pyright)
- [ ] No behavioral change for the success path -- identical UX when copy works

## Backlog

- **Upstream issue:** File a Textual issue for `Selection.extract()` missing `start_line` bounds check. The root cause analysis in this spec is detailed enough to submit verbatim. If Textual adds bounds-checking upstream, the try/except becomes a harmless no-op.

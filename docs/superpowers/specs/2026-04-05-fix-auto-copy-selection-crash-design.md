# Fix Auto-Copy Selection Crash

**Date:** 2026-04-05
**Status:** Approved (Approach A implemented)

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
3. **During that 50ms window** (or during `get_selected_text()` itself), the widget's content changes — streaming chat messages, tool results arriving, collapsibles toggling, ThinkingIndicator animating.
4. **`get_selection()` re-renders the widget** (`self._render()` at widget.py:4185) to get the *current* text, but the `Selection` object still holds coordinates from the *old* text.
5. **`Selection.extract()`** splits the *new* (shorter) text into lines, then tries to index with `start_line` from the *old* (longer) text — **`IndexError`**.

The bug is **not in claudechic** — it's an upstream issue in Textual's `Selection.extract()` which doesn't bounds-check `start_line` against `len(lines)`. However, claudechic triggers it more often than most Textual apps because:

- Widgets stream content in real-time (chat messages, tool output)
- Widgets collapse/expand (tool uses auto-collapse)
- The ThinkingIndicator animates continuously
- Selection coordinates go stale very fast

## Call Chain

```
on_mouse_up (app.py:1500)
  └─ set_timer(0.05, _check_and_copy_selection)
       └─ _check_and_copy_selection (app.py:1586)
            └─ screen.get_selected_text() (screen.py:960)
                 └─ widget.get_selection(selection) (widget.py:4176)
                      └─ self._render()  ← re-renders widget, gets NEW text
                      └─ selection.extract(text) (selection.py:30)
                           └─ lines[start_line]  ← OLD coordinates, NEW text → IndexError
```

## Approaches Considered

### Approach A: try/except in `_check_and_copy_selection` (CHOSEN)

Wrap the `get_selected_text()` call in a `try/except (IndexError, KeyError)` and return early on failure.

```python
def _check_and_copy_selection(self) -> None:
    try:
        selected = self.screen.get_selected_text()
    except (IndexError, KeyError):
        return
    if selected and len(selected.strip()) > 0:
        success = self.copy_to_clipboard(selected)
        ...
```

**Pros:**
- 3-line change, zero risk of side effects
- Catches the exact failure at the exact call site
- Correct UX: stale selection that can't be resolved is not worth surfacing — user just re-selects

**Cons:**
- Swallows a symptom — if a *different* IndexError appears in the same path, it won't be visible
- Doesn't fix the underlying Textual bug

### Approach B: Override `get_selection` on streaming widgets

Override `get_selection()` on `ChatMessage`, `ToolUseWidget`, and other claudechic widgets that stream content. Add bounds checks before calling `selection.extract()`.

```python
def get_selection(self, selection: Selection) -> tuple[str, str] | None:
    visual = self._render()
    if isinstance(visual, (Text, Content)):
        text = str(visual)
    else:
        return None
    lines = text.splitlines()
    if selection.start and selection.start.transpose[0] >= len(lines):
        return None
    return selection.extract(text), "\n"
```

**Pros:**
- More targeted — only guards the widgets that actually mutate during selection
- Doesn't mask unrelated errors in other code paths

**Cons:**
- More code to maintain across multiple widget classes
- The crash can still happen on *any* widget Textual iterates over in `get_selected_text()`, including widgets we don't override
- Fragile: new widgets added later might miss the override

### Approach C: Monkey-patch `Selection.extract` at startup

Patch `Selection.extract` to clamp `start_line` and `end_line` to `len(lines) - 1`, fixing it globally.

```python
# In app startup
_original_extract = Selection.extract

def _safe_extract(self, text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    # Clamp coordinates to actual line count
    ...
    return _original_extract(self, text)

Selection.extract = _safe_extract
```

**Pros:**
- Fixes the root cause at the Textual layer for the entire app
- All widgets protected automatically, including future ones

**Cons:**
- Monkey-patching a framework is fragile — a Textual upgrade could break it or silently revert it
- Harder to discover and debug if something goes wrong
- Might mask real bugs in selection logic during development

## Decision

**Approach A** chosen for its simplicity and correctness of semantics. The `_check_and_copy_selection` method is a convenience feature (auto-copy on select). If it can't extract text due to a race, silently doing nothing is the correct UX — the user just re-selects. No upstream dependency, no monkey-patching, no multi-file changes.

`KeyError` is included in the except clause because Textual's `get_selected_text` traverses widget trees by ID, and concurrent widget removal (e.g., a collapsible being replaced) could raise it.

## Future Consideration

If Textual ever adds bounds-checking to `Selection.extract()` upstream, the try/except becomes a harmless no-op. It can be removed at that point but does no harm if left in place.

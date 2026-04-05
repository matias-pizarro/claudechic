# Fix Auto-Copy Selection Crash

**Date:** 2026-04-05
**Status:** Approved

## Problem

claudechic crashes several times a day with an `IndexError` in `_check_and_copy_selection`. The traceback originates in Textual's `Selection.extract()` when it indexes into widget text using stale line/offset coordinates.

## Root Cause

Race condition between widget content mutation and selection extraction:

1. User selects text. Textual records selection coordinates relative to the rendered content at that moment.
2. Mouse-up fires. A 50ms timer calls `_check_and_copy_selection`.
3. During that window, widget content changes (streaming chat, tool results, collapsible state changes, animations).
4. `get_selected_text()` re-renders the widget to get its *current* text, but the `Selection` object still holds coordinates from the *old* text.
5. `Selection.extract()` indexes into a shorter (or differently shaped) text with stale line numbers, raising `IndexError`.

## Fix

Wrap the `self.screen.get_selected_text()` call in `_check_and_copy_selection` with a `try/except` that catches `IndexError` and `KeyError`, returning early on failure. This is the smallest possible change that eliminates the crash while preserving all existing behavior for the success path.

`KeyError` is included because Textual's `get_selected_text` traverses widget trees by ID, and concurrent widget removal (e.g., a collapsible being replaced) could raise it.

## Why This Approach

- **Minimal blast radius.** A one-line guard around an inherently racy call. No changes to Textual internals, widget lifecycle, or selection flow.
- **Correct semantics.** A stale selection that can no longer be resolved is not an error worth surfacing to the user -- the text they selected simply no longer exists in that form. Silently dropping it is the right UX.
- **No upstream dependency.** Fixing the race properly would require Textual to snapshot widget text at selection time, which is a larger upstream change outside our control.

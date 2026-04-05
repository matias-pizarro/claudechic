# Fix Auto-Copy Selection Crash — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Contain the `IndexError` crash in all selection-copy paths, add observability, debounce timer accumulation, and cover with 9 regression tests.

**Architecture:** Shared `_safe_get_selected_text()` helper wraps `screen.get_selected_text()` in try/except IndexError with debug logging. Both copy call sites (`action_copy_selection` and `_check_and_copy_selection`) route through the helper. Timer debounce via stored `_copy_timer` handle with cancel-before-set pattern.

**Tech Stack:** Python 3.12+, Textual 8.2.2, pytest with `run_test()` for async UI tests

**Spec:** `docs/superpowers/specs/2026-04-05-fix-auto-copy-selection-crash-design.md` (rev 7)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `claudechic/app.py` | Modify | Add `_safe_get_selected_text`, `_copy_timer`, update `action_copy_selection`, `_check_and_copy_selection`, `on_mouse_up` |
| `tests/test_app_ui.py` | Modify | Add 9 new tests for selection-copy paths |

No new files created. All changes are within existing files.

---

## Phase 1: Shared Helper + Crash Containment (tests 1, 2, 5)

### Task 1: Write failing tests for the shared helper and both crash paths

**Files:**
- Modify: `tests/test_app_ui.py` (append at end)

- [ ] **Step 1: Write 3 failing tests**

Append the following to the end of `tests/test_app_ui.py`:

```python
# --- Selection copy safety tests ---


@pytest.mark.asyncio
async def test_check_and_copy_selection_handles_index_error(mock_sdk):
    """Auto-copy does not crash when get_selected_text raises IndexError."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with patch.object(
            type(app.screen), "get_selected_text", side_effect=IndexError
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        # No crash, no "Copied" notification
        assert not any(
            n.message == "Copied" for n in app._notifications
        )


@pytest.mark.asyncio
async def test_action_copy_selection_handles_index_error(mock_sdk):
    """Manual copy does not crash when get_selected_text raises IndexError."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with patch.object(
            type(app.screen), "get_selected_text", side_effect=IndexError
        ):
            app.action_copy_selection()
            await pilot.pause()
        # No crash, no "Copied to clipboard" notification
        assert not any(
            n.message == "Copied to clipboard" for n in app._notifications
        )


@pytest.mark.asyncio
async def test_safe_get_selected_text_logs_on_stale_selection(mock_sdk):
    """Stale selection emits a debug log entry."""
    app = ChatApp()
    async with app.run_test():
        with (
            patch.object(
                type(app.screen), "get_selected_text", side_effect=IndexError
            ),
            patch("claudechic.app.log") as mock_log,
        ):
            result = app._safe_get_selected_text()
        assert result is None
        mock_log.debug.assert_called_once_with(
            "Stale selection coordinates, skipping copy"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_ui.py::test_check_and_copy_selection_handles_index_error tests/test_app_ui.py::test_action_copy_selection_handles_index_error tests/test_app_ui.py::test_safe_get_selected_text_logs_on_stale_selection -v`

Expected: `test_check_and_copy_selection_handles_index_error` PASS (existing quick-fix catches it), `test_action_copy_selection_handles_index_error` FAIL (IndexError not caught), `test_safe_get_selected_text_logs_on_stale_selection` FAIL (`_safe_get_selected_text` does not exist).

### Task 2: Implement the shared helper and wire both call sites

**Files:**
- Modify: `claudechic/app.py` — methods `action_copy_selection` and `_check_and_copy_selection`, attribute `_copy_failed_notified`

- [ ] **Step 3: Add `_safe_get_selected_text` method**

Add this method to `ChatApp` in `claudechic/app.py`, just before the `_copy_failed_notified` class attribute:

```python
    def _safe_get_selected_text(self) -> str | None:
        """Get selected text, returning None if selection coords are stale."""
        try:
            return self.screen.get_selected_text()
        except IndexError:
            log.debug("Stale selection coordinates, skipping copy")
            return None
```

- [ ] **Step 4: Update `action_copy_selection` to use the helper**

Replace the `action_copy_selection` method in `claudechic/app.py`:

```python
    def action_copy_selection(self) -> None:
        selected = self._safe_get_selected_text()
        if selected:
            success = self.copy_to_clipboard(selected)
            if success:
                self.notify("Copied to clipboard")
            else:
                self.notify("Copy failed", severity="warning", timeout=2)
```

- [ ] **Step 5: Update `_check_and_copy_selection` to use the helper**

Replace the `_check_and_copy_selection` method in `claudechic/app.py`:

```python
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

- [ ] **Step 6: Run the 3 tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_ui.py::test_check_and_copy_selection_handles_index_error tests/test_app_ui.py::test_action_copy_selection_handles_index_error tests/test_app_ui.py::test_safe_get_selected_text_logs_on_stale_selection -v`

Expected: All 3 PASS.

- [ ] **Step 7: Run pre-commit hooks**

Run: `.venv/bin/python -m pre_commit run --files claudechic/app.py tests/test_app_ui.py`

Expected: All hooks pass (ruff, ruff-format, pyright).

- [ ] **Step 8: Commit**

```bash
git add claudechic/app.py tests/test_app_ui.py
git commit -m "fix: add _safe_get_selected_text helper, guard both copy paths

Extract shared helper that wraps screen.get_selected_text() in
try/except IndexError with debug logging. Wire both action_copy_selection
and _check_and_copy_selection through the helper. Remove speculative
KeyError from the except clause. Replaces quick-fix commit f3516ff."
```

---

## Phase 2: Timer Debounce (tests 4, 7)

### Task 3: Write failing tests for timer debounce

**Files:**
- Modify: `tests/test_app_ui.py` (append after previous tests)

- [ ] **Step 9: Write 2 failing tests**

Append to `tests/test_app_ui.py`:

```python
@pytest.mark.asyncio
async def test_mouse_up_debounce_cancels_previous_timer(mock_sdk):
    """Second mouse-up cancels the first timer before scheduling a new one."""
    app = ChatApp()
    async with app.run_test() as pilot:
        mock_timer_1 = MagicMock()
        mock_timer_2 = MagicMock()
        timers = iter([mock_timer_1, mock_timer_2])

        with patch.object(app, "set_timer", side_effect=lambda *a, **k: next(timers)):
            # Simulate two mouse-up events
            # MouseUp(widget, x, y, delta_x, delta_y, button, shift, meta, ctrl, ...)
            from textual.events import MouseUp

            event = MouseUp(None, 0, 0, 0, 0, 0, False, False, False, screen_x=0, screen_y=0)
            app.on_mouse_up(event)
            assert app._copy_timer is mock_timer_1

            app.on_mouse_up(event)
            mock_timer_1.stop.assert_called_once()
            assert app._copy_timer is mock_timer_2


@pytest.mark.asyncio
async def test_check_and_copy_selection_does_not_clear_copy_timer(mock_sdk):
    """Regression guard: _check_and_copy_selection must never clear _copy_timer.

    If someone adds self._copy_timer = None to the callback, it would
    introduce a handle-clobbering race where an old callback firing after
    a newer timer is stored would lose the new timer reference.
    This test passes trivially today (the callback doesn't touch _copy_timer),
    but guards against that regression.
    """
    app = ChatApp()
    async with app.run_test() as pilot:
        mock_new_timer = MagicMock()
        app._copy_timer = mock_new_timer

        # Simulate the old callback firing
        with patch.object(
            type(app.screen), "get_selected_text", return_value=None
        ):
            app._check_and_copy_selection()

        # _copy_timer must still reference the new timer, not be cleared
        assert app._copy_timer is mock_new_timer
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_app_ui.py::test_mouse_up_debounce_cancels_previous_timer tests/test_app_ui.py::test_old_timer_callback_does_not_clobber_new_timer -v`

Expected: `test_mouse_up_debounce_cancels_previous_timer` FAIL (`_copy_timer` attribute does not exist, `on_mouse_up` does not cancel previous timer). `test_check_and_copy_selection_does_not_clear_copy_timer` PASS (regression guard — the callback already does not touch `_copy_timer`).

### Task 4: Implement timer debounce

**Files:**
- Modify: `claudechic/app.py` — method `on_mouse_up`, attribute area near `_copy_failed_notified`

- [ ] **Step 11: Add `_copy_timer` attribute and update `on_mouse_up`**

Add `_copy_timer` next to `_copy_failed_notified` in `claudechic/app.py`:

```python
    _copy_failed_notified: bool = False
    _copy_timer: Timer | None = None
```

Replace the last line of `on_mouse_up` (`self.set_timer(0.05, self._check_and_copy_selection)`):

```python
        if self._copy_timer is not None:
            self._copy_timer.stop()
        self._copy_timer = self.set_timer(0.05, self._check_and_copy_selection)
```

- [ ] **Step 12: Run the 2 debounce tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app_ui.py::test_mouse_up_debounce_cancels_previous_timer tests/test_app_ui.py::test_old_timer_callback_does_not_clobber_new_timer -v`

Expected: Both PASS.

- [ ] **Step 13: Run pre-commit hooks**

Run: `.venv/bin/python -m pre_commit run --files claudechic/app.py tests/test_app_ui.py`

Expected: All hooks pass.

- [ ] **Step 14: Commit**

```bash
git add claudechic/app.py tests/test_app_ui.py
git commit -m "fix: debounce auto-copy timer in on_mouse_up

Store _copy_timer handle and cancel previous timer before scheduling
new one. Prevents timer accumulation on rapid mouse-ups. Callback
does not clear the handle to avoid clobbering a newer reference."
```

---

## Phase 3: Edge Cases and Failure Modes (tests 3, 6, 8, 9)

### Task 5: Write remaining tests for success path, whitespace, and clipboard failure

**Files:**
- Modify: `tests/test_app_ui.py` (append after previous tests)

- [ ] **Step 15: Write 4 tests**

Append to `tests/test_app_ui.py`:

```python
@pytest.mark.asyncio
async def test_check_and_copy_selection_copies_on_success(mock_sdk):
    """Auto-copy calls copy_to_clipboard and shows 'Copied' on success.

    Note: The "Copied" notification has timeout=1. Assertions run immediately
    after pilot.pause(), well within the 1s window. If this test becomes flaky
    under heavy CI load, the notification may be reaped before the assertion.
    """
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="hello world",
            ),
            patch.object(app, "copy_to_clipboard", return_value=True) as mock_copy,
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        mock_copy.assert_called_once_with("hello world")
        assert any(n.message == "Copied" for n in app._notifications)


@pytest.mark.asyncio
async def test_check_and_copy_selection_ignores_whitespace(mock_sdk):
    """Auto-copy skips whitespace-only selections."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="\n  \n",
            ),
            patch.object(app, "copy_to_clipboard") as mock_copy,
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        mock_copy.assert_not_called()


@pytest.mark.asyncio
async def test_action_copy_selection_copies_whitespace_only(mock_sdk):
    """Manual copy preserves whitespace-only selections (backwards compat)."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="\n  \n",
            ),
            patch.object(app, "copy_to_clipboard", return_value=True) as mock_copy,
        ):
            app.action_copy_selection()
            await pilot.pause()
        mock_copy.assert_called_once_with("\n  \n")


@pytest.mark.asyncio
async def test_check_and_copy_selection_clipboard_failure_shows_warning(mock_sdk):
    """Clipboard failure shows 'Copy failed' warning (distinguishes from stale selection)."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="hello world",
            ),
            patch.object(app, "copy_to_clipboard", return_value=False),
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        assert any(
            n.message == "Copy failed" and n.severity == "warning"
            for n in app._notifications
        )
```

- [ ] **Step 16: Run all 4 tests**

Run: `.venv/bin/python -m pytest tests/test_app_ui.py::test_check_and_copy_selection_copies_on_success tests/test_app_ui.py::test_check_and_copy_selection_ignores_whitespace tests/test_app_ui.py::test_action_copy_selection_copies_whitespace_only tests/test_app_ui.py::test_check_and_copy_selection_clipboard_failure_shows_warning -v`

Expected: All 4 PASS (these test existing behavior that is unchanged, not new functionality).

- [ ] **Step 17: Run full test suite**

Run: `.venv/bin/python -m pytest tests/test_app_ui.py -v`

Expected: All tests pass (existing + 9 new).

- [ ] **Step 18: Run pre-commit hooks on all changed files**

Run: `.venv/bin/python -m pre_commit run --files claudechic/app.py tests/test_app_ui.py`

Expected: All hooks pass.

- [ ] **Step 19: Commit**

```bash
git add tests/test_app_ui.py
git commit -m "test: add edge case and failure mode tests for selection copy

Cover success path with notification assertion, whitespace-only
handling for both auto-copy (skips) and manual copy (preserves),
and clipboard failure warning toast distinguishability."
```

---

## Phase 4: Final Verification

### Task 6: Full regression check

- [ ] **Step 20: Run entire test suite in parallel**

Run: `.venv/bin/python -m pytest tests/ -n auto -q`

Expected: All tests pass. No regressions in existing tests.

- [ ] **Step 21: Run pre-commit on all files**

Run: `.venv/bin/python -m pre_commit run --all-files`

Expected: All hooks pass.

- [ ] **Step 22: Verify acceptance criteria**

Check against spec acceptance criteria:
- `_check_and_copy_selection` does not crash on IndexError: covered by test 1
- `action_copy_selection` does not crash on IndexError: covered by test 2
- Successful auto-copy shows "Copied" (1s): covered by test 3
- Stale selections emit `log.debug()`: covered by test 5
- Timer debounce cancels previous: covered by test 4
- Timer handle not clobbered by old callback: covered by test 7 (regression guard)
- Whitespace-only auto-copy skipped: covered by test 6
- Whitespace-only manual copy preserved: covered by test 8
- Clipboard failure shows warning: covered by test 9
- Pre-commit hooks pass: verified in step 21
- No behavioral change for success path: verified by test 3

All 9 tests pass. All acceptance criteria covered. Implementation complete.

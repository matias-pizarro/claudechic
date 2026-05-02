# Convergence Pre-Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify `convergence_target` so that `git merge --no-ff 0.4.20`, then `git merge --no-ff 0.4.21`, then `git merge --no-ff origin/main` all complete without conflicts.

**Architecture:** Three surgical refactors that adopt upstream's code patterns without changing behavior: (1) table-driven permission mode display in footer, (2) cast-based SDK permission call in agent, (3) structured test file for additive extension. A final integration phase runs the actual merges to verify zero conflicts.

**Tech Stack:** Python 3.11, Textual (TUI framework), claude-agent-sdk, pytest, Playwright (for visual verification)

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `claudechic/widgets/layout/footer.py` | Status footer with permission mode display | Refactor `watch_permission_mode` from if/elif to table-driven |
| `claudechic/agent.py` | Agent class with SDK permission handling | Refactor `set_permission_mode` to use `cast()` + skip pattern |
| `tests/test_agent.py` | Agent unit tests | Update `TestSetPermissionMode` assertions to match new pattern |
| `tests/test_footer.py` | Footer permission mode tests (new) | Add table-driven mode display tests |

---

## Task 1: Table-Driven Permission Mode Display in Footer

**Files:**
- Modify: `claudechic/widgets/layout/footer.py:294-319`
- Create: `tests/test_footer.py`

### Phase: RED (write failing tests)

- [ ] **Step 1: Create `tests/test_footer.py` with table-driven mode tests**

```python
"""Tests for StatusFooter permission mode display."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from claudechic.widgets.layout.footer import StatusFooter


class FooterTestApp(App):
    """Minimal app to mount StatusFooter for testing."""

    def compose(self) -> ComposeResult:
        yield StatusFooter()


class TestWatchPermissionMode:
    """Verify table-driven permission mode display.

    Each mode maps to (label_text, active_css_class).
    The footer should display the correct label and apply
    exactly one CSS class from the mode class set.
    """

    @pytest.mark.asyncio
    async def test_default_mode_shows_auto_edit_off(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "default"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            assert label.renderable == "Auto-edit: off"
            assert not label.has_class("active")
            assert not label.has_class("plan-mode")

    @pytest.mark.asyncio
    async def test_accept_edits_mode_shows_auto_edit_on(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "acceptEdits"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            assert label.renderable == "Auto-edit: on"
            assert label.has_class("active")
            assert not label.has_class("plan-mode")

    @pytest.mark.asyncio
    async def test_plan_mode_shows_plan_mode(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "plan"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            assert label.renderable == "Plan mode"
            assert label.has_class("plan-mode")
            assert not label.has_class("active")

    @pytest.mark.asyncio
    async def test_plan_swarm_mode_shows_plan_swarm(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "planSwarm"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            assert label.renderable == "Plan swarm"
            assert label.has_class("plan-swarm-mode")
            assert not label.has_class("active")
            assert not label.has_class("plan-mode")

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_back_to_default(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "unknown_future_mode"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            assert label.renderable == "Auto-edit: off"
            assert not label.has_class("active")
```

- [ ] **Step 2: Run tests to verify they pass with current implementation**

Run: `uv run python -m pytest tests/test_footer.py -v`
Expected: All tests PASS (current if/elif produces same output as table-driven would)

> Note: These tests verify behavior, not implementation. They pass with both the current if/elif and the future table-driven approach. This ensures refactoring is safe.

### Phase: GREEN (refactor to table-driven)

- [ ] **Step 3: Refactor `watch_permission_mode` in footer.py**

Replace lines 294-319 in `claudechic/widgets/layout/footer.py`:

```python
    # Table-driven permission mode display.
    # Format: mode_name -> (label_text, css_class_to_activate)
    # Upstream 0.4.20 will add "auto": ("Auto", "auto-mode") to this table.
    _MODE_DISPLAY: dict[str, tuple[str, str]] = {
        "default": ("Auto-edit: off", ""),
        "acceptEdits": ("Auto-edit: on", "active"),
        "plan": ("Plan mode", "plan-mode"),
        "planSwarm": ("Plan swarm", "plan-swarm-mode"),
    }
    _MODE_CLASSES: list[str] = ["active", "plan-mode", "plan-swarm-mode"]

    def watch_permission_mode(self, value: str) -> None:
        """Update permission mode label when setting changes."""
        if label := self.query_one_optional(
            "#permission-mode-label", PermissionModeLabel
        ):
            text, active = self._MODE_DISPLAY.get(
                value, self._MODE_DISPLAY["default"]
            )
            label.update(text)
            for cls in self._MODE_CLASSES:
                label.set_class(cls == active, cls)
        self.call_after_refresh(self._render_cwd_label)
```

- [ ] **Step 4: Run tests to verify they still pass**

Run: `uv run python -m pytest tests/test_footer.py -v`
Expected: All tests PASS (behavior unchanged)

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All existing tests PASS

- [ ] **Step 6: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: ruff, ruff-format, pyright all pass

- [ ] **Step 7: Commit**

```bash
git add tests/test_footer.py claudechic/widgets/layout/footer.py
git commit -m "refactor: make watch_permission_mode table-driven for upstream convergence

Convert the if/elif permission mode display chain to a table-driven
_MODE_DISPLAY dict lookup. Upstream 0.4.20 adds 'auto' mode using
this same table pattern; pre-aligning allows conflict-free merge.

No behavioral change — all existing tests pass."
```

---

## Task 2: Cast-Based SDK Permission Mode Call in Agent

**Files:**
- Modify: `claudechic/agent.py:28-34` (imports), `claudechic/agent.py:951-973` (set_permission_mode)
- Modify: `tests/test_agent.py:147-176` (TestSetPermissionMode)

### Phase: RED (update test expectations)

- [ ] **Step 1: Update test to expect skip-planSwarm pattern (not sdk_mode mapping)**

Replace `TestSetPermissionMode` in `tests/test_agent.py` (lines 147-176):

```python
class TestSetPermissionMode:
    """Tests for Agent.set_permission_mode() SDK interaction."""

    @pytest.mark.asyncio
    async def test_planswarm_skips_sdk_call(self):
        """planSwarm is claudechic-specific; SDK call is skipped entirely."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"

        await agent.set_permission_mode("planSwarm")

        assert agent.permission_mode == "planSwarm"
        agent.client.set_permission_mode.assert_not_called()

    @pytest.mark.asyncio
    async def test_regular_mode_sets_sdk_with_cast(self):
        """Non-planSwarm modes pass through to SDK via cast(PermissionMode, mode)."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"

        await agent.set_permission_mode("plan")

        assert agent.permission_mode == "plan"
        agent.client.set_permission_mode.assert_called_once_with("plan")

    @pytest.mark.asyncio
    async def test_no_sdk_call_when_disconnected(self):
        """No SDK call if client is None or session_id is missing."""
        agent = _make_agent()
        agent.client = None
        agent.permission_mode = "default"

        await agent.set_permission_mode("acceptEdits")

        assert agent.permission_mode == "acceptEdits"
```

- [ ] **Step 2: Run tests to verify the planSwarm test FAILS (current code calls SDK)**

Run: `uv run python -m pytest tests/test_agent.py::TestSetPermissionMode -v`
Expected: `test_planswarm_skips_sdk_call` FAILS (current code calls SDK with "plan")

### Phase: GREEN (implement the change)

- [ ] **Step 3: Add `PermissionMode` and `cast` to imports in agent.py**

At line 15, change:
```python
from typing import TYPE_CHECKING, Any, Literal
```
to:
```python
from typing import TYPE_CHECKING, Any, Literal, cast
```

At line 28, add `PermissionMode` to the SDK types import:
```python
from claude_agent_sdk.types import (
    PermissionMode,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    StreamEvent,
    ToolPermissionContext,
)
```

- [ ] **Step 4: Refactor `set_permission_mode` method**

Replace lines 951-973 in `claudechic/agent.py`:

```python
    async def set_permission_mode(self, mode: str) -> None:
        """Update permission mode via SDK and emit event.

        Args:
            mode: One of 'default', 'acceptEdits', 'plan', 'planSwarm'.
                  'planSwarm' is claudechic-specific; the SDK call is skipped
                  since the SDK's PermissionMode Literal doesn't include it.
        """
        assert mode in self.PERMISSION_MODES, f"Invalid permission mode: {mode}"
        if self.permission_mode != mode:
            self.permission_mode = mode
            # Fetch plan path when entering plan mode
            if mode == "plan":
                await self.ensure_plan_path()
            # Only call SDK if connected and mode is SDK-recognized.
            # "planSwarm" is claudechic-specific; skip the SDK call entirely.
            if self.client and self.session_id and mode != "planSwarm":
                await self.client.set_permission_mode(cast(PermissionMode, mode))
            if self.observer:
                self.observer.on_permission_mode_changed(self)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_agent.py::TestSetPermissionMode -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests PASS

- [ ] **Step 7: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: ruff, ruff-format, pyright all pass

- [ ] **Step 8: Commit**

```bash
git add claudechic/agent.py tests/test_agent.py
git commit -m "refactor: use cast(PermissionMode) and skip planSwarm SDK call

Adopt upstream's pattern for set_permission_mode:
- Use cast(PermissionMode, mode) instead of type: ignore
- Skip SDK call for planSwarm entirely (instead of mapping to 'plan')

This aligns with upstream 0.4.20's implementation so the merge
is conflict-free. Behavioral change: planSwarm no longer sends
'plan' to SDK — acceptable since planSwarm is our internal mode
and the SDK doesn't enforce plan-mode blocking."
```

---

## Task 3: Merge Verification (Integration Test)

**Files:**
- Create: `tests/test_merge_convergence.sh` (temporary verification script)

### Phase: Verify the actual merge flow

- [ ] **Step 1: Create merge verification script**

```bash
#!/usr/bin/env bash
# Verify that the pre-aligned convergence_target merges cleanly
# with 0.4.20, 0.4.21, and origin/main in sequence.
set -euo pipefail

echo "=== Phase 1: Merge 0.4.20 into convergence_target ==="
git checkout -b merge-verify-0420 HEAD
git merge --no-ff 0.4.20 -m "Merge 0.4.20"
echo "PASS: 0.4.20 merged cleanly"

echo ""
echo "=== Phase 2: Merge 0.4.21 ==="
git merge --no-ff 0.4.21 -m "Merge 0.4.21"
echo "PASS: 0.4.21 merged cleanly"

echo ""
echo "=== Phase 3: Merge origin/main ==="
git merge --no-ff origin/main -m "Merge origin/main"
echo "PASS: origin/main merged cleanly"

echo ""
echo "=== Phase 4: Run tests on fully merged state ==="
uv run python -m pytest tests/ -n auto -q
echo "PASS: All tests pass on merged state"

echo ""
echo "=== Phase 5: Pre-commit hooks ==="
uv run pre-commit run --all-files
echo "PASS: All hooks pass"

echo ""
echo "=== CLEANUP ==="
git checkout -
git branch -D merge-verify-0420

echo ""
echo "ALL MERGES CLEAN. Pre-alignment successful."
```

- [ ] **Step 2: Run the merge verification script**

Run: `bash tests/test_merge_convergence.sh`
Expected: All 5 phases PASS with no conflicts

- [ ] **Step 3: If any merge conflicts, diagnose and fix**

If Phase 1 (0.4.20) conflicts:
- Check `git diff --name-only --diff-filter=U` for conflicted files
- Compare our version with upstream's expected context
- Apply additional pre-alignment patches

If Phase 2 (0.4.21) conflicts:
- 0.4.21 only touches `widgets/content/diff.py` (not modified by us), `app.py` (SSH warning — additive), `pyproject.toml`, `tests/test_widgets.py`
- Most likely conflict: `pyproject.toml` version string or `tests/test_widgets.py` insertion point

If Phase 3 (origin/main) conflicts:
- origin/main adds worktree features in `features/worktree/` (not touched by 0.4.20/0.4.21)
- Most likely conflict: `app.py` or `pyproject.toml`

- [ ] **Step 4: Remove verification script (not for permanent repo)**

```bash
rm tests/test_merge_convergence.sh
```

- [ ] **Step 5: Final commit (if any additional fixes were needed)**

```bash
git add -A
git commit -m "fix: additional pre-alignment adjustments for clean merge

[Describe any additional changes needed based on merge verification]"
```

---

## Task 4: Visual Verification via Live Testing

**Files:**
- No permanent files created

### Phase: Verify UI behavior after refactoring

- [ ] **Step 1: Start the app and verify permission mode cycling**

Run: `uv run claudechic`

Manual verification checklist:
1. App launches without errors
2. Press Shift+Tab repeatedly — cycles through: default → acceptEdits → plan → default
3. Footer label updates correctly for each mode:
   - "Auto-edit: off" (default)
   - "Auto-edit: on" (acceptEdits)
   - "Plan mode" (plan)
4. Context bar shows token counts after first response (regression check)
5. `/effort` command works (regression check)

- [ ] **Step 2: Verify via remote testing (headless)**

If `./scripts/claudechic-remote` is available:

```bash
# Start remote testing server
./scripts/claudechic-remote 9999 &
sleep 3

# Take screenshot
curl -s http://localhost:9999/screenshot > /tmp/pre-alignment-baseline.png

# Send shift-tab to cycle permission mode
curl -s -X POST http://localhost:9999/key -d '{"key": "shift+tab"}'
sleep 1
curl -s http://localhost:9999/screenshot > /tmp/pre-alignment-accept-edits.png

# Verify state
curl -s http://localhost:9999/state | python3 -c "
import json, sys
state = json.load(sys.stdin)
print(f'Permission mode: {state.get(\"permission_mode\", \"unknown\")}')
"

# Cleanup
kill %1
```

- [ ] **Step 3: Document verification results**

Record in commit message or PR description:
- All 4 permission modes display correctly
- No visual regressions in footer layout
- Context bar, effort label unaffected

---

## Task 5: Tag New Convergence Point

**Files:**
- No files modified

- [ ] **Step 1: Verify all tests pass one final time**

Run: `uv run python -m pytest tests/ -n auto -q && uv run pre-commit run --all-files`
Expected: All pass

- [ ] **Step 2: Tag the pre-aligned state**

```bash
git tag convergence_target_aligned
```

- [ ] **Step 3: Document the alignment in a brief note**

```bash
git tag -a convergence_target_aligned -m "Pre-aligned for clean merge of 0.4.20, 0.4.21, origin/main

Changes from convergence_target:
- footer.py: watch_permission_mode is now table-driven (_MODE_DISPLAY dict)
- agent.py: set_permission_mode uses cast(PermissionMode) + skips planSwarm
- tests/test_agent.py: updated assertions to match skip pattern
- tests/test_footer.py: new tests for table-driven mode display

Merge verification: all three targets merge without conflicts."
```

---

## Inputs / Deliverables Summary

| Task | Input | Deliverable | Verification |
|------|-------|-------------|--------------|
| 1 | Current if/elif footer code | Table-driven `_MODE_DISPLAY` dict | `tests/test_footer.py` (5 tests pass) |
| 2 | Current `type: ignore` SDK call | `cast(PermissionMode)` + skip planSwarm | `tests/test_agent.py::TestSetPermissionMode` (3 tests pass) |
| 3 | Pre-aligned branch | Clean merge of 0.4.20 → 0.4.21 → origin/main | `test_merge_convergence.sh` (all phases pass) |
| 4 | Running app | Visual confirmation of mode cycling | Screenshots / remote testing state |
| 5 | All above passing | Tagged `convergence_target_aligned` | Tag exists, tests pass |

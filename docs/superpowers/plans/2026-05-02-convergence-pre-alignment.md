# Convergence Pre-Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify `convergence_target` so that `git merge --no-ff 0.4.20`, then `git merge --no-ff 0.4.21`, then `git merge --no-ff origin/main` all complete without conflicts.

**Architecture:** Two surgical refactors that adopt upstream 0.4.20's exact code patterns without changing behavior: (1) table-driven permission mode display in footer matching upstream's `_MODE_DISPLAY` signature exactly, (2) `cast(PermissionMode, ...)` for the SDK call while preserving planSwarm→"plan" mapping. A merge verification phase runs the actual `git merge --no-ff` sequence to confirm zero conflicts.

**Tech Stack:** Python 3.11, Textual (TUI framework), claude-agent-sdk, pytest

**Non-goals:**
- Adding "auto" mode (that comes from 0.4.20 via merge)
- Changing planSwarm behavioral semantics (planSwarm still sends "plan" to SDK)
- Restructuring test file organization

**Rollback:** If Task 3 (merge verification) fails, `git reset --hard convergence_target` and resolve conflicts manually during the merge instead.

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `claudechic/widgets/layout/footer.py` | Status footer with permission mode display | Refactor `watch_permission_mode` to upstream's exact table-driven pattern |
| `claudechic/agent.py` | Agent class with SDK permission handling | Use `cast(PermissionMode, ...)` for type safety; keep planSwarm→"plan" mapping |
| `tests/test_agent.py` | Agent unit tests | Update `TestSetPermissionMode` to use `cast()` assertion pattern |
| `tests/test_footer.py` | Footer permission mode tests (new) | Characterization tests for table-driven mode display |

---

## Task 1: Table-Driven Permission Mode Display in Footer

**Files:**
- Modify: `claudechic/widgets/layout/footer.py:294-319`
- Create: `tests/test_footer.py`

### Phase: CHARACTERIZE (write behavior-locking tests before refactoring)

- [ ] **Step 1: Create `tests/test_footer.py` with mode display tests**

```python
"""Tests for StatusFooter permission mode display."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

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
            rendered = label.render()
            assert "auto-edit: off" in rendered.plain.lower()
            assert not label.has_class("active")
            assert not label.has_class("plan-mode")
            assert not label.has_class("plan-swarm-mode")

    @pytest.mark.asyncio
    async def test_accept_edits_mode_shows_auto_edit_on(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "acceptEdits"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "auto-edit: on" in rendered.plain.lower()
            assert label.has_class("active")
            assert not label.has_class("plan-mode")

    @pytest.mark.asyncio
    async def test_plan_mode_shows_plan_mode(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "plan"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "plan mode" in rendered.plain.lower()
            assert label.has_class("plan-mode")
            assert not label.has_class("active")

    @pytest.mark.asyncio
    async def test_plan_swarm_mode_shows_plan_swarm(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "planSwarm"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "plan swarm" in rendered.plain.lower()
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
            rendered = label.render()
            assert "auto-edit: off" in rendered.plain.lower()
            assert not label.has_class("active")

    @pytest.mark.asyncio
    async def test_transition_clears_previous_mode_class(self):
        """Switching modes must remove the previous mode's CSS class."""
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            label = footer.query_one("#permission-mode-label")

            # Enter planSwarm
            footer.permission_mode = "planSwarm"
            await pilot.pause()
            assert label.has_class("plan-swarm-mode")

            # Switch to acceptEdits — planSwarm class must be gone
            footer.permission_mode = "acceptEdits"
            await pilot.pause()
            assert label.has_class("active")
            assert not label.has_class("plan-swarm-mode")
            assert not label.has_class("plan-mode")

            # Switch to default — all classes must be gone
            footer.permission_mode = "default"
            await pilot.pause()
            assert not label.has_class("active")
            assert not label.has_class("plan-swarm-mode")
            assert not label.has_class("plan-mode")
```

- [ ] **Step 2: Run tests to verify they pass with current implementation**

Run: `uv run python -m pytest tests/test_footer.py -v`
Expected: All 6 tests PASS (characterization: behavior is same before and after refactor)

### Phase: REFACTOR (adopt upstream's exact pattern)

- [ ] **Step 3: Refactor `watch_permission_mode` in footer.py**

Replace the if/elif chain (lines 294-319) in `claudechic/widgets/layout/footer.py` with upstream's exact pattern:

```python
    # Table-driven permission mode display.
    # Format: mode_name -> (label_text, css_class_to_activate_or_None)
    # Upstream 0.4.20 will add "auto": ("Auto", "auto-mode") to this table
    # and "auto-mode" will appear in _MODE_CLASSES automatically.
    _MODE_DISPLAY: dict[str, tuple[str, str | None]] = {
        "default": ("Auto-edit: off", None),
        "planSwarm": ("Plan swarm", "plan-swarm-mode"),
        "plan": ("Plan mode", "plan-mode"),
        "acceptEdits": ("Auto-edit: on", "active"),
    }
    _MODE_CLASSES = tuple(cls for _, cls in _MODE_DISPLAY.values() if cls)

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

Key details matching upstream exactly:
- `dict[str, tuple[str, str | None]]` — `None` for no-class, not empty string
- `_MODE_CLASSES = tuple(...)` — derived from dict values, not a separate list
- Same `get()` fallback pattern with default entry
- Same `cls == active` loop pattern

- [ ] **Step 4: Run tests to verify they still pass**

Run: `uv run python -m pytest tests/test_footer.py -v`
Expected: All 6 tests PASS (behavior unchanged)

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All existing tests PASS

- [ ] **Step 6: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: ruff, ruff-format, pyright all pass

- [ ] **Step 7: Commit**

```bash
git add tests/test_footer.py claudechic/widgets/layout/footer.py
git commit -m "refactor: make watch_permission_mode table-driven for upstream convergence

Convert the if/elif permission mode display chain to upstream 0.4.20's
exact _MODE_DISPLAY dict pattern: dict[str, tuple[str, str | None]] with
_MODE_CLASSES derived as tuple from dict values. Upstream's merge adds
'auto': ('Auto', 'auto-mode') as a new dict entry — conflict-free.

No behavioral change — all characterization tests pass."
```

---

## Task 2: Cast-Based SDK Permission Mode Call in Agent

**Files:**
- Modify: `claudechic/agent.py:15` (typing import), `claudechic/agent.py:28-34` (SDK types import), `claudechic/agent.py:951-973` (set_permission_mode)
- Modify: `tests/test_agent.py:147-176` (TestSetPermissionMode)

**Behavioral constraint:** planSwarm STILL sends "plan" to SDK. This is intentional — the SDK enforces plan-mode restrictions server-side, and our `_handle_permission` also blocks mutating tools. Removing this call would create an enforcement gap. The only change is using `cast(PermissionMode, ...)` instead of `# type: ignore[arg-type]`.

### Phase: RED (update test expectations to match cast pattern)

- [ ] **Step 1: Update `TestSetPermissionMode` in `tests/test_agent.py`**

Replace lines 147-176:

```python
class TestSetPermissionMode:
    """Tests for Agent.set_permission_mode() SDK interaction."""

    @pytest.mark.asyncio
    async def test_planswarm_sends_plan_to_sdk(self):
        """planSwarm maps to 'plan' for SDK enforcement (plan-mode blocking)."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"

        await agent.set_permission_mode("planSwarm")

        assert agent.permission_mode == "planSwarm"
        # SDK receives "plan" (closest enforceable mode)
        agent.client.set_permission_mode.assert_called_once_with("plan")

    @pytest.mark.asyncio
    async def test_regular_mode_passes_through_to_sdk(self):
        """Non-planSwarm modes pass through to SDK via cast(PermissionMode)."""
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

    @pytest.mark.asyncio
    async def test_plan_mode_calls_ensure_plan_path(self):
        """Entering plan mode should trigger plan path fetch."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"
        agent.ensure_plan_path = AsyncMock()

        await agent.set_permission_mode("plan")

        agent.ensure_plan_path.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they pass (these match current behavior)**

Run: `uv run python -m pytest tests/test_agent.py::TestSetPermissionMode -v`
Expected: All 4 tests PASS (characterization — behavior is unchanged)

### Phase: GREEN (refactor to use cast)

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

- [ ] **Step 4: Refactor `set_permission_mode` method (preserve planSwarm behavior)**

Replace lines 951-973 in `claudechic/agent.py`:

```python
    async def set_permission_mode(self, mode: str) -> None:
        """Update permission mode via SDK and emit event.

        Args:
            mode: One of 'default', 'acceptEdits', 'plan', 'planSwarm'.
                  'planSwarm' is claudechic-specific; the SDK receives 'plan'
                  (closest enforceable mode) to maintain server-side blocking.
        """
        assert mode in self.PERMISSION_MODES, f"Invalid permission mode: {mode}"
        if self.permission_mode != mode:
            self.permission_mode = mode
            # Fetch plan path when entering plan mode
            if mode == "plan":
                await self.ensure_plan_path()
            # Only call SDK if connected (client exists and has active session).
            # "planSwarm" maps to "plan" for SDK enforcement; all other modes
            # pass through directly. Uses cast() for type safety (validated
            # by the assert above).
            if self.client and self.session_id:
                sdk_mode = "plan" if mode == "planSwarm" else mode
                await self.client.set_permission_mode(
                    cast(PermissionMode, sdk_mode)
                )
            if self.observer:
                self.observer.on_permission_mode_changed(self)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_agent.py::TestSetPermissionMode -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests PASS

- [ ] **Step 7: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: ruff, ruff-format, pyright all pass

- [ ] **Step 8: Commit**

```bash
git add claudechic/agent.py tests/test_agent.py
git commit -m "refactor: use cast(PermissionMode) in set_permission_mode

Replace type: ignore with cast(PermissionMode, sdk_mode) for type safety.
Preserves existing behavior: planSwarm still sends 'plan' to SDK for
server-side plan-mode enforcement. The cast pattern matches upstream
0.4.20's approach, enabling conflict-free merge.

No behavioral change — planSwarm continues to send 'plan' to SDK."
```

---

## Task 3: Merge Verification

**Files:**
- No permanent files created (script is ephemeral)

### Phase: Verify the actual merge flow

- [ ] **Step 1: Run merge verification inline**

```bash
#!/usr/bin/env bash
# Verify pre-aligned state merges cleanly with all three targets.
set -euo pipefail

STARTING_BRANCH=$(git branch --show-current)
TEMP_BRANCH="merge-verify-$$"

# Cleanup on any failure
trap 'git merge --abort 2>/dev/null; git checkout "$STARTING_BRANCH" 2>/dev/null; git branch -D "$TEMP_BRANCH" 2>/dev/null' ERR EXIT

echo "=== Phase 1: Merge 0.4.20 ==="
git checkout -b "$TEMP_BRANCH" HEAD
git merge --no-ff 0.4.20 -m "Merge 0.4.20"
echo "PASS: 0.4.20 merged cleanly"

echo ""
echo "=== Phase 2: Run tests after 0.4.20 merge ==="
uv run python -m pytest tests/ -n auto -q
echo "PASS: Tests pass after 0.4.20"

echo ""
echo "=== Phase 3: Merge 0.4.21 ==="
git merge --no-ff 0.4.21 -m "Merge 0.4.21"
echo "PASS: 0.4.21 merged cleanly"

echo ""
echo "=== Phase 4: Run tests after 0.4.21 merge ==="
uv run python -m pytest tests/ -n auto -q
echo "PASS: Tests pass after 0.4.21"

echo ""
echo "=== Phase 5: Merge origin/main ==="
git merge --no-ff origin/main -m "Merge origin/main"
echo "PASS: origin/main merged cleanly"

echo ""
echo "=== Phase 6: Run tests after full merge ==="
uv run python -m pytest tests/ -n auto -q
echo "PASS: All tests pass on fully merged state"

echo ""
echo "=== Phase 7: Pre-commit hooks ==="
uv run pre-commit run --all-files
echo "PASS: All hooks pass"

echo ""
echo "ALL MERGES CLEAN. Pre-alignment successful."
```

- [ ] **Step 2: Analyze results**

If any phase fails:
- **Phase 1 conflict:** Our pre-alignment missed something. Check `git diff --name-only --diff-filter=U`. The most likely cause is a `_MODE_DISPLAY` type or value mismatch. Fix and re-run.
- **Phase 2 test failure:** The merge introduced test incompatibilities. Check which tests fail and whether upstream's test additions conflict with our fixtures.
- **Phase 3-4 conflict/failure:** 0.4.21 touches `widgets/content/diff.py` (we haven't modified — should be clean) and `pyproject.toml` (version). Fix version conflict.
- **Phase 5-6 conflict/failure:** origin/main adds worktree features in files 0.4.20/0.4.21 don't touch. Should be clean. If `pyproject.toml` conflicts, fix version.

- [ ] **Step 3: If additional fixes needed, apply and re-verify**

```bash
# Fix any issues, then re-run from Phase 1
```

---

## Task 4: Tag New Convergence Point

**Files:**
- No files modified

- [ ] **Step 1: Verify all tests pass one final time**

Run: `uv run python -m pytest tests/ -n auto -q && uv run pre-commit run --all-files`
Expected: All pass

- [ ] **Step 2: Create annotated tag**

```bash
git tag -a convergence_target_aligned -m "Pre-aligned for clean merge of 0.4.20, 0.4.21, origin/main

Changes from convergence_target:
- footer.py: watch_permission_mode uses _MODE_DISPLAY dict (matching upstream 0.4.20 exactly)
- agent.py: set_permission_mode uses cast(PermissionMode, sdk_mode) (type-safe)
- tests/test_agent.py: updated to 4 tests covering cast pattern + ensure_plan_path
- tests/test_footer.py: new characterization tests (6 tests) for mode display + transitions

Merge verification: all three targets merge without conflicts.
Behavioral: no change — planSwarm still sends 'plan' to SDK."
```

---

## Inputs / Deliverables Summary

| Task | Input | Deliverable | Verification |
|------|-------|-------------|--------------|
| 1 | Current if/elif footer code | `_MODE_DISPLAY: dict[str, tuple[str, str \| None]]` matching upstream exactly | `tests/test_footer.py` (6 tests pass) |
| 2 | Current `type: ignore` + planSwarm→plan mapping | `cast(PermissionMode, sdk_mode)` preserving planSwarm behavior | `tests/test_agent.py::TestSetPermissionMode` (4 tests pass) |
| 3 | Pre-aligned branch | Clean merge of 0.4.20 → 0.4.21 → origin/main with intermediate test runs | All 7 phases pass |
| 4 | All above passing | Tagged `convergence_target_aligned` | Tag exists, tests pass |

---

## Acceptance Criteria

1. `git merge --no-ff 0.4.20` from `convergence_target_aligned` completes with 0 conflicts
2. `git merge --no-ff 0.4.21` on top of that completes with 0 conflicts
3. `git merge --no-ff origin/main` on top of that completes with 0 conflicts
4. `uv run python -m pytest tests/ -n auto -q` passes after each merge
5. `uv run pre-commit run --all-files` passes on final state
6. planSwarm still sends "plan" to SDK (verified by `test_planswarm_sends_plan_to_sdk`)
7. All 4 permission modes display correctly in footer (verified by `test_footer.py`)

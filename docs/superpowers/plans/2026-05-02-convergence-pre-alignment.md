# Convergence Pre-Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify `convergence_target` to achieve zero-conflict merge of `0.4.20` on `agent.py` and minimize the footer conflict to a trivial 1-line resolution, enabling the sequence `git merge --no-ff 0.4.20`, then `0.4.21`, then `origin/main`.

**Architecture:** Two surgical refactors: (1) table-driven permission mode display in footer using upstream 0.4.20's exact `_MODE_DISPLAY` pattern, (2) `cast(PermissionMode, mode)` in `set_permission_mode` reverting to the base's `mode != "planSwarm"` skip pattern (matching upstream exactly). A merge verification phase confirms the results.

**Tech Stack:** Python 3.11, Textual (TUI framework), claude-agent-sdk, pytest

**Non-goals:**
- Adding "auto" mode (arrives via 0.4.20 merge)
- Re-adding planSwarm→"plan" SDK enforcement (defer to post-merge commit)

**Rollback:** If merge verification fails beyond the documented trivial residual, abandon pre-alignment and resolve conflicts manually during merge (~10 minutes for 3 hunks).

**Expected merge outcome after pre-alignment:**
- `agent.py`: ZERO conflict (our output is byte-identical to upstream's)
- `footer.py`: 1 trivial conflict (upstream adds `"auto"` dict entry; ours has `call_after_refresh`)
- `tests/test_agent.py`: add/add conflict (trivial: concatenate both test classes)
- All other files: auto-merge clean

Total manual resolution: ~2 minutes (add 1 dict entry + concatenate test classes).

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `claudechic/widgets/layout/footer.py` | Status footer with permission mode display | Refactor `watch_permission_mode` to upstream's exact table-driven pattern |
| `claudechic/agent.py` | Agent class with SDK permission handling | Use `cast(PermissionMode, mode)` + revert to base's `mode != "planSwarm"` skip |
| `tests/test_agent.py` | Agent unit tests | Update `TestSetPermissionMode` to match skip pattern |
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
            assert "auto-edit: off" in rendered.plain.lower()  # type: ignore[union-attr]
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
            assert "auto-edit: on" in rendered.plain.lower()  # type: ignore[union-attr]
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
            assert "plan mode" in rendered.plain.lower()  # type: ignore[union-attr]
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
            assert "plan swarm" in rendered.plain.lower()  # type: ignore[union-attr]
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
            assert "auto-edit: off" in rendered.plain.lower()  # type: ignore[union-attr]
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
Expected: All 6 tests PASS (characterization — behavior is same before and after refactor)

### Phase: REFACTOR (adopt upstream's exact pattern)

- [ ] **Step 3: Refactor `watch_permission_mode` in footer.py**

Replace the if/elif chain (lines 294-319) in `claudechic/widgets/layout/footer.py` with upstream 0.4.20's exact pattern (matching comment text, type signature, and derivation):

```python
    # Maps permission_mode → (display text, active CSS class or None).
    # "default" gets no class and keeps plain styling. Adding a new mode
    # means one entry here; _MODE_CLASSES is derived below.
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
            text, active = self._MODE_DISPLAY.get(value, self._MODE_DISPLAY["default"])
            label.update(text)
            for cls in self._MODE_CLASSES:
                label.set_class(cls == active, cls)
        self.call_after_refresh(self._render_cwd_label)
```

Key details matching upstream EXACTLY:
- Comment text: upstream's exact 3-line comment (not our own)
- Type: `dict[str, tuple[str, str | None]]` — `None` for no-class
- Derivation: `_MODE_CLASSES = tuple(cls for _, cls in _MODE_DISPLAY.values() if cls)`
- Lookup: single-line `self._MODE_DISPLAY.get(value, self._MODE_DISPLAY["default"])`
- Loop: `for cls in self._MODE_CLASSES: label.set_class(cls == active, cls)`
- Our addition (not in upstream): `self.call_after_refresh(self._render_cwd_label)` at end

- [ ] **Step 4: Run tests to verify they still pass**

Run: `uv run python -m pytest tests/test_footer.py -v`
Expected: All 6 tests PASS (behavior unchanged)

- [ ] **Step 5: Run full test suite + pre-commit**

Run: `uv run python -m pytest tests/ -n auto -q && uv run pre-commit run --all-files`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add tests/test_footer.py claudechic/widgets/layout/footer.py
git commit -m "refactor: make watch_permission_mode table-driven for upstream convergence

Convert the if/elif permission mode display chain to upstream 0.4.20's
exact _MODE_DISPLAY dict pattern with matching comment text, type
signature (dict[str, tuple[str, str | None]]), and _MODE_CLASSES
derivation. Upstream's merge adds 'auto': ('Auto', 'auto-mode') as
a new dict entry — trivial 1-line merge resolution.

No behavioral change — all characterization tests pass."
```

---

## Task 2: Cast-Based SDK Permission Mode Call in Agent

**Files:**
- Modify: `claudechic/agent.py:15` (typing import), `claudechic/agent.py:28-34` (SDK types import), `claudechic/agent.py:951-973` (set_permission_mode)
- Modify: `tests/test_agent.py:147-176` (TestSetPermissionMode)

**Behavioral note:** This task reverts the planSwarm→"plan" SDK mapping that convergence_target added (commit `d16d091`), returning to the base's (`a6624cf`) behavior where planSwarm skips the SDK call entirely. This makes our code identical in structure to upstream's, enabling zero-conflict merge. The planSwarm→"plan" enforcement can be re-added as a separate commit AFTER the merge sequence.

**Why this is safe:** The base version (released as 0.4.19) already skipped the SDK call for planSwarm. To prevent any enforcement gap, this task ALSO adds `"planSwarm"` to the local `_handle_permission()` blocking check (Step 5). After this change, planSwarm is enforced locally (our `_handle_permission` blocks mutating tools for both `"plan"` and `"planSwarm"`) without depending on the SDK call. This is strictly BETTER than the base (which had no planSwarm enforcement at all) and equivalent to convergence_target's current enforcement (which relied on the SDK).

### Phase: RED (write tests matching upstream's skip pattern)

- [ ] **Step 1: Update `TestSetPermissionMode` in `tests/test_agent.py`**

Replace lines 147-176:

```python
class TestSetPermissionMode:
    """Tests for Agent.set_permission_mode() SDK interaction."""

    @pytest.mark.asyncio
    async def test_planswarm_skips_sdk_call(self):
        """planSwarm is claudechic-specific; SDK call is skipped entirely.

        This matches the base (a6624cf) and upstream (0.4.20) behavior.
        planSwarm enforcement relies on local _handle_permission blocking.
        """
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"

        await agent.set_permission_mode("planSwarm")

        assert agent.permission_mode == "planSwarm"
        agent.client.set_permission_mode.assert_not_called()

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
        """Entering plan mode triggers plan path fetch.

        Note: planSwarm intentionally does NOT call ensure_plan_path because
        it is not entering SDK-level plan mode (just local UI state).
        """
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"
        agent.ensure_plan_path = AsyncMock()

        await agent.set_permission_mode("plan")

        agent.ensure_plan_path.assert_called_once()
```

- [ ] **Step 2: Run tests to verify `test_planswarm_skips_sdk_call` FAILS**

Run: `uv run python -m pytest tests/test_agent.py::TestSetPermissionMode::test_planswarm_skips_sdk_call -v`
Expected: FAILS (current code sends "plan" to SDK, test expects no call)

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

- [ ] **Step 4: Refactor `set_permission_mode` to produce upstream's EXACT output**

Replace lines 951-973 in `claudechic/agent.py`. The ONLY diff from base should be: the `await` line gains `cast()` + an inline comment (matching upstream byte-for-byte). All other lines (docstring, comment block) remain UNCHANGED from base:

```python
    async def set_permission_mode(self, mode: str) -> None:
        """Update permission mode via SDK and emit event.

        Args:
            mode: One of 'default', 'acceptEdits', 'plan'
        """
        assert mode in self.PERMISSION_MODES, f"Invalid permission mode: {mode}"
        if self.permission_mode != mode:
            self.permission_mode = mode
            # Fetch plan path when entering plan mode
            if mode == "plan":
                await self.ensure_plan_path()
            # Only call SDK if connected (client exists and has active connection).
            # "planSwarm" is claudechic-specific; skip SDK call for it since the
            # SDK's PermissionMode Literal doesn't include it.
            if self.client and self.session_id and mode != "planSwarm":
                # Validated by the assert above; cast for the SDK's Literal type.
                await self.client.set_permission_mode(cast(PermissionMode, mode))
            if self.observer:
                self.observer.on_permission_mode_changed(self)
```

This is byte-identical to upstream 0.4.20's version. Both sides produce identical output from the same base → git auto-resolves (keeps one copy).

> **Note on docstring:** The method docstring says `mode: One of 'default', 'acceptEdits', 'plan'` (matching upstream exactly). `planSwarm` is intentionally omitted from the docstring (as upstream does) because it's a local-only mode not exposed to users. The `PERMISSION_MODES` set at line 931 still includes `"planSwarm"` and the assert validates it — the docstring is for human readers, not enforcement.

- [ ] **Step 5: Add planSwarm to local `_handle_permission` enforcement**

In `claudechic/agent.py`, find line ~811:
```python
        if self.permission_mode == "plan" and tool_name in self.PLAN_MODE_BLOCKED_TOOLS:
```

Replace with:
```python
        if self.permission_mode in ("plan", "planSwarm") and tool_name in self.PLAN_MODE_BLOCKED_TOOLS:
```

This ensures planSwarm retains plan-mode tool blocking via the `can_use_tool` callback even though the SDK call is skipped.

- [ ] **Step 5b: Add planSwarm to `_plan_mode_hooks` in app.py (defense-in-depth)**

In `claudechic/app.py`, find line ~677:
```python
            if permission_mode == "plan" and tool_name in blocked_tools:
```

Replace with:
```python
            if permission_mode in ("plan", "planSwarm") and tool_name in blocked_tools:
```

This updates the `PreToolUse` hook (a secondary enforcement layer that checks the SDK-reported mode). Note: when planSwarm skips the SDK call, the SDK-reported mode remains the previous value (e.g., "default"), so this hook won't fire. The primary enforcement is `_handle_permission` (Step 5). This change ensures defense-in-depth if the SDK ever reports "planSwarm" in hook_input.

> **Merge safety:** Upstream 0.4.20 does NOT modify `_plan_mode_hooks` — this line is in a stable region untouched by the merge.

- [ ] **Step 5c: Add test for planSwarm blocking in `_handle_permission`**

Add to `tests/test_agent.py` after the `TestSetPermissionMode` class:

```python
class TestPlanSwarmEnforcement:
    """Verify planSwarm blocks mutating tools via _handle_permission."""

    @pytest.mark.asyncio
    async def test_planswarm_blocks_mutating_tools(self):
        """planSwarm must deny Edit/Write/Bash just like plan mode."""
        from claudechic.permissions import PermissionRequest

        agent = _make_agent()
        agent.permission_mode = "planSwarm"

        # Mock the observer to capture permission requests (we won't resolve them)
        agent.observer = MagicMock()
        agent.observer.on_prompt_added = MagicMock()

        # Directly test _handle_permission blocks Bash in planSwarm
        from claude_agent_sdk.types import ToolPermissionContext

        context = ToolPermissionContext(tool_name="Bash", tool_input={})
        result = await agent._handle_permission("Bash", {"command": "rm -rf /"}, context)

        # Should deny (not allow)
        from claude_agent_sdk.types import PermissionResultDeny
        assert isinstance(result, PermissionResultDeny)
```

- [ ] **Step 6: Run tests to verify all pass**

Run: `uv run python -m pytest tests/test_agent.py::TestSetPermissionMode -v`
Expected: All 4 tests PASS

- [ ] **Step 7: Run full test suite + pre-commit**

Run: `uv run python -m pytest tests/ -n auto -q && uv run pre-commit run --all-files`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add claudechic/agent.py tests/test_agent.py
git commit -m "refactor: use cast(PermissionMode), skip planSwarm SDK call, add local enforcement

Adopt upstream 0.4.20's exact set_permission_mode code (byte-identical
output for zero-conflict merge). planSwarm SDK call is skipped (matching
base/upstream), but local _handle_permission now enforces plan-mode
blocking for planSwarm too (no enforcement gap).

This reverts to the shipped 0.4.19 SDK behavior. The planSwarm->plan
enforcement can be re-added post-merge if desired (it was added
by convergence_target but the base never had it)."
```

---

## Task 3: Merge Verification

**Files:**
- No permanent files created (script is ephemeral)

### Phase: Verify the actual merge flow

- [ ] **Step 1: Run merge verification**

```bash
#!/usr/bin/env bash
set -euo pipefail

STARTING_REF=$(git rev-parse HEAD)
TEMP_BRANCH="merge-verify-$$"
# Only clean up on SUCCESS; leave state for diagnosis on failure
cleanup_success() {
    git checkout "$STARTING_REF" 2>/dev/null
    git branch -D "$TEMP_BRANCH" 2>/dev/null
}

echo "=== Creating temp branch ==="
git branch -D "$TEMP_BRANCH" 2>/dev/null || true
git checkout -b "$TEMP_BRANCH" HEAD

echo ""
echo "=== Phase 1: Merge 0.4.20 ==="
if ! git merge --no-ff 0.4.20 -m "Merge 0.4.20"; then
    echo "CONFLICT in Phase 1. Conflicted files:"
    git diff --name-only --diff-filter=U
    echo ""
    echo "Expected conflicts: footer.py (add 'auto' entry) and/or tests/test_agent.py (add/add)."
    echo ""
    echo "To resolve and continue:"
    echo "  1. Fix all conflicted files (see Step 2 below)"
    echo "  2. git add <resolved files>"
    echo "  3. git merge --continue"
    echo "  4. Then run remaining phases manually:"
    echo "     uv run python -m pytest tests/ -n auto -q"
    echo "     git merge --no-ff 0.4.21 -m 'Merge 0.4.21'"
    echo "     uv run python -m pytest tests/ -n auto -q"
    echo "     git merge --no-ff origin/main -m 'Merge origin/main'"
    echo "     uv run python -m pytest tests/ -n auto -q"
    echo "     uv run pre-commit run --all-files"
    echo ""
    echo "Temp branch '$TEMP_BRANCH' left intact for resolution."
    exit 1
fi

echo "PASS: 0.4.20 merged cleanly"

echo ""
echo "=== Phase 2: Tests after 0.4.20 ==="
uv run python -m pytest tests/ -n auto -q
echo "PASS: Tests pass"

echo ""
echo "=== Phase 3: Merge 0.4.21 ==="
git merge --no-ff 0.4.21 -m "Merge 0.4.21"
echo "PASS: 0.4.21 merged cleanly"

echo ""
echo "=== Phase 4: Tests after 0.4.21 ==="
uv run python -m pytest tests/ -n auto -q
echo "PASS: Tests pass"

echo ""
echo "=== Phase 5: Merge origin/main ==="
git merge --no-ff origin/main -m "Merge origin/main"
echo "PASS: origin/main merged cleanly"

echo ""
echo "=== Phase 6: Final tests + hooks ==="
uv run python -m pytest tests/ -n auto -q
uv run pre-commit run --all-files
echo "PASS: All pass"

echo ""
echo "ALL MERGES COMPLETE."
cleanup_success
```

- [ ] **Step 2: Handle expected trivial conflicts**

When Phase 1 reports a conflict on `footer.py`:
1. Open `claudechic/widgets/layout/footer.py`
2. Find the `_MODE_DISPLAY` dict conflict
3. Resolution: add upstream's `"auto": ("Auto", "auto-mode"),` entry to our dict
4. `git add claudechic/widgets/layout/footer.py && git merge --continue`
5. Continue with Phase 2+

When `tests/test_agent.py` has add/add conflict:
1. Resolution: combine both files into one coherent module:
   - Union of imports (ours: `Path, AsyncMock, MagicMock, pytest, Agent`; theirs adds: `json, tmp_path fixture`)
   - Keep our `_make_agent()` helper
   - Keep theirs: `_write_settings(tmp_path, data)` helper
   - Keep ALL test classes from both sides:
     - Ours: `TestUpdateContext`, `TestPreparePrompt`, `TestTokenReminderPattern`, `TestSetPermissionMode`, `TestPlanSwarmEnforcement`
     - Theirs: `test_get_default_permission_mode_*` (module-level functions), `test_to_ui_permission_mode_*` (module-level functions)
   - Ensure no duplicate imports or name collisions
2. `git add tests/test_agent.py`

- [ ] **Step 3: Verify final state**

After all merges complete, verify:
- All tests pass
- Pre-commit hooks pass
- `git log --oneline --graph` shows clean merge history

---

## Task 4: Tag and Document

- [ ] **Step 1: Create annotated tag on pre-aligned state (before merges)**

```bash
git checkout <pre-aligned-sha>  # The commit after Tasks 1+2
git tag -a convergence_target_aligned -m "Pre-aligned for merge of 0.4.20, 0.4.21, origin/main

Changes from convergence_target:
- footer.py: watch_permission_mode uses _MODE_DISPLAY dict (upstream pattern)
- agent.py: set_permission_mode uses cast() + skip planSwarm (upstream pattern)
- tests/test_agent.py: 4 tests covering skip pattern + ensure_plan_path
- tests/test_footer.py: 6 characterization tests for mode display + transitions

Expected merge residuals:
- footer.py: 1-line trivial conflict (add 'auto' dict entry)
- tests/test_agent.py: add/add (concatenate test classes)
- agent.py: ZERO conflict (identical change on both sides)"
```

---

## Acceptance Criteria

1. `agent.py:set_permission_mode` produces byte-identical output to upstream 0.4.20's version
2. `agent.py:_handle_permission` blocks mutating tools for both `"plan"` and `"planSwarm"`
3. `footer.py:watch_permission_mode` uses `_MODE_DISPLAY` dict with upstream's exact comment (Unicode `→`), type, and derivation
4. `uv run python -m pytest tests/ -n auto -q` passes on pre-aligned state
5. `uv run pre-commit run --all-files` passes
6. `git merge --no-ff 0.4.20` produces: zero conflict on agent.py, trivial 1-line conflict on footer.py, add/add on tests/test_agent.py
7. After resolving trivial residuals (~2 min), all 3 merges complete and tests pass
8. planSwarm enforcement is preserved via local `_handle_permission` check (no enforcement gap)

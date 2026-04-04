# Hide Sidebar CWD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide the CWD row in sidebar AgentItem via CSS so each agent entry shows 2 rows instead of 3.

**Architecture:** Two CSS value changes in `AgentItem.DEFAULT_CSS` within `sidebar.py`. No Python logic, test, or data flow changes.

**Tech Stack:** Python, Textual CSS

**Spec:** `docs/superpowers/specs/2026-04-04-remove-sidebar-cwd-design.md`

---

## File Map

- Modify: `claudechic/widgets/layout/sidebar.py:410-467` (DEFAULT_CSS string only)

No files created. No tests modified.

---

### Task 1: Hide CWD row and adjust height

**Files:**
- Modify: `claudechic/widgets/layout/sidebar.py:410-467`

- [ ] **Step 1: Run tests to confirm green baseline**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass.

- [ ] **Step 2: Change `AgentItem` height from 5 to 4**

In `claudechic/widgets/layout/sidebar.py`, inside the `DEFAULT_CSS` string of `AgentItem` (line ~412-413), change:

```python
# Before
    AgentItem {
        height: 5;
        min-height: 5;

# After
    AgentItem {
        height: 4;
        min-height: 4;
```

- [ ] **Step 3: Add `display: none` to `.agent-cwd` rule**

In the same `DEFAULT_CSS` string (line ~453-457), change:

```python
# Before
    AgentItem .agent-cwd {
        height: 1;
        padding: 0 0 0 2;
        overflow: hidden;
    }

# After
    AgentItem .agent-cwd {
        height: 1;
        padding: 0 0 0 2;
        overflow: hidden;
        display: none;
    }
```

- [ ] **Step 4: Run tests to confirm nothing broke**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass (same count as Step 1).

- [ ] **Step 5: Commit**

```bash
git add claudechic/widgets/layout/sidebar.py
git commit -m "feat: hide CWD row in sidebar AgentItem via CSS"
```

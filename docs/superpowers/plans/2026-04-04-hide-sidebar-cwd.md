# Hide Sidebar CWD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide the CWD row in sidebar AgentItem via CSS so each agent entry shows 2 rows instead of 3.

**Architecture:** Two CSS value changes in `AgentItem.DEFAULT_CSS` within `sidebar.py`, plus fix a stale sidebar packing constant in `app.py`. No test changes.

**Tech Stack:** Python, Textual CSS

**Spec:** `docs/superpowers/specs/2026-04-04-remove-sidebar-cwd-design.md`

---

## File Map

- Modify: `claudechic/widgets/layout/sidebar.py:410-467` (DEFAULT_CSS string only)
- Modify: `claudechic/app.py:1305` (fix stale `AGENT_EXPANDED` packing constant)

No files created. No tests modified.

---

### Task 1: Hide CWD row and adjust height

**Files:**
- Modify: `claudechic/widgets/layout/sidebar.py:410-467`

- [ ] **Step 1: Run tests to confirm green baseline**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass.

- [ ] **Step 2: Change `AgentItem` height from 5 to 4 with comment**

In `claudechic/widgets/layout/sidebar.py`, inside the `DEFAULT_CSS` string of `AgentItem` (line ~411-416), change:

```python
# Before
    AgentItem {
        height: 5;
        min-height: 5;
        layout: vertical;
        padding: 1 1 0 2;
    }

# After
    AgentItem {
        /* height = top-padding(1) + name-row(1) + context-row(1) + spacing(1) = 4 */
        /* If restoring .agent-cwd, change back to height: 5; min-height: 5 */
        height: 4;
        min-height: 4;
        layout: vertical;
        padding: 1 1 0 2;
    }
```

- [ ] **Step 3: Add `display: none` to `.agent-cwd` rule with comment**

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
        display: none;  /* Hidden: CWD shown in footer only. Restore -> also set height: 5 above */
    }
```

- [ ] **Step 4: Fix stale `AGENT_EXPANDED` packing constant**

In `claudechic/app.py` (line ~1305), the sidebar packing logic hard-codes the expanded agent height. This constant was stale (said 3, actual CSS was 5). Update it to match the new height of 4:

```python
# Before
        AGENT_EXPANDED = 3  # height: 3 with padding

# After
        AGENT_EXPANDED = 4  # height: 4 (name + context + padding + spacing)
```

- [ ] **Step 5: Run tests to confirm nothing broke**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass (same count as Step 1).

- [ ] **Step 6: Visual verification**

Launch: `uv run claudechic`
Verify:
- Agent items show 2 rows (name + context), no blank gap where CWD was
- Footer still shows CWD for active agent
- Compact mode still works (sidebar items collapse to 1 row)
- `/agent` command still lists per-agent directories

- [ ] **Step 7: Commit**

```bash
git add claudechic/widgets/layout/sidebar.py claudechic/app.py
git commit -m "feat: hide CWD row in sidebar AgentItem via CSS

Also fix stale AGENT_EXPANDED packing constant (3 -> 4)."
```

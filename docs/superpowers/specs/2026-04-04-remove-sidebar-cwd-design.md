# Hide CWD Row from Sidebar AgentItem

**Date:** 2026-04-04
**Status:** Draft (v2 — revised after 4-agent review)
**Branch:** simplify-sidebar

## Summary

Hide the CWD display row in sidebar `AgentItem` via CSS (`display: none`). Each agent entry visually shrinks from three rows to two. All Python code, data flow, and tests remain intact. Trivially reversible by removing one CSS line.

## Motivation

The CWD is already shown in the status footer for the active agent. Hiding it in the sidebar reduces visual noise and gives more vertical space to the agent list and todo panel.

### Accepted tradeoff

The footer only shows the **active** agent's CWD. Hiding sidebar CWD means users with multiple agents lose at-a-glance directory visibility for inactive agents. This is accepted because:

- The `/agent` command still lists all agent directories
- The MCP `list_agents` tool still shows per-agent CWD
- The remote API (`/state`) still exposes `item._cwd` per agent
- Clicking an agent switches to it and updates the footer CWD immediately

## Design

### Approach: CSS-only hide

After a 4-agent review (RoboRev, code reviewer, architect, contrarian), the original deletion approach was replaced with a CSS-only hide. Rationale:

- **Zero Python changes** — no risk of breaking `remote.py` (`item._cwd` access), MCP tools, or call chains
- **Zero test changes** — all existing tests continue to pass
- **Trivially reversible** — remove one CSS rule to restore sidebar CWD
- **No height/layout bugs** — Textual's `display: none` collapses the row automatically

### What changes

#### `claudechic/widgets/layout/sidebar.py` — `AgentItem.DEFAULT_CSS`

Add `display: none` to the existing `.agent-cwd` rule and reduce `AgentItem` height from 5 to 4:

```python
# Before
AgentItem {
    height: 5;
    min-height: 5;
    ...
}
AgentItem .agent-cwd {
    height: 1;
    padding: 0 0 0 2;
    overflow: hidden;
}

# After
AgentItem {
    height: 4;
    min-height: 4;
    ...
}
AgentItem .agent-cwd {
    height: 1;
    padding: 0 0 0 2;
    overflow: hidden;
    display: none;
}
```

That's it. Two edits in one string literal.

### What stays unchanged

- **All Python code** — `_cwd` field, `_render_cwd_label()`, `max_cwd_length`, `update_context(cwd=...)`, `_refresh_detail_rows()`, `format_cwd` import — all remain.
- **All call chains** — `ChatApp._update_sidebar_agent_context()` → `AgentSection.update_agent_context()` → `AgentItem.update_context()` — unchanged.
- **`remote.py`** — `/state` endpoint reads `item._cwd` — continues to work.
- **`mcp.py`** — `list_agents` tool shows `agent.cwd` — continues to work.
- **Footer CWD display** — `StatusFooter.set_cwd()` and `#cwd-label` — unchanged.
- **`format_cwd()` in `formatting.py`** — still used by footer, still tested.
- **All tests** — `tests/test_sidebar_context.py` and `tests/test_formatting.py` — unchanged.
- **Observer protocols** — no changes needed.
- **`WorktreeItem`** — does not display CWD, no changes needed.

### Non-goals

- No changes to the agent data flow or observer protocols
- No changes to `remote.py`, `mcp.py`, or `/agent` command output
- No config flag (YAGNI — if we want one later, the CSS hide is easy to gate)

### Acceptance criteria

- No blank row where CWD was — Textual collapses `display: none` elements
- Footer remains the only visible CWD surface
- Agent switching and reconnect still update footer CWD
- `/agent` command still lists per-agent directories
- Remote API `/state` still returns per-agent `_cwd`
- All existing tests pass without modification

## Risks

- **Minimal.** Two CSS values change in one string literal. No logic changes.
- **Reversibility:** Remove `display: none` and restore `height: 5` / `min-height: 5`.

## Testing

- `uv run python -m pytest tests/ -n auto -q` — all tests pass unchanged
- Visual: sidebar shows 2-row agent items (name + context), no blank gaps
- Visual: footer CWD updates on agent switch
- Visual: compact mode still works (CWD was already hidden in compact)
- Verify: `/agent` command output unchanged

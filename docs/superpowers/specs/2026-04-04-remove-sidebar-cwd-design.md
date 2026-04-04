# Remove CWD Row from Sidebar AgentItem

**Date:** 2026-04-04
**Status:** Draft
**Branch:** simplify-sidebar

## Summary

Remove the current working directory (CWD) display row from the sidebar `AgentItem` widget. Each agent entry shrinks from three rows (name, CWD, context/tokens) to two rows (name, context/tokens). The footer CWD display and all supporting utilities remain untouched.

## Motivation

The CWD is already shown in the status footer for the active agent. Displaying it again in the sidebar adds visual noise without providing new information. Removing it tightens the sidebar, giving more vertical space to the agent list and todo panel.

## Design

### What changes

#### `claudechic/widgets/layout/sidebar.py` — `AgentItem` class

1. **Remove `_cwd` field** and its `""` initialization in `__init__`.
2. **Remove `max_cwd_length`** constant (currently `20`).
3. **Remove `_render_cwd_label()` method** that formats the CWD text.
4. **Remove the `.agent-cwd` Label** from `compose()` — the label that occupies row 2.
5. **Simplify `update_context()`** — remove the `cwd` parameter and all CWD-related update logic. Keep only `tokens` and `max_tokens` parameters and their rendering.

After these changes, `AgentItem` renders:

```
Row 1: [status-indicator] [agent-name] [close-button]
Row 2: [context-usage %] [token-counts]
```

#### `claudechic/styles.tcss`

Remove the following CSS rules:

```tcss
AgentItem .agent-cwd {
    height: 1;
    padding: 0 0 0 2;
    overflow: hidden;
}
```

Also remove any `AgentItem.compact .agent-cwd` rule if it exists as a separate block.

#### Tests — `tests/test_sidebar_context.py`

Remove or update assertions that check for CWD content in `AgentItem`. Any test that calls `update_context()` with a `cwd` argument must be updated to drop that parameter.

### What stays unchanged

- **Footer CWD display** — `StatusFooter.set_cwd()`, `StatusFooter._render_cwd_label()`, and the `#cwd-label` CSS rule all remain.
- **`format_cwd()` in `formatting.py`** — still used by the footer. All constants (`MAX_CWD_LENGTH`, `MIN_CWD_LENGTH`) remain.
- **`tests/test_formatting.py`** — `format_cwd()` tests stay as-is.
- **`WorktreeItem`** — does not display CWD today, no changes needed.
- **Observer protocol** — `AgentObserver` and `AgentManagerObserver` are unaffected; CWD data still flows to the footer via `ChatApp` handlers.

### Callers of `update_context()`

Any call site passing `cwd=` to `AgentItem.update_context()` must be updated to drop that argument. This is expected to be in `ChatApp` (or `ChatScreen`) where agent status events are handled. The CWD value continues to be forwarded to `StatusFooter.set_cwd()` — only the sidebar path is removed.

## Risks

- **Low risk.** This is a deletion-only change with no new logic.
- **Reversibility:** If sidebar CWD is wanted later, re-add the label and method. `format_cwd()` and all infrastructure remain.

## Testing

- Run existing test suite: `uv run python -m pytest tests/ -n auto -q`
- Verify sidebar renders correctly with 1 and multiple agents
- Verify footer CWD still updates on agent switch

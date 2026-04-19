# Upstream Merge: Apply `0411d0c..a6624cf` onto `ebe0694`

**Date:** 2026-04-19
**Branch:** integration
**Method:** Merge (`git merge a6624cf` into integration)

## Problem

The `integration` branch diverged from `upstream/main` at `0411d0c`.
Upstream has since gained 6 commits (effort command, ToolSearch rendering,
/ultrareview passthrough, SDK-based context usage, footer model trimming,
worktree spawn fix).  Integration has ~7,600 lines of local work across
49 files (session-id display, auto-copy fix, tokens-in-footer, special-char
sanitization, toast MarkupError fix).  The two branches must be reconciled.

## Upstream Commits (6)

| SHA | Summary |
|-----|---------|
| `efd3a69` | `/effort` command — per-agent thinking depth (low/medium/high/xhigh/max), `EffortPrompt` widget, disconnect/reconnect cycle |
| `e893a8c` | ToolSearch compact rendering — `strip_mcp_prefix()`, `extract_tool_search_names()`, collapsible tool list |
| `620cfa9` | `/ultrareview` passthrough — added to `SDK_PASSTHROUGH_COMMANDS` |
| `0e220c9` | SDK `get_context_usage()` — live per-model context window, replaces session-file parsing, renames `MAX_CONTEXT_TOKENS` → `DEFAULT_CONTEXT_WINDOW` |
| `c7484d3` | `EffortLabel` in footer + `trim_model_name()` — strips "with 1M context" / "(beta)" suffixes |
| `a6624cf` | `spawn_worktree` fix — `base_branch` actually forwarded, parallel spawns fork from main as siblings |

## Conflict Analysis

### Files with textual conflicts (manual resolution required)

| File | Conflict | Resolution |
|------|----------|------------|
| `claudechic/agent.py` | Both add fields after `self.model` at L184 | Keep both: upstream's `self.effort` + local's `self.tokens`, `self.max_tokens`, `self._context_initialized` |
| `claudechic/app.py` (refresh_context) | Upstream rewrites to use `get_context_usage()` directly; local rewrites with fallback + `agent.update_context()` | **Adopt upstream's SDK-based approach.** Wire `agent.update_context()` to use values from `get_context_usage()` instead of `parse_context_size()`. Keep local's `_context_initialized` gating and `_prepare_prompt()` token injection — they consume context values, they don't produce them. |
| `claudechic/app.py` (reconnect area) | Upstream adds `effort=agent.effort`; local adds `session_id = None` clearing | Keep both changes (additive, non-overlapping logic) |
| `claudechic/widgets/layout/indicators.py` | Import conflict (`DEFAULT_CONTEXT_WINDOW` vs `MAX_CONTEXT_TOKENS`) + local's complete `ContextBar` rewrite | Adopt upstream's `DEFAULT_CONTEXT_WINDOW` rename. Apply it to local's rewritten `ContextBar` (gradient colors, text format). Local's `format_tokens` import stays. |
| `tests/test_formatting.py` | Add/add conflict — upstream creates 22-line file, local creates 323-line file | Concatenate both: local's extensive tests + upstream's `trim_model_name` tests |
| `tests/test_widgets.py` | Both insert tests after `test_status_footer_permission_mode` at L418 | Keep both test blocks sequentially (upstream's effort tests + local's footer tests) |
| `tests/test_app_ui.py` | Import line: upstream adds `AsyncMock`, local adds `patch` | Combine: `from unittest.mock import AsyncMock, MagicMock, patch` |

### Semantic conflicts (no textual conflict, but code breaks post-merge)

| File | Issue | Resolution |
|------|-------|------------|
| `claudechic/formatting.py` | Upstream renames `MAX_CONTEXT_TOKENS` → `DEFAULT_CONTEXT_WINDOW`; local code still imports old name in `app.py` and `indicators.py` | Update all local references to use `DEFAULT_CONTEXT_WINDOW` after merge |
| `claudechic/app.py` | Local's `parse_context_size()` fallback in `refresh_context()` no longer needed since SDK provides `rawMaxTokens` | Remove `parse_context_size()` call from `refresh_context()`. Keep `parse_context_size()` function in `formatting.py` — it's still useful as a pre-connect fallback in `_update_footer_model()`. |

### Files that auto-merge cleanly (no action needed)

`commands.py`, `sessions.py`, `styles.tcss`, `footer.py`, `formatting.py` (textually clean, but semantic fixup needed for the rename).

## Resolution Strategy for `refresh_context()` (The Hard Conflict)

This is the most complex conflict.  Two approaches to context tracking collide.

**Upstream's approach** (adopt as canonical):
```python
async def refresh_context(self) -> None:
    agent = self._agent
    if not agent or not agent.client:
        self.context_bar.tokens = 0
        return
    try:
        usage = await agent.client.get_context_usage()
    except Exception as e:
        log.debug(f"get_context_usage failed: {e}")
        return
    raw_max = usage.get("rawMaxTokens")
    if isinstance(raw_max, int) and raw_max > 0:
        self.context_bar.max_tokens = raw_max
    self.context_bar.tokens = usage.get("totalTokens", 0)
```

**Local additions to preserve** (token injection into prompts):
- `agent.update_context(tokens, max_tokens)` — stores values on the Agent for `_prepare_prompt()` to inject `<system-reminder>N/M tokens</system-reminder>` into every prompt
- `_context_initialized` flag — gates injection until first real data arrives

**Merged resolution:**
```python
async def refresh_context(self) -> None:
    agent = self._agent
    if not agent or not agent.client:
        self.context_bar.tokens = 0
        return
    try:
        usage = await agent.client.get_context_usage()
    except Exception as e:
        log.debug(f"get_context_usage failed: {e}")
        return
    tokens = usage.get("totalTokens", 0)
    raw_max = usage.get("rawMaxTokens")
    max_tokens = raw_max if isinstance(raw_max, int) and raw_max > 0 else None
    # Update UI bar
    if max_tokens:
        self.context_bar.max_tokens = max_tokens
    self.context_bar.tokens = tokens
    # Update agent for prompt injection
    agent.update_context(tokens, max_tokens or self.context_bar.max_tokens)
```

This gives the SDK values to both the UI bar and the prompt injection system.

## Execution Steps

1. `git checkout integration`
2. `git merge a6624cf` — will report conflicts
3. Resolve each conflicting file per the table above
4. Run `uv run python -m pytest tests/ -n auto -q` — verify all tests pass
5. Fix semantic conflicts (rename references, remove stale fallbacks)
6. Run tests again
7. `git add . && git commit` (merge commit)

## Non-goals

- Refactoring local code beyond what's needed for conflict resolution.
- Changing upstream's implementation choices (effort levels, ToolSearch format, etc.).
- Removing `parse_context_size()` entirely — it remains as a pre-connect fallback.

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| `refresh_context()` merge produces subtle bugs | Upstream's test (`test_refresh_context_reads_sdk_usage`) + local's context bar tests both run post-merge |
| `DEFAULT_CONTEXT_WINDOW` rename breaks imports | Grep for all `MAX_CONTEXT_TOKENS` references post-merge, update systematically |
| EffortLabel/EffortPrompt interact with local footer changes | Upstream's effort widgets are additive; local's footer changes (cwd, session-id) are in different compose slots |
| test_formatting.py add/add produces incomplete test file | Concatenate both and verify all tests pass |

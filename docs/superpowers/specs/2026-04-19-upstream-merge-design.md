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
| `claudechic/app.py` (`refresh_context`) | Upstream rewrites to use `get_context_usage()` directly; local rewrites with fallback + `agent.update_context()` | **Adopt upstream's SDK-based approach.** Wire `agent.update_context()` to use SDK values. Keep local sidebar/cwd refresh calls. See merged resolution below. |
| `claudechic/app.py` (`_start_new_session`) | Upstream reformats `_make_options` call to multi-line + adds `effort=agent.effort`; local adds `agent.session_id = None` and `set_session_id(None)` before disconnect | Keep both: local's session-id clearing + upstream's effort param and formatting |
| `claudechic/app.py` (`_update_footer_model`) | Upstream changes `self.status_footer.model = model_name` to `trim_model_name(model_name)`; local uses the original line as anchor for `parse_context_size()` fallback code | Keep upstream's `trim_model_name()` change. Append local's `parse_context_size()` fallback after the modified line. |
| `claudechic/widgets/layout/indicators.py` | Import conflict (`DEFAULT_CONTEXT_WINDOW` vs `MAX_CONTEXT_TOKENS`) + local's complete `ContextBar` rewrite | Adopt upstream's `DEFAULT_CONTEXT_WINDOW` rename. Apply it to local's rewritten `ContextBar` (gradient colors, text format). Local's `format_tokens` import stays. |
| `tests/test_formatting.py` | Add/add conflict — upstream creates 22-line file, local creates 323-line file | Merge both: unify import blocks (local's `format_cwd`, `format_tokens`, `parse_context_size`, `strip_ansi` + upstream's `trim_model_name`), concatenate test classes. No name collisions. |
| `tests/test_widgets.py` (context_bar area) | Upstream inserts `test_context_bar_scales_with_max_tokens` after base's `test_context_bar_rendering`; local rewrites `test_context_bar_rendering` entirely | Place upstream's scaling test after local's rewritten test class. Update upstream's test to use `DEFAULT_CONTEXT_WINDOW` consistently. |
| `tests/test_widgets.py` (footer area) | Both insert tests after `test_status_footer_permission_mode` | Keep both: upstream's `test_status_footer_effort_label` + local's 200+ lines of cwd/session footer tests, sequentially |
| `tests/test_app_ui.py` | Import line: upstream adds `AsyncMock`, local adds `patch` and `make_fake_pty` | Combine: `from unittest.mock import AsyncMock, MagicMock, patch` + keep local's `from tests.conftest import ..., make_fake_pty` |

### Semantic conflicts (no textual conflict, but code breaks post-merge)

| File | Issue | Resolution |
|------|-------|------------|
| `claudechic/formatting.py` | Upstream renames `MAX_CONTEXT_TOKENS` → `DEFAULT_CONTEXT_WINDOW` | Auto-merges textually but **all 4 local files** importing the old name will break |
| `claudechic/agent.py` L39, L190 | Imports and uses `MAX_CONTEXT_TOKENS` | Update to `DEFAULT_CONTEXT_WINDOW` |
| `claudechic/app.py` L62, L3060 | Imports and compares against `MAX_CONTEXT_TOKENS` | Update to `DEFAULT_CONTEXT_WINDOW` |
| `claudechic/widgets/layout/indicators.py` L10, L117 | Imports and uses `MAX_CONTEXT_TOKENS` as reactive default | Update to `DEFAULT_CONTEXT_WINDOW` (also textual conflict — resolved together) |
| `claudechic/widgets/layout/sidebar.py` L17, L486 | Imports and uses `MAX_CONTEXT_TOKENS` | Update to `DEFAULT_CONTEXT_WINDOW` |
| `claudechic/app.py` (`refresh_context` import) | Local imports `get_context_from_session` from `sessions.py`; upstream deletes that function | Remove the import (upstream's deletion is correct; merged `refresh_context()` no longer calls it) |

### Files that auto-merge cleanly (no action needed)

`commands.py`, `sessions.py`, `styles.tcss`, `footer.py`.

## Resolution Strategy for `refresh_context()` (The Hard Conflict)

Two approaches to context tracking collide. The merged resolution adopts
upstream's SDK-based approach while preserving local's prompt-injection
wiring and sidebar/cwd UI updates.

**Merged resolution:**
```python
@work(group="refresh_context", exclusive=True)
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
    if not isinstance(tokens, int):
        tokens = 0
    raw_max = usage.get("rawMaxTokens")
    max_tokens = raw_max if isinstance(raw_max, int) and raw_max > 0 else None
    # Update UI bar
    if max_tokens:
        self.context_bar.max_tokens = max_tokens
    self.context_bar.tokens = tokens
    # Update agent for prompt injection (<system-reminder>N/M tokens</system-reminder>)
    agent.update_context(tokens, max_tokens or self.context_bar.max_tokens)
    # Keep sidebar and footer in sync (local additions)
    self._update_sidebar_agent_context(agent)
    self.call_after_refresh(self.status_footer.refresh_cwd_label)
```

**Key decisions:**
- SDK's `get_context_usage()` is the sole source of truth (no session-file fallback)
- `totalTokens` is validated as int with default 0
- `rawMaxTokens` is validated as positive int; if missing, falls back to bar's current value
- `agent.update_context()` feeds the prompt-injection system
- Sidebar and cwd refresh calls are preserved from local (fire after every response)
- `@work` decorator preserved for async scheduling

**Error handling:** If `get_context_usage()` raises, the bar and agent keep their previous values. This is safe because `refresh_context()` is called after every response — a transient failure means one turn of stale data.

**SDK dependency:** Requires `claude-agent-sdk>=0.1.56` (already pinned in `pyproject.toml`). The `get_context_usage()` method is available in this version. No `hasattr` guard needed.

## `parse_context_size()` status

`parse_context_size()` remains in `formatting.py` as a **marginal pre-connect fallback** used in `_update_footer_model()`. It provides a better-than-200K guess for the context window during the brief window between SDK connection and the first `refresh_context()` call (0–1 turns). The SDK's `rawMaxTokens` supersedes it on the first response.

This function is **not needed for correctness** — the context bar would show 200K briefly then correct itself. It is kept for polish, not safety. It could be removed in a future cleanup without functional impact.

## Success Criteria

Post-merge, verify these behaviors:

1. **Context bar:** Shows live token usage from SDK; updates `max_tokens` per-model (Opus 1M shows as 1M, not 200K)
2. **Prompt injection:** `<system-reminder>N/M tokens</system-reminder>` appears in every prompt with SDK-sourced values
3. **Sidebar:** Per-agent token counts update after each response
4. **Footer cwd:** Recomputes budget after context changes
5. **Effort command:** `/effort` sets thinking depth, persists across reconnects
6. **ToolSearch:** Compact rendering with tool names (not raw JSON)
7. **Session-ID:** Clears on `/clear`, shows on `/session-id`
8. **Auto-copy:** No crash on copy selection
9. **Toast notifications:** No `MarkupError` from ANSI codes or brackets
10. **All tests pass:** `uv run python -m pytest tests/ -n auto -q`

## Execution Steps

1. `git checkout integration`
2. `git merge a6624cf` — will report conflicts in ~7 files
3. Resolve textual conflicts per the table above
4. Fix semantic conflicts — update all `MAX_CONTEXT_TOKENS` → `DEFAULT_CONTEXT_WINDOW` references in `agent.py`, `app.py`, `indicators.py`, `sidebar.py`; remove stale `get_context_from_session` import
5. Run `uv run python -m pytest tests/ -n auto -q` — verify all tests pass
6. Stage resolved files explicitly: `git add <file1> <file2> ...` (do not use `git add .` — review `git status` first to avoid staging secrets or untracked files)
7. `git commit` (merge commit)

**Note:** Semantic fixes (step 4) MUST happen before the test run (step 5). The renamed constant causes `ImportError` at import time, so tests cannot even start until all references are updated.

## Non-goals

- Refactoring local code beyond what's needed for conflict resolution.
- Changing upstream's implementation choices (effort levels, ToolSearch format, etc.).
- Removing `parse_context_size()` entirely (marginal but harmless).

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| `refresh_context()` merge produces subtle bugs | Upstream's test (`test_refresh_context_reads_sdk_usage`) + local's context bar tests both run post-merge; sidebar/cwd refresh calls preserved |
| `DEFAULT_CONTEXT_WINDOW` rename breaks imports | All 4 affected files listed explicitly; fixed before test run |
| `get_context_from_session` import left dangling | Explicitly listed in semantic conflicts; removed in step 4 |
| EffortLabel/EffortPrompt interact with local footer changes | Upstream's effort widgets are additive in different compose slots; local's cwd/session-id are separate widgets |
| `_start_new_session` conflict drops session-id clearing | Conflict explicitly listed; resolution keeps local's `agent.session_id = None` |
| `_update_footer_model` anchor line changed | Conflict explicitly listed; local's fallback code appended after upstream's modified line |
| test_formatting.py add/add produces incomplete file | Import blocks unified; test classes concatenated; no name collisions |
| test_widgets.py overlapping insertions | context_bar and footer areas both listed as separate conflicts with specific resolution |
| Accidental staging of secrets | Step 6 requires explicit file staging with `git status` review |
| `get_context_usage()` unavailable on older SDK | `pyproject.toml` pins `>=0.1.56` which includes the method |
| `get_context_usage()` returns malformed data | Both `totalTokens` and `rawMaxTokens` validated with type checks and defaults |

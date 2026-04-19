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
| `tests/test_widgets.py` (context_bar area) | Upstream inserts `test_context_bar_scales_with_max_tokens` after base's `test_context_bar_rendering`; local rewrites `test_context_bar_rendering` entirely | Place upstream's scaling test after local's rewritten test class |
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
    if not isinstance(usage, dict):
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
- `usage` is guarded against `None` return (SDK may return `None` on error)
- `totalTokens` is validated as int with default 0
- `rawMaxTokens` is validated as positive int; if missing, falls back to bar's current value
- `agent.update_context()` feeds the prompt-injection system
- Sidebar and cwd refresh calls are preserved from local (fire after every response)
- `@work` decorator preserved for async scheduling

**Error handling:** If `get_context_usage()` raises or returns a non-dict value (including `None`), the function returns early and the bar/agent keep their previous values. This is safe because `refresh_context()` is called after every response — a transient failure means one turn of stale data. Repeated failures preserve the last known good values indefinitely (no reset to zero).

**SDK dependency:** Requires `claude-agent-sdk>=0.1.56` (already pinned in `pyproject.toml`). The `get_context_usage()` method is available in this version. No `hasattr` guard needed.

## `parse_context_size()` status

`parse_context_size()` remains in `formatting.py` as a **marginal pre-connect fallback** used in `_update_footer_model()`. It provides a better-than-200K guess for the context window during the brief window between SDK connection and the first `refresh_context()` call (0–1 turns). The SDK's `rawMaxTokens` supersedes it on the first response.

This function is **not needed for correctness** — the context bar would show 200K briefly then correct itself. It is kept for polish, not safety. It could be removed in a future cleanup without functional impact.

## Success Criteria

Post-merge, verify these behaviors:

| # | Criterion | Verification |
|---|-----------|--------------|
| 1 | Context bar shows live SDK token usage; `max_tokens` updates per-model | `test_refresh_context_reads_sdk_usage`, `test_context_bar_scales_with_max_tokens` |
| 2 | Prompt injection uses SDK-sourced values | `test_prepare_prompt_*` (local agent tests) |
| 3 | Sidebar per-agent token counts update | `test_sidebar_*` (local sidebar tests) |
| 4 | Footer cwd recomputes after context changes | `test_status_footer_cwd_*` (local footer tests) |
| 5 | `/effort` sets thinking depth, persists across reconnects | `test_effort_prompt_*` (upstream widget tests) |
| 6 | ToolSearch compact rendering | Visual: invoke ToolSearch, verify tool list not raw JSON |
| 7 | Session-ID clears on `/clear`, shows on `/session-id` | `test_session_id_*` (local app tests) |
| 8 | Auto-copy no crash | `test_copy_*` (local app tests) |
| 9 | Toast notifications safe from ANSI/brackets | `test_notify_defaults_markup_false`, `test_sdk_stderr_strips_ansi` |
| 10 | All tests pass | `uv run python -m pytest tests/ -n auto -q` |

**UI smoke checks** (manual, post-merge):
- Launch `uv run claudechic`, verify footer shows model name (trimmed), effort level, cwd, session-id
- Send a message, verify context bar updates with SDK values
- Run `/effort medium`, verify effort label changes
- Run `/session-id`, verify ID displayed and copied

## Execution Steps

1. **Checkout:** `git checkout integration`
2. **Verify SDK version:** `uv run python -c "from claude_agent_sdk import ClaudeSDKClient; assert hasattr(ClaudeSDKClient, 'get_context_usage'), 'SDK too old'"` — confirms the method exists before merging
3. **Merge:** `git merge a6624cf` — will report conflicts in ~7 files
4. **Resolve textual conflicts** per the table above (agent.py, app.py ×3, indicators.py, test_formatting.py, test_widgets.py ×2, test_app_ui.py)
5. **Fix semantic conflicts:**
   - Update `MAX_CONTEXT_TOKENS` → `DEFAULT_CONTEXT_WINDOW` in `agent.py`, `app.py`, `indicators.py`, `sidebar.py`
   - Remove stale `get_context_from_session` import from `app.py`
6. **Verify no stale references:** `rg "MAX_CONTEXT_TOKENS|get_context_from_session" claudechic/ tests/` — must return zero matches in both production and test code
7. **Run targeted tests first:**
   - `uv run python -m pytest tests/test_formatting.py -v` — verify merged test file
   - `uv run python -m pytest tests/test_widgets.py -v` — verify merged widget tests
   - `uv run python -m pytest tests/test_app_ui.py -v` — verify merged app tests
8. **Run full suite:** `uv run python -m pytest tests/ -n auto -q` — verify all tests pass
9. **Review staging:** `git status --short` — review changed files, verify no secrets or untracked files
10. **Stage and commit:** `git add <resolved files>` then `git commit`

**Note:** Semantic fixes (step 5) MUST happen before any test run (steps 7-8). The renamed constant causes `ImportError` at import time, so tests cannot even start until all references are updated.

## Non-goals

- Refactoring local code beyond what's needed for conflict resolution.
- Changing upstream's implementation choices (effort levels, ToolSearch format, etc.).
- Removing `parse_context_size()` entirely (marginal but harmless).

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| `refresh_context()` merge produces subtle bugs | Upstream's test (`test_refresh_context_reads_sdk_usage`) + local's context bar tests both run post-merge; sidebar/cwd refresh calls preserved |
| `DEFAULT_CONTEXT_WINDOW` rename breaks imports | All 4 affected files listed explicitly; fixed before test run |
| `get_context_from_session` import left dangling | Explicitly listed in semantic conflicts; removed in step 5 |
| EffortLabel/EffortPrompt interact with local footer changes | Upstream's effort widgets are additive in different compose slots; local's cwd/session-id are separate widgets |
| `_start_new_session` conflict drops session-id clearing | Conflict explicitly listed; resolution keeps local's `agent.session_id = None` |
| `_update_footer_model` anchor line changed | Conflict explicitly listed; local's fallback code appended after upstream's modified line |
| test_formatting.py add/add produces incomplete file | Import blocks unified; test classes concatenated; no name collisions |
| test_widgets.py overlapping insertions | context_bar and footer areas both listed as separate conflicts with specific resolution |
| Accidental staging of secrets | Step 9 requires `git status --short` review; step 10 uses explicit file staging |
| `get_context_usage()` unavailable on older SDK | `pyproject.toml` pins `>=0.1.56` which includes the method |
| `get_context_usage()` returns malformed data | Both `totalTokens` and `rawMaxTokens` validated with type checks and defaults |

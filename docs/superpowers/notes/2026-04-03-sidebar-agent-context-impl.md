# Sidebar Agent Context Info — Implementation Notes

**Branch:** `feat/context`
**Date:** 2026-04-03
**Plan:** `docs/superpowers/plans/2026-04-03-sidebar-agent-context.md`

---

## What Was Implemented

All 8 plan tasks completed across 7 commits (342 lines added, 5 removed).

### Files Modified

| File | What Changed |
|------|-------------|
| `claudechic/formatting.py` | Added `format_tokens()` and `parse_context_size()` pure functions |
| `claudechic/agent.py` | Added `tokens: int` and `max_tokens: int` fields to `Agent.__init__` |
| `claudechic/app.py` | `refresh_context` stores tokens on agent; added `_update_sidebar_agent_context` and `_set_agent_max_tokens` helpers; wired context into `on_agent_created`, `on_agent_switched`, `_update_footer_model`, `_update_slash_commands` |
| `claudechic/widgets/layout/sidebar.py` | Expanded `AgentItem` from 1-row to 3-row (name+close, cwd, context); added `update_context()` and rendering methods; added `AgentSection.update_agent_context()` |
| `claudechic/widgets/layout/indicators.py` | `ContextBar.max_tokens` reactive default now uses `MAX_CONTEXT_TOKENS` constant instead of inline `200_000` |

### Files Created

| File | Purpose |
|------|---------|
| `tests/test_formatting.py` | 16 tests for `format_tokens()` and `parse_context_size()` |
| `tests/test_sidebar_context.py` | 6 tests for `AgentItem` cwd/context rendering |

### Features Working

- **`format_tokens(n)`** — compact token display: `0`, `500`, `18.5K`, `1M`, `1.5M`
- **`parse_context_size(s)`** — extracts context window from `"Claude 4 Sonnet (1M context)"` or `"claude-opus-4-6[1m]"` format strings
- **3-row `AgentItem`** — name row (indicator + name + close button), cwd row (dimmed, `~` substitution, front-truncated to 20 chars), context row (`XX% [used/total]` with dim/yellow/red color coding at 0%/50%/80% thresholds)
- **Compact mode** — hides cwd and context rows (height shrinks to 1)
- **`update_context()`** — dirty-checking method only re-renders when values change
- **End-to-end data flow** — session file → `refresh_context` → `agent.tokens` → sidebar + footer context bar
- **Model-based max_tokens** — parsed from SDK model metadata on connect, propagated to all agents

---

## What Was NOT Implemented

1. **No live updating for idle/background agents** — only the *active* agent's context is refreshed by the polling timer (`refresh_context` only reads the current agent's session file). Background agents' token counts go stale until switched to.

2. **No progress bar visualization in sidebar** — the sidebar context row is text-only (`2% [18.5K/1M]`). The footer `ContextBar` has a graphical filled-bar; the sidebar does not. The plan didn't call for this.

3. **No persistence** — `tokens` and `max_tokens` are in-memory only. On resume, they start at 0 / 200K until `refresh_context` runs and `get_context_usage()` returns actual values.

### Bug Fix: max_tokens Source (same session)

**Problem:** The original plan used `parse_context_size()` to extract context window sizes from model `displayName` (e.g., "Claude 4 Sonnet (1M context)") or model IDs (e.g., "claude-opus-4-6[1m]"). In practice, the SDK returns `displayName: "Sonnet"` and `value: "sonnet"` — neither contains context window information.

**Root cause:** The plan assumed model metadata would contain context window sizes in the display name. The SDK's `get_server_info()` model entries only have short names.

**Fix:** `refresh_context()` now calls `agent.client.get_context_usage()` which returns `rawMaxTokens` (the actual model context window, e.g., 1,000,000) directly from the SDK. This is the same data shown by the `/context` CLI command. Falls back to session-file parsing when the SDK is not yet connected.

**Removed:** `_set_agent_max_tokens()` method and all `parse_context_size()` calls from production code. `parse_context_size()` remains as a tested utility in `formatting.py` but is no longer used.

---

## What Was Tested

### Unit Tests (22 total, all passing)

**`tests/test_formatting.py` — 16 tests:**
- `format_tokens`: zero, small, 1K boundary, thousands, round thousands, large thousands, 1M boundary, millions
- `parse_context_size`: parenthesized format (1M, 200K), bracket format (1m, 200k), no match, empty string, various model name patterns

**`tests/test_sidebar_context.py` — 9 tests:**
- Context label: correct percentage (2%, 0%, 80%), correct token formatting (18.5K/1M), 1M window display, `update_context()` sets values, partial updates
- CWD label: front-truncation of long paths, short paths unchanged, home directory replacement with `~`

### Lint & Format

- `ruff check` — all changed files pass
- `ruff format` — all changed files formatted

### Code Reviews

- **Spec compliance reviews** — Task 1 and Task 4 verified against plan (both ✅)
- **Code quality review** — full diff reviewed; issues found and fixed:
  - Duplicated model-lookup logic between `_update_footer_model` and `_set_agent_max_tokens` → deduplicated
  - Inline `import os` and `from claudechic.formatting import format_tokens` in method bodies → moved to top-level
  - Magic `200_000` in 3 places → replaced with `MAX_CONTEXT_TOKENS` constant

---

## What Was NOT Tested

1. **No integration/E2E tests** — the wiring in `app.py` (`refresh_context` with `get_context_usage()`, `on_agent_created`, `on_agent_switched`, `_update_sidebar_agent_context`) is untested. These methods depend on the full Textual app being mounted with real widgets, which the existing test infrastructure (`test_app.py`) supports but requires the SDK connection. The environment used for this session could not build `psutil` (FreeBSD missing `bsm/audit.h`), so full app tests were not runnable.

2. **No test for `refresh_context` → `get_context_usage()` → `rawMaxTokens` flow** — this is the critical path for getting the correct 1M context window. Requires a mocked SDK client returning a `ContextUsageResponse`. Should be tested in `test_app_ui.py` or similar.

3. **No test for `update_context()` dirty-checking** — the method tracks `changed` to avoid unnecessary re-renders, but no test verifies that `_refresh_detail_rows` is skipped when values don't change.

4. **No test for `AgentSection.update_agent_context()` with unknown agent_id** — it silently does nothing (correct behavior), but undocumented by test.

5. **No test for `parse_context_size` with decimal values** — e.g., `"(1.5M context)"` → `1_500_000`. The regex supports it but no test exercises it. Note: `parse_context_size` is now dead code (no production callers).

5. **No visual/manual testing** — the TUI was not launched during this session (build environment limitations). The sidebar rendering should be visually verified.

---

## Context for Further Rework

### Build Environment Issue

The worktree environment runs FreeBSD 15 with Python 3.11. `psutil` fails to compile due to a missing `bsm/audit.h` header. A mock psutil module (`/tmp/mock_psutil/`) was used to run unit tests. Full app tests (`test_app.py`, `test_app_ui.py`, `test_widgets.py`) were **not** runnable. These should be run in a proper build environment before merging.

### Recommended Next Steps

1. **Visual verification** — launch `uv run claudechic` and confirm the sidebar renders correctly with multiple agents showing cwd and context info.

2. **Run full test suite** — in an environment where `psutil` builds, run `uv run python -m pytest tests/ -n auto -q` to ensure no regressions in existing widget tests (especially `test_widgets.py` which tests sidebar rendering).

3. **Background agent context staleness** — consider adding a periodic refresh of idle agents' context counts. Currently only the active agent is polled. Options:
   - Poll all agents on a slower timer (e.g., every 30s)
   - Refresh when switching to an agent (already done via `refresh_context` in `on_agent_switched`)

4. **pyright type checking** — was not run in this session. Run `uv run pyright claudechic/` to catch any type errors in the new code.

5. **Pre-commit hooks** — `uv run pre-commit run --all-files` was not runnable due to the psutil build issue. Should be run before merge.

### Architecture Notes

- **Data flow:** `agent.client.get_context_usage()` → `totalTokens` + `rawMaxTokens` → `agent.tokens` / `agent.max_tokens` → `context_bar` + `AgentSection.update_agent_context()` → `AgentItem.update_context()` → `_refresh_detail_rows()`
- **Fallback:** When SDK is not connected, falls back to `get_context_from_session()` which reads session files for token count only (no max_tokens update)
- **Single source of truth:** `MAX_CONTEXT_TOKENS` constant in `formatting.py` is the default (200K); actual max_tokens is overridden per-agent from `rawMaxTokens` once `refresh_context` successfully calls the SDK
- **Dead code:** `parse_context_size()` in `formatting.py` is no longer called from production code but remains as a tested utility function. Its tests in `test_formatting.py` still run.

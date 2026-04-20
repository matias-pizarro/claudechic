# Worktree Finish Mode: Design

## Goals

Allow users to choose how `/worktree finish` integrates feature branches:

1. **rebase** (default): Rebase onto base branch, then fast-forward merge. Produces linear history.
2. **no-ff**: Merge with `--no-ff`. Preserves branch topology with merge commits.

## Non-Goals

- No interactive mode selection at finish time (config only)
- No squash mode
- No remote interaction during finish

## Configuration

```yaml
# ~/.claude/.claudechic.yaml
worktree:
  finish_mode: "rebase"  # or "no-ff"
```

- **Default**: `"rebase"` (preserves existing behavior)
- **Invalid values**: Any value other than `"no-ff"` falls through to rebase behavior (safe default)
- **Config loaded at import time**: Changes require app restart (consistent with `path_template`)

## Decision: `determine_resolution_action()` Branching

The resolution action function checks `WORKTREE_FINISH_MODE == "no-ff"` explicitly:

```python
if WORKTREE_FINISH_MODE == "no-ff":
    return ResolutionAction.NO_FF
else:
    if status.can_fast_forward:
        return ResolutionAction.FAST_FORWARD
    return ResolutionAction.REBASE
```

**Rationale**: Only one explicit match prevents typos from activating experimental behavior.

## No-FF Mode Behavior

| Status | Action |
|--------|--------|
| No commits, clean | NONE (skip to cleanup) |
| Already merged | NONE (skip to cleanup) |
| Gitignored untracked only | CLEAN_GITIGNORED, then re-check |
| Uncommitted changes | PROMPT_UNCOMMITTED (user decides) |
| Has commits, clean | **NO_FF** (Claude does merge) |

In no-ff mode:
- Fast-forward is **never** used (intent is always to create merge commit)
- `can_fast_forward` computation is skipped (no subprocess call)
- Claude receives a prompt to `git merge --no-ff <branch>` in the main dir
- No `git checkout` is needed (worktrees are pinned to their branch)
- Explicit "Do NOT rebase" instruction prevents Claude from adding a rebase step

## Security

- Shell commands in prompts use `shlex.quote()` for all interpolated paths and branch names
- Branch names from git are generally safe (restricted character set) but quoting provides defense-in-depth
- `_validate_base_branch` removed (unused); inline check at worktree creation rejects `-` prefix refs

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Main dir has uncommitted changes (pre-merge) | App-side check returns `MAIN_DIR_NOT_READY`; user sees error; finish aborted. No prompt sent to Claude. |
| Main dir on wrong branch | App-side check returns `MAIN_DIR_NOT_READY`; user sees error; finish aborted. No prompt sent to Claude. |
| Merge conflict during no-ff | `MERGE_HEAD` detected on re-diagnosis → main_dir check skipped (merge in progress is expected); Claude receives prompt to resolve conflicts; flow retries after Claude responds |
| Already merged branch in no-ff mode | Returns NONE (merge already done), skips to cleanup |
| 0 commits ahead, clean | Returns NONE regardless of mode |
| Invalid config value ("noff", "merge") | Falls through to rebase behavior |
| `start_worktree(base="")` | Rejected with clear error (empty base not allowed) |

## Two Entry Points

Both `commands.py` (TUI) and `mcp.py` (MCP tool) handle `ResolutionAction.NO_FF`:
- TUI: `_run_resolution` → sends prompt to agent
- MCP: `_process_finish_resolution` → returns prompt as text response

Both call `get_no_ff_finish_prompt(info)` for consistency.

## Test Coverage

`tests/test_resolution_action.py` — 26+ tests covering:
- Rebase mode: 7 tests (all ResolutionAction values)
- No-ff mode: 6 tests (including FF-eligible returning NO_FF)
- Unknown mode: 7 parametrized (None, "", typos → rebase behavior)
- Prompt content: 4 tests (no checkout, has --no-ff, no-rebase instruction, shlex quoting)

## Success Criteria

- [x] `finish_mode: "rebase"` → existing behavior unchanged (fast-forward or rebase)
- [x] `finish_mode: "no-ff"` → for clean branches with unmerged commits, integrates via `--no-ff`; branches with no unmerged commits skip merge and proceed to cleanup
- [x] Invalid config → defaults to rebase
- [x] Shell injection in branch names → quoted in prompt commands
- [x] Dirty worktree in no-ff mode → prompts user before merge
- [x] Dirty main dir (pre-merge) → app blocks with MAIN_DIR_NOT_READY error
- [x] Merge conflict (post-merge) → MERGE_HEAD detected, Claude resolves
- [x] Already-merged branch → skips to cleanup
- [x] Tests pass: 24+ in `test_resolution_action.py`

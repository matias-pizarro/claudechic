# PRD: Parametrized Base Branch for `/worktree finish`

**Date:** 2026-04-21
**Author:** Claude (AI)
**Status:** Draft
**Branch:** `parametrized-finish`

---

## 1. Problem Statement

When a user invokes `/worktree finish`, the command auto-detects the parent branch by finding the closest ancestor among active worktree branches. This heuristic fails in several real-world scenarios:

1. **Intermediate branch deleted:** A user creates `feature-a` from `main`, then `feature-b` from `feature-a`. After `feature-a` is merged and its worktree removed, `feature-b` can no longer detect its parent because the branch is gone — it falls back to `main`, which may not be the intended target.

2. **Targeting a non-ancestor branch:** A user wants to merge into a branch that is not an ancestor (e.g., merging a hotfix worktree into a `release/1.2` branch rather than the branch it was forked from).

3. **Shared base branches:** Multiple worktrees share the same parent (e.g., all forked from `develop`). The auto-detection works, but the user may want to redirect one worktree to merge into a different integration branch mid-flight.

In all cases, the user has no recourse — they must manually perform git operations outside claudechic.

## 2. Goal

Allow the user to **explicitly specify the target branch** when finishing a worktree, overriding the auto-detected parent branch.
support both existing merge strategies

## 3. Non-Goals

- Changing the default behavior when no base branch is specified (existing auto-detection remains unchanged).
- Adding branch creation (the target branch must already exist).
- Adding UI for branch selection (text input only — no picker/modal).

## 4. User Stories

### US-1: User specifies base branch via positional argument
> As a claudechic user working in a worktree, I want to run `/worktree finish main` so that my worktree merges into `main` regardless of what the auto-detection would choose.

### US-2: User specifies base branch via flag
> As a claudechic user, I want to run `/worktree finish --into develop` so that I can clearly express intent using a named flag.

### US-3: MCP tool accepts base branch
> As Claude (the AI agent), I want to call `finish_worktree` with an optional `base_branch` parameter so that I can finish a worktree into a specific branch when instructed.

### US-4: Invalid branch produces clear error
> As a user, if I specify a branch that does not exist, I want an immediate, clear error message explaining the branch was not found — before any git operations are attempted.

### US-5: Self-merge is rejected
> As a user, if I accidentally specify the current feature branch as the target, I want an error explaining that a branch cannot be merged into itself.

## 5. Proposed Interface

### 5.1 Command-Line (TUI)

```
/worktree finish                    # existing behavior (auto-detect)
/worktree finish <branch>           # positional: merge into <branch>
/worktree finish --into <branch>    # flag: merge into <branch>
```

Both positional and `--into` forms are equivalent. The flag form exists for readability and discoverability.

### 5.2 MCP Tool Schema

```json
{
  "name": "finish_worktree",
  "description": "Finish current worktree: commit, rebase, merge, cleanup.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "base_branch": {
        "type": "string",
        "description": "Optional branch to merge into. Overrides auto-detected parent branch."
      }
    }
  }
}
```

### 5.3 Autocomplete

The autocomplete hint changes from `/worktree finish` to include the optional parameter:
```
/worktree finish [<branch>]
```

## 6. Validation Rules

| Condition | Behavior |
|-----------|----------|
| `base_branch` not provided | Auto-detect parent (current behavior) |
| `base_branch` does not exist as a local branch | Error: "Branch '{name}' does not exist" |
| `base_branch` equals current feature branch | Error: "Cannot merge a branch into itself" |
| `base_branch` is not an ancestor | Proceed normally — `diagnose_worktree` will detect divergence and trigger rebase via Claude |

## 7. Architecture

### 7.1 Affected Components

| Layer | File | Change |
|-------|------|--------|
| Git operations | `features/worktree/git.py` | Add `base_branch` param to `get_finish_info()`; add `branch_exists()` helper |
| Command handler | `features/worktree/commands.py` | Parse `base_branch` from command string; pass to `get_finish_info()` |
| MCP tool | `mcp.py` | Add `base_branch` to tool schema; extract from args |
| Autocomplete | `commands.py` | Update hint text |

### 7.2 Data Flow

```
User types: /worktree finish main
       │
       ▼
handle_worktree_command() ─── parses "main" as base_branch
       │
       ▼
_handle_finish(app, base_branch="main")
       │
       ▼
get_finish_info(cwd, base_branch="main")
       │
       ├── validates branch exists (git rev-parse --verify refs/heads/main)
       ├── validates not self-merge
       ├── skips get_parent_branch() call
       └── resolves parent_dir from worktree list
       │
       ▼
Returns FinishInfo(base_branch="main", ...)
       │
       ▼
diagnose_worktree(info) → normal flow continues
```

### 7.3 MCP Data Flow

```
Claude calls: finish_worktree({"base_branch": "main"})
       │
       ▼
finish_worktree(args) ─── extracts args.get("base_branch")
       │
       ▼
get_finish_info(agent.cwd, base_branch="main")
       │
       ▼
(same validation and flow as above)
```

## 8. Error Messages

| Scenario | Message |
|----------|---------|
| Branch does not exist | `"Branch 'xyz' does not exist. Available branches: main, develop, feature-x"` |
| Self-merge | `"Cannot merge branch 'feature-a' into itself"` |
| Not in worktree | `"Not in a feature worktree. Switch to a worktree first."` (existing) |

The "does not exist" error includes up to 5 available branches for discoverability.

## 9. Backward Compatibility

- **Full backward compatibility:** When `base_branch` is `None` (no argument provided), the existing `get_parent_branch()` auto-detection is used unchanged.
- **No breaking changes** to the MCP tool: the new field is optional with no default enforcement.
- **No config changes** required.

## 10. Testing Strategy

| Type | What | Where |
|------|------|-------|
| Unit | `branch_exists()` helper | `tests/test_worktree_finish.py` |
| Unit | `get_finish_info()` with override, invalid, self-merge | `tests/test_worktree_finish.py` |
| Unit | Command parsing (positional, flag, none) | `tests/test_worktree_finish.py` |
| Unit | MCP tool schema and arg extraction | `tests/test_worktree_finish.py` |
| Integration | Full `/worktree finish main` in real git repo | `tests/test_worktree_finish.py` |
| E2E | TUI interaction via remote testing endpoint | Manual or Playwright |

## 11. Security Considerations

- **No new attack surface:** The `base_branch` parameter is validated against existing local branches via `git rev-parse --verify`. No user input is passed to shell commands unsanitized.
- **Subprocess safety:** All git commands use list-form `subprocess.run()` (no shell=True).
- **Path traversal:** Branch names are validated by git itself — they cannot contain `..` or other filesystem-unsafe sequences.

## 12. Success Metrics

- Zero regressions in existing `/worktree finish` behavior (auto-detection path).
- All 3 input forms work correctly (no-arg, positional, flag).
- Clear error messages for invalid inputs.
- MCP tool accepts and uses the parameter.
- All existing tests continue to pass.

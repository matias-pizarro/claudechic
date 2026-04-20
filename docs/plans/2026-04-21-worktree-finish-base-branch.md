# PRD: Parametrized Base Branch for `/worktree finish`

**Date:** 2026-04-21
**Author:** Claude (AI)
**Status:** Draft
**Branch:** `parametrized-finish2`

---

## 1. Problem Statement

When a user invokes `/worktree finish`, the command auto-detects the parent branch by finding the closest ancestor among active worktree branches. This heuristic fails in several real-world scenarios:

1. **Intermediate branch deleted:** A user creates `feature-a` from `main`, then `feature-b` from `feature-a`. After `feature-a` is merged and its worktree removed, `feature-b` can no longer detect its parent because the branch is gone — it falls back to `main`, which may not be the intended target.

2. **Targeting a non-ancestor branch:** A user wants to merge into a branch that is not an ancestor (e.g., merging a hotfix worktree into a `release/1.2` branch rather than the branch it was forked from).

3. **Shared base branches:** Multiple worktrees share the same parent (e.g., all forked from `develop`). The auto-detection works, but the user may want to redirect one worktree to merge into a different integration branch mid-flight.

In all cases, the user has no recourse — they must manually perform git operations outside claudechic.

## 2. Goal

Allow the user to **explicitly specify the target branch** when finishing a worktree, overriding the auto-detected parent branch, while supporting both existing merge strategies (rebase and no-ff).

## 3. Non-Goals

- **Changing the default behavior** when no base branch is specified (existing auto-detection remains unchanged).
- **Adding branch creation:** The target branch must already exist as a local branch. Rationale: creating branches as a side effect of a merge command risks creating branches in the wrong place (wrong base commit) and conflates two distinct operations. Users can create branches explicitly with standard git or `/worktree <name>`.
- **Adding UI for branch selection:** Text input only — no picker/modal.
- **Supporting remote-tracking branches:** Only local branches (`refs/heads/*`) are valid targets. Users must check out remote branches locally first (e.g., `git checkout release/1.2`). See validation rules for the specific error message when a remote ref is detected.
- **Dynamic branch name autocomplete:** The autocomplete system provides static completion strings, not dynamic suggestions. Branch name completion is out of scope for this feature.

## 4. Terminology

| User-facing term | Parameter name | Internal function | Description |
|-----------------|---------------|-------------------|-------------|
| "target branch" / "base branch" | `base_branch` | — | The branch to merge into |
| — | — | `get_parent_branch()` | Auto-detection function (used when `base_branch` is `None`) |
| "merge directory" | — | `FinishInfo.main_dir` | The worktree directory where the merge is performed |

> **Note:** `FinishInfo.main_dir` is named historically but actually represents the directory of the target branch's worktree (which may differ from the main worktree). This naming inconsistency is pre-existing and out of scope for this change, but the implementation must be aware of it.

## 5. User Stories

### US-1: User specifies base branch via positional argument
> As a claudechic user working in a worktree, I want to run `/worktree finish main` so that my worktree merges into `main` regardless of what the auto-detection would choose.

### US-2: MCP tool accepts base branch
> As Claude (the AI agent), I want to call `finish_worktree` with an optional `base_branch` parameter so that I can finish a worktree into a specific branch when instructed.

### US-3: Invalid branch produces clear error
> As a user, if I specify a branch that does not exist, I want an immediate, clear error message explaining the branch was not found — before any git operations are attempted.

### US-4: Self-merge is rejected
> As a user, if I accidentally specify the current feature branch as the target, I want an error explaining that a branch cannot be merged into itself.

### US-5: Remote ref produces targeted error
> As a user, if I type `/worktree finish origin/main`, I want a clear error explaining that only local branches are supported, with a hint to check out the branch locally.

### US-6: Target branch requires active worktree in no-ff mode
> As a user, if I specify a target branch that has no active worktree and I am using `no-ff` finish mode, I want a clear error explaining that the target branch must have an active worktree checked out to it — because no-ff merges are performed in the target worktree's directory.

### US-7: Non-ancestor target with rebase mode shows warning
> As a user, if I specify a target branch that is not an ancestor of my feature branch (in rebase mode), I want to understand that my feature branch will be rebased onto the target, potentially rewriting commit history.

## 6. Proposed Interface

### 6.1 Command-Line (TUI)

```
/worktree finish                    # existing behavior (auto-detect)
/worktree finish <branch>           # positional: merge into <branch>
```

Only the positional form is supported. A `--into` flag was considered and rejected because:
- It doubles the parsing surface area and testing burden for no additional capability.
- The existing codebase uses positional arguments consistently (`/worktree cleanup <branches>`, `/agent close <name>`, `/resume <id>`).
- It introduces ambiguity when both positional and flag forms are provided.
- Discoverability is served by the help display and autocomplete hint.

### 6.2 Parsing Algorithm

The existing `handle_worktree_command()` uses `command.split(maxsplit=2)`, producing `parts = ["/worktree", "finish", "<rest>"]`. The parsing for `/worktree finish` will extract `base_branch` from `parts[2]` if present:

```python
# In _handle_finish() — to be added:
base_branch = parts[2].strip() if len(parts) > 2 else None
```

> **Implementation note:** Currently `_handle_finish(app)` takes only `app` and `handle_worktree_command()` does not extract or pass `parts[2]`. The implementation must add `base_branch` as a parameter to `_handle_finish()` and thread it through from `handle_worktree_command()`. See Section 8.8 for the full list of required API changes.

**Edge cases:**
- `/worktree finish` → `base_branch = None` (auto-detect)
- `/worktree finish main` → `base_branch = "main"`
- `/worktree finish main extra args` → `base_branch = "main extra args"` → fails validation (branch name with spaces does not exist)
- `/worktree finish --into main` → `base_branch = "--into main"` → fails validation (starts with `-`)
- `/worktree finish -mybranch` → `base_branch = "-mybranch"` → rejected: "Branch name must not start with '-'"
- `/worktree finish   ` → Python's default `split()` skips whitespace, producing only 2 parts → `base_branch = None` (auto-detect)

> **Note on empty strings:** An empty `base_branch` can only arrive via the MCP path (`args.get("base_branch")` returning `""`), not from TUI parsing — Python's `str.split()` without a separator argument skips whitespace and never produces empty elements.

All invalid inputs are caught by the validation rules in Section 7 before any git operations execute. All validation failures return `(False, error_message, None)`, consistent with the existing `get_finish_info()` early-return pattern.

### 6.3 MCP Tool Schema

The `finish_worktree` tool adds optional `base_branch` support. **Critical constraint:** The `@tool` decorator's `_build_schema()` generates `"required": list(properties.keys())`, making all dict keys **required**. Therefore, the schema must remain `{}` (empty dict) to preserve backward compatibility. The `base_branch` parameter is extracted from raw args manually:

```python
@tool(
    "finish_worktree",
    "When you're done working in a worktree, call this to clean it up. "
    "Handles committing, merging (rebase or no-ff per config), and removing the worktree. "
    "Prefer this over manual git worktree commands. "
    "Optionally pass base_branch (string) to specify the target branch to merge into. "
    "Must be a local branch name (not a remote ref like origin/main), must already exist, "
    "and cannot be the current branch. If omitted, the target is auto-detected.",
    {},  # Keep empty — base_branch extracted manually from args to stay optional
)
async def finish_worktree(args: dict[str, Any]) -> dict[str, Any]:
    base_branch = args.get("base_branch")  # None if not provided — auto-detect
    # ... pass to get_finish_info()
```

**Backward compatibility:** The schema stays `{}`. Existing calls with no arguments continue to work. `args.get("base_branch")` returns `None` for callers that don't provide it, triggering auto-detection. The parameter constraints are documented in the tool's description string (the only mechanism available since the `@tool` decorator does not support per-parameter descriptions).

**Experimental flag:** The `finish_worktree` tool is gated behind `CONFIG.get("experimental", {}).get("finish_worktree", False)`. The `base_branch` parameter is added to this existing experimental tool — no additional gating is needed. Tests must enable the experimental flag.

### 6.4 Autocomplete and Help

**Autocomplete** (static completion strings in `COMMANDS` list): Remains `/worktree finish` (unchanged). The autocomplete system provides literal completion strings for typing assistance, not dynamic branch suggestions.

**Help display** (`get_help_commands()`): Updated to show the optional argument:
```python
elif name == "/worktree":
    display_name = "/worktree <name> | finish [branch]"
```

This is concise and consistent with other help entries (e.g., `/resume [id]`, `/agent [name] [path]`). Subcommands `cleanup` and `discard` are already discoverable via autocomplete variants.

## 7. Validation Rules

Validation occurs in `get_finish_info()` before any git operations. All rules are applied in order; the first failure short-circuits.

| # | Condition | Behavior | Error Message |
|---|-----------|----------|---------------|
| V1 | `base_branch` is `None` | Auto-detect parent (current behavior, unchanged) | — |
| V2 | `base_branch` is empty after `.strip()` | Error | `"Branch name must not be empty"` |
| V3 | `base_branch` starts with `-` | Error | `"Branch name must not start with '-'"` |
| V4 | `base_branch` matches `origin/` or `remotes/` prefix | Error | `"'{name}' appears to be a remote branch. Specify a local branch (e.g., '{suggestion}'). Run 'git checkout {suggestion}' to create a local branch first."` where `suggestion` strips the remote prefix |
| V5 | `base_branch` does not exist as a local branch (`refs/heads/<name>`) | Error | `"Branch '{name}' does not exist. Local branches: {branch_list}"` — lists up to 5 local branches alphabetically, excluding the current feature branch |
| V6 | `base_branch` equals current feature branch | Error | `"Cannot merge branch '{name}' into itself"` |
| V7 | `base_branch` is valid but no worktree is checked out to it (no-ff mode only) | Error | `"Cannot merge into '{name}': no worktree is checked out to that branch. Create a worktree with '/worktree {name}', or check out the branch in an existing worktree."` |
| V7b | `base_branch` is valid but no worktree is checked out to it (rebase mode) | Proceed with preflight checks — verify main worktree is clean and not in mid-rebase/merge state before falling back. The rebase prompt will include a `git checkout {base_branch}` step. | — |
| V8 | `base_branch` is not an ancestor of feature branch (rebase mode) | Proceed — the non-ancestor condition is detected in `diagnose_worktree()` (via existing `needs_rebase()` / `can_fast_forward` check), not in `get_finish_info()`. When `can_fast_forward` is `False`, the rebase prompt is generated, which naturally handles the non-ancestor case. An informational note is added to the prompt: `"Note: {base_branch} is not an ancestor of {branch}. Rebasing will rewrite commit history."` | — |

### 7.1 `branch_exists()` Helper

```python
def branch_exists(branch: str, cwd: Path | None = None) -> bool:
    """Check if a local branch exists."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=cwd,
        capture_output=True,
    )
    return result.returncode == 0
```

This function is a pure boolean check. Error message construction (including the branch list) is the responsibility of the caller (`get_finish_info()`), keeping `branch_exists()` focused and testable.

The `cwd` parameter defaults to `None` (inherits process cwd). Callers should pass `cwd=info.worktree_dir` or the cwd passed to `get_finish_info()` for consistency. Since worktrees share the same repository, any worktree's directory resolves the same local branches.

### 7.2 `get_local_branches()` Helper

For error messages listing available branches:

```python
def get_local_branches(cwd: Path | None = None, exclude: str | None = None, limit: int = 5) -> list[str]:
    """List local branch names, sorted alphabetically, excluding one branch."""
    result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=cwd, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    branches = sorted(line for line in result.stdout.strip().split("\n") if line)
    if exclude:
        branches = [b for b in branches if b != exclude]
    return branches[:limit]
```

## 8. Architecture

### 8.1 Affected Components

| Layer | File | Change |
|-------|------|--------|
| Git operations | `features/worktree/git.py` | Add `base_branch` param to `get_finish_info()`; add `branch_exists()` and `get_local_branches()` helpers; add validation logic |
| Command handler | `features/worktree/commands.py` | Parse `base_branch` from command string; pass to `get_finish_info()`; add `base_branch` to analytics event |
| MCP tool | `mcp.py` | Add `base_branch` to tool schema; extract from args; pass to `get_finish_info()` |
| Help display | `commands.py` | Update help text for `/worktree` |

### 8.2 Data Flow (TUI)

```
User types: /worktree finish main
       │
       ▼
handle_worktree_command() ─── splits command, parts[2] = "main"
       │
       ▼
_handle_finish(app, base_branch="main")
       │
       ▼
get_finish_info(cwd, base_branch="main")
       │
       ├── V2: validates not empty
       ├── V3: validates not starting with '-'
       ├── V4: validates not a remote ref
       ├── V5: validates branch exists (git rev-parse --verify refs/heads/main)
       ├── V6: validates not self-merge
       ├── V7: validates target has worktree (no-ff mode) OR proceeds (rebase mode)
       ├── skips get_parent_branch() call (override provided)
       └── resolves main_dir: finds worktree on base_branch, falls back to main worktree
       │
       ▼
Returns FinishInfo(base_branch="main", main_dir=<target worktree dir>, ...)
       │
       ▼
diagnose_worktree(info) → normal flow continues
```

### 8.3 Data Flow (MCP)

```
Claude calls: finish_worktree({"base_branch": "main"})
       │
       ▼
finish_worktree(args) ─── args.get("base_branch") → "main"
       │
       ▼
get_finish_info(agent.cwd, base_branch="main")
       │
       ▼
(same validation and flow as TUI path)
```

### 8.4 `main_dir` Resolution with Override

This is the most architecturally significant aspect of this feature. The `FinishInfo.main_dir` field determines where merge operations happen. With a user-specified `base_branch`, the resolution must ensure the merge directory is on the correct branch.

**Resolution algorithm** (in `get_finish_info()` when `base_branch` is provided):

```python
# 1. Find a worktree checked out to the target branch
target_wt = next((wt for wt in worktrees if wt.branch == base_branch), None)

if target_wt:
    # Target branch has an active worktree — use its directory
    main_dir = target_wt.path
    needs_checkout = False
elif WORKTREE_FINISH_MODE == "no-ff":
    # No-ff mode requires the target to have a worktree (merge happens there)
    return (False, f"Cannot merge into '{base_branch}': no worktree is checked out "
            f"to that branch. Create a worktree with '/worktree {base_branch}', "
            f"or check out the branch in an existing worktree.", None)
else:
    # Rebase mode: merge happens in main worktree after rebase
    # Preflight: verify main worktree is safe for checkout
    main_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=main_wt_path, capture_output=True, text=True
    )
    if main_status.stdout.strip():
        return (False, f"Cannot use main worktree for merge: it has uncommitted changes. "
                f"Clean it up first or create a worktree for '{base_branch}'.", None)
    merge_head = subprocess.run(
        ["git", "rev-parse", "--verify", "MERGE_HEAD"],
        cwd=main_wt_path, capture_output=True
    )
    if merge_head.returncode == 0:
        return (False, f"Cannot use main worktree for merge: a merge is in progress.", None)
    main_dir = main_wt_path
    needs_checkout = True
```

**`needs_checkout` field on `FinishInfo`:** A new boolean field `needs_checkout: bool` is added to `FinishInfo` (default `False`). This signals to prompt generation functions and `fast_forward_merge()` that a `git checkout {base_branch}` must precede the merge in `main_dir`. This avoids merging into whatever branch the main worktree happens to be on.

**Rebase mode with non-worktree target:** When `needs_checkout` is `True`, `get_rebase_finish_prompt()` will generate an augmented prompt:

```
Steps:
1. Check for uncommitted changes in the worktree (fail if any)
2. Rebase {branch} onto the LOCAL {base_branch}:
   git rebase {base_branch}
3. In the main dir ({main_dir}), check out the target branch and merge:
   cd {main_dir} && git checkout {base_branch} && git merge {branch}
```

When `needs_checkout` is `False` (target has its own worktree or auto-detected), the existing prompt is used unchanged.

**`fast_forward_merge()` with checkout:** When `needs_checkout` is `True`, `fast_forward_merge()` must also run `git checkout {base_branch}` in `main_dir` before `git merge --ff-only`. The function will accept `FinishInfo` (which it already does) and conditionally add the checkout step:

```python
def fast_forward_merge(info: FinishInfo) -> tuple[bool, str]:
    if info.needs_checkout:
        checkout = subprocess.run(
            ["git", "checkout", info.base_branch],
            cwd=info.main_dir, capture_output=True, text=True,
        )
        if checkout.returncode != 0:
            return False, f"Failed to check out '{info.base_branch}': {checkout.stderr.strip()}"
    # ... existing merge logic
```

### 8.5 Interaction with `finish_mode`

| Finish Mode | Target has worktree? | `needs_checkout` | Behavior |
|-------------|---------------------|-----------------|----------|
| `rebase` | Yes | `False` | Standard: rebase in feature worktree, merge in target worktree (existing flow) |
| `rebase` | No | `True` | Preflight checks on main worktree (clean, no mid-merge); rebase in feature worktree; checkout target + merge in main worktree |
| `no-ff` | Yes | `False` | Standard: merge `--no-ff` in target worktree (existing flow) |
| `no-ff` | No | N/A | **Error:** target must have a worktree for no-ff merges |
| (auto-detect) | Always yes | `False` | Existing behavior unchanged — auto-detection always finds a worktree branch |

### 8.6 Persistence Across Retries

The `base_branch` override is stored in `FinishInfo`, which is stored in `FinishState`, which is stored on the `Agent` object. This persists across:
- Conflict resolution retries (Claude responds, `on_response_complete_finish` re-diagnoses)
- Cleanup retries (`finish_cleanup` uses `state.info`)
- MCP tool re-invocations (state is checked at the beginning of `finish_worktree`)

No additional persistence mechanism is needed — the existing state management handles this.

### 8.7 `FinishInfo` Immutability

Per the project's Python coding style (frozen dataclasses), `FinishInfo` should be made `frozen=True` as part of this change:

```python
@dataclass(frozen=True)
class FinishInfo:
    """Info needed to finish a worktree."""
    branch_name: str
    base_branch: str
    worktree_dir: Path
    main_dir: Path
    needs_checkout: bool = False  # True when main_dir is a fallback, not already on base_branch
```

This is a low-risk change: `FinishInfo` is already treated as immutable throughout the codebase — no code mutates its fields after construction.

> **Note:** `FinishState` intentionally remains mutable (`@dataclass` without `frozen=True`). It tracks evolving process state — `phase`, `status`, `cleanup_attempts`, and `last_error` are all mutated during the finish flow. Only `FinishInfo` (a value object) gets frozen.

### 8.8 Required API Changes (Current State → Target State)

This section summarizes the specific code changes needed. The current codebase has none of this plumbing — all changes are new.

| Component | Current Signature/State | Target Signature/State |
|-----------|------------------------|----------------------|
| `get_finish_info()` | `(cwd: Path \| None = None)` | `(cwd: Path \| None = None, base_branch: str \| None = None)` |
| `_handle_finish()` | `(app: "ChatApp")` | `(app: "ChatApp", base_branch: str \| None = None)` |
| `handle_worktree_command()` | Calls `_handle_finish(app)` | Extracts `parts[2]`, calls `_handle_finish(app, base_branch)` |
| `finish_worktree()` MCP | Schema `{}`, ignores args | Schema `{}`, extracts `args.get("base_branch")`, passes to `get_finish_info()` |
| `FinishInfo` | `@dataclass`, 4 fields | `@dataclass(frozen=True)`, 5 fields (adds `needs_checkout: bool = False`) |
| `get_rebase_finish_prompt()` | No checkout step | Conditional checkout when `info.needs_checkout` is `True` |
| `fast_forward_merge()` | No checkout step | Conditional checkout when `info.needs_checkout` is `True` |
| `branch_exists()` | Does not exist | New helper in `git.py` |
| `get_local_branches()` | Does not exist | New helper in `git.py` |
| Analytics call | Fires before parsing, no `base_branch_override` | Moves inside `_handle_finish()` after parsing; adds `base_branch_override` property |
| Help display | `"/worktree <name>"` | `"/worktree <name> \| finish [branch]"` |

**Callers of `get_finish_info()` that are NOT affected:** `_handle_discard()` (at `commands.py:385`) also calls `get_finish_info(app.sdk_cwd)`. This caller continues using auto-detection (`base_branch=None` default). No changes needed.

## 9. Error Messages

| Scenario | Message |
|----------|---------|
| Branch name empty | `"Branch name must not be empty"` |
| Branch name starts with `-` | `"Branch name must not start with '-'"` |
| Remote ref detected | `"'origin/main' appears to be a remote branch. Specify a local branch (e.g., 'main'). Run 'git checkout main' to create a local branch first."` |
| Branch does not exist | `"Branch 'xyz' does not exist. Local branches: main, develop, feature-x"` (up to 5, alphabetical, excludes current branch) |
| Self-merge | `"Cannot merge branch 'feature-a' into itself"` |
| No worktree for target (no-ff) | `"Cannot merge into 'release/1.2': no worktree is checked out to that branch. Create a worktree with '/worktree release/1.2', or check out the branch in an existing worktree."` |
| Main worktree dirty (rebase fallback) | `"Cannot use main worktree for merge: it has uncommitted changes. Clean it up first or create a worktree for 'release/1.2'."` |
| Main worktree in mid-merge (rebase fallback) | `"Cannot use main worktree for merge: a merge is in progress."` |
| Not in worktree | `"Not in a feature worktree. Switch to a worktree first."` (existing, unchanged) |

> **Note on "main worktree" in error messages:** When using the rebase-mode fallback (target has no worktree), errors reference the "main worktree" because that is the worktree being used as a fallback merge directory. When a target worktree exists, errors from `diagnose_worktree()` reference the "target directory" (via `MAIN_DIR_NOT_READY`). The existing error message in `commands.py:243` says "main worktree" but `main_dir` may actually be a target worktree — this pre-existing inconsistency is out of scope but should be addressed in a follow-up.

> **Note on V4 (remote ref detection):** V4 checks for `origin/` and `remotes/` prefixes. Other remote names (e.g., `upstream/main`) will fall through to V5 with a less specific error. This is an acceptable limitation — the V4 check is a UX shortcut for the most common case, not a correctness requirement.

## 10. Backward Compatibility

- **Full backward compatibility:** When `base_branch` is `None` (no argument provided), the existing `get_parent_branch()` auto-detection is used unchanged.
- **No breaking changes** to the MCP tool: the new field is optional with no default enforcement. Existing calls with `{}` continue to work.
- **No config changes** required.
- **Existing cleanup/retry state** works unchanged when no override is provided — the code path is identical.
- **Feature rollout:** The TUI path is always available (no feature flag). The MCP path is already gated by the `experimental.finish_worktree` flag. No additional gating is needed for the `base_branch` parameter.

## 11. Recovery

If the user specifies an incorrect `base_branch` and the merge/rebase proceeds:
- **Rebase mode:** `git reflog` shows the pre-rebase state. `git reset --hard <pre-rebase-sha>` reverts.
- **No-ff mode:** The merge commit can be reverted with `git revert <merge-sha>`, or `git reset --hard HEAD~1` before pushing.

These are standard git recovery mechanisms — no claudechic-specific recovery is needed.

> **Safety note:** `git reset --hard` destroys uncommitted changes. This is safe in the post-finish context because the finish process commits or discards all changes before merging. Users should verify they have no uncommitted work before running reset commands.

## 12. Analytics

The analytics event must move from `handle_worktree_command()` (where it currently fires before parsing) into `_handle_finish()` (after `base_branch` is parsed). This allows including the `base_branch_override` property:

```python
# Inside _handle_finish(), after parsing base_branch:
app.run_worker(capture(
    "worktree_action",
    action="finish",
    agent_id=agent_id,
    base_branch_override=base_branch is not None,
))
```

The MCP path tracks tool usage generically via `_track_mcp_tool("finish_worktree")` — no additional MCP-specific analytics are needed.

## 13. Known Limitations

1. **Race condition:** Branch existence is validated at `get_finish_info()` time, but the actual merge happens later (possibly minutes later, after Claude performs a rebase). If the branch is deleted between validation and merge, the merge will fail with a git error. This is acceptable — the user can re-run the command.

2. **Base branch ahead of feature branch:** If the user specifies a base branch that is *ahead* of the feature branch (i.e., the feature is already an ancestor of the target), the rebase is a no-op and the merge will succeed. This is correct behavior but may indicate the user picked the wrong target. No special handling is added — the user can verify the result.

## 14. Pre-Existing Issue: No-ff Merge Conflict Recovery

**Not introduced by this change**, but noted for context: When a `MERGE_HEAD` exists in no-ff mode (merge conflict in progress), `diagnose_worktree()` skips the dirty-check and `determine_resolution_action()` returns `NO_FF` again, re-sending the initial merge prompt instead of a conflict-resolution prompt. This can cause Claude to attempt a duplicate merge. This issue affects the existing no-ff flow regardless of `base_branch` override and should be addressed in a separate PR with a dedicated `RESOLVE_MERGE_CONFLICT` resolution action.

## 15. Testing Strategy

### 15.1 Unit Tests (`tests/test_worktree_finish.py`)

**`branch_exists()` helper:**
- Branch that exists → `True`
- Branch that does not exist → `False`
- Verifies `refs/heads/` prefix (does not match tags or remote refs)

**`get_local_branches()` helper:**
- Returns sorted branch names
- Excludes specified branch
- Respects limit
- Empty repo returns empty list

**`get_finish_info()` with `base_branch` override:**
- Valid branch → returns `FinishInfo` with overridden `base_branch`
- `None` → auto-detect (existing behavior, unchanged)
- Empty string → error
- Starts with `-` → error
- `origin/main` → remote ref error with suggestion
- Non-existent branch → error with branch list
- Self-merge → error
- Valid branch with active worktree → `main_dir` points to that worktree, `needs_checkout = False`
- Valid branch without worktree (rebase mode, main clean) → `main_dir` falls back to main worktree, `needs_checkout = True`
- Valid branch without worktree (rebase mode, main dirty) → error "has uncommitted changes"
- Valid branch without worktree (rebase mode, mid-merge) → error "merge is in progress"
- Valid branch without worktree (no-ff mode) → error

**Command parsing (`_handle_finish`):**
- `/worktree finish` → `base_branch = None`
- `/worktree finish main` → `base_branch = "main"`
- `/worktree finish main extra` → `base_branch = "main extra"` → fails validation (branch not found)
- `/worktree finish -mybranch` → `base_branch = "-mybranch"` → fails validation
- `/worktree finish   main   ` → `base_branch = "main"` (trimmed)

**MCP tool arg extraction:**
- `{}` → `base_branch = None`
- `{"base_branch": "main"}` → `base_branch = "main"`
- `{"base_branch": ""}` → `base_branch = ""` → fails validation
- `{"base_branch": "origin/main"}` → fails validation

### 15.2 Integration Tests (`tests/test_worktree_finish.py`)

All integration tests use `tmp_path` fixtures for isolation, following the pattern in `tests/test_worktree_config.py` (`TestStartWorktreeIntegration`).

- Full `/worktree finish main` in a real git repo with a feature branch that can be fast-forwarded
- Full `/worktree finish main` where rebase is needed (base branch has diverged)
- Full `/worktree finish main` in no-ff mode
- `base_branch` override pointing to a branch with no worktree (rebase mode, main clean) — verify `needs_checkout = True`, prompt includes checkout step
- `base_branch` override pointing to a branch with no worktree (rebase mode, main dirty) — verify error
- `base_branch` override pointing to a branch with no worktree (no-ff mode) — verify error
- `fast_forward_merge()` with `needs_checkout = True` — verify checkout + merge
- `fast_forward_merge()` with `needs_checkout = False` — verify no checkout (existing behavior)
- Verify `FinishInfo` is frozen (attempt attribute assignment raises `FrozenInstanceError`)

> **Note:** `tests/test_worktree_finish.py` is a new file, distinct from `tests/test_worktree_config.py` (which covers path templates and `start_worktree`) and `tests/test_resolution_action.py` (which covers resolution logic). The new file focuses specifically on the `base_branch` override feature.

### 15.3 E2E Tests

- TUI interaction via remote testing endpoint: type `/worktree finish main`, verify merge completes
- MCP tool invocation with `base_branch`, verify merge completes

## 16. Security Considerations

- **No new attack surface:** The `base_branch` parameter is validated against existing local branches via `git rev-parse --verify refs/heads/<name>`. No user input is passed to shell commands unsanitized.
- **Subprocess safety:** All git commands use list-form `subprocess.run()` (no `shell=True`).
- **Option injection prevention:** Branch names starting with `-` are rejected before reaching any git command, matching the pattern in `start_worktree()` (line 296).
- **Path traversal:** Branch names are validated by git itself — `refs/heads/` prefix prevents resolving arbitrary objects.
- **Prompt injection:** Branch names appear in generated prompts for Claude. They are passed through `shlex.quote()` in prompt generation functions, preventing shell metacharacter issues.

## 17. Acceptance Criteria

- [ ] Zero regressions in existing `/worktree finish` behavior (auto-detection path)
- [ ] Positional form `/worktree finish <branch>` works correctly
- [ ] MCP tool accepts and uses the `base_branch` parameter (schema stays `{}`, extracted manually)
- [ ] All validation rules (V1–V8) produce correct errors
- [ ] Both finish modes (rebase, no-ff) work with explicit `base_branch`
- [ ] `needs_checkout` flag triggers `git checkout` in both `fast_forward_merge()` and rebase prompt
- [ ] Rebase fallback path validates main worktree is clean before using it
- [ ] `FinishInfo` is frozen; `FinishState` remains mutable
- [ ] Analytics tracks `base_branch_override` (event moved inside `_handle_finish()`)
- [ ] All existing tests continue to pass
- [ ] New tests achieve 80%+ coverage on changed code (measured via `pytest --cov`)

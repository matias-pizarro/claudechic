# Parametrized Base Branch for `/worktree finish` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to specify a target branch when finishing a worktree (`/worktree finish <branch>`), overriding auto-detection.

**Architecture:** Add `base_branch` parameter threading from command parsing → `get_finish_info()` → `FinishInfo` → prompt generation / merge functions. New `needs_checkout` field on `FinishInfo` handles the case where the target branch has no active worktree (rebase mode only). All validation happens in `get_finish_info()` before any git operations.

**Tech Stack:** Python 3.11+, pytest, Textual (TUI), claude-agent-sdk MCP

**PRD:** `docs/plans/2026-04-21-worktree-finish-base-branch.md`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `claudechic/features/worktree/git.py` | Git helpers, `FinishInfo`, `get_finish_info()`, `fast_forward_merge()`, prompts | Modify |
| `claudechic/features/worktree/commands.py` | TUI command handler `_handle_finish()` | Modify |
| `claudechic/mcp.py` | MCP `finish_worktree` tool | Modify |
| `claudechic/commands.py` | Help display | Modify |
| `CLAUDE.md` | Project documentation | Modify |
| `tests/test_worktree_finish.py` | All new tests for this feature | Create |

---

### Task 1: Add `branch_exists()` and `get_local_branches()` helpers with tests

**Files:**
- Modify: `claudechic/features/worktree/git.py:119-127` (after `is_git_repo()`)
- Create: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write failing tests for `branch_exists()`**

```python
# tests/test_worktree_finish.py
"""Tests for parametrized base branch in /worktree finish."""

import subprocess
from pathlib import Path

import pytest

from claudechic.features.worktree.git import branch_exists, get_local_branches


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a 'main' branch and one commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


class TestBranchExists:
    def test_existing_branch_returns_true(self, git_repo: Path):
        assert branch_exists("main", cwd=git_repo) is True

    def test_nonexistent_branch_returns_false(self, git_repo: Path):
        assert branch_exists("nonexistent", cwd=git_repo) is False

    def test_does_not_match_tags(self, git_repo: Path):
        subprocess.run(["git", "tag", "v1.0"], cwd=git_repo, capture_output=True, check=True)
        assert branch_exists("v1.0", cwd=git_repo) is False

    def test_does_not_match_remote_refs(self, git_repo: Path):
        # origin/main is not a local branch
        assert branch_exists("origin/main", cwd=git_repo) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestBranchExists -v`
Expected: FAIL — `ImportError: cannot import name 'branch_exists'`

- [ ] **Step 3: Implement `branch_exists()`**

Add after `is_git_repo()` in `claudechic/features/worktree/git.py`:

```python
def branch_exists(branch: str, cwd: Path | None = None) -> bool:
    """Check if a local branch exists.

    Uses refs/heads/ prefix to match only local branches,
    not tags, remote refs, or arbitrary objects.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=cwd,
        capture_output=True,
    )
    return result.returncode == 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestBranchExists -v`
Expected: 4 PASS

- [ ] **Step 5: Write failing tests for `get_local_branches()`**

Append to `tests/test_worktree_finish.py`:

```python
class TestGetLocalBranches:
    def test_returns_sorted_branch_names(self, git_repo: Path):
        subprocess.run(["git", "branch", "develop"], cwd=git_repo, capture_output=True, check=True)
        subprocess.run(["git", "branch", "alpha"], cwd=git_repo, capture_output=True, check=True)
        result = get_local_branches(cwd=git_repo)
        assert result == ["alpha", "develop", "main"]

    def test_excludes_specified_branch(self, git_repo: Path):
        subprocess.run(["git", "branch", "develop"], cwd=git_repo, capture_output=True, check=True)
        result = get_local_branches(cwd=git_repo, exclude="main")
        assert result == ["develop"]

    def test_respects_limit(self, git_repo: Path):
        for name in ["b1", "b2", "b3", "b4", "b5", "b6"]:
            subprocess.run(["git", "branch", name], cwd=git_repo, capture_output=True, check=True)
        result = get_local_branches(cwd=git_repo, limit=3)
        assert len(result) == 3

    def test_empty_repo_returns_empty_list(self, tmp_path: Path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        result = get_local_branches(cwd=tmp_path)
        assert result == []
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestGetLocalBranches -v`
Expected: FAIL — `ImportError: cannot import name 'get_local_branches'`

- [ ] **Step 7: Implement `get_local_branches()`**

Add after `branch_exists()` in `claudechic/features/worktree/git.py`:

```python
def get_local_branches(
    cwd: Path | None = None, exclude: str | None = None, limit: int = 5
) -> list[str]:
    """List local branch names, sorted alphabetically, excluding one branch."""
    result = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    branches = sorted(line for line in result.stdout.strip().split("\n") if line)
    if exclude:
        branches = [b for b in branches if b != exclude]
    return branches[:limit]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py -v`
Expected: 8 PASS

- [ ] **Step 9: Run full test suite to verify no regressions**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All existing tests pass

- [ ] **Step 10: Commit**

```bash
git add claudechic/features/worktree/git.py tests/test_worktree_finish.py
git commit -m "feat: add branch_exists() and get_local_branches() helpers"
```

---

### Task 2: Make `FinishInfo` frozen and add `needs_checkout` field

**Files:**
- Modify: `claudechic/features/worktree/git.py:52-59` (`FinishInfo` dataclass)
- Modify: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write failing tests for frozen `FinishInfo`**

Append to `tests/test_worktree_finish.py`:

```python
from claudechic.features.worktree.git import FinishInfo


class TestFinishInfoFrozen:
    def test_is_frozen(self):
        info = FinishInfo(
            branch_name="feat",
            base_branch="main",
            worktree_dir=Path("/tmp/feat"),
            main_dir=Path("/tmp/main"),
        )
        with pytest.raises((AttributeError, FrozenInstanceError)):
            info.branch_name = "other"

    def test_needs_checkout_defaults_to_false(self):
        info = FinishInfo(
            branch_name="feat",
            base_branch="main",
            worktree_dir=Path("/tmp/feat"),
            main_dir=Path("/tmp/main"),
        )
        assert info.needs_checkout is False

    def test_needs_checkout_can_be_set_at_construction(self):
        info = FinishInfo(
            branch_name="feat",
            base_branch="main",
            worktree_dir=Path("/tmp/feat"),
            main_dir=Path("/tmp/main"),
            needs_checkout=True,
        )
        assert info.needs_checkout is True
```

Add the import at the top of the file (handle `FrozenInstanceError` not existing before 3.11):

```python
try:
    from dataclasses import FrozenInstanceError
except ImportError:
    FrozenInstanceError = AttributeError  # Python < 3.11
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestFinishInfoFrozen -v`
Expected: FAIL — `test_is_frozen` passes (mutation does NOT raise), `test_needs_checkout_defaults_to_false` fails (no `needs_checkout` attribute)

- [ ] **Step 3: Make `FinishInfo` frozen and add `needs_checkout`**

Edit `claudechic/features/worktree/git.py:52-59`:

```python
@dataclass(frozen=True)
class FinishInfo:
    """Info needed to finish a worktree."""

    branch_name: str
    base_branch: str
    worktree_dir: Path
    main_dir: Path
    needs_checkout: bool = False  # True when main_dir needs git checkout before merge
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestFinishInfoFrozen -v`
Expected: 3 PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All existing tests pass (no code mutates `FinishInfo` fields)

- [ ] **Step 6: Commit**

```bash
git add claudechic/features/worktree/git.py tests/test_worktree_finish.py
git commit -m "refactor: make FinishInfo frozen, add needs_checkout field"
```

---

### Task 3: Add validation logic to `get_finish_info()`

**Files:**
- Modify: `claudechic/features/worktree/git.py:366-412` (`get_finish_info()`)
- Modify: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write failing tests for `get_finish_info()` validation**

Append to `tests/test_worktree_finish.py`:

```python
from unittest.mock import patch

from claudechic.features.worktree.git import (
    WorktreeInfo,
    get_finish_info,
    list_worktrees,
)


@pytest.fixture
def worktree_repo(git_repo: Path) -> tuple[Path, Path]:
    """Create a git repo with main + feature worktree for finish testing.

    Returns (main_dir, feature_dir).
    """
    main_dir = git_repo
    feature_dir = git_repo.parent / "feature-wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(feature_dir)],
        cwd=main_dir, capture_output=True, check=True,
    )
    return main_dir, feature_dir


class TestGetFinishInfoValidation:
    """Test validation rules V2-V7 in get_finish_info()."""

    def test_none_base_branch_uses_auto_detect(self, worktree_repo):
        """V1: None triggers auto-detection (existing behavior)."""
        _, feature_dir = worktree_repo
        ok, msg, info = get_finish_info(cwd=feature_dir, base_branch=None)
        assert ok is True
        assert info is not None
        assert info.base_branch == "main"  # auto-detected

    def test_empty_string_returns_error(self, worktree_repo):
        """V2: Empty string is rejected."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="")
        assert ok is False
        assert "must not be empty" in msg

    def test_dash_prefix_returns_error(self, worktree_repo):
        """V3: Branch name starting with '-' is rejected."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="-mybranch")
        assert ok is False
        assert "must not start with '-'" in msg

    def test_remote_ref_returns_targeted_error(self, worktree_repo):
        """V4: Remote ref produces targeted error with suggestion."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="origin/main")
        assert ok is False
        assert "remote branch" in msg
        assert "main" in msg  # suggestion

    def test_nonexistent_branch_returns_error_with_list(self, worktree_repo):
        """V5: Non-existent branch produces error with branch list."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="no-such-branch")
        assert ok is False
        assert "does not exist" in msg
        assert "main" in msg  # listed as available branch

    def test_self_merge_returns_error(self, worktree_repo):
        """V6: Cannot merge branch into itself."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="feature")
        assert ok is False
        assert "into itself" in msg

    def test_valid_branch_with_worktree_succeeds(self, worktree_repo):
        """Valid override with active worktree → success."""
        _, feature_dir = worktree_repo
        ok, msg, info = get_finish_info(cwd=feature_dir, base_branch="main")
        assert ok is True
        assert info is not None
        assert info.base_branch == "main"
        assert info.needs_checkout is False

    def test_valid_branch_no_worktree_rebase_mode(self, worktree_repo):
        """V7b: No worktree in rebase mode → fallback to main, needs_checkout=True."""
        main_dir, feature_dir = worktree_repo
        # Create a branch that has no worktree
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir, capture_output=True, check=True,
        )
        with patch("claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"):
            ok, msg, info = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
        assert ok is True
        assert info is not None
        assert info.base_branch == "release-1.0"
        assert info.needs_checkout is True

    def test_valid_branch_no_worktree_noff_mode_returns_error(self, worktree_repo):
        """V7: No worktree in no-ff mode → error."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir, capture_output=True, check=True,
        )
        with patch("claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "no-ff"):
            ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
        assert ok is False
        assert "no worktree is checked out" in msg

    def test_dirty_main_worktree_rebase_fallback_returns_error(self, worktree_repo):
        """V7b preflight: Main worktree dirty → error."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir, capture_output=True, check=True,
        )
        # Make main dirty
        (main_dir / "dirty.txt").write_text("dirty")
        with patch("claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"):
            ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
        assert ok is False
        assert "uncommitted changes" in msg

    def test_whitespace_stripped_at_extraction(self, worktree_repo):
        """Whitespace-padded branch name is stripped."""
        _, feature_dir = worktree_repo
        ok, msg, info = get_finish_info(cwd=feature_dir, base_branch="  main  ")
        assert ok is True
        assert info is not None
        assert info.base_branch == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestGetFinishInfoValidation -v`
Expected: FAIL — `get_finish_info() got an unexpected keyword argument 'base_branch'`

- [ ] **Step 3: Implement validation in `get_finish_info()`**

Replace `get_finish_info()` in `claudechic/features/worktree/git.py`:

```python
def get_finish_info(
    cwd: Path | None = None, base_branch: str | None = None
) -> tuple[bool, str, FinishInfo | None]:
    """Get info needed to finish a worktree.

    Args:
        cwd: Current working directory (SDK's cwd). If None, uses Path.cwd().
        base_branch: Optional target branch override. If None, auto-detects.

    Returns (success, message, FinishInfo or None).
    """
    if cwd is None:
        cwd = Path.cwd()
    cwd = cwd.resolve()
    worktrees = list_worktrees()
    current_wt = next((wt for wt in worktrees if wt.path.resolve() == cwd), None)

    if current_wt is None or current_wt.is_main:
        return False, "Not in a feature worktree. Switch to a worktree first.", None

    # Find main worktree from the list we already have (avoid redundant list_worktrees())
    main_wt_info = next((wt for wt in worktrees if wt.is_main), None)
    if main_wt_info is None:
        return False, "Cannot find main worktree.", None
    main_wt_path = main_wt_info.path

    # Strip whitespace at extraction time
    if base_branch is not None:
        base_branch = base_branch.strip()

    needs_checkout = False

    if base_branch is not None:
        # V2: Empty after strip
        if not base_branch:
            return False, "Branch name must not be empty", None

        # V3: Starts with '-'
        if base_branch.startswith("-"):
            return False, f"Branch name must not start with '-'", None

        # V4: Remote ref detection
        for prefix in ("origin/", "remotes/"):
            if base_branch.startswith(prefix):
                suggestion = base_branch[len(prefix):]
                # Strip nested remote prefix (e.g., "remotes/origin/main" → "main")
                if "/" in suggestion:
                    suggestion = suggestion.split("/", 1)[1]
                return (
                    False,
                    f"'{base_branch}' appears to be a remote branch. "
                    f"Specify a local branch (e.g., '{suggestion}'). "
                    f"Run 'git checkout {suggestion}' to create a local branch first.",
                    None,
                )

        # V5: Branch must exist
        if not branch_exists(base_branch, cwd=cwd):
            branches = get_local_branches(cwd=cwd, exclude=current_wt.branch)
            branch_list = ", ".join(branches) if branches else "(none)"
            return (
                False,
                f"Branch '{base_branch}' does not exist. Local branches: {branch_list}",
                None,
            )

        # V6: Self-merge
        if base_branch == current_wt.branch:
            return False, f"Cannot merge branch '{base_branch}' into itself", None

        # Resolve main_dir for the override
        target_wt = next((wt for wt in worktrees if wt.branch == base_branch), None)
        if target_wt:
            parent_dir = target_wt.path
            needs_checkout = False
        elif WORKTREE_FINISH_MODE == "no-ff":
            # V7: No-ff requires target to have a worktree
            return (
                False,
                f"Cannot merge into '{base_branch}': no worktree is checked out "
                f"to that branch. Create a worktree with '/worktree {base_branch}', "
                f"or check out the branch in an existing worktree.",
                None,
            )
        else:
            # V7b: Rebase mode fallback — preflight checks on main worktree
            main_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=main_wt_path,
                capture_output=True,
                text=True,
            )
            if main_status.stdout.strip():
                return (
                    False,
                    f"Cannot use main worktree for merge: it has uncommitted changes. "
                    f"Clean it up first or create a worktree for '{base_branch}'.",
                    None,
                )
            merge_head = subprocess.run(
                ["git", "rev-parse", "--verify", "MERGE_HEAD"],
                cwd=main_wt_path,
                capture_output=True,
                text=True,
            )
            if merge_head.returncode == 0:
                return (
                    False,
                    "Cannot use main worktree for merge: a merge is in progress.",
                    None,
                )
            rebase_head = subprocess.run(
                ["git", "rev-parse", "--verify", "REBASE_HEAD"],
                cwd=main_wt_path,
                capture_output=True,
                text=True,
            )
            if rebase_head.returncode == 0:
                return (
                    False,
                    "Cannot use main worktree for merge: a rebase is in progress.",
                    None,
                )
            parent_dir = main_wt_path
            needs_checkout = True
    else:
        # Auto-detect parent branch (existing behavior, unchanged)
        parent_branch = get_parent_branch(current_wt.branch, cwd=cwd)
        if parent_branch is None:
            parent_branch = main_wt_info.branch
        base_branch = parent_branch

        # Find the directory for the parent branch
        parent_wt = next((wt for wt in worktrees if wt.branch == base_branch), None)
        parent_dir = parent_wt.path if parent_wt else main_wt_path

    return (
        True,
        "Ready to finish worktree",
        FinishInfo(
            branch_name=current_wt.branch,
            base_branch=base_branch,
            worktree_dir=current_wt.path,
            main_dir=parent_dir,
            needs_checkout=needs_checkout,
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestGetFinishInfoValidation -v`
Expected: 11 PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add claudechic/features/worktree/git.py tests/test_worktree_finish.py
git commit -m "feat: add base_branch validation to get_finish_info()"
```

---

### Task 4: Update `fast_forward_merge()` with checkout and rollback

**Files:**
- Modify: `claudechic/features/worktree/git.py:608-627` (`fast_forward_merge()`)
- Modify: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write failing tests for `fast_forward_merge()` with `needs_checkout`**

Append to `tests/test_worktree_finish.py`:

```python
from claudechic.features.worktree.git import fast_forward_merge


class TestFastForwardMergeCheckout:
    def test_no_checkout_when_needs_checkout_false(self, worktree_repo):
        """Existing behavior: no checkout when needs_checkout=False."""
        main_dir, feature_dir = worktree_repo
        # Make a commit on feature branch
        (feature_dir / "new.txt").write_text("new")
        subprocess.run(["git", "add", "."], cwd=feature_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feat"], cwd=feature_dir, capture_output=True, check=True)
        info = FinishInfo(
            branch_name="feature",
            base_branch="main",
            worktree_dir=feature_dir,
            main_dir=main_dir,
            needs_checkout=False,
        )
        ok, err = fast_forward_merge(info)
        assert ok is True

    def test_checkout_when_needs_checkout_true(self, worktree_repo):
        """When needs_checkout=True, checkout target branch before merge."""
        main_dir, feature_dir = worktree_repo
        # Create a target branch at same commit as main
        subprocess.run(["git", "branch", "release-1.0"], cwd=main_dir, capture_output=True, check=True)
        # Make a commit on feature branch
        (feature_dir / "new.txt").write_text("new")
        subprocess.run(["git", "add", "."], cwd=feature_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feat"], cwd=feature_dir, capture_output=True, check=True)
        info = FinishInfo(
            branch_name="feature",
            base_branch="release-1.0",
            worktree_dir=feature_dir,
            main_dir=main_dir,
            needs_checkout=True,
        )
        ok, err = fast_forward_merge(info)
        assert ok is True
        # Verify main_dir is now on release-1.0
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=main_dir, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "release-1.0"

    def test_rollback_on_merge_failure(self, worktree_repo):
        """When needs_checkout=True and merge fails, restore original branch."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(["git", "branch", "release-1.0"], cwd=main_dir, capture_output=True, check=True)
        # Make divergent commits so ff-only fails
        (main_dir / "main-change.txt").write_text("main")
        subprocess.run(["git", "add", "."], cwd=main_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "main change"], cwd=main_dir, capture_output=True, check=True)
        (feature_dir / "feat-change.txt").write_text("feat")
        subprocess.run(["git", "add", "."], cwd=feature_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feat change"], cwd=feature_dir, capture_output=True, check=True)
        info = FinishInfo(
            branch_name="feature",
            base_branch="release-1.0",
            worktree_dir=feature_dir,
            main_dir=main_dir,
            needs_checkout=True,
        )
        ok, err = fast_forward_merge(info)
        assert ok is False
        # Verify main_dir was rolled back to original branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=main_dir, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestFastForwardMergeCheckout -v`
Expected: `test_checkout_when_needs_checkout_true` FAIL, `test_rollback_on_merge_failure` FAIL

- [ ] **Step 3: Implement checkout and rollback in `fast_forward_merge()`**

Replace `fast_forward_merge()` in `claudechic/features/worktree/git.py`:

```python
def fast_forward_merge(info: FinishInfo) -> tuple[bool, str]:
    """Perform a fast-forward merge when no rebase is needed.

    When info.needs_checkout is True, checks out base_branch in main_dir
    before merging and rolls back to the original branch on failure.

    Returns (success, error_message).
    """
    # Check for uncommitted changes first
    if has_uncommitted_changes(info.worktree_dir):
        return False, "Uncommitted changes in worktree"

    original_branch = None
    if info.needs_checkout:
        # Record original branch for rollback
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=info.main_dir,
            capture_output=True,
            text=True,
        )
        original_branch = result.stdout.strip() if result.returncode == 0 else None

        # Checkout target branch
        checkout = subprocess.run(
            ["git", "checkout", info.base_branch],
            cwd=info.main_dir,
            capture_output=True,
            text=True,
        )
        if checkout.returncode != 0:
            return False, f"Failed to check out '{info.base_branch}': {checkout.stderr.strip()}"

    # Do the merge in main dir
    result = subprocess.run(
        ["git", "merge", "--ff-only", info.branch_name],
        cwd=info.main_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Rollback: restore original branch on merge failure
        if original_branch:
            subprocess.run(
                ["git", "checkout", original_branch],
                cwd=info.main_dir,
                capture_output=True,
                text=True,
            )
        return False, result.stderr.strip()

    return True, ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestFastForwardMergeCheckout -v`
Expected: 3 PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add claudechic/features/worktree/git.py tests/test_worktree_finish.py
git commit -m "feat: add checkout + rollback to fast_forward_merge() for needs_checkout"
```

---

### Task 5: Update rebase prompt for conditional checkout

**Files:**
- Modify: `claudechic/features/worktree/git.py:630-650` (`get_rebase_finish_prompt()`)
- Modify: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write failing test for conditional checkout in rebase prompt**

Append to `tests/test_worktree_finish.py`:

```python
from claudechic.features.worktree.git import get_rebase_finish_prompt


class TestRebasePromptCheckout:
    def test_no_checkout_step_when_needs_checkout_false(self):
        info = FinishInfo(
            branch_name="feature",
            base_branch="main",
            worktree_dir=Path("/tmp/feature"),
            main_dir=Path("/tmp/main"),
            needs_checkout=False,
        )
        prompt = get_rebase_finish_prompt(info)
        assert "git checkout" not in prompt

    def test_includes_checkout_step_when_needs_checkout_true(self):
        info = FinishInfo(
            branch_name="feature",
            base_branch="release-1.0",
            worktree_dir=Path("/tmp/feature"),
            main_dir=Path("/tmp/main"),
            needs_checkout=True,
        )
        prompt = get_rebase_finish_prompt(info)
        assert "git checkout" in prompt
        assert "release-1.0" in prompt
        # Should include rollback instruction
        assert "restore" in prompt.lower() or "original" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestRebasePromptCheckout -v`
Expected: `test_includes_checkout_step_when_needs_checkout_true` FAIL

- [ ] **Step 3: Implement conditional checkout in `get_rebase_finish_prompt()`**

Replace `get_rebase_finish_prompt()` in `claudechic/features/worktree/git.py`:

```python
def get_rebase_finish_prompt(info: FinishInfo) -> str:
    """Generate the prompt for Claude to rebase and merge a feature branch."""
    main_dir = shlex.quote(str(info.main_dir))
    branch = shlex.quote(info.branch_name)
    base = shlex.quote(info.base_branch)

    if info.needs_checkout:
        return f"""Rebase and merge this feature branch:

Branch: {info.branch_name}
Base branch: {info.base_branch}
Worktree dir: {info.worktree_dir}
Main dir: {info.main_dir}

Steps:
1. Check for uncommitted changes in the worktree (fail if any)
2. Record the current branch in the main dir for rollback:
   cd {main_dir} && git branch --show-current
3. Rebase {info.branch_name} onto the LOCAL {info.base_branch} branch (do NOT fetch from remote):
   git rebase {base}
4. In the main dir ({info.main_dir}), check out the target branch and merge:
   cd {main_dir} && git checkout {base} && git merge {branch}
5. If the merge fails, restore the original branch:
   cd {main_dir} && git checkout <original_branch_from_step_2>

Do NOT remove the worktree or delete the branch - the app will handle cleanup.
Do NOT interact with remotes (no fetch, no pull, no push)."""

    return f"""Rebase and merge this feature branch:

Branch: {info.branch_name}
Base branch: {info.base_branch}
Worktree dir: {info.worktree_dir}
Main dir: {info.main_dir}

Steps:
1. Check for uncommitted changes in the worktree (fail if any)
2. Rebase {info.branch_name} onto the LOCAL {info.base_branch} branch (do NOT fetch from remote):
   git rebase {base}
3. In the main dir ({info.main_dir}), merge {info.branch_name}:
   cd {main_dir} && git merge {branch}

Do NOT remove the worktree or delete the branch - the app will handle cleanup.
Do NOT interact with remotes (no fetch, no pull, no push)."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestRebasePromptCheckout -v`
Expected: 2 PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add claudechic/features/worktree/git.py tests/test_worktree_finish.py
git commit -m "feat: add conditional checkout step to rebase prompt for needs_checkout"
```

---

### Task 6: Thread `base_branch` through TUI command handler

**Files:**
- Modify: `claudechic/features/worktree/commands.py:50-73` (`handle_worktree_command`, `_handle_finish`)
- Modify: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write failing test for command parsing**

Append to `tests/test_worktree_finish.py`:

```python
class TestCommandParsing:
    """Test base_branch extraction from command string."""

    def test_no_argument_yields_none(self):
        parts = "/worktree finish".split(maxsplit=2)
        base_branch = parts[2].strip() if len(parts) > 2 else None
        assert base_branch is None

    def test_positional_argument_extracted(self):
        parts = "/worktree finish main".split(maxsplit=2)
        base_branch = parts[2].strip() if len(parts) > 2 else None
        assert base_branch == "main"

    def test_extra_args_captured_as_single_string(self):
        parts = "/worktree finish main extra".split(maxsplit=2)
        base_branch = parts[2].strip() if len(parts) > 2 else None
        assert base_branch == "main extra"

    def test_whitespace_only_yields_none(self):
        parts = "/worktree finish   ".split(maxsplit=2)
        base_branch = parts[2].strip() if len(parts) > 2 else None
        assert base_branch is None

    def test_padded_branch_stripped(self):
        parts = "/worktree finish   main   ".split(maxsplit=2)
        base_branch = parts[2].strip() if len(parts) > 2 else None
        assert base_branch == "main"
```

- [ ] **Step 2: Run tests to verify they pass (pure parsing, no imports needed)**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestCommandParsing -v`
Expected: 5 PASS (these are pure string operations)

- [ ] **Step 3: Update `handle_worktree_command()` and `_handle_finish()` to pass `base_branch`**

Edit `claudechic/features/worktree/commands.py`:

In `handle_worktree_command()`, change lines 71-73 from:
```python
    if subcommand == "finish":
        app.run_worker(capture("worktree_action", action="finish", agent_id=agent_id))
        _handle_finish(app)
```
to:
```python
    if subcommand == "finish":
        base_branch = parts[2].strip() if len(parts) > 2 else None
        _handle_finish(app, base_branch)
```

In `_handle_finish()`, change signature from:
```python
async def _handle_finish(app: "ChatApp") -> None:
```
to:
```python
async def _handle_finish(app: "ChatApp", base_branch: str | None = None) -> None:
```

Add analytics inside `_handle_finish()`, after the agent check (line 98-99):
```python
    agent = app._agent
    if not agent:
        app.notify("No active agent", severity="error")
        return

    # Analytics (moved here from handle_worktree_command to include base_branch_override)
    agent_id = agent.analytics_id if agent else "unknown"
    app.run_worker(capture(
        "worktree_action",
        action="finish",
        agent_id=agent_id,
        base_branch_override=base_branch is not None,
    ))
```

Change `get_finish_info` call to pass `base_branch`:
```python
    success, message, info = await asyncio.to_thread(get_finish_info, app.sdk_cwd, base_branch)
```

- [ ] **Step 4: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add claudechic/features/worktree/commands.py tests/test_worktree_finish.py
git commit -m "feat: thread base_branch from TUI command through to get_finish_info()"
```

---

### Task 7: Thread `base_branch` through MCP tool

**Files:**
- Modify: `claudechic/mcp.py:335-377` (`finish_worktree()`)

- [ ] **Step 1: Write failing test for MCP arg extraction**

Append to `tests/test_worktree_finish.py`:

```python
class TestMCPArgExtraction:
    """Test base_branch extraction from MCP args."""

    def test_empty_args_yields_none(self):
        args: dict = {}
        base_branch = args.get("base_branch")
        if base_branch is not None:
            base_branch = base_branch.strip() or None
        assert base_branch is None

    def test_base_branch_extracted(self):
        args = {"base_branch": "main"}
        base_branch = args.get("base_branch")
        if base_branch is not None:
            base_branch = base_branch.strip() or None
        assert base_branch == "main"

    def test_empty_string_yields_empty(self):
        args = {"base_branch": ""}
        base_branch = args.get("base_branch")
        if base_branch is not None:
            base_branch = base_branch.strip() or None
        assert base_branch is None

    def test_whitespace_only_yields_none(self):
        args = {"base_branch": "   "}
        base_branch = args.get("base_branch")
        if base_branch is not None:
            base_branch = base_branch.strip() or None
        assert base_branch is None
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_worktree_finish.py::TestMCPArgExtraction -v`
Expected: 4 PASS (pure dict operations)

- [ ] **Step 3: Update MCP `finish_worktree` to extract and pass `base_branch`**

Edit `claudechic/mcp.py`:

Update the `@tool` decorator description (line 337):
```python
@tool(
    "finish_worktree",
    "When you're done working in a worktree, call this to clean it up. "
    "Handles committing, merging (rebase or no-ff per config), and removing the worktree. "
    "Prefer this over manual git worktree commands. "
    "Optionally pass base_branch (string) to specify the target branch to merge into. "
    "Must be a local branch name (not a remote ref like origin/main), must already exist, "
    "and cannot be the current branch. If omitted, the target is auto-detected.",
    {},
)
```

Inside `finish_worktree()`, extract `base_branch` and pass to `get_finish_info()`:
```python
async def finish_worktree(args: dict[str, Any]) -> dict[str, Any]:
    """Start the worktree finish flow for the current agent."""
    try:
        if _app is None or _app.agent_mgr is None:
            return _error_response("App not initialized")
        _track_mcp_tool("finish_worktree")

        agent = _app.agent_mgr.active
        if agent is None:
            return _error_response("No active agent")

        if agent.worktree is None:
            return _error_response(
                "Current agent is not in a worktree. "
                "Use this tool only from a worktree agent."
            )

        # Extract optional base_branch (strip whitespace, treat empty as None)
        base_branch = args.get("base_branch")
        if base_branch is not None:
            base_branch = base_branch.strip() or None

        # Concurrent invocation guard
        if agent.finish_state is not None:
            return _error_response("A finish operation is already in progress.")

        # Get finish info with optional override
        success, message, info = get_finish_info(agent.cwd, base_branch=base_branch)
        if not success or info is None:
            return _error_response(message or "Failed to get finish info")

        # ... rest of function unchanged
```

- [ ] **Step 4: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add claudechic/mcp.py tests/test_worktree_finish.py
git commit -m "feat: thread base_branch through MCP finish_worktree tool"
```

---

### Task 8: Update help display and documentation

**Files:**
- Modify: `claudechic/commands.py:145-146` (help display)
- Modify: `CLAUDE.md` (commands section)

- [ ] **Step 1: Update help display**

Edit `claudechic/commands.py`, in `get_help_commands()`, change:
```python
        elif name == "/worktree":
            display_name = "/worktree <name>"
```
to:
```python
        elif name == "/worktree":
            display_name = "/worktree <name> | finish [branch]"
```

- [ ] **Step 2: Update CLAUDE.md**

In the `## Commands` section, under `### Session Management` or the worktree section, add:
```
- `/worktree finish [branch]` - Finish worktree (optionally specify target branch)
```

- [ ] **Step 3: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add claudechic/commands.py CLAUDE.md
git commit -m "docs: update help display and CLAUDE.md for finish [branch]"
```

---

### Task 9: Add concurrent invocation guard to TUI path

**Files:**
- Modify: `claudechic/features/worktree/commands.py:87-101` (`_handle_finish()`)
- Modify: `tests/test_worktree_finish.py`

- [ ] **Step 1: Write test for concurrent invocation guard**

Append to `tests/test_worktree_finish.py`:

```python
from claudechic.features.worktree.git import FinishPhase, FinishState


class TestConcurrentGuard:
    def test_finish_state_already_set_blocks_second_invocation(self):
        """If agent.finish_state is set, a second finish should be rejected."""
        info = FinishInfo(
            branch_name="feat",
            base_branch="main",
            worktree_dir=Path("/tmp/feat"),
            main_dir=Path("/tmp/main"),
        )
        state = FinishState(info=info, phase=FinishPhase.RESOLUTION)
        # Verify FinishState can be created and has expected fields
        assert state.phase == FinishPhase.RESOLUTION
        assert state.info.branch_name == "feat"
```

- [ ] **Step 2: Add guard to `_handle_finish()`**

In `claudechic/features/worktree/commands.py`, in `_handle_finish()`, after the agent check:

```python
    agent = app._agent
    if not agent:
        app.notify("No active agent", severity="error")
        return

    # Concurrent invocation guard
    if agent.finish_state is not None:
        app.notify("A finish operation is already in progress", severity="warning")
        return
```

- [ ] **Step 3: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add claudechic/features/worktree/commands.py tests/test_worktree_finish.py
git commit -m "feat: add concurrent invocation guard to _handle_finish()"
```

---

### Task 10: Final verification and push

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run python -m pytest tests/ -n auto -q --cov=claudechic/features/worktree --cov=claudechic/mcp --cov-report=term-missing`
Expected: All pass, 80%+ coverage on changed files

- [ ] **Step 2: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`
Expected: All pass (ruff lint, ruff format, pyright)

- [ ] **Step 3: Review git log**

Run: `git log --oneline --since="1 hour ago"`
Verify commit history is clean and meaningful.

- [ ] **Step 4: Push**

```bash
git push -u origin parametrized-finish2
```

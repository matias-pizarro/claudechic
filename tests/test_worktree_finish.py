"""Tests for parametrized base branch in /worktree finish."""

import subprocess
from pathlib import Path

import pytest

from claudechic.features.worktree.git import (
    FinishInfo,
    branch_exists,
    get_local_branches,
)

try:
    from dataclasses import FrozenInstanceError
except ImportError:
    FrozenInstanceError = AttributeError  # type: ignore[misc,assignment]


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a 'main' branch and one commit."""
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True
    )
    return tmp_path


class TestBranchExists:
    def test_existing_branch_returns_true(self, git_repo: Path):
        assert branch_exists("main", cwd=git_repo) is True

    def test_nonexistent_branch_returns_false(self, git_repo: Path):
        assert branch_exists("nonexistent", cwd=git_repo) is False

    def test_does_not_match_tags(self, git_repo: Path):
        subprocess.run(
            ["git", "tag", "v1.0"], cwd=git_repo, capture_output=True, check=True
        )
        assert branch_exists("v1.0", cwd=git_repo) is False

    def test_does_not_match_remote_refs(self, git_repo: Path):
        assert branch_exists("origin/main", cwd=git_repo) is False


class TestGetLocalBranches:
    def test_returns_sorted_branch_names(self, git_repo: Path):
        subprocess.run(
            ["git", "branch", "develop"], cwd=git_repo, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "branch", "alpha"], cwd=git_repo, capture_output=True, check=True
        )
        result = get_local_branches(cwd=git_repo)
        assert result == ["alpha", "develop", "main"]

    def test_excludes_specified_branch(self, git_repo: Path):
        subprocess.run(
            ["git", "branch", "develop"], cwd=git_repo, capture_output=True, check=True
        )
        result = get_local_branches(cwd=git_repo, exclude="main")
        assert result == ["develop"]

    def test_respects_limit(self, git_repo: Path):
        for name in ["b1", "b2", "b3", "b4", "b5", "b6"]:
            subprocess.run(
                ["git", "branch", name], cwd=git_repo, capture_output=True, check=True
            )
        result = get_local_branches(cwd=git_repo, limit=3)
        assert len(result) == 3

    def test_empty_repo_returns_empty_list(self, tmp_path: Path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        result = get_local_branches(cwd=tmp_path)
        assert result == []


class TestFinishInfoFrozen:
    def test_is_frozen(self):
        info = FinishInfo(
            branch_name="feat",
            base_branch="main",
            worktree_dir=Path("/tmp/feat"),
            main_dir=Path("/tmp/main"),
        )
        with pytest.raises((AttributeError, FrozenInstanceError)):
            info.branch_name = "other"  # type: ignore[misc]

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


from unittest.mock import patch

from claudechic.features.worktree.git import (
    get_finish_info,
)


@pytest.fixture
def worktree_repo(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Create a git repo with main + feature worktree for finish testing.

    Changes CWD to the feature worktree so list_worktrees() finds the right repo.

    Returns (main_dir, feature_dir).
    """
    main_dir = git_repo
    # Use a sibling dir named after the git_repo dir to avoid collisions
    feature_dir = git_repo.parent / f"{git_repo.name}-feature-wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feature", str(feature_dir)],
        cwd=main_dir,
        capture_output=True,
        check=True,
    )
    monkeypatch.chdir(feature_dir)
    return main_dir, feature_dir


class TestGetFinishInfoValidation:
    """Test validation rules V2-V7 in get_finish_info()."""

    def test_none_base_branch_uses_auto_detect(self, worktree_repo: tuple[Path, Path]):
        """V1: None triggers auto-detection (existing behavior)."""
        _, feature_dir = worktree_repo
        ok, msg, info = get_finish_info(cwd=feature_dir, base_branch=None)
        assert ok is True
        assert info is not None
        assert info.base_branch == "main"  # auto-detected

    def test_empty_string_returns_error(self, worktree_repo: tuple[Path, Path]):
        """V2: Empty string is rejected."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="")
        assert ok is False
        assert "must not be empty" in msg

    def test_dash_prefix_returns_error(self, worktree_repo: tuple[Path, Path]):
        """V3: Branch name starting with '-' is rejected."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="-mybranch")
        assert ok is False
        assert "must not start with '-'" in msg

    def test_remote_ref_returns_targeted_error(self, worktree_repo: tuple[Path, Path]):
        """V4: Remote ref produces targeted error with suggestion."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="origin/main")
        assert ok is False
        assert "remote branch" in msg
        assert "main" in msg  # suggestion

    def test_nonexistent_branch_returns_error_with_list(
        self, worktree_repo: tuple[Path, Path]
    ):
        """V5: Non-existent branch produces error with branch list."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="no-such-branch")
        assert ok is False
        assert "does not exist" in msg
        assert "main" in msg  # listed as available branch

    def test_self_merge_returns_error(self, worktree_repo: tuple[Path, Path]):
        """V6: Cannot merge branch into itself."""
        _, feature_dir = worktree_repo
        ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="feature")
        assert ok is False
        assert "into itself" in msg

    def test_valid_branch_with_worktree_succeeds(
        self, worktree_repo: tuple[Path, Path]
    ):
        """Valid override with active worktree -> success."""
        _, feature_dir = worktree_repo
        ok, msg, info = get_finish_info(cwd=feature_dir, base_branch="main")
        assert ok is True
        assert info is not None
        assert info.base_branch == "main"
        assert info.needs_checkout is False

    def test_valid_branch_no_worktree_rebase_mode(
        self, worktree_repo: tuple[Path, Path]
    ):
        """V7b: No worktree in rebase mode -> fallback to main, needs_checkout=True."""
        main_dir, feature_dir = worktree_repo
        # Create a branch that has no worktree
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        with patch("claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"):
            ok, msg, info = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
        assert ok is True
        assert info is not None
        assert info.base_branch == "release-1.0"
        assert info.needs_checkout is True

    def test_valid_branch_no_worktree_noff_mode_returns_error(
        self, worktree_repo: tuple[Path, Path]
    ):
        """V7: No worktree in no-ff mode -> error."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        with patch("claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "no-ff"):
            ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
        assert ok is False
        assert "no worktree is checked out" in msg

    def test_dirty_main_worktree_rebase_fallback_returns_error(
        self, worktree_repo: tuple[Path, Path]
    ):
        """V7b preflight: Main worktree dirty -> error."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        # Make main dirty
        (main_dir / "dirty.txt").write_text("dirty")
        with patch("claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"):
            ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
        assert ok is False
        assert "uncommitted changes" in msg

    def test_mid_merge_main_worktree_rebase_fallback_returns_error(
        self, worktree_repo: tuple[Path, Path]
    ):
        """V7b preflight: Main worktree in mid-merge -> error."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        # Simulate MERGE_HEAD by creating the ref (use absolute path)
        git_dir_abs = main_dir / ".git"
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=main_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        merge_head_path = git_dir_abs / "MERGE_HEAD"
        merge_head_path.write_text(head_sha)
        try:
            with patch(
                "claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"
            ):
                ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
            assert ok is False
            assert "merge is in progress" in msg
        finally:
            merge_head_path.unlink(missing_ok=True)

    def test_mid_rebase_main_worktree_rebase_fallback_returns_error(
        self, worktree_repo: tuple[Path, Path]
    ):
        """V7b preflight: Main worktree in mid-rebase -> error."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        # Simulate REBASE_HEAD (use absolute path)
        git_dir_abs = main_dir / ".git"
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=main_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        rebase_head_path = git_dir_abs / "REBASE_HEAD"
        rebase_head_path.write_text(head_sha)
        try:
            with patch(
                "claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"
            ):
                ok, msg, _ = get_finish_info(cwd=feature_dir, base_branch="release-1.0")
            assert ok is False
            assert "rebase is in progress" in msg
        finally:
            rebase_head_path.unlink(missing_ok=True)

    def test_whitespace_stripped_at_extraction(self, worktree_repo: tuple[Path, Path]):
        """Whitespace-padded branch name is stripped."""
        _, feature_dir = worktree_repo
        ok, msg, info = get_finish_info(cwd=feature_dir, base_branch="  main  ")
        assert ok is True
        assert info is not None
        assert info.base_branch == "main"


from claudechic.features.worktree.git import fast_forward_merge


class TestFastForwardMergeCheckout:
    def test_no_checkout_when_needs_checkout_false(
        self, worktree_repo: tuple[Path, Path]
    ):
        """Existing behavior: no checkout when needs_checkout=False."""
        main_dir, feature_dir = worktree_repo
        # Make a commit on feature branch
        (feature_dir / "new.txt").write_text("new")
        subprocess.run(
            ["git", "add", "."], cwd=feature_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "feat"],
            cwd=feature_dir,
            capture_output=True,
            check=True,
        )
        info = FinishInfo(
            branch_name="feature",
            base_branch="main",
            worktree_dir=feature_dir,
            main_dir=main_dir,
            needs_checkout=False,
        )
        ok, err = fast_forward_merge(info)
        assert ok is True

    def test_checkout_when_needs_checkout_true(self, worktree_repo: tuple[Path, Path]):
        """When needs_checkout=True, checkout target branch before merge."""
        main_dir, feature_dir = worktree_repo
        # Create a target branch at same commit as main
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        # Make a commit on feature branch
        (feature_dir / "new.txt").write_text("new")
        subprocess.run(
            ["git", "add", "."], cwd=feature_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "feat"],
            cwd=feature_dir,
            capture_output=True,
            check=True,
        )
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
            cwd=main_dir,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "release-1.0"

    def test_rollback_on_merge_failure(self, worktree_repo: tuple[Path, Path]):
        """When needs_checkout=True and merge fails, restore original branch."""
        main_dir, feature_dir = worktree_repo
        subprocess.run(
            ["git", "branch", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        # Make a divergent commit on release-1.0 so ff-only fails
        subprocess.run(
            ["git", "checkout", "release-1.0"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        (main_dir / "release-change.txt").write_text("release")
        subprocess.run(
            ["git", "add", "."], cwd=main_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "release diverge"],
            cwd=main_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "main"], cwd=main_dir, capture_output=True, check=True
        )
        # Make a commit on feature branch (diverged from release-1.0)
        (feature_dir / "feat-change.txt").write_text("feat")
        subprocess.run(
            ["git", "add", "."], cwd=feature_dir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "feat change"],
            cwd=feature_dir,
            capture_output=True,
            check=True,
        )
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
            cwd=main_dir,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "main"


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

"""Tests for determine_resolution_action with finish_mode variations."""

from unittest.mock import patch

import pytest

from claudechic.features.worktree.git import (
    ResolutionAction,
    WorktreeStatus,
    determine_resolution_action,
    get_no_ff_finish_prompt,
)


def _make_status(
    *,
    commits_ahead: int = 1,
    is_merged: bool = False,
    can_fast_forward: bool = False,
    uncommitted_files: list[str] | None = None,
    untracked_gitignored: list[str] | None = None,
    untracked_other: list[str] | None = None,
) -> WorktreeStatus:
    """Create a WorktreeStatus for testing."""
    return WorktreeStatus(
        commits_ahead=commits_ahead,
        is_merged=is_merged,
        can_fast_forward=can_fast_forward,
        uncommitted_files=uncommitted_files or [],
        untracked_gitignored=untracked_gitignored or [],
        untracked_other=untracked_other or [],
    )


class TestDetermineResolutionActionRebaseMode:
    """Test determine_resolution_action when finish_mode is 'rebase' (default)."""

    @pytest.fixture(autouse=True)
    def _patch_mode(self):
        with patch(
            "claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "rebase"
        ):
            yield

    def test_no_commits_and_clean_returns_none(self):
        status = _make_status(commits_ahead=0)
        assert determine_resolution_action(status) == ResolutionAction.NONE

    def test_already_merged_and_clean_returns_none(self):
        status = _make_status(is_merged=True)
        assert determine_resolution_action(status) == ResolutionAction.NONE

    def test_only_gitignored_untracked_returns_clean_gitignored(self):
        status = _make_status(untracked_gitignored=["__pycache__/foo.pyc"])
        assert (
            determine_resolution_action(status) == ResolutionAction.CLEAN_GITIGNORED
        )

    def test_uncommitted_changes_returns_prompt(self):
        status = _make_status(uncommitted_files=["modified.py"])
        assert (
            determine_resolution_action(status) == ResolutionAction.PROMPT_UNCOMMITTED
        )

    def test_untracked_other_returns_prompt(self):
        status = _make_status(untracked_other=["new_file.py"])
        assert (
            determine_resolution_action(status) == ResolutionAction.PROMPT_UNCOMMITTED
        )

    def test_can_fast_forward_returns_fast_forward(self):
        status = _make_status(can_fast_forward=True)
        assert determine_resolution_action(status) == ResolutionAction.FAST_FORWARD

    def test_cannot_fast_forward_returns_rebase(self):
        status = _make_status(can_fast_forward=False)
        assert determine_resolution_action(status) == ResolutionAction.REBASE


class TestDetermineResolutionActionNoFfMode:
    """Test determine_resolution_action when finish_mode is 'no-ff'."""

    @pytest.fixture(autouse=True)
    def _patch_mode(self):
        with patch(
            "claudechic.features.worktree.git.WORKTREE_FINISH_MODE", "no-ff"
        ):
            yield

    def test_no_commits_and_clean_returns_none(self):
        """Even in no-ff mode, no commits means nothing to merge."""
        status = _make_status(commits_ahead=0)
        assert determine_resolution_action(status) == ResolutionAction.NONE

    def test_already_merged_returns_none(self):
        status = _make_status(is_merged=True)
        assert determine_resolution_action(status) == ResolutionAction.NONE

    def test_only_gitignored_untracked_returns_clean_gitignored(self):
        """Gitignored cleanup happens before mode-specific logic."""
        status = _make_status(untracked_gitignored=["build/output.o"])
        assert (
            determine_resolution_action(status) == ResolutionAction.CLEAN_GITIGNORED
        )

    def test_uncommitted_changes_returns_prompt(self):
        """Uncommitted changes prompt happens before mode-specific logic."""
        status = _make_status(uncommitted_files=["dirty.py"])
        assert (
            determine_resolution_action(status) == ResolutionAction.PROMPT_UNCOMMITTED
        )

    def test_can_fast_forward_still_returns_no_ff(self):
        """In no-ff mode, even FF-eligible branches get merge --no-ff."""
        status = _make_status(can_fast_forward=True)
        assert determine_resolution_action(status) == ResolutionAction.NO_FF

    def test_cannot_fast_forward_returns_no_ff(self):
        status = _make_status(can_fast_forward=False)
        assert determine_resolution_action(status) == ResolutionAction.NO_FF


class TestDetermineResolutionActionUnknownMode:
    """Test that unknown/invalid finish_mode values default to rebase behavior."""

    @pytest.mark.parametrize(
        "mode",
        [None, "", "typo", "rebse", "merge", "noff", "NO-FF"],
    )
    def test_unknown_mode_defaults_to_rebase_behavior(self, mode):
        """Any value other than 'no-ff' should produce rebase behavior."""
        with patch(
            "claudechic.features.worktree.git.WORKTREE_FINISH_MODE", mode
        ):
            status = _make_status(can_fast_forward=True)
            assert determine_resolution_action(status) == ResolutionAction.FAST_FORWARD

            status = _make_status(can_fast_forward=False)
            assert determine_resolution_action(status) == ResolutionAction.REBASE


class TestGetNoFfFinishPrompt:
    """Test the no-ff finish prompt content."""

    def test_does_not_contain_git_checkout(self):
        """The no-ff prompt should not include git checkout (worktree already on branch)."""
        from pathlib import Path

        from claudechic.features.worktree.git import FinishInfo

        info = FinishInfo(
            branch_name="feature-x",
            base_branch="main",
            worktree_dir=Path("/tmp/worktree"),
            main_dir=Path("/tmp/main"),
        )
        prompt = get_no_ff_finish_prompt(info)
        assert "git checkout" not in prompt

    def test_contains_merge_no_ff(self):
        """The prompt must include --no-ff merge instruction."""
        from pathlib import Path

        from claudechic.features.worktree.git import FinishInfo

        info = FinishInfo(
            branch_name="feature-x",
            base_branch="main",
            worktree_dir=Path("/tmp/worktree"),
            main_dir=Path("/tmp/main"),
        )
        prompt = get_no_ff_finish_prompt(info)
        assert "git merge --no-ff" in prompt

    def test_contains_no_rebase_instruction(self):
        """The no-ff prompt must explicitly say not to rebase."""
        from pathlib import Path

        from claudechic.features.worktree.git import FinishInfo

        info = FinishInfo(
            branch_name="feature-x",
            base_branch="main",
            worktree_dir=Path("/tmp/worktree"),
            main_dir=Path("/tmp/main"),
        )
        prompt = get_no_ff_finish_prompt(info)
        assert "Do NOT rebase" in prompt

    def test_shell_quotes_branch_names(self):
        """Branch names with special chars should be shell-quoted in commands."""
        from pathlib import Path

        from claudechic.features.worktree.git import FinishInfo

        info = FinishInfo(
            branch_name="feature/with-slash",
            base_branch="main",
            worktree_dir=Path("/tmp/worktree"),
            main_dir=Path("/tmp/main dir"),
        )
        prompt = get_no_ff_finish_prompt(info)
        # shlex.quote wraps in single quotes when needed
        assert "feature/with-slash" in prompt  # Shown in info section
        assert "'/tmp/main dir'" in prompt  # Path with space is quoted


class TestGetRebaseFinishPromptShellQuoting:
    """Test that get_rebase_finish_prompt also uses shlex.quote."""

    def test_shell_quotes_branch_and_paths(self):
        """Rebase prompt should quote branch names and paths in shell commands."""
        from pathlib import Path

        from claudechic.features.worktree.git import FinishInfo, get_rebase_finish_prompt

        info = FinishInfo(
            branch_name="feature/with-slash",
            base_branch="main",
            worktree_dir=Path("/tmp/worktree"),
            main_dir=Path("/tmp/main dir"),
        )
        prompt = get_rebase_finish_prompt(info)
        # Path with space must be quoted in command lines
        assert "'/tmp/main dir'" in prompt
        # Branch info section shows unquoted for readability
        assert "Branch: feature/with-slash" in prompt

    def test_base_branch_quoted_in_rebase_command(self):
        """Base branch with special chars should be quoted in git rebase command."""
        from pathlib import Path

        from claudechic.features.worktree.git import FinishInfo, get_rebase_finish_prompt

        info = FinishInfo(
            branch_name="feature-x",
            base_branch="release/1.0",
            worktree_dir=Path("/tmp/worktree"),
            main_dir=Path("/tmp/main"),
        )
        prompt = get_rebase_finish_prompt(info)
        # git rebase command should use the quoted base
        assert "git rebase release/1.0" in prompt  # shlex doesn't quote simple slashes

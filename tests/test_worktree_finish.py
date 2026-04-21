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
        assert branch_exists("origin/main", cwd=git_repo) is False


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

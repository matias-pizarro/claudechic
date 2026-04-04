"""Tests for AgentItem context display in sidebar."""

import os

from rich.text import Text

from claudechic.widgets.layout.sidebar import AgentItem
from claudechic.enums import AgentStatus


def _plain(text: Text) -> str:
    """Extract plain string from Rich Text."""
    return text.plain


class TestAgentItemContextLabel:
    def test_context_label_format(self):
        """Context row shows percentage and token counts."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 18500
        item._max_tokens = 1_000_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "2%" in plain
        assert "18.5K" in plain
        assert "1M" in plain

    def test_context_label_zero_tokens(self):
        """Zero tokens shows 0% with counts."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 0
        item._max_tokens = 200_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "0%" in plain

    def test_context_label_high_usage(self):
        """High usage shows correct percentage."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 160_000
        item._max_tokens = 200_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "80%" in plain

    def test_context_label_1m_window(self):
        """1M context window shows correct percentage and total."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 50_000
        item._max_tokens = 1_000_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "5%" in plain
        assert "50.0K" in plain
        assert "1M" in plain

    def test_update_context_sets_values(self):
        """update_context() sets internal state correctly."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item.update_context(cwd="/home/user/project", tokens=75_000, max_tokens=1_000_000)
        assert item._cwd == "/home/user/project"
        assert item._tokens == 75_000
        assert item._max_tokens == 1_000_000

    def test_update_context_partial(self):
        """update_context() only updates provided fields."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 50_000
        item._max_tokens = 200_000
        item.update_context(max_tokens=1_000_000)
        assert item._tokens == 50_000  # Unchanged
        assert item._max_tokens == 1_000_000  # Updated


class TestAgentItemCwdLabel:
    def test_cwd_truncation(self):
        """Long paths are front-truncated."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._cwd = "/very/long/path/to/some/deeply/nested/project"
        label = item._render_cwd_label()
        plain = _plain(label)
        assert plain.startswith("\u2026") or len(plain) <= 20

    def test_cwd_short_path(self):
        """Short paths are shown as-is."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._cwd = "~/project"
        label = item._render_cwd_label()
        plain = _plain(label)
        assert plain == "~/project"

    def test_cwd_home_replacement(self):
        """Home directory is replaced with ~."""
        home = os.path.expanduser("~")
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._cwd = f"{home}/code/myproject"
        label = item._render_cwd_label()
        plain = _plain(label)
        assert plain.startswith("~")
        assert home not in plain

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

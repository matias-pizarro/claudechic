"""Tests for formatting functions."""

import os
from unittest.mock import patch

from claudechic.formatting import format_cwd, format_tokens, parse_context_size


class TestFormatTokens:
    def test_zero(self):
        assert format_tokens(0) == "0"

    def test_small_number(self):
        assert format_tokens(500) == "500"

    def test_exactly_1k(self):
        assert format_tokens(1000) == "1.0K"

    def test_thousands(self):
        assert format_tokens(18500) == "18.5K"

    def test_round_thousands(self):
        assert format_tokens(42000) == "42.0K"

    def test_large_thousands(self):
        assert format_tokens(200000) == "200.0K"

    def test_exactly_1m(self):
        assert format_tokens(1000000) == "1M"

    def test_millions(self):
        assert format_tokens(1500000) == "1.5M"


class TestParseContextSize:
    def test_1m_context(self):
        assert parse_context_size("Claude 4 Sonnet (1M context)") == 1_000_000

    def test_200k_context(self):
        assert parse_context_size("Claude 3.5 Haiku (200K context)") == 200_000

    def test_no_parentheses(self):
        assert parse_context_size("Claude 4 Sonnet") is None

    def test_empty_string(self):
        assert parse_context_size("") is None

    def test_opus_1m(self):
        assert parse_context_size("Opus 4.6 (1M context)") == 1_000_000

    def test_model_id_format(self):
        assert parse_context_size("claude-opus-4-6[1m]") == 1_000_000

    def test_model_id_200k(self):
        assert parse_context_size("claude-sonnet-4-6[200k]") == 200_000

    def test_model_id_no_bracket(self):
        assert parse_context_size("claude-sonnet-4-6") is None

    def test_plain_with_keyword(self):
        """SDK displayName format: 'Opus 4.6 with 1M context'."""
        assert parse_context_size("Opus 4.6 with 1M context") == 1_000_000

    def test_plain_middle_dot(self):
        """SDK description format: 'Opus 4.6 · 1M context · ...'."""
        assert (
            parse_context_size("Opus 4.6 · 1M context · extended thinking") == 1_000_000
        )

    def test_plain_200k(self):
        assert parse_context_size("Haiku 4.5 with 200K context") == 200_000

    def test_short_name_no_context(self):
        """SDK may return just 'Sonnet' with no context info."""
        assert parse_context_size("Sonnet") is None


class TestFormatCwd:
    """Tests for format_cwd() — segment-based path truncation."""

    def test_empty_path(self):
        assert format_cwd("", 40) == ""

    def test_budget_below_minimum(self):
        """Budget < 4 returns empty (can't display anything useful)."""
        assert format_cwd("/home/user/project", 3) == ""

    def test_budget_exactly_4(self):
        """Budget = 4 is the minimum useful display."""
        result = format_cwd("/some/long/path", 4)
        assert len(result) <= 4
        assert result != ""

    def test_short_path_fits(self):
        """Paths that fit within budget are returned as-is (after ~ sub)."""
        assert format_cwd("~/project", 20) == "~/project"

    def test_exact_fit(self):
        """Path that exactly equals max_length is not truncated."""
        path = "~/myproject"
        assert format_cwd(path, len(path)) == path

    @patch.dict(os.environ, {"HOME": "/home/testuser"})
    def test_home_substitution(self):
        """Home directory prefix is replaced with ~."""
        result = format_cwd("/home/testuser/code/myproject", 40)
        assert result.startswith("~")
        assert "/home/testuser" not in result

    def test_no_home_prefix(self):
        """Paths not under home are shown as-is."""
        result = format_cwd("/var/log/app", 40)
        assert result == "/var/log/app"

    def test_segment_truncation(self):
        """Long paths truncate at segment boundaries with … prefix."""
        result = format_cwd("~/code/projects/claudechic/statusline", 20)
        assert result.startswith("\u2026/")
        # Should show last segment(s) that fit
        assert "statusline" in result

    def test_segment_truncation_shows_most_segments(self):
        """Truncation includes as many right-side segments as fit."""
        path = "~/a/b/c/d/e/project"
        result = format_cwd(path, 15)
        assert result.startswith("\u2026/")
        assert "project" in result

    def test_last_segment_fallback_char_truncate(self):
        """When last segment alone exceeds budget, fall back to char truncation."""
        result = format_cwd("~/very-long-directory-name-that-exceeds-budget", 15)
        assert result.startswith("\u2026")
        assert len(result) <= 15

    def test_single_segment_path(self):
        """Single segment (just filename, no dirs) fits or truncates."""
        assert format_cwd("project", 20) == "project"

    def test_root_path(self):
        """Root path '/' is handled."""
        result = format_cwd("/", 10)
        assert result == "/"

    def test_segment_truncation_various_budgets(self):
        """Test segment truncation at different budgets."""
        path = "~/code/projects/claudechic/claudechic-statusline"
        # Large budget — fits or shows many segments
        result_35 = format_cwd(path, 35)
        # Medium budget — fewer segments
        result_25 = format_cwd(path, 25)
        # Small budget — just last segment or truncated
        result_15 = format_cwd(path, 15)

        assert len(result_35) <= 35
        assert len(result_25) <= 25
        assert len(result_15) <= 15
        # Larger budgets show more
        assert len(result_35) >= len(result_25) >= len(result_15)

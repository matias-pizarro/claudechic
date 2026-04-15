"""Tests for formatting functions."""

import os
from unittest.mock import patch

from claudechic.formatting import (
    format_cwd,
    format_tokens,
    parse_context_size,
    strip_ansi,
)


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


class TestStripAnsi:
    """Tests for strip_ansi() — comprehensive ANSI/terminal escape removal."""

    # --- SGR (Select Graphic Rendition) ---

    def test_strips_sgr_reset(self):
        """ANSI reset code \x1b[0m is stripped."""
        assert strip_ansi("=> Checking PostgreSQL\x1b[0m") == "=> Checking PostgreSQL"

    def test_strips_sgr_color(self):
        """ANSI color codes are stripped."""
        assert strip_ansi("\x1b[32mOK\x1b[0m") == "OK"

    def test_multiple_sgr_sequences(self):
        """Multiple SGR sequences in one string are all removed."""
        assert strip_ansi("\x1b[1m\x1b[33mWarning:\x1b[0m something") == (
            "Warning: something"
        )

    def test_sgr_with_many_params(self):
        """SGR with many semicolon-separated parameters."""
        assert strip_ansi("\x1b[1;2;3;4;5;6;7;8;9mtext\x1b[0m") == "text"

    # --- CSI (Control Sequence Introducer) ---

    def test_cursor_movement(self):
        """CSI cursor movement sequences are stripped."""
        assert strip_ansi("text\x1b[2Amore") == "textmore"

    def test_dec_private_mode(self):
        """DEC private mode sequences (with ?) are stripped."""
        assert strip_ansi("\x1b[?25hvisible\x1b[?25l") == "visible"

    def test_bracketed_paste_mode(self):
        """Bracketed paste mode (DEC private 2004) is stripped."""
        assert strip_ansi("\x1b[?2004hpasted\x1b[?2004l") == "pasted"

    def test_true_color_sgr_colon_params(self):
        """True-color SGR with colon-separated params (ECMA-48) is stripped."""
        assert strip_ansi("\x1b[38:2::255:0:0mred text\x1b[0m") == "red text"

    def test_device_private_mode_greater(self):
        """CSI with > private parameter prefix is stripped."""
        assert strip_ansi("\x1b[>4;2mtext") == "text"

    # --- OSC (Operating System Command) ---

    def test_osc_terminal_title_bel(self):
        """OSC 0 (set title) terminated by BEL is stripped."""
        assert strip_ansi("\x1b]0;My Title\x07text after") == "text after"

    def test_osc_terminal_title_st(self):
        """OSC 0 (set title) terminated by ST is stripped."""
        assert strip_ansi("\x1b]0;My Title\x1b\\text after") == "text after"

    def test_osc8_hyperlink(self):
        """OSC 8 hyperlink sequences are stripped."""
        text = "\x1b]8;;https://example.com\x1b\\click here\x1b]8;;\x1b\\"
        assert strip_ansi(text) == "click here"

    # --- DCS / PM / APC (string sequences) ---

    def test_dcs_sequence(self):
        """DCS (Device Control String) sequences are stripped."""
        assert strip_ansi("\x1bPmalicious\x1b\\text") == "text"

    def test_pm_sequence(self):
        """PM (Privacy Message) sequences are stripped."""
        assert strip_ansi("\x1b^private\x1b\\text") == "text"

    def test_apc_sequence(self):
        """APC (Application Program Command) sequences are stripped."""
        assert strip_ansi("\x1b_command\x1b\\text") == "text"

    def test_unterminated_osc_preserved(self):
        """Unterminated OSC is preserved (not greedily consumed)."""
        result = strip_ansi("\x1b]0;unterminated")
        assert "unterminated" in result

    # --- Character set designation ---

    def test_charset_designation(self):
        """Character set designation sequences (e.g. \\x1b(B) are stripped."""
        assert strip_ansi("\x1b(Btext") == "text"

    # --- Two-character sequences ---

    def test_two_char_save_cursor(self):
        """Two-character escape sequences (DEC save/restore) are stripped."""
        assert strip_ansi("\x1b7saved\x1b8") == "saved"

    # --- 8-bit C1 sequences ---

    def test_c1_csi(self):
        """8-bit C1 CSI (\\x9b) sequences are stripped."""
        assert strip_ansi("\x9b31mred\x9b0m") == "red"

    def test_c1_osc(self):
        """8-bit C1 OSC (\\x9d) terminated by ST (\\x9c) is stripped."""
        assert strip_ansi("\x9d0;title\x9ctext") == "text"

    def test_c1_dcs(self):
        """8-bit C1 DCS (\\x90) terminated by ST (\\x9c) is stripped."""
        assert strip_ansi("\x90payload\x9ctext") == "text"

    def test_c1_pm(self):
        """8-bit C1 PM (\\x9e) terminated by ST (\\x9c) is stripped."""
        assert strip_ansi("\x9eprivate\x9ctext") == "text"

    def test_c1_apc(self):
        """8-bit C1 APC (\\x9f) terminated by ST (\\x9c) is stripped."""
        assert strip_ansi("\x9fcommand\x9ctext") == "text"

    # --- Edge cases ---

    def test_clean_input_unchanged(self):
        """Normal text passes through without modification."""
        assert strip_ansi("Normal message") == "Normal message"

    def test_empty_string(self):
        assert strip_ansi("") == ""

    def test_brackets_preserved(self):
        """Literal square brackets are NOT stripped (just ANSI codes)."""
        assert strip_ansi("Error [code 42]") == "Error [code 42]"

    def test_combined_ansi_and_brackets(self):
        """ANSI codes stripped but brackets preserved."""
        assert strip_ansi("\x1b[31m[ERROR]\x1b[0m fail") == "[ERROR] fail"

    def test_nested_brackets(self):
        """Nested brackets are preserved."""
        assert strip_ansi("data[[0]]") == "data[[0]]"

    def test_lone_escape_at_end(self):
        """Trailing \\x1b without following character is preserved."""
        assert strip_ansi("text\x1b") == "text\x1b"

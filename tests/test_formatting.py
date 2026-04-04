"""Tests for token formatting functions."""

from claudechic.formatting import format_tokens, parse_context_size


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

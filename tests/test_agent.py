"""Tests for Agent prompt preparation and context management."""

from __future__ import annotations

from pathlib import Path

import pytest

from claudechic.agent import Agent


def _make_agent() -> Agent:
    """Create a minimal Agent for testing (no SDK connection needed).

    Note: Agent.__init__ imports FinishState from worktree.git (a dataclass) —
    this is safe without git installed. disconnect() on an unconnected agent
    skips the client/task cleanup and only runs asyncio.sleep(0) + gc cleanup.
    """
    return Agent(name="test", cwd=Path("/tmp"))


class TestUpdateContext:
    def test_sets_tokens_and_max(self):
        agent = _make_agent()
        agent.update_context(14000, 200000)
        assert agent.tokens == 14000
        assert agent.max_tokens == 200000
        assert agent._context_initialized is True

    def test_tokens_only_preserves_max(self):
        agent = _make_agent()
        agent.max_tokens = 500000  # Set before
        agent.update_context(7000)
        assert agent.tokens == 7000
        assert agent.max_tokens == 500000  # Preserved
        assert agent._context_initialized is True

    def test_not_initialized_by_default(self):
        agent = _make_agent()
        assert agent._context_initialized is False

    @pytest.mark.asyncio
    async def test_disconnect_resets_flag(self):
        agent = _make_agent()
        agent.update_context(14000, 200000)
        assert agent._context_initialized is True
        await agent.disconnect()
        assert agent._context_initialized is False

    @pytest.mark.asyncio
    async def test_no_injection_after_disconnect(self):
        """After disconnect, _prepare_prompt should not inject (spec test #13)."""
        agent = _make_agent()
        agent.update_context(14000, 200000)
        await agent.disconnect()
        assert agent._context_initialized is False
        result = agent._prepare_prompt("hello")
        assert result == "hello"
        assert "<system-reminder>" not in result


class TestPreparePrompt:
    def test_injects_when_initialized(self):
        agent = _make_agent()
        agent.update_context(14000, 200000)
        result = agent._prepare_prompt("hello")
        assert result.startswith("<system-reminder>14000/200000 tokens</system-reminder>")
        assert result.endswith("hello")

    def test_skips_when_not_initialized(self):
        agent = _make_agent()
        result = agent._prepare_prompt("hello")
        assert result == "hello"
        assert "<system-reminder>" not in result

    def test_tokens_zero_with_initialized(self):
        agent = _make_agent()
        agent.update_context(0, 200000)
        result = agent._prepare_prompt("hello")
        assert "<system-reminder>0/200000 tokens</system-reminder>" in result

    def test_plan_mode_ordering(self):
        """Token reminder first, plan-mode second, user prompt last."""
        agent = _make_agent()
        agent.update_context(14000, 200000)
        agent.permission_mode = "plan"
        result = agent._prepare_prompt("hello")
        # Token reminder comes first
        token_pos = result.index("<system-reminder>14000/200000 tokens</system-reminder>")
        # Plan mode instructions come second
        plan_pos = result.index("PLAN MODE ACTIVE")
        # User prompt comes last
        user_pos = result.index("hello")
        assert token_pos < plan_pos < user_pos

    def test_plan_mode_without_context(self):
        """Plan mode instructions still prepend even without context init."""
        agent = _make_agent()
        agent.permission_mode = "plan"
        result = agent._prepare_prompt("hello")
        assert "PLAN MODE ACTIVE" in result
        assert "<system-reminder>0/" not in result  # No token injection

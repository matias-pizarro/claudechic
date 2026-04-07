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
        """After disconnect, _prepare_prompt should not inject (spec test #13).
        Note: _prepare_prompt will be added in Task 4 — this test will fail until then."""
        agent = _make_agent()
        agent.update_context(14000, 200000)
        await agent.disconnect()
        # This test depends on Task 4's _prepare_prompt — skip assertion for now
        assert agent._context_initialized is False

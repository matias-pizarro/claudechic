"""Tests for Agent prompt preparation and context management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
        assert result.startswith(
            "<system-reminder>14000/200000 tokens</system-reminder>"
        )
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
        token_pos = result.index(
            "<system-reminder>14000/200000 tokens</system-reminder>"
        )
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


class TestTokenReminderPattern:
    def test_matches_token_reminder_at_start(self):
        from claudechic.formatting import TOKEN_REMINDER_PATTERN

        text = "<system-reminder>14000/200000 tokens</system-reminder>\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == "hello"

    def test_preserves_plan_mode_tags(self):
        from claudechic.formatting import TOKEN_REMINDER_PATTERN

        text = "<system-reminder>\nPLAN MODE ACTIVE\n</system-reminder>\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == text  # Unchanged

    def test_preserves_mid_message_content(self):
        from claudechic.formatting import TOKEN_REMINDER_PATTERN

        text = "user said <system-reminder>42/100 tokens</system-reminder> here"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == text  # Unchanged (not at start)

    def test_strips_with_leading_whitespace(self):
        from claudechic.formatting import TOKEN_REMINDER_PATTERN

        text = "  <system-reminder>5000/200000 tokens</system-reminder>\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == "hello"

    def test_strips_trailing_newlines(self):
        from claudechic.formatting import TOKEN_REMINDER_PATTERN

        text = "<system-reminder>14000/200000 tokens</system-reminder>\n\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == "hello"


class TestSetPermissionMode:
    """Tests for Agent.set_permission_mode() SDK interaction."""

    @pytest.mark.asyncio
    async def test_planswarm_skips_sdk_call(self):
        """planSwarm is claudechic-specific; SDK call is skipped entirely."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"

        await agent.set_permission_mode("planSwarm")

        assert agent.permission_mode == "planSwarm"
        agent.client.set_permission_mode.assert_not_called()

    @pytest.mark.asyncio
    async def test_regular_mode_passes_through_to_sdk(self):
        """Non-planSwarm modes pass through to SDK via cast(PermissionMode)."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"

        await agent.set_permission_mode("plan")

        assert agent.permission_mode == "plan"
        agent.client.set_permission_mode.assert_called_once_with("plan")

    @pytest.mark.asyncio
    async def test_no_sdk_call_when_disconnected(self):
        """No SDK call if client is None or session_id is missing."""
        agent = _make_agent()
        agent.client = None
        agent.permission_mode = "default"

        await agent.set_permission_mode("acceptEdits")

        assert agent.permission_mode == "acceptEdits"

    @pytest.mark.asyncio
    async def test_plan_mode_calls_ensure_plan_path(self):
        """Entering plan mode triggers plan path fetch."""
        agent = _make_agent()
        agent.client = MagicMock()
        agent.client.set_permission_mode = AsyncMock()
        agent.session_id = "test-session"
        agent.permission_mode = "default"
        agent.ensure_plan_path = AsyncMock()

        await agent.set_permission_mode("plan")

        agent.ensure_plan_path.assert_called_once()


class TestPlanSwarmEnforcement:
    """Verify planSwarm blocks mutating tools via _handle_permission."""

    @pytest.mark.asyncio
    async def test_planswarm_blocks_mutating_tools(self):
        """planSwarm must deny Edit/Write/Bash just like plan mode."""
        from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext

        agent = _make_agent()
        agent.permission_mode = "planSwarm"

        context = ToolPermissionContext()
        result = await agent._handle_permission(
            "Bash", {"command": "rm -rf /"}, context
        )

        assert isinstance(result, PermissionResultDeny)

    @pytest.mark.asyncio
    async def test_planswarm_allows_write_to_plan_file(self):
        """planSwarm allows Write/Edit to ~/.claude/plans/ (same as plan mode)."""
        from claude_agent_sdk.types import PermissionResultAllow, ToolPermissionContext
        from pathlib import Path

        agent = _make_agent()
        agent.permission_mode = "planSwarm"

        plans_dir = str(Path.home() / ".claude" / "plans")
        plan_file = f"{plans_dir}/test-plan.md"

        context = ToolPermissionContext()
        result = await agent._handle_permission(
            "Write", {"file_path": plan_file, "content": "# Plan"}, context
        )

        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_planswarm_blocks_sibling_prefix_path(self):
        """Sibling directories sharing a prefix must NOT pass the plan-file check."""
        from claude_agent_sdk.types import PermissionResultDeny, ToolPermissionContext
        from pathlib import Path

        agent = _make_agent()
        agent.permission_mode = "planSwarm"

        # ~/.claude/plans-evil/ shares the prefix but is NOT inside ~/.claude/plans/
        evil_path = str(Path.home() / ".claude" / "plans-evil" / "payload.md")

        context = ToolPermissionContext()
        result = await agent._handle_permission(
            "Write", {"file_path": evil_path, "content": "malicious"}, context
        )

        assert isinstance(result, PermissionResultDeny)


class TestPlanModeHooks:
    """Verify the PreToolUse hook path (app.py _plan_mode_hooks) blocks correctly."""

    @pytest.mark.asyncio
    async def test_hook_blocks_bash_in_plan_mode(self):
        """PreToolUse hook blocks Bash when SDK reports permission_mode='plan'."""
        from claudechic.app import ChatApp

        app = ChatApp()
        hooks = app._plan_mode_hooks()
        hook_fn = hooks["PreToolUse"][0].hooks[0]

        result = await hook_fn(
            {"permission_mode": "plan", "tool_name": "Bash", "tool_input": {"command": "echo"}},
            None, None,
        )
        assert result.get("decision") == "block"

    @pytest.mark.asyncio
    async def test_hook_allows_write_to_plan_file(self):
        """PreToolUse hook allows Write to ~/.claude/plans/ in plan mode."""
        from claudechic.app import ChatApp
        from pathlib import Path

        app = ChatApp()
        hooks = app._plan_mode_hooks()
        hook_fn = hooks["PreToolUse"][0].hooks[0]

        plan_file = str(Path.home() / ".claude" / "plans" / "test.md")
        result = await hook_fn(
            {"permission_mode": "plan", "tool_name": "Write", "tool_input": {"file_path": plan_file}},
            None, None,
        )
        assert result == {}  # Empty dict = allow

    @pytest.mark.asyncio
    async def test_hook_blocks_sibling_prefix_path(self):
        """PreToolUse hook blocks Write to sibling directories sharing plans prefix."""
        from claudechic.app import ChatApp
        from pathlib import Path

        app = ChatApp()
        hooks = app._plan_mode_hooks()
        hook_fn = hooks["PreToolUse"][0].hooks[0]

        evil_path = str(Path.home() / ".claude" / "plans-evil" / "payload.md")
        result = await hook_fn(
            {"permission_mode": "plan", "tool_name": "Write", "tool_input": {"file_path": evil_path}},
            None, None,
        )
        assert result.get("decision") == "block"

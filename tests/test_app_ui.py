"""App-level UI tests without SDK dependency."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudechic.app import ChatApp
from claudechic.widgets import (
    ChatInput,
    ChatMessage,
    AgentSection,
    TodoPanel,
    StatusFooter,
)
from claudechic.messages import (
    ResponseComplete,
    ToolUseMessage,
    ToolResultMessage,
)
from claude_agent_sdk import ToolUseBlock, ToolResultBlock
from tests.conftest import wait_for_workers, submit_command, make_fake_pty


@pytest.mark.asyncio
async def test_app_mounts_basic_widgets(mock_sdk):
    """App mounts all expected widgets on startup."""
    app = ChatApp()
    async with app.run_test():
        # Check key widgets exist
        assert app.query_one("#input", ChatInput)
        assert app.query_one("#agent-section", AgentSection)
        assert app.query_one("#todo-panel", TodoPanel)
        assert app.query_one(StatusFooter)


@pytest.mark.asyncio
async def test_permission_mode_cycle(mock_sdk):
    """Shift+Tab cycles permission mode: default -> acceptEdits -> plan -> default."""
    app = ChatApp()
    async with app.run_test() as pilot:
        assert app._agent is not None
        assert app._agent.permission_mode == "default"

        await pilot.press("shift+tab")
        assert app._agent.permission_mode == "acceptEdits"

        await pilot.press("shift+tab")
        assert app._agent.permission_mode == "plan"

        await pilot.press("shift+tab")
        assert app._agent.permission_mode == "default"


@pytest.mark.asyncio
async def test_permission_mode_footer_updates(mock_sdk):
    """Footer reflects permission mode state."""
    app = ChatApp()
    async with app.run_test() as pilot:
        footer = app.query_one(StatusFooter)
        assert footer.permission_mode == "default"

        await pilot.press("shift+tab")
        assert footer.permission_mode == "acceptEdits"


@pytest.mark.asyncio
async def test_clear_command(mock_sdk):
    """'/clear' removes chat messages."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Add some fake messages
        msg1 = ChatMessage("Test 1")
        msg2 = ChatMessage("Test 2")
        chat_view.mount(msg1)
        chat_view.mount(msg2)
        await pilot.pause()

        assert len(chat_view.children) == 2

        # Send /clear (which clears UI and sends to SDK)
        await submit_command(app, pilot, "/clear")
        await wait_for_workers(app)
        await pilot.pause()  # Let DOM updates complete

        # Chat view should be empty
        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 0  # Our messages were cleared


@pytest.mark.asyncio
async def test_agent_list_command(mock_sdk):
    """'/agent' lists agents."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Should have one default agent
        assert len(app.agents) == 1

        await submit_command(app, pilot, "/agent")

        # The command shows notifications - just verify we have one agent
        assert len(app.agents) == 1


@pytest.mark.asyncio
async def test_agent_create_command(mock_sdk):
    """'/agent foo' creates new agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        assert len(app.agents) == 1

        await submit_command(app, pilot, "/agent test-agent")
        await wait_for_workers(app)

        assert len(app.agents) == 2
        agent_names = [a.name for a in app.agents.values()]
        assert "test-agent" in agent_names


@pytest.mark.asyncio
async def test_agent_switch_keybinding(mock_sdk):
    """Ctrl+1-9 switches agents."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Create second agent
        await submit_command(app, pilot, "/agent second")
        await wait_for_workers(app)

        assert len(app.agents) == 2
        agent_ids = list(app.agents.keys())

        # Should be on second agent now (just created)
        assert app.active_agent_id == agent_ids[1]

        # Switch to first agent with ctrl+1
        await pilot.press("ctrl+1")
        assert app.active_agent_id == agent_ids[0]

        # Switch to second agent with ctrl+2
        await pilot.press("ctrl+2")
        assert app.active_agent_id == agent_ids[1]


@pytest.mark.asyncio
async def test_agent_close_command(mock_sdk):
    """'/agent close' closes current agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Create second agent first
        await submit_command(app, pilot, "/agent to-close")
        await wait_for_workers(app)

        assert len(app.agents) == 2
        assert any(a.name == "to-close" for a in app.agents.values())

        # Close current agent
        await submit_command(app, pilot, "/agent close")
        await wait_for_workers(app)
        await pilot.pause()  # Let DOM updates complete

        # Should be back to one agent
        assert len(app.agents) == 1


@pytest.mark.asyncio
async def test_cannot_close_last_agent(mock_sdk):
    """Cannot close the last remaining agent."""
    app = ChatApp()
    async with app.run_test() as pilot:
        assert len(app.agents) == 1

        await submit_command(app, pilot, "/agent close")
        await wait_for_workers(app)

        # Still have one agent
        assert len(app.agents) == 1


@pytest.mark.asyncio
async def test_sidebar_agent_selection(mock_sdk):
    """Clicking agent in sidebar switches to it."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # Create second agent
        await submit_command(app, pilot, "/agent sidebar-test")
        await wait_for_workers(app)

        sidebar = app.query_one("#agent-section", AgentSection)
        agent_ids = list(app.agents.keys())

        # Second agent should be active (just created)
        assert app.active_agent_id == agent_ids[1]

        # Simulate clicking first agent
        first_agent_widget = sidebar._agents[agent_ids[0]]
        first_agent_widget.post_message(first_agent_widget.Selected(agent_ids[0]))
        await pilot.pause()

        # First agent should now be active
        assert app.active_agent_id == agent_ids[0]


@pytest.mark.asyncio
async def test_resume_shows_session_screen(mock_sdk):
    """'/resume' shows session screen."""
    from claudechic.screens import SessionScreen

    app = ChatApp()
    async with app.run_test() as pilot:
        await submit_command(app, pilot, "/resume")

        # Session screen should be on screen stack
        assert isinstance(app.screen, SessionScreen)


@pytest.mark.asyncio
async def test_escape_hides_session_screen(mock_sdk):
    """Escape hides session screen."""
    from claudechic.screens import SessionScreen

    app = ChatApp()
    async with app.run_test() as pilot:
        await submit_command(app, pilot, "/resume")

        assert isinstance(app.screen, SessionScreen)

        # Press escape to dismiss screen
        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, SessionScreen)


@pytest.mark.asyncio
async def test_double_ctrl_c_quits(mock_sdk):
    """Double Ctrl+C quits app."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # First Ctrl+C shows warning
        await pilot.press("ctrl+c")
        assert hasattr(app, "_last_quit_time")

        # Second quick Ctrl+C would exit (but we can't test actual exit easily)
        # Just verify the mechanism exists
        import time

        assert time.time() - app._last_quit_time < 2.0


@pytest.mark.asyncio
async def test_stream_chunk_creates_message(mock_sdk):
    """Text streaming creates ChatMessage widget."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Simulate text chunk (now direct call, not message)
        chat_view.append_text("Hello ", new_message=True, parent_tool_id=None)
        await pilot.pause()

        # Should have created a ChatMessage
        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 1
        assert messages[0].get_raw_content() == "Hello "


@pytest.mark.asyncio
async def test_stream_chunk_appends_to_message(mock_sdk):
    """Sequential text chunks append to same message."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        chat_view.append_text("Hello ", new_message=True, parent_tool_id=None)
        await pilot.pause()
        chat_view.append_text("world!", new_message=False, parent_tool_id=None)
        await pilot.pause()

        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 1
        assert messages[0].get_raw_content() == "Hello world!"


@pytest.mark.asyncio
async def test_stream_chunks_interleaved_with_tools(mock_sdk):
    """Text after tool use creates a new ChatMessage (not appended to first)."""
    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None
        agent_id = app.active_agent_id

        # First text chunk
        chat_view.append_text("Planning...", new_message=True, parent_tool_id=None)
        await pilot.pause()

        # Tool use
        tool_block = ToolUseBlock(
            id="tool-1", name="Read", input={"file_path": "/test.py"}
        )
        app.post_message(ToolUseMessage(tool_block, agent_id=agent_id))
        await pilot.pause()

        # Tool result
        result_block = ToolResultBlock(
            tool_use_id="tool-1", content="file contents", is_error=False
        )
        app.post_message(ToolResultMessage(result_block, agent_id=agent_id))
        await pilot.pause()

        # Second text chunk (should be new_message=True after tool)
        chat_view.append_text("Done!", new_message=True, parent_tool_id=None)
        await pilot.pause()

        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
        assert messages[0].get_raw_content() == "Planning..."
        assert messages[1].get_raw_content() == "Done!"


@pytest.mark.asyncio
async def test_response_complete_enables_input(mock_sdk):
    """ResponseComplete focuses input."""
    app = ChatApp()
    async with app.run_test() as pilot:
        agent_id = app.active_agent_id
        app.post_message(ResponseComplete(None, agent_id=agent_id))
        await pilot.pause()

        input_widget = app.query_one("#input", ChatInput)
        assert app.focused == input_widget


@pytest.mark.asyncio
async def test_sidebar_hidden_when_single_agent(mock_sdk):
    """Right sidebar hidden with single agent and no todos."""
    app = ChatApp()
    async with app.run_test(size=(100, 40)):
        sidebar = app.query_one("#right-sidebar")
        # With single agent and no todos, sidebar should be hidden
        assert sidebar.has_class("hidden")


@pytest.mark.asyncio
async def test_sidebar_shows_with_multiple_agents(mock_sdk):
    """Right sidebar shows with multiple agents when wide enough."""
    app = ChatApp()
    async with app.run_test(size=(160, 40)) as pilot:
        # Create second agent
        await submit_command(app, pilot, "/agent second")
        await wait_for_workers(app)

        # Trigger resize handling
        app._position_right_sidebar()

        sidebar = app.query_one("#right-sidebar")
        # With multiple agents and wide enough, sidebar should show
        assert sidebar.display is True


@pytest.mark.asyncio
async def test_command_output_displays(mock_sdk):
    """CommandOutputMessage displays content in chat."""
    from claudechic.messages import CommandOutputMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Post a command output message
        agent_id = app.active_agent_id
        app.post_message(
            CommandOutputMessage("## Test Output\n\nSome content", agent_id=agent_id)
        )
        await pilot.pause()

        # Should have created a ChatMessage with system-message class
        messages = list(chat_view.query(ChatMessage))
        assert len(messages) == 1
        assert "## Test Output" in messages[0].get_raw_content()
        assert messages[0].has_class("system-message")


@pytest.mark.asyncio
async def test_context_report_displays(mock_sdk):
    """Context command output displays as ContextReport widget."""
    from claudechic.messages import CommandOutputMessage
    from claudechic.widgets.reports.context import ContextReport

    CONTEXT_OUTPUT = """## Context Usage

**Model:** claude-opus-4-5-20251101
**Tokens:** 81.0k / 200.0k (41%)

### Categories

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 3.0k | 1.5% |
| Messages | 58.8k | 29.4% |
| Free space | 74.0k | 36.9% |
"""

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        agent_id = app.active_agent_id
        app.post_message(CommandOutputMessage(CONTEXT_OUTPUT, agent_id=agent_id))
        await pilot.pause()

        # Should have created a ContextReport, not ChatMessage
        reports = list(chat_view.query(ContextReport))
        assert len(reports) == 1

        # Verify data was parsed
        assert reports[0].data["model"] == "claude-opus-4-5-20251101"
        assert reports[0].data["tokens_used"] == 81000


@pytest.mark.asyncio
async def test_refresh_context_reads_sdk_usage(mock_sdk):
    """refresh_context() pulls rawMaxTokens and totalTokens from the SDK.

    Regression guard: if the SDK renames either field, the bar silently
    stays stuck at its previous value — this test locks the contract.
    """
    mock_sdk.get_context_usage = AsyncMock(
        return_value={
            "rawMaxTokens": 1_000_000,
            "totalTokens": 42_000,
        }
    )

    app = ChatApp()
    async with app.run_test():
        app.refresh_context()
        await wait_for_workers(app)

        assert app.context_bar.max_tokens == 1_000_000
        assert app.context_bar.tokens == 42_000


@pytest.mark.asyncio
async def test_refresh_context_survives_sdk_error(mock_sdk):
    """SDK failure is non-fatal — bar keeps its prior value, app doesn't crash."""
    mock_sdk.get_context_usage = AsyncMock(side_effect=RuntimeError("boom"))

    app = ChatApp()
    async with app.run_test():
        app.context_bar.max_tokens = 500_000
        app.context_bar.tokens = 123
        app.refresh_context()
        await wait_for_workers(app)

        # Bar unchanged; no exception propagated to the app.
        assert app.context_bar.max_tokens == 500_000
        assert app.context_bar.tokens == 123


@pytest.mark.asyncio
async def test_system_notification_shows_in_chat(mock_sdk):
    """SystemNotification creates SystemInfo widget in chat."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Create a system message (simulating SDK)
        sdk_msg = SystemMessage(
            subtype="test_notification",
            data={"content": "Test system message", "level": "info"},
        )

        # Post the notification
        app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))
        await pilot.pause()

        # Should have a SystemInfo widget in chat
        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert info_widgets[0]._message == "Test system message"


@pytest.mark.asyncio
async def test_system_notification_api_error(mock_sdk):
    """API error notification displays correctly."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Create an api_error system message
        sdk_msg = SystemMessage(
            subtype="api_error",
            data={
                "level": "error",
                "error": {"error": {"message": "Rate limited"}},
                "retryAttempt": 2,
                "maxRetries": 10,
            },
        )

        app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))
        await pilot.pause()

        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert "retry 2/10" in info_widgets[0]._message
        assert "Rate limited" in info_widgets[0]._message


@pytest.mark.asyncio
async def test_system_notification_compact_boundary(mock_sdk):
    """Compact boundary notification displays."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        sdk_msg = SystemMessage(
            subtype="compact_boundary",
            data={"content": "Conversation compacted", "level": "info"},
        )

        app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))
        await pilot.pause()

        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert "compacted" in info_widgets[0]._message.lower()


@pytest.mark.asyncio
async def test_system_notification_ignored_subtypes(mock_sdk):
    """Certain subtypes are silently ignored."""
    from claudechic.messages import SystemNotification
    from claudechic.widgets import SystemInfo
    from claude_agent_sdk import SystemMessage

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # These subtypes should not create widgets
        for subtype in ["stop_hook_summary", "turn_duration", "local_command"]:
            sdk_msg = SystemMessage(subtype=subtype, data={"level": "info"})
            app.post_message(SystemNotification(sdk_msg, agent_id=app.active_agent_id))

        await pilot.pause()

        # No SystemInfo widgets should be created
        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 0


@pytest.mark.asyncio
async def test_sdk_stderr_shows_in_chat(mock_sdk):
    """SDK stderr callback routes messages to chat view."""
    from claudechic.widgets import SystemInfo

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Simulate SDK stderr output
        app._handle_sdk_stderr("An update to our Terms of Service")
        await pilot.pause()

        # Should create a SystemInfo widget
        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        assert "Terms of Service" in info_widgets[0]._message


@pytest.mark.asyncio
async def test_sdk_stderr_ignores_empty(mock_sdk):
    """SDK stderr callback ignores empty/whitespace messages."""
    from claudechic.widgets import SystemInfo

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Simulate empty stderr output
        app._handle_sdk_stderr("")
        app._handle_sdk_stderr("   ")
        app._handle_sdk_stderr("\n")
        await pilot.pause()

        # No widgets should be created
        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 0


@pytest.mark.asyncio
async def test_sdk_stderr_strips_ansi(mock_sdk):
    """SDK stderr with ANSI escape codes is cleaned before display."""
    from claudechic.widgets import SystemInfo

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        # Simulate ANSI-laden SDK stderr (the original crash trigger)
        app._handle_sdk_stderr("=> Checking PostgreSQL\x1b[0m\n")
        await pilot.pause()

        info_widgets = list(chat_view.query(SystemInfo))
        assert len(info_widgets) == 1
        # ANSI codes should be stripped, visible text preserved
        assert "Checking PostgreSQL" in info_widgets[0]._message
        assert "\x1b" not in info_widgets[0]._message


@pytest.mark.asyncio
async def test_bang_command_inline_shell(mock_sdk):
    """'!cmd' runs shell command and displays output inline."""
    from claudechic.widgets import ShellOutputWidget

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        with patch(
            "claudechic.shell_runner.run_in_pty_cancellable",
            new=make_fake_pty(output="hello\r\n"),
        ):
            input_widget = app.query_one("#input", ChatInput)
            input_widget.text = "!echo hello"
            await pilot.press("enter")
            await pilot.pause()
            await wait_for_workers(app)
            await pilot.pause()

        # Should create a ShellOutputWidget
        widgets = list(chat_view.query(ShellOutputWidget))
        assert len(widgets) == 1
        assert widgets[0].command == "echo hello"
        assert "hello" in widgets[0].stdout


@pytest.mark.asyncio
async def test_bang_command_captures_stderr(mock_sdk):
    """'!cmd' captures stderr output (merged with stdout via PTY)."""
    from claudechic.widgets import ShellOutputWidget

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        with patch(
            "claudechic.shell_runner.run_in_pty_cancellable",
            new=make_fake_pty(output="error\r\n"),
        ):
            input_widget = app.query_one("#input", ChatInput)
            input_widget.text = "!echo error >&2"
            await pilot.press("enter")
            await pilot.pause()
            await wait_for_workers(app)
            await pilot.pause()

        widgets = list(chat_view.query(ShellOutputWidget))
        assert len(widgets) == 1
        # PTY merges stdout/stderr, so check stdout (which contains both)
        assert "error" in widgets[0].stdout


@pytest.mark.asyncio
async def test_bang_command_shows_exit_code(mock_sdk):
    """'!cmd' shows non-zero exit code in title."""
    from claudechic.widgets import ShellOutputWidget

    app = ChatApp()
    async with app.run_test() as pilot:
        chat_view = app._chat_view
        assert chat_view is not None

        with patch(
            "claudechic.shell_runner.run_in_pty_cancellable",
            new=make_fake_pty(returncode=42),
        ):
            input_widget = app.query_one("#input", ChatInput)
            input_widget.text = "!exit 42"
            await pilot.press("enter")
            await pilot.pause()
            await wait_for_workers(app)
            await pilot.pause()

        widgets = list(chat_view.query(ShellOutputWidget))
        assert len(widgets) == 1
        assert widgets[0].returncode == 42


@pytest.mark.asyncio
async def test_hamburger_button_narrow_screen(mock_sdk):
    """Hamburger button appears on narrow screens with multiple agents."""
    from claudechic.widgets import HamburgerButton

    app = ChatApp()
    # Start narrow (below SIDEBAR_MIN_WIDTH=110)
    async with app.run_test(size=(80, 40)) as pilot:
        # Create second agent so sidebar has content
        await submit_command(app, pilot, "/agent second")
        await wait_for_workers(app)

        hamburger = app.query_one("#hamburger-btn", HamburgerButton)

        # Trigger layout update
        app._position_right_sidebar()
        await pilot.pause()

        # Hamburger should be visible on narrow screen with multiple agents
        assert hamburger.display is True

        # Sidebar should be hidden (not overlay yet)
        sidebar = app.query_one("#right-sidebar")
        assert sidebar.display is False


@pytest.mark.asyncio
async def test_hamburger_opens_sidebar_overlay(mock_sdk):
    """Clicking hamburger opens sidebar as overlay."""

    app = ChatApp()
    async with app.run_test(size=(80, 40)) as pilot:
        # Create second agent
        await submit_command(app, pilot, "/agent second")
        await wait_for_workers(app)

        app._position_right_sidebar()
        await pilot.pause()

        sidebar = app.query_one("#right-sidebar")

        # Click hamburger
        await pilot.click("#hamburger-btn")
        await pilot.pause()

        # Sidebar should now be visible as overlay
        assert sidebar.display is True
        assert sidebar.has_class("overlay")


@pytest.mark.asyncio
async def test_escape_closes_sidebar_overlay(mock_sdk):
    """Escape key closes sidebar overlay."""

    app = ChatApp()
    async with app.run_test(size=(80, 40)) as pilot:
        # Create second agent
        await submit_command(app, pilot, "/agent second")
        await wait_for_workers(app)

        app._position_right_sidebar()
        await pilot.pause()

        # Open overlay via state directly (more reliable than click in test)
        app._sidebar_overlay_open = True
        app._position_right_sidebar()
        await pilot.pause()

        sidebar = app.query_one("#right-sidebar")
        assert sidebar.display is True, (
            "Sidebar should be visible after opening overlay"
        )
        assert app._sidebar_overlay_open, "Overlay state should be True"

        # Call action_escape directly (escape key may be consumed by input widget)
        app.action_escape()
        await pilot.pause()

        # Sidebar should be hidden again
        assert not app._sidebar_overlay_open, (
            "Overlay state should be False after escape"
        )
        assert sidebar.display is False, "Sidebar should be hidden after escape"


# =============================================================================
# Agent-scoped review polling (_stop_review_polling)
# =============================================================================


@pytest.mark.asyncio
async def test_stop_review_polling_ignores_other_agent(mock_sdk):
    """Stopping polling for agent B does not cancel agent A's timer."""
    app = ChatApp()
    async with app.run_test():
        # Simulate agent A owning the poll timer
        fake_timer = MagicMock()
        app._review_poll_timer = fake_timer
        app._review_poll_agent_id = "agent-a"

        # Stopping for a different agent should be a no-op
        app._stop_review_polling("agent-b")

        fake_timer.stop.assert_not_called()
        assert app._review_poll_timer is fake_timer
        assert app._review_poll_agent_id == "agent-a"


@pytest.mark.asyncio
async def test_stop_review_polling_stops_own_agent(mock_sdk):
    """Stopping polling for the owning agent cancels the timer."""
    app = ChatApp()
    async with app.run_test():
        fake_timer = MagicMock()
        app._review_poll_timer = fake_timer
        app._review_poll_agent_id = "agent-a"

        app._stop_review_polling("agent-a")

        fake_timer.stop.assert_called_once()
        assert app._review_poll_timer is None
        assert app._review_poll_agent_id is None


@pytest.mark.asyncio
async def test_stop_review_polling_unconditional(mock_sdk):
    """Stopping polling with no agent_id cancels unconditionally."""
    app = ChatApp()
    async with app.run_test():
        fake_timer = MagicMock()
        app._review_poll_timer = fake_timer
        app._review_poll_agent_id = "agent-a"

        app._stop_review_polling()  # No agent_id

        fake_timer.stop.assert_called_once()
        assert app._review_poll_timer is None
        assert app._review_poll_agent_id is None


# =============================================================================
# CLI --width option tests
# =============================================================================


def test_cli_width_parsing():
    """Test that positive_int validator works correctly."""
    import argparse

    from claudechic.__main__ import positive_int

    # Test valid positive integers
    assert positive_int("150") == 150
    assert positive_int("1") == 1
    assert positive_int("999") == 999

    # Test invalid values
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("-10")
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("abc")
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int("12.5")


@pytest.mark.asyncio
async def test_app_width_stored(mock_sdk):
    """Test that ChatApp stores width parameter."""
    app = ChatApp(width=150)
    assert app._width == 150

    app_default = ChatApp()
    assert app_default._width is None


def test_main_calls_run_with_size_when_width_provided():
    """Test that main() passes width to ChatApp and calls app.run() without size.

    Width is applied via CSS max-width in on_chat_screen_ready, not via
    Textual's app.run(size=) which only works in headless/testing mode.
    See commit 51f132b.
    """
    import sys
    from unittest.mock import patch, MagicMock

    # Mock sys.argv with --width option
    test_argv = ["claudechic", "--width", "200"]

    with (
        patch.object(sys, "argv", test_argv),
        patch("claudechic.__main__.ChatApp") as mock_app_class,
    ):
        mock_app = MagicMock()
        mock_app_class.return_value = mock_app

        from claudechic.__main__ import main

        main()

        # Verify ChatApp was created with width=200
        mock_app_class.assert_called_once()
        _, kwargs = mock_app_class.call_args
        assert kwargs.get("width") == 200

        # Verify app.run was called without size= (width applied via CSS, not size param)
        mock_app.run.assert_called_once_with()


def test_main_calls_run_without_size_when_no_width():
    """Test that main() calls app.run() without size when --width is not provided."""
    import sys
    from unittest.mock import patch, MagicMock

    # Mock sys.argv without --width option
    test_argv = ["claudechic"]

    with (
        patch.object(sys, "argv", test_argv),
        patch("claudechic.__main__.ChatApp") as mock_app_class,
    ):
        mock_app = MagicMock()
        mock_app_class.return_value = mock_app

        from claudechic.__main__ import main

        main()

        # Verify ChatApp was created with width=None
        mock_app_class.assert_called_once()
        _, kwargs = mock_app_class.call_args
        assert kwargs.get("width") is None

        # Verify app.run was called without size parameter
        mock_app.run.assert_called_once_with()


# --- Selection copy safety tests ---


@pytest.mark.asyncio
async def test_check_and_copy_selection_handles_index_error(mock_sdk):
    """Auto-copy does not crash when get_selected_text raises IndexError."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with patch.object(
            type(app.screen), "get_selected_text", side_effect=IndexError
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        # No crash, no "Copied" notification
        assert not any(n.message == "Copied" for n in app._notifications)


@pytest.mark.asyncio
async def test_action_copy_selection_handles_index_error(mock_sdk):
    """Manual copy does not crash when get_selected_text raises IndexError."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with patch.object(
            type(app.screen), "get_selected_text", side_effect=IndexError
        ):
            app.action_copy_selection()
            await pilot.pause()
        # No crash, no "Copied to clipboard" notification
        assert not any(n.message == "Copied to clipboard" for n in app._notifications)


@pytest.mark.asyncio
async def test_safe_get_selected_text_logs_on_stale_selection(mock_sdk):
    """Stale selection emits a debug log entry."""
    app = ChatApp()
    async with app.run_test():
        with (
            patch.object(type(app.screen), "get_selected_text", side_effect=IndexError),
            patch("claudechic.app.log") as mock_log,
        ):
            result = app._safe_get_selected_text()
        assert result is None
        mock_log.debug.assert_called_once_with(
            "Stale selection coordinates, skipping copy"
        )


@pytest.mark.asyncio
async def test_mouse_up_debounce_cancels_previous_timer(mock_sdk):
    """Second mouse-up cancels the first timer before scheduling a new one."""
    app = ChatApp()
    async with app.run_test():
        mock_timer_1 = MagicMock()
        mock_timer_2 = MagicMock()
        timers = iter([mock_timer_1, mock_timer_2])

        with patch.object(app, "set_timer", side_effect=lambda *a, **k: next(timers)):
            # Simulate two mouse-up events
            # MouseUp(widget, x, y, delta_x, delta_y, button, shift, meta, ctrl, ...)
            from textual.events import MouseUp

            event = MouseUp(
                None, 0, 0, 0, 0, 0, False, False, False, screen_x=0, screen_y=0
            )
            app.on_mouse_up(event)
            assert app._copy_timer is mock_timer_1

            app.on_mouse_up(event)
            mock_timer_1.stop.assert_called_once()
            assert app._copy_timer is mock_timer_2


@pytest.mark.asyncio
async def test_check_and_copy_selection_does_not_clear_copy_timer(mock_sdk):
    """Regression guard: _check_and_copy_selection must never clear _copy_timer.

    If someone adds self._copy_timer = None to the callback, it would
    introduce a handle-clobbering race where an old callback firing after
    a newer timer is stored would lose the new timer reference.
    This test passes trivially today (the callback doesn't touch _copy_timer),
    but guards against that regression.
    """
    app = ChatApp()
    async with app.run_test():
        mock_new_timer = MagicMock()
        app._copy_timer = mock_new_timer

        # Simulate the old callback firing
        with patch.object(type(app.screen), "get_selected_text", return_value=None):
            app._check_and_copy_selection()

        # _copy_timer must still reference the new timer, not be cleared
        assert app._copy_timer is mock_new_timer


@pytest.mark.asyncio
async def test_check_and_copy_selection_copies_on_success(mock_sdk):
    """Auto-copy calls copy_to_clipboard and shows 'Copied' on success.

    Note: The "Copied" notification has timeout=1. Assertions run immediately
    after pilot.pause(), well within the 1s window. If this test becomes flaky
    under heavy CI load, the notification may be reaped before the assertion.
    """
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="hello world",
            ),
            patch.object(app, "copy_to_clipboard", return_value=True) as mock_copy,
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        mock_copy.assert_called_once_with("hello world")
        assert any(n.message == "Copied" for n in app._notifications)


@pytest.mark.asyncio
async def test_check_and_copy_selection_ignores_whitespace(mock_sdk):
    """Auto-copy skips whitespace-only selections."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="\n  \n",
            ),
            patch.object(app, "copy_to_clipboard") as mock_copy,
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        mock_copy.assert_not_called()


@pytest.mark.asyncio
async def test_action_copy_selection_copies_whitespace_only(mock_sdk):
    """Manual copy preserves whitespace-only selections (backwards compat)."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="\n  \n",
            ),
            patch.object(app, "copy_to_clipboard", return_value=True) as mock_copy,
        ):
            app.action_copy_selection()
            await pilot.pause()
        mock_copy.assert_called_once_with("\n  \n")


@pytest.mark.asyncio
async def test_check_and_copy_selection_clipboard_failure_shows_warning(mock_sdk):
    """Clipboard failure shows 'Copy failed' warning (distinguishes from stale selection)."""
    app = ChatApp()
    async with app.run_test() as pilot:
        with (
            patch.object(
                type(app.screen),
                "get_selected_text",
                return_value="hello world",
            ),
            patch.object(app, "copy_to_clipboard", return_value=False),
        ):
            app._check_and_copy_selection()
            await pilot.pause()
        assert any(
            n.message == "Copy failed" and n.severity == "warning"
            for n in app._notifications
        )


@pytest.mark.asyncio
async def test_notify_defaults_markup_false(mock_sdk):
    """ChatApp.notify() defaults to markup=False, preventing MarkupError on
    messages containing ANSI codes or literal brackets."""
    app = ChatApp()
    async with app.run_test() as pilot:
        # These would crash with markup=True because [0m and [code 42]
        # are parsed as Rich markup tags
        app.notify("=> Checking PostgreSQL\x1b[0m")
        app.notify("Error [code 42]")
        app.notify("\x1b[31m[ERROR]\x1b[0m fail")
        await pilot.pause()
        # Success criteria: all three render without MarkupError,
        # visible text preserved, no crash from brackets or ANSI codes
        assert len(app._notifications) == 3


def test_widget_notify_calls_use_markup_false():
    """Enforce that all widget/screen notify() calls pass markup=False.

    Textual's Widget.notify() defaults markup=True and passes it explicitly
    to self.app.notify(), bypassing ChatApp's markup=False override.

    This test catches TWO patterns in widget/screen files:
    1. self.notify(...) — Widget-originated, bypasses ChatApp default
    2. self.app.notify(...) — Direct app call; ChatApp default protects
       these, but we enforce markup=False explicitly for defense-in-depth

    Covered patterns: self.notify(), self.app.notify()
    Not covered (none exist): super().notify(), aliased notify
    """
    import ast
    from pathlib import Path

    def _is_notify_call(func: ast.expr) -> bool:
        """Match self.notify(...) and self.app.notify(...) patterns."""
        if not isinstance(func, ast.Attribute) or func.attr != "notify":
            return False
        val = func.value
        # self.notify(...)
        if isinstance(val, ast.Name) and val.id == "self":
            return True
        # self.app.notify(...)
        if (
            isinstance(val, ast.Attribute)
            and val.attr == "app"
            and isinstance(val.value, ast.Name)
            and val.value.id == "self"
        ):
            return True
        return False

    root = Path(__file__).parent.parent / "claudechic"
    violations: list[str] = []

    for py_file in sorted(root.rglob("*.py")):
        # Only check widget and screen files (not app.py which has the override)
        rel = py_file.relative_to(root)
        parts = rel.parts
        if not any(p in ("widgets", "screens") for p in parts):
            continue

        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_notify_call(node.func):
                continue
            # Check that markup=False is passed as a keyword
            has_markup_false = any(
                kw.arg == "markup"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            )
            if not has_markup_false:
                violations.append(f"{rel}:{node.lineno}")

    assert not violations, (
        "Widget/screen notify() calls missing markup=False "
        f"(bypasses ChatApp override): {violations}"
    )


def test_no_markup_true_with_dynamic_content():
    """Enforce that no notify(markup=True) call uses dynamic (interpolated) content.

    markup=True is only safe with static, application-authored strings.
    f-strings, format(), or variable references with markup=True would
    re-introduce the MarkupError risk this fix addresses.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).parent.parent / "claudechic"
    violations: list[str] = []

    for py_file in sorted(root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        rel = py_file.relative_to(root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "notify"):
                continue
            # Check if markup=True is explicitly passed
            has_markup_true = any(
                kw.arg == "markup"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if not has_markup_true:
                continue
            # markup=True found — check if message arg is dynamic.
            # Resolve from positional args[0] or keyword message=.
            msg_arg = node.args[0] if node.args else None
            if msg_arg is None:
                msg_kw = [kw for kw in node.keywords if kw.arg == "message"]
                if msg_kw:
                    msg_arg = msg_kw[0].value
            if msg_arg and not isinstance(msg_arg, ast.Constant):
                violations.append(f"{rel}:{node.lineno}")

    assert not violations, (
        "notify(markup=True) with dynamic content is unsafe "
        f"(can cause MarkupError): {violations}"
    )


@pytest.mark.asyncio
async def test_plan_swarm_permission_mode_footer(mock_sdk):
    """Setting planSwarm mode updates footer text and applies plan-swarm-mode CSS class."""
    app = ChatApp()
    async with app.run_test() as pilot:
        footer = app.query_one(StatusFooter)
        agent = app._agent
        assert agent is not None

        # Enter planSwarm mode via the agent
        await agent.set_permission_mode("planSwarm")
        await pilot.pause()

        # Footer should reflect planSwarm mode
        assert footer.permission_mode == "planSwarm"

        # Verify the label text
        label = footer.query_one("#permission-mode-label")
        rendered = label.render()
        assert "plan swarm" in rendered.plain.lower()

        # Verify plan-swarm-mode CSS class is applied
        assert label.has_class("plan-swarm-mode")
        # Other mode classes should not be present
        assert not label.has_class("active")
        assert not label.has_class("plan-mode")

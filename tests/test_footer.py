"""Tests for StatusFooter permission mode display.

These are characterization tests for the table-driven permission mode refactoring,
exercising mode-specific behavior in isolation. For broader widget composition and
rendering tests (including StatusFooter within larger widget hierarchies), see
tests/test_widgets.py.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from claudechic.widgets.layout.footer import StatusFooter


class FooterTestApp(App):
    """Minimal app to mount StatusFooter for testing."""

    def compose(self) -> ComposeResult:
        yield StatusFooter()


class TestWatchPermissionMode:
    """Verify table-driven permission mode display."""

    @pytest.mark.asyncio
    async def test_default_mode_shows_auto_edit_off(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "default"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "auto-edit: off" in rendered.plain.lower()  # type: ignore[union-attr]
            assert not label.has_class("active")
            assert not label.has_class("plan-mode")
            assert not label.has_class("plan-swarm-mode")

    @pytest.mark.asyncio
    async def test_accept_edits_mode_shows_auto_edit_on(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "acceptEdits"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "auto-edit: on" in rendered.plain.lower()  # type: ignore[union-attr]
            assert label.has_class("active")
            assert not label.has_class("plan-mode")

    @pytest.mark.asyncio
    async def test_plan_mode_shows_plan_mode(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "plan"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "plan mode" in rendered.plain.lower()  # type: ignore[union-attr]
            assert label.has_class("plan-mode")
            assert not label.has_class("active")

    @pytest.mark.asyncio
    async def test_plan_swarm_mode_shows_plan_swarm(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "planSwarm"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "plan swarm" in rendered.plain.lower()  # type: ignore[union-attr]
            assert label.has_class("plan-swarm-mode")
            assert not label.has_class("active")
            assert not label.has_class("plan-mode")

    @pytest.mark.asyncio
    async def test_unknown_mode_falls_back_to_default(self):
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            footer.permission_mode = "unknown_future_mode"
            await pilot.pause()
            label = footer.query_one("#permission-mode-label")
            rendered = label.render()
            assert "auto-edit: off" in rendered.plain.lower()  # type: ignore[union-attr]
            assert not label.has_class("active")

    @pytest.mark.asyncio
    async def test_transition_clears_previous_mode_class(self):
        """Switching modes must remove the previous mode's CSS class."""
        async with FooterTestApp().run_test() as pilot:
            footer = pilot.app.query_one(StatusFooter)
            label = footer.query_one("#permission-mode-label")

            footer.permission_mode = "planSwarm"
            await pilot.pause()
            assert label.has_class("plan-swarm-mode")

            footer.permission_mode = "acceptEdits"
            await pilot.pause()
            assert label.has_class("active")
            assert not label.has_class("plan-swarm-mode")
            assert not label.has_class("plan-mode")

            footer.permission_mode = "default"
            await pilot.pause()
            assert not label.has_class("active")
            assert not label.has_class("plan-swarm-mode")
            assert not label.has_class("plan-mode")

"""Custom footer widget."""

import asyncio
import logging

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.containers import Horizontal
from textual.widgets import Static

from claudechic.formatting import format_cwd, format_session_id, MIN_CWD_LENGTH, MAX_CWD_LENGTH, MIN_SESSION_LENGTH
from claudechic.widgets.base.clickable import ClickableLabel
from claudechic.widgets.layout.indicators import CPUBar, ContextBar, ProcessIndicator
from claudechic.processes import BackgroundProcess
from claudechic.widgets.input.vi_mode import ViMode

log = logging.getLogger(__name__)

# CSS "padding: 0 1" on #cwd-label = 1 left + 1 right = 2 horizontal cells
CWD_PADDING = 2
SESSION_PADDING = 2  # CSS "padding: 0 1" on .footer-label = 1 left + 1 right


class PermissionModeLabel(ClickableLabel):
    """Clickable permission mode status label."""

    class Toggled(Message):
        """Emitted when permission mode is toggled."""

    def on_click(self, event) -> None:
        self.post_message(self.Toggled())


class ModelLabel(ClickableLabel):
    """Clickable model label."""

    class ModelChangeRequested(Message):
        """Emitted when user wants to change the model."""

    def on_click(self, event) -> None:
        self.post_message(self.ModelChangeRequested())


class ViModeLabel(Static):
    """Shows current vim mode: INSERT, NORMAL, VISUAL."""

    DEFAULT_CSS = """
    ViModeLabel {
        width: auto;
        padding: 0 1;
        text-style: bold;
        &.vi-insert { color: $success; }
        &.vi-normal { color: $primary; }
        &.vi-visual { color: $warning; }
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._mode: ViMode | None = None
        self._enabled: bool = False

    def set_mode(self, mode: ViMode | None, enabled: bool = True) -> None:
        """Update the displayed mode."""
        self._mode = mode
        self._enabled = enabled

        self.remove_class("vi-insert", "vi-normal", "vi-visual", "hidden")

        if not enabled:
            self.add_class("hidden")
            return

        if mode == ViMode.INSERT:
            self.update("INSERT")
            self.add_class("vi-insert")
        elif mode == ViMode.NORMAL:
            self.update("NORMAL")
            self.add_class("vi-normal")
        elif mode == ViMode.VISUAL:
            self.update("VISUAL")
            self.add_class("vi-visual")


class SessionIndicator(Static):
    """Passive session ID label in the footer. Content set by StatusFooter._render_cwd_label."""

    pass


async def get_git_branch(cwd: str | None = None) -> str:
    """Get current git branch name (async)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "branch",
            "--show-current",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1)
        return stdout.decode().strip() or "detached"
    except Exception:
        return ""


class StatusFooter(Static):
    """Footer showing git branch, model, auto-edit status, and resource indicators."""

    can_focus = False
    permission_mode = reactive("default")  # default, acceptEdits, plan
    model = reactive("")
    branch = reactive("")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cwd: str = ""
        self._session_id: str | None = None

    async def on_mount(self) -> None:
        self.branch = await get_git_branch()

    async def refresh_branch(self, cwd: str | None = None) -> None:
        """Update branch from given directory (async)."""
        self.branch = await get_git_branch(cwd)

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-content"):
            yield ViModeLabel("", id="vi-mode-label", classes="hidden")
            yield ModelLabel("", id="model-label", classes="footer-label")
            yield Static("·", classes="footer-sep")
            yield PermissionModeLabel(
                "Auto-edit: off", id="permission-mode-label", classes="footer-label"
            )
            yield Static("", id="footer-spacer")
            yield ProcessIndicator(id="process-indicator", classes="hidden")
            yield ContextBar(id="context-bar")
            yield CPUBar(id="cpu-bar")
            yield Static("", id="cwd-label", classes="footer-label hidden")
            yield SessionIndicator("", id="session-label", classes="footer-label hidden")
            yield Static("", id="branch-label", classes="footer-label")

    def set_cwd(self, cwd: str) -> None:
        """Update cwd value and schedule re-render."""
        self._cwd = cwd
        self.call_after_refresh(self._render_cwd_label)

    def set_session_id(self, session_id: str | None) -> None:
        """Update session_id value and schedule re-render."""
        self._session_id = session_id
        self.call_after_refresh(self._render_cwd_label)

    def refresh_cwd_label(self) -> None:
        """Public API for app to trigger cwd budget recomputation."""
        self._render_cwd_label()

    def _render_cwd_label(self) -> None:
        """Recompute cwd and session label budgets from sibling widths and render.

        Also renders session label (budget split).

        CONVENTION: Every method that changes a footer widget's content or
        visibility MUST defer a call to this method via call_after_refresh.
        Integration tests in test_app_ui.py verify this behaviorally.
        """
        cwd_label = self.query_one_optional("#cwd-label", Static)
        session_label = self.query_one_optional("#session-label", SessionIndicator)

        has_cwd = bool(self._cwd)
        has_session = bool(self._session_id)

        # Hide labels with no content
        if cwd_label and not has_cwd:
            cwd_label.add_class("hidden")
        if session_label and not has_session:
            session_label.add_class("hidden")

        # Nothing to render — early return
        if not has_cwd and not has_session:
            return

        try:
            app_width = self.app.size.width
            footer_content = self.query_one("#footer-content")
        except Exception:
            if cwd_label:
                cwd_label.add_class("hidden")
            if session_label:
                session_label.add_class("hidden")
            log.debug("_render_cwd_label: footer not ready", exc_info=True)
            return

        # Sum widths of all fixed-size siblings
        used = sum(
            child.outer_size.width
            for child in footer_content.children
            if child.id not in ("cwd-label", "session-label", "footer-spacer")
        )

        # Only subtract SESSION_PADDING when session_id is present
        total_budget = max(
            app_width - used - CWD_PADDING - (SESSION_PADDING if has_session else 0), 0
        )

        # Compute per-label budgets based on actual rendered lengths.
        # Cwd is formatted first to determine its real width, then session
        # gets all remaining space. This avoids over-allocating to cwd when
        # the path is short (e.g. "~" only uses 1 char, not the full budget).
        if has_cwd and has_session:
            cwd_max = min(total_budget, MAX_CWD_LENGTH)
            cwd_text = format_cwd(self._cwd, cwd_max)
            cwd_actual = len(cwd_text)
            if cwd_actual < MIN_CWD_LENGTH:
                # Cwd too short to display — give everything to session
                cwd_budget = 0
                session_budget = total_budget
            else:
                cwd_budget = cwd_actual
                session_budget = max(total_budget - cwd_actual, 0)
        elif has_session:
            session_budget = total_budget
            cwd_budget = 0
            cwd_text = ""
        else:  # has_cwd only
            cwd_budget = min(total_budget, MAX_CWD_LENGTH)
            session_budget = 0
            cwd_text = ""

        # Render cwd label
        if cwd_label:
            if cwd_budget < MIN_CWD_LENGTH or not has_cwd:
                cwd_label.add_class("hidden")
            else:
                # Use pre-formatted text when available (dual-label path)
                if has_session and cwd_text:
                    cwd_label.update(cwd_text)
                else:
                    cwd_label.update(format_cwd(self._cwd, cwd_budget))
                cwd_label.remove_class("hidden")

        # Render session label
        if session_label:
            if session_budget < MIN_SESSION_LENGTH or not has_session:
                session_label.add_class("hidden")
            else:
                assert self._session_id is not None  # narrowed by has_session guard
                session_label.update(format_session_id(self._session_id, session_budget))
                session_label.remove_class("hidden")

    def watch_branch(self, value: str) -> None:
        """Update branch label when branch changes."""
        if label := self.query_one_optional("#branch-label", Static):
            label.update(f"⎇ {value}" if value else "")
        self.call_after_refresh(self._render_cwd_label)

    def watch_model(self, value: str) -> None:
        """Update model label when model changes."""
        if label := self.query_one_optional("#model-label", ModelLabel):
            label.update(value if value else "")
        self.call_after_refresh(self._render_cwd_label)

    def watch_permission_mode(self, value: str) -> None:
        """Update permission mode label when setting changes."""
        if label := self.query_one_optional(
            "#permission-mode-label", PermissionModeLabel
        ):
            if value == "planSwarm":
                label.update("Plan swarm")
                label.set_class(False, "active")
                label.set_class(False, "plan-mode")
                label.set_class(True, "plan-swarm-mode")
            elif value == "plan":
                label.update("Plan mode")
                label.set_class(False, "active")
                label.set_class(True, "plan-mode")
                label.set_class(False, "plan-swarm-mode")
            elif value == "acceptEdits":
                label.update("Auto-edit: on")
                label.set_class(True, "active")
                label.set_class(False, "plan-mode")
                label.set_class(False, "plan-swarm-mode")
            else:  # default
                label.update("Auto-edit: off")
                label.set_class(False, "active")
                label.set_class(False, "plan-mode")
                label.set_class(False, "plan-swarm-mode")
        self.call_after_refresh(self._render_cwd_label)

    def update_processes(self, processes: list[BackgroundProcess]) -> None:
        """Update the process indicator."""
        if indicator := self.query_one_optional("#process-indicator", ProcessIndicator):
            indicator.update_processes(processes)
        self.call_after_refresh(self._render_cwd_label)

    def update_vi_mode(self, mode: ViMode | None, enabled: bool = True) -> None:
        """Update the vi-mode indicator."""
        if label := self.query_one_optional("#vi-mode-label", ViModeLabel):
            label.set_mode(mode, enabled)
        self.call_after_refresh(self._render_cwd_label)

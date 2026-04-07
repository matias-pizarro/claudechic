"""Resource indicator widgets - context bar, CPU monitor, and process indicator."""

import psutil

from textual.app import RenderResult
from textual.reactive import reactive
from textual.widgets import Static
from rich.text import Text

from claudechic.formatting import MAX_CONTEXT_TOKENS, format_tokens
from claudechic.profiling import profile, timed
from claudechic.processes import BackgroundProcess


class IndicatorWidget(Static):
    """Base class for clickable indicator widgets in the footer.

    Pointer cursor is set via CSS (pointer: pointer).
    Override on_click() to handle click events.
    """

    DEFAULT_CSS = """
    IndicatorWidget {
        pointer: pointer;
    }
    """

    can_focus = True


class CPUBar(IndicatorWidget):
    """Display CPU usage. Click to show profiling stats."""

    cpu_pct = reactive(0.0)

    def on_mount(self) -> None:
        self._process = psutil.Process()
        self._process.cpu_percent()  # Prime the measurement
        self.set_interval(2.0, self._update_cpu)

    @profile
    def _update_cpu(self) -> None:
        try:
            with timed("CPUBar.psutil_call"):
                pct = self._process.cpu_percent()
            # Only update if rounded value changed (avoids unnecessary refresh)
            if round(pct) != round(self.cpu_pct):
                with timed("CPUBar.reactive_set"):
                    self.cpu_pct = pct
        except Exception:
            pass  # Process may have exited

    def render(self) -> RenderResult:
        pct = min(self.cpu_pct / 100.0, 1.0)
        if pct < 0.3:
            color = "dim"
        elif pct < 0.7:
            color = "yellow"
        else:
            color = "red"
        return Text.assemble(("CPU ", "dim"), (f"{self.cpu_pct:3.0f}%", color))

    def on_click(self, event) -> None:
        """Show profile modal on click."""
        from claudechic.widgets.modals.profile import ProfileModal

        self.app.push_screen(ProfileModal())


class ContextBar(IndicatorWidget):
    """Display context usage as a progress bar. Click to run /context."""

    tokens = reactive(0)
    max_tokens = reactive(MAX_CONTEXT_TOKENS)

    def render(self) -> RenderResult:
        pct = min(self.tokens / self.max_tokens, 1.0) if self.max_tokens else 0
        pct_int = int(pct * 100)
        if pct < 0.5:
            fg, bg = "white", "#333333"
        elif pct < 0.8:
            fg, bg = "black", "#aaaa00"
        else:
            fg, bg = "white", "#cc3333"
        used = format_tokens(self.tokens)
        total = format_tokens(self.max_tokens)
        return Text.assemble(
            (f" {pct_int}% ", f"{fg} on {bg}"),
            (f"[{used}/{total}]", f"dim on {bg}"),
            (" ", f"on {bg}"),
        )

    def on_click(self, event) -> None:
        """Run /context command on click."""
        from claudechic.app import ChatApp

        if isinstance(self.app, ChatApp):
            self.app._handle_prompt("/context")


class ProcessIndicator(IndicatorWidget):
    """Display count of background processes. Click to show details."""

    DEFAULT_CSS = """
    ProcessIndicator {
        width: auto;
        padding: 0 1;
    }
    ProcessIndicator:hover {
        background: $panel;
    }
    ProcessIndicator.hidden {
        display: none;
    }
    """

    count = reactive(0)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._processes: list[BackgroundProcess] = []

    def update_processes(self, processes: list[BackgroundProcess]) -> None:
        """Update the process list and count."""
        self._processes = processes
        self.count = len(processes)
        self.set_class(self.count == 0, "hidden")

    def render(self) -> RenderResult:
        return Text.assemble(("⚙ ", "yellow"), (f"{self.count}", ""))

    def on_click(self, event) -> None:
        """Show process modal on click."""
        from claudechic.widgets.modals.process_modal import ProcessModal

        self.app.push_screen(ProcessModal(self._processes))

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


def _context_bar_color(pct: float) -> tuple[str, str, str]:
    """Return (fg, fg_dim, bg) hex colors for a context usage percentage.

    Gradient: green (0%) → orange (30%) → red (50%) → dark crimson (100%).
    Linear RGB interpolation between anchor points.
    fg is the main text color, fg_dim is a muted version for brackets.
    """
    # Anchor colors (R, G, B)
    green = (0x11, 0x77, 0x33)    # #117733
    orange = (0xCC, 0x77, 0x00)   # #CC7700
    red = (0xCC, 0x33, 0x33)      # #CC3333
    crimson = (0x66, 0x11, 0x11)  # #661111

    if pct <= 0.30:
        # Green → Orange
        t = pct / 0.30
        r, g, b = (int(a + (b - a) * t) for a, b in zip(green, orange))
    elif pct <= 0.50:
        # Orange → Red
        t = (pct - 0.30) / 0.20
        r, g, b = (int(a + (b - a) * t) for a, b in zip(orange, red))
    else:
        # Red → Dark Crimson
        t = min((pct - 0.50) / 0.50, 1.0)
        r, g, b = (int(a + (b - a) * t) for a, b in zip(red, crimson))

    bg = f"#{r:02x}{g:02x}{b:02x}"
    # White text on darker backgrounds, black on lighter ones
    lum = r * 0.299 + g * 0.587 + b * 0.114
    if lum > 140:
        fg = "black"
        # Dim = blend fg toward bg (40% black + 60% bg)
        dr, dg, db = int(r * 0.6), int(g * 0.6), int(b * 0.6)
    else:
        fg = "white"
        # Dim = blend fg toward bg (40% white + 60% bg)
        dr = int(r * 0.6 + 255 * 0.4)
        dg = int(g * 0.6 + 255 * 0.4)
        db = int(b * 0.6 + 255 * 0.4)
    fg_dim = f"#{dr:02x}{dg:02x}{db:02x}"
    return fg, fg_dim, bg


class ContextBar(IndicatorWidget):
    """Display context usage as a progress bar. Click to run /context."""

    tokens = reactive(0)
    max_tokens = reactive(MAX_CONTEXT_TOKENS)

    def render(self) -> RenderResult:
        pct = min(self.tokens / self.max_tokens, 1.0) if self.max_tokens else 0
        pct_int = int(pct * 100)
        fg, fg_dim, bg = _context_bar_color(pct)
        used = format_tokens(self.tokens)
        total = format_tokens(self.max_tokens)
        return Text.assemble(
            (f" {pct_int}% ", f"{fg} on {bg}"),
            (f"[{used}/{total}]", f"{fg_dim} on {bg}"),
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

# Sidebar Agent Context Info Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show per-agent cwd and context token usage (with human-friendly formatting) in the sidebar's AgentItem widget.

**Architecture:** Each `Agent` gets `tokens` and `max_tokens` fields, updated from session files. The sidebar's `AgentItem` renders three rows: name, cwd, and context info. A new pure function `format_tokens()` in `formatting.py` handles K/M formatting. Context window size is parsed from the SDK's model `displayName` (e.g., "Claude 4 Sonnet (1M context)") and stored per-agent.

**Tech Stack:** Python, Textual (reactive properties, Rich Text rendering), claude_agent_sdk

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `claudechic/formatting.py` | Modify | Add `format_tokens()` and `parse_context_size()` pure functions |
| `claudechic/agent.py` | Modify | Add `tokens`, `max_tokens` fields to `Agent` |
| `claudechic/widgets/layout/sidebar.py` | Modify | Expand `AgentItem` to 3-row layout with cwd + context |
| `claudechic/widgets/layout/indicators.py` | Modify | Remove hardcoded `MAX_CONTEXT_TOKENS` import, use agent's value |
| `claudechic/app.py` | Modify | Pipe token data to sidebar, set `max_tokens` from model info |
| `tests/test_formatting.py` | Create | Tests for `format_tokens()` and `parse_context_size()` |
| `tests/test_sidebar_context.py` | Create | Tests for `AgentItem` rendering with context info |

---

### Task 1: Pure functions for token formatting

**Files:**
- Modify: `claudechic/formatting.py:14` (near `MAX_CONTEXT_TOKENS`)
- Create: `tests/test_formatting.py`

- [ ] **Step 1: Write failing tests for `format_tokens()`**

```python
# tests/test_formatting.py
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
        # model.id like "claude-opus-4-6[1m]"
        assert parse_context_size("claude-opus-4-6[1m]") == 1_000_000

    def test_model_id_200k(self):
        assert parse_context_size("claude-sonnet-4-6[200k]") == 200_000

    def test_model_id_no_bracket(self):
        assert parse_context_size("claude-sonnet-4-6") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/test_formatting.py -v`
Expected: FAIL — `format_tokens` and `parse_context_size` don't exist yet.

- [ ] **Step 3: Implement `format_tokens()` and `parse_context_size()`**

In `claudechic/formatting.py`, add after the `MAX_CONTEXT_TOKENS` line:

```python
import re

# ... existing code ...

def format_tokens(n: int) -> str:
    """Format token count with K/M suffixes for compact display.

    Examples: 500 -> "500", 18500 -> "18.5K", 1000000 -> "1M"
    """
    if n >= 1_000_000:
        value = n / 1_000_000
        return f"{value:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        value = n / 1_000
        return f"{value:.1f}K"
    return str(n)


def parse_context_size(display_name: str) -> int | None:
    """Extract context window size from model display name or model ID.

    Handles:
    - "Claude 4 Sonnet (1M context)" -> 1_000_000
    - "claude-opus-4-6[1m]" -> 1_000_000
    - "Claude 3.5 Haiku (200K context)" -> 200_000
    - "claude-sonnet-4-6[200k]" -> 200_000

    Returns None if no context size found.
    """
    # Try parenthesized format: "... (1M context)" or "... (200K context)"
    m = re.search(r"\((\d+(?:\.\d+)?)(K|M)\s*context\)", display_name, re.IGNORECASE)
    if m:
        value = float(m.group(1))
        unit = m.group(2).upper()
        return int(value * 1_000_000) if unit == "M" else int(value * 1_000)

    # Try bracket format from model ID: "...[1m]" or "...[200k]"
    m = re.search(r"\[(\d+(?:\.\d+)?)(k|m)\]", display_name, re.IGNORECASE)
    if m:
        value = float(m.group(1))
        unit = m.group(2).upper()
        return int(value * 1_000_000) if unit == "M" else int(value * 1_000)

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/test_formatting.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add claudechic/formatting.py tests/test_formatting.py
git commit -m "feat: add format_tokens() and parse_context_size() helpers"
```

---

### Task 2: Add `tokens` and `max_tokens` to Agent

**Files:**
- Modify: `claudechic/agent.py:186` (near existing `self.model`)

- [ ] **Step 1: Add fields to Agent.__init__**

In `claudechic/agent.py`, in the `__init__` method, after `self.model: str | None = None`:

```python
        self.tokens: int = 0  # Current context token usage
        self.max_tokens: int = 200_000  # Context window size (updated from model info)
```

- [ ] **Step 2: Run existing tests to verify nothing breaks**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All existing tests still pass.

- [ ] **Step 3: Commit**

```bash
git add claudechic/agent.py
git commit -m "feat: add tokens and max_tokens fields to Agent"
```

---

### Task 3: Update `refresh_context` to store tokens on Agent

**Files:**
- Modify: `claudechic/app.py:961-970` (`refresh_context` method)
- Modify: `claudechic/app.py:2793-2813` (`_update_footer_model` method)

- [ ] **Step 1: Update `refresh_context` to store tokens on the agent**

Replace the existing `refresh_context` method in `claudechic/app.py`:

```python
    @work(group="refresh_context", exclusive=True)
    async def refresh_context(self) -> None:
        """Update context bar and agent tokens from session file (no API call)."""
        agent = self._agent
        if not agent or not agent.session_id:
            self.context_bar.tokens = 0
            return
        tokens = await get_context_from_session(agent.session_id, cwd=agent.cwd)
        if tokens is not None:
            agent.tokens = tokens
            self.context_bar.tokens = tokens
            # Update sidebar
            self._update_sidebar_agent_context(agent)
```

- [ ] **Step 2: Add `_update_sidebar_agent_context` helper**

Add this method to `ChatApp`, near the existing `_update_footer_model`:

```python
    def _update_sidebar_agent_context(self, agent: Agent) -> None:
        """Push agent's tokens/max_tokens and cwd to its sidebar item."""
        from claudechic.widgets.layout.sidebar import AgentSection

        section = self.query_one_optional(AgentSection)
        if section:
            section.update_agent_context(
                agent.id,
                cwd=str(agent.cwd),
                tokens=agent.tokens,
                max_tokens=agent.max_tokens,
            )
```

- [ ] **Step 3: Set `max_tokens` on agent when model info is available**

In `_update_footer_model`, after finding the active model dict (around line 2804), add code to set `max_tokens` on the agent:

```python
    def _update_footer_model(self, model: str | None) -> None:
        """Update footer to show agent's model."""
        if not self._available_models:
            self.status_footer.model = model.capitalize() if model else ""
            return
        # Find matching model, or default if model is None
        active = self._available_models[0]
        for m in self._available_models:
            if model and m.get("value") == model:
                active = m
                break
            if not model and m.get("value") == "default":
                active = m
                break
        # Extract short name from description like "Opus 4.5 · ..."
        desc = active.get("description", "")
        model_name = (
            desc.split("·")[0].strip() if "·" in desc else active.get("displayName", "")
        )
        self.status_footer.model = model_name

        # Set max_tokens on agent from model's display name or ID
        agent = self._agent
        if agent:
            from claudechic.formatting import parse_context_size

            display_name = active.get("displayName", "")
            context_size = parse_context_size(display_name)
            if context_size is None:
                # Try model ID (e.g., "claude-opus-4-6[1m]")
                model_id = active.get("value", "")
                context_size = parse_context_size(model_id)
            if context_size:
                agent.max_tokens = context_size
                self.context_bar.max_tokens = context_size
                self._update_sidebar_agent_context(agent)
```

- [ ] **Step 4: Run existing tests**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add claudechic/app.py
git commit -m "feat: store per-agent tokens/max_tokens, update sidebar on context refresh"
```

---

### Task 4: Expand AgentItem to show cwd and context

**Files:**
- Modify: `claudechic/widgets/layout/sidebar.py:391-468` (`AgentItem` class)
- Create: `tests/test_sidebar_context.py`

- [ ] **Step 1: Write failing tests for AgentItem rendering**

```python
# tests/test_sidebar_context.py
"""Tests for AgentItem context display in sidebar."""

from pathlib import Path

from rich.text import Text

from claudechic.widgets.layout.sidebar import AgentItem
from claudechic.enums import AgentStatus


def _plain(text: Text) -> str:
    """Extract plain string from Rich Text."""
    return text.plain


class TestAgentItemContextLabel:
    def test_context_label_format(self):
        """Context row shows percentage and token counts."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 18500
        item._max_tokens = 1_000_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "2%" in plain
        assert "18.5K" in plain
        assert "1M" in plain

    def test_context_label_zero_tokens(self):
        """Zero tokens shows 0% with counts."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 0
        item._max_tokens = 200_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "0%" in plain

    def test_context_label_high_usage(self):
        """High usage shows correct percentage."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._tokens = 160_000
        item._max_tokens = 200_000
        label = item._render_context_label()
        plain = _plain(label)
        assert "80%" in plain


class TestAgentItemCwdLabel:
    def test_cwd_truncation(self):
        """Long paths are front-truncated."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._cwd = "/very/long/path/to/some/deeply/nested/project"
        label = item._render_cwd_label()
        plain = _plain(label)
        # Should be truncated with ellipsis at front
        assert plain.startswith("…") or len(plain) <= 20

    def test_cwd_short_path(self):
        """Short paths are shown as-is."""
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._cwd = "~/project"
        label = item._render_cwd_label()
        plain = _plain(label)
        assert plain == "~/project"

    def test_cwd_home_replacement(self):
        """Home directory is replaced with ~."""
        import os
        home = os.path.expanduser("~")
        item = AgentItem("id1", "main", AgentStatus.IDLE)
        item._cwd = f"{home}/code/myproject"
        label = item._render_cwd_label()
        plain = _plain(label)
        assert plain.startswith("~")
        assert home not in plain
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/test_sidebar_context.py -v`
Expected: FAIL — `_tokens`, `_max_tokens`, `_cwd`, `_render_context_label`, `_render_cwd_label` don't exist.

- [ ] **Step 3: Implement expanded AgentItem**

Replace the `AgentItem` class in `claudechic/widgets/layout/sidebar.py`. Key changes:
- Add `_cwd`, `_tokens`, `_max_tokens` instance attributes
- Change `compose()` to yield 3 statics: label, cwd, context
- Add `_render_cwd_label()` and `_render_context_label()` methods
- Add `update_context()` method for external updates
- Increase height to 5 (name + cwd + context + padding)

```python
class AgentItem(SidebarItem):
    """A single agent in the sidebar."""

    class Selected(Message):
        """Posted when agent is clicked."""

        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            super().__init__()

    class CloseRequested(Message):
        """Posted when close button is clicked."""

        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            super().__init__()

    DEFAULT_CSS = """
    AgentItem {
        height: 5;
        min-height: 5;
        layout: vertical;
        padding: 1 1 0 2;
    }
    AgentItem.compact {
        height: 1;
        min-height: 1;
        padding: 0 1 0 2;
    }
    AgentItem.active {
        padding: 1 1 0 1;
        border-left: wide $primary;
        background: $surface;
    }
    AgentItem.active.compact {
        padding: 0 1 0 1;
    }
    AgentItem .agent-top-row {
        layout: horizontal;
        height: 1;
    }
    AgentItem .agent-label {
        width: 1fr;
        height: 1;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    AgentItem .agent-close {
        width: 3;
        min-width: 3;
        height: 1;
        padding: 0;
        background: $panel;
        color: $text-muted;
        text-align: center;
    }
    AgentItem .agent-close:hover {
        color: $error;
        background: $panel-lighten-1;
    }
    AgentItem .agent-cwd {
        height: 1;
        padding: 0 0 0 2;
        overflow: hidden;
    }
    AgentItem .agent-context {
        height: 1;
        padding: 0 0 0 2;
        overflow: hidden;
    }
    AgentItem.compact .agent-cwd,
    AgentItem.compact .agent-context {
        display: none;
    }
    """

    max_name_length: int = 14
    max_cwd_length: int = 20

    status: reactive[AgentStatus] = reactive(AgentStatus.IDLE)

    def __init__(
        self, agent_id: str, display_name: str, status: AgentStatus = AgentStatus.IDLE
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.display_name = display_name
        self.status = status
        self._cwd: str = ""
        self._tokens: int = 0
        self._max_tokens: int = 200_000

    def compose(self) -> ComposeResult:
        with Horizontal(classes="agent-top-row"):
            yield Static(self._render_label(), classes="agent-label")
            yield Static(Text("X"), classes="agent-close")
        yield Static(self._render_cwd_label(), classes="agent-cwd")
        yield Static(self._render_context_label(), classes="agent-context")

    def _render_label(self) -> Text:
        if self.status == AgentStatus.BUSY:
            indicator = "\u25cf"
            style = ""
        elif self.status == AgentStatus.NEEDS_INPUT:
            indicator = "\u25cf"
            style = self.app.current_theme.primary if self.app else "bold"
        else:
            indicator = "\u25cb"
            style = "dim"
        name = self.truncate_name(self.display_name)
        return Text.assemble((indicator, style), " ", (name, ""))

    def _render_cwd_label(self) -> Text:
        """Render the cwd row (dim, front-truncated)."""
        import os

        cwd = self._cwd
        if not cwd:
            return Text("")
        # Replace home dir with ~
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        # Front-truncate if too long
        if len(cwd) > self.max_cwd_length:
            cwd = "\u2026" + cwd[-(self.max_cwd_length - 1):]
        return Text(cwd, style="dim")

    def _render_context_label(self) -> Text:
        """Render the context row: percentage and token counts."""
        from claudechic.formatting import format_tokens

        pct = (
            min(self._tokens / self._max_tokens, 1.0) if self._max_tokens else 0
        )
        pct_str = f"{pct * 100:.0f}%"

        # Color by threshold
        if pct < 0.5:
            color = "dim"
        elif pct < 0.8:
            color = "yellow"
        else:
            color = "red"

        used = format_tokens(self._tokens)
        total = format_tokens(self._max_tokens)
        return Text.assemble(
            (pct_str, color),
            (" ", ""),
            (f"[{used}/{total}]", "dim"),
        )

    def update_context(
        self, *, cwd: str | None = None, tokens: int | None = None, max_tokens: int | None = None
    ) -> None:
        """Update context info and refresh display."""
        changed = False
        if cwd is not None and cwd != self._cwd:
            self._cwd = cwd
            changed = True
        if tokens is not None and tokens != self._tokens:
            self._tokens = tokens
            changed = True
        if max_tokens is not None and max_tokens != self._max_tokens:
            self._max_tokens = max_tokens
            changed = True
        if changed:
            self._refresh_detail_rows()

    def _refresh_detail_rows(self) -> None:
        """Update the cwd and context static widgets."""
        if cwd_widget := self.query_one_optional(".agent-cwd", Static):
            cwd_widget.update(self._render_cwd_label())
        if ctx_widget := self.query_one_optional(".agent-context", Static):
            ctx_widget.update(self._render_context_label())

    def watch_status(self, _status: str) -> None:
        """Update label when status changes."""
        if label := self.query_one_optional(".agent-label", Static):
            label.update(self._render_label())

    def on_click(self, event: Click) -> None:
        """Handle clicks - check if on close button."""
        if event.widget and event.widget.has_class("agent-close"):
            event.stop()
            self.post_message(self.CloseRequested(self.agent_id))
        else:
            self.post_message(self.Selected(self.agent_id))
```

Note: Add the missing `Horizontal` import at the top of `sidebar.py`:

```python
from textual.containers import Horizontal
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/test_sidebar_context.py -v`
Expected: All PASS.

- [ ] **Step 5: Run all tests to verify nothing breaks**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add claudechic/widgets/layout/sidebar.py tests/test_sidebar_context.py
git commit -m "feat: show cwd and context token usage in sidebar AgentItem"
```

---

### Task 5: Add `update_agent_context` to AgentSection

**Files:**
- Modify: `claudechic/widgets/layout/sidebar.py:484-558` (`AgentSection` class)

- [ ] **Step 1: Add `update_agent_context` method to `AgentSection`**

Add this method to `AgentSection`, after the existing `update_status` method:

```python
    def update_agent_context(
        self,
        agent_id: str,
        *,
        cwd: str | None = None,
        tokens: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Update context info for a specific agent's sidebar item."""
        if agent_id in self._agents:
            self._agents[agent_id].update_context(
                cwd=cwd, tokens=tokens, max_tokens=max_tokens
            )
```

- [ ] **Step 2: Run all tests**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add claudechic/widgets/layout/sidebar.py
git commit -m "feat: add update_agent_context to AgentSection"
```

---

### Task 6: Wire up cwd on agent creation and switch

**Files:**
- Modify: `claudechic/app.py` (in `on_agent_created`, `on_agent_switched`)

- [ ] **Step 1: Push cwd to sidebar when agent is created**

Find the `on_agent_created` handler in `app.py`. After the agent is added to the sidebar, call:

```python
        self._update_sidebar_agent_context(agent)
```

- [ ] **Step 2: Push cwd + context on agent switch**

Find the `on_agent_switched` handler. After updating the footer model and context bar, add:

```python
        self._update_sidebar_agent_context(new_agent)
        # Also update context bar max_tokens for switched agent
        self.context_bar.max_tokens = new_agent.max_tokens
```

- [ ] **Step 3: Push context for all agents after model info arrives**

In `_update_slash_commands`, after `_update_footer_model` is called and `_available_models` is populated, update all agents' `max_tokens`:

```python
                    # Set max_tokens for all agents based on their model
                    if self.agent_mgr:
                        for ag in self.agent_mgr:
                            self._set_agent_max_tokens(ag)
                            self._update_sidebar_agent_context(ag)
```

Add the helper:

```python
    def _set_agent_max_tokens(self, agent: Agent) -> None:
        """Set agent's max_tokens from available model info."""
        from claudechic.formatting import parse_context_size

        if not self._available_models:
            return
        # Find matching model
        target = self._available_models[0]  # default
        for m in self._available_models:
            if agent.model and m.get("value") == agent.model:
                target = m
                break
            if not agent.model and m.get("value") == "default":
                target = m
                break
        display_name = target.get("displayName", "")
        context_size = parse_context_size(display_name)
        if context_size is None:
            model_id = target.get("value", "")
            context_size = parse_context_size(model_id)
        if context_size:
            agent.max_tokens = context_size
```

- [ ] **Step 4: Run all tests**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add claudechic/app.py
git commit -m "feat: wire agent cwd and max_tokens to sidebar on create/switch"
```

---

### Task 7: Remove hardcoded MAX_CONTEXT_TOKENS from ContextBar default

**Files:**
- Modify: `claudechic/widgets/layout/indicators.py:74`

- [ ] **Step 1: Change ContextBar default to use inline value**

Replace:
```python
from claudechic.formatting import MAX_CONTEXT_TOKENS
```
with just importing what's needed. Change the `max_tokens` reactive default:

```python
    max_tokens = reactive(200_000)
```

Remove the `MAX_CONTEXT_TOKENS` import from indicators.py (but keep the constant in `formatting.py` since other code may use it).

- [ ] **Step 2: Run all tests**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All pass.

- [ ] **Step 3: Run the app manually to verify**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run claudechic`
Expected: Sidebar shows agent name, cwd, and context info on three rows. Context bar in footer still works. After connecting, the context size should update from model info.

- [ ] **Step 4: Commit**

```bash
git add claudechic/widgets/layout/indicators.py
git commit -m "refactor: remove hardcoded MAX_CONTEXT_TOKENS import from ContextBar"
```

---

### Task 8: Run pre-commit and final verification

- [ ] **Step 1: Run pre-commit hooks**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run pre-commit run --all-files`
Expected: All pass (ruff lint, ruff-format, pyright).

- [ ] **Step 2: Fix any issues found**

Address any formatting or type errors from the hooks.

- [ ] **Step 3: Run full test suite**

Run: `cd /agents/worktrees/development/claudechic/claudechic && uv run python -m pytest tests/ -n auto -q`
Expected: All pass.

- [ ] **Step 4: Final commit if needed**

```bash
git add -u
git commit -m "chore: fix lint and type issues"
```

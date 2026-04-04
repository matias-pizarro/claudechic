# Session ID Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display the Claude session_id in the StatusFooter and via a `/session-id` command, so users can see which session they're in and copy the full ID.

**Architecture:** `StatusFooter` owns `_session_id` state and `set_session_id()` method (matching the existing `_cwd`/`set_cwd()` pattern). `SessionIndicator` is a passive `Static` subclass rendered by `_render_cwd_label`. `format_session_id()` is a pure function in `formatting.py`. The app wires 7 trigger points in `app.py` with active-agent gating.

**Tech Stack:** Python 3.12, Textual TUI framework, pytest, Claude Agent SDK

**Spec:** `docs/superpowers/specs/2026-04-04-session-id-display-design.md` (rev 5)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `claudechic/formatting.py` | Modify | Add `MIN_SESSION_LENGTH`, `format_session_id()` |
| `claudechic/widgets/layout/footer.py` | Modify | Add `SessionIndicator`, `SESSION_PADDING`, `_session_id`, `set_session_id()`, budget logic in `_render_cwd_label` |
| `claudechic/commands.py` | Modify | Add `/session-id` to `COMMANDS`, handler function |
| `claudechic/app.py` | Modify | 7 trigger points, `_start_new_session` bug fix, `on_system_notification` init branch |
| `tests/test_widgets.py` | Modify | `format_session_id` unit tests, `SessionIndicator` widget tests |
| `CLAUDE.md` | Modify | Add `/session-id` to Commands, `SessionIndicator` to Widget Hierarchy |

---

## Task 1: Lifecycle fix — clear session_id in `_start_new_session`

**Files:**
- Modify: `claudechic/app.py:2003-2017`

This is a bug fix. Today, `/clear` leaves a stale `session_id` on the agent because neither `disconnect()` nor `connect()` (without resume) clears it.

- [ ] **Step 1: Add `agent.session_id = None` before `disconnect()` in `_start_new_session`**

In `claudechic/app.py`, find `_start_new_session` at line 2003. Add the clear before `disconnect()`:

```python
    async def _start_new_session(self) -> None:
        """Start a fresh session for the current agent."""
        agent = self._agent
        if not agent:
            return
        chat_view = self._chat_view
        if chat_view:
            chat_view.clear()
        agent.session_id = None  # Clear stale session_id before reconnect
        await agent.disconnect()
        options = self._make_options(
            cwd=agent.cwd, agent_name=agent.name, model=agent.model
        )
        await agent.connect(options)
        self.refresh_context()
        self.notify("New session started")
```

- [ ] **Step 2: Run existing tests to ensure no regression**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All existing tests pass (this change only affects runtime behavior with a real SDK connection).

- [ ] **Step 3: Commit**

```bash
git add claudechic/app.py
git commit -m "fix: clear session_id before reconnect in _start_new_session"
```

---

## Task 2: `format_session_id()` — write failing tests

**Files:**
- Create tests in: `tests/test_widgets.py` (append to existing file)
- Create function in: `claudechic/formatting.py`

- [ ] **Step 1: Write failing tests for `format_session_id`**

Append to `tests/test_widgets.py`:

```python
from claudechic.formatting import format_session_id, MIN_SESSION_LENGTH


class TestFormatSessionId:
    """Tests for format_session_id() adaptive truncation."""

    def test_full_id_when_budget_sufficient(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert format_session_id(sid, 36) == sid

    def test_full_id_when_budget_exceeds_length(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert format_session_id(sid, 100) == sid

    def test_truncation_with_ellipsis(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        result = format_session_id(sid, 12)
        assert result == "a1b2c3d4-e5\u2026"
        assert len(result) == 12

    def test_truncation_at_min_session_length(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        result = format_session_id(sid, 8)
        assert result == "a1b2c3d\u2026"
        assert len(result) == 8

    def test_budget_2_returns_one_char_plus_ellipsis(self):
        sid = "a1b2c3d4"
        result = format_session_id(sid, 2)
        assert result == "a\u2026"

    def test_budget_1_returns_ellipsis(self):
        sid = "a1b2c3d4"
        result = format_session_id(sid, 1)
        assert result == "\u2026"

    def test_budget_0_returns_ellipsis(self):
        sid = "a1b2c3d4"
        result = format_session_id(sid, 0)
        assert result == "\u2026"

    def test_short_id_fits_in_budget(self):
        sid = "short"
        assert format_session_id(sid, 10) == "short"

    def test_short_id_truncated_when_budget_tight(self):
        sid = "abcdef"
        result = format_session_id(sid, 4)
        assert result == "abc\u2026"

    def test_min_session_length_constant(self):
        assert MIN_SESSION_LENGTH == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_widgets.py::TestFormatSessionId -v`
Expected: FAIL with `ImportError: cannot import name 'format_session_id'`

- [ ] **Step 3: Implement `format_session_id` and `MIN_SESSION_LENGTH`**

In `claudechic/formatting.py`, after `MAX_CWD_LENGTH = 80` (line 18), add:

```python
MIN_SESSION_LENGTH = 8  # Below this budget, hide session_id entirely


def format_session_id(session_id: str, budget: int) -> str:
    """Format session ID with adaptive truncation.

    Truncates from the end (suffix removed, prefix preserved).
    Opposite direction from format_cwd() which preserves suffixes.
    Uses U+2026 (…) for ellipsis, consistent with format_cwd().

    Precondition: session_id is non-empty.

    Rules:
        budget >= len(session_id) -> full ID
        budget >= 2               -> first (budget-1) chars + "…"
        budget < 2                -> "…"
    """
    if budget >= len(session_id):
        return session_id
    if budget >= 2:
        return session_id[: budget - 1] + "\u2026"
    return "\u2026"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_widgets.py::TestFormatSessionId -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add claudechic/formatting.py tests/test_widgets.py
git commit -m "feat: add format_session_id() with adaptive truncation"
```

---

## Task 3: `SessionIndicator` widget + `StatusFooter` state ownership

**Files:**
- Modify: `claudechic/widgets/layout/footer.py`

- [ ] **Step 1: Write failing test for `StatusFooter.set_session_id`**

Append to `tests/test_widgets.py`:

```python
@pytest.mark.asyncio
async def test_status_footer_set_session_id_hides_when_none():
    """set_session_id(None) hides the session label."""
    app = WidgetTestApp(StatusFooter)
    async with app.run_test(size=(120, 3)):
        footer = app.query_one(StatusFooter)
        footer.set_session_id(None)
        await asyncio.sleep(0.05)
        label = footer.query_one("#session-label")
        assert "hidden" in label.classes


@pytest.mark.asyncio
async def test_status_footer_set_session_id_hides_when_empty():
    """set_session_id('') hides the session label."""
    app = WidgetTestApp(StatusFooter)
    async with app.run_test(size=(120, 3)):
        footer = app.query_one(StatusFooter)
        footer.set_session_id("")
        await asyncio.sleep(0.05)
        label = footer.query_one("#session-label")
        assert "hidden" in label.classes


@pytest.mark.asyncio
async def test_status_footer_set_session_id_shows_value():
    """set_session_id with a value shows formatted session in the label."""
    app = WidgetTestApp(StatusFooter)
    async with app.run_test(size=(120, 3)):
        footer = app.query_one(StatusFooter)
        footer.set_session_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        await asyncio.sleep(0.05)
        label = footer.query_one("#session-label")
        # Should not be hidden (at 120 cols there's enough budget)
        assert "hidden" not in label.classes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_widgets.py::test_status_footer_set_session_id_hides_when_none -v`
Expected: FAIL — no `#session-label` in the DOM

- [ ] **Step 3: Implement `SessionIndicator`, `set_session_id()`, budget logic**

In `claudechic/widgets/layout/footer.py`:

**A. Add import** at line 12 (after `format_cwd` import):

```python
from claudechic.formatting import format_cwd, format_session_id, MIN_CWD_LENGTH, MAX_CWD_LENGTH, MIN_SESSION_LENGTH
```

**B. Add `SESSION_PADDING` constant** after `CWD_PADDING = 2` (line 21):

```python
SESSION_PADDING = 2  # CSS "padding: 0 1" on .footer-label = 1 left + 1 right
```

**C. Add `SessionIndicator` class** after `ViModeLabel` class (after line 83):

```python
class SessionIndicator(Static):
    """Passive session ID label in the footer. Content set by StatusFooter._render_cwd_label."""

    pass
```

**D. Add `_session_id` field** in `StatusFooter.__init__` (after `self._cwd = ""`):

```python
        self._session_id: str | None = None
```

**E. Add `set_session_id` method** after `set_cwd` (after line 139):

```python
    def set_session_id(self, session_id: str | None) -> None:
        """Update session_id value and schedule re-render."""
        self._session_id = session_id
        self.call_after_refresh(self._render_cwd_label)
```

**F. Add `SessionIndicator` to `compose()`** between cwd-label and branch-label:

```python
            yield Static("", id="cwd-label", classes="footer-label hidden")
            yield SessionIndicator("", id="session-label", classes="footer-label hidden")
            yield Static("", id="branch-label", classes="footer-label")
```

**G. Update `_render_cwd_label`** to handle both labels. Replace the method body (lines 145-175):

```python
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

        # Compute per-label budgets
        if has_cwd and has_session:
            session_budget = min(12, total_budget)
            cwd_budget = min(total_budget - session_budget, MAX_CWD_LENGTH)
        elif has_session:
            session_budget = min(12, total_budget)
            cwd_budget = 0
        else:  # has_cwd only
            cwd_budget = min(total_budget, MAX_CWD_LENGTH)
            session_budget = 0

        # Render cwd label
        if cwd_label:
            if cwd_budget < MIN_CWD_LENGTH or not has_cwd:
                cwd_label.add_class("hidden")
            else:
                cwd_label.update(format_cwd(self._cwd, cwd_budget))
                cwd_label.remove_class("hidden")

        # Render session label
        if session_label:
            if session_budget < MIN_SESSION_LENGTH or not has_session:
                session_label.add_class("hidden")
            else:
                session_label.update(format_session_id(self._session_id, session_budget))
                session_label.remove_class("hidden")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_widgets.py::test_status_footer_set_session_id_hides_when_none tests/test_widgets.py::test_status_footer_set_session_id_hides_when_empty tests/test_widgets.py::test_status_footer_set_session_id_shows_value -v`
Expected: All 3 PASS

- [ ] **Step 5: Run ALL tests to ensure no regression**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass. The `_render_cwd_label` refactor preserves existing cwd behavior.

- [ ] **Step 6: Commit**

```bash
git add claudechic/widgets/layout/footer.py tests/test_widgets.py
git commit -m "feat: add SessionIndicator widget and StatusFooter.set_session_id()"
```

---

## Task 4: Event wiring — 7 trigger points in `app.py`

**Files:**
- Modify: `claudechic/app.py`

- [ ] **Step 1: Wire trigger 1 — `on_response_complete`**

In `claudechic/app.py`, find `on_response_complete` at line 1363. After `self.refresh_context()` (line 1369), add the gated footer push:

```python
            # Update session ID in footer (gated to active agent)
            if self.agent_mgr and event.agent_id == self.agent_mgr.active_id:
                self.status_footer.set_session_id(agent.session_id)
```

- [ ] **Step 2: Wire trigger 2 — `on_system_notification` (init subtype)**

In `on_system_notification` at line 1139, add a new branch after the `api_error` handling block (after line 1168) and before the generic `error` block. Also add `"init"` to the suppression set at the catch-all guard:

After the `compact_boundary` block and before the `error` block, add:

```python
        elif subtype == "init":
            # Session ID was captured by agent layer; push to footer
            agent = self._get_agent(event.agent_id)
            if agent and self.agent_mgr and event.agent_id == self.agent_mgr.active_id:
                self.status_footer.set_session_id(agent.session_id)
```

Update the catch-all suppression set at line 1179 to include `"init"`:

```python
        elif subtype not in ("stop_hook_summary", "turn_duration", "local_command", "init"):
```

- [ ] **Step 3: Wire trigger 3 — `on_agent_switched`**

In `on_agent_switched` at line 2383, after `self.status_footer.set_cwd(str(new_agent.cwd))` (line 2443), add:

```python
        self.status_footer.set_session_id(new_agent.session_id)
```

- [ ] **Step 4: Wire trigger 4 — `resume_session`**

In `resume_session` at line 1416, after `agent.session_id = session_id` (line 1426), add the gated footer push:

```python
            # Update footer if this agent is still active (async — user may have switched)
            if self.agent_mgr and agent.id == self.agent_mgr.active_id:
                self.status_footer.set_session_id(agent.session_id)
```

- [ ] **Step 5: Wire trigger 5 — `_start_new_session`**

In `_start_new_session`, after `agent.session_id = None` (the line added in Task 1), add:

```python
        self.status_footer.set_session_id(None)
```

- [ ] **Step 6: Wire trigger 6 — `_reconnect_sdk` (no resume)**

In `_reconnect_sdk`, after `agent.session_id = None` at line 1831, add a guarded footer push:

```python
                agent.session_id = None
                if agent is self._agent:
                    self.status_footer.set_session_id(None)
                self.notify(f"SDK reconnected in {new_cwd.name}")
```

- [ ] **Step 7: Wire trigger 7 — `_reconnect_sdk` (with resume)**

In `_reconnect_sdk`, after the `_load_and_display_history` call at line 1826-1828, add a guarded footer push:

```python
            if resume_id:
                await self._load_and_display_history(
                    resume_id, cwd=new_cwd, agent=agent
                )
                if agent is self._agent:
                    self.status_footer.set_session_id(agent.session_id)
                self.notify(f"Resumed session in {new_cwd.name}")
```

- [ ] **Step 8: Run ALL tests**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass.

- [ ] **Step 9: Commit**

```bash
git add claudechic/app.py
git commit -m "feat: wire session_id to footer at 7 trigger points with active-agent gating"
```

---

## Task 5: `/session-id` command

**Files:**
- Modify: `claudechic/commands.py`

- [ ] **Step 1: Write failing test for `/session-id` command registry**

Append to `tests/test_widgets.py`:

```python
def test_session_id_command_in_registry():
    """The /session-id command is registered for autocomplete and help."""
    from claudechic.commands import COMMANDS, get_autocomplete_commands

    names = [name for name, _, _ in COMMANDS]
    assert "/session-id" in names

    autocomplete = get_autocomplete_commands()
    assert "/session-id" in autocomplete
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_widgets.py::test_session_id_command_in_registry -v`
Expected: FAIL — `/session-id` not in COMMANDS

- [ ] **Step 3: Add `/session-id` to COMMANDS and implement handler**

In `claudechic/commands.py`:

**A. Add to COMMANDS list** (after the `/reviews` entry, before `/analytics`):

```python
    ("/session-id", "Show and copy session ID", []),
```

**B. Add handler in `handle_command`**. After the `/usage` block (after line 217), add:

```python
    if cmd == "/session-id":
        _track_command(app, "session-id")
        return _handle_session_id(app)
```

**C. Add the handler function** at the end of the file:

```python
def _handle_session_id(app: "ChatApp") -> bool:
    """Handle /session-id - show and copy the current session ID."""
    agent = app._agent
    session_id = agent.session_id if agent else None

    if not session_id:
        app._show_system_info("No active session")
        return True

    agent_name = agent.name if agent else "unknown"
    msg = f"Session ID ({agent_name}): {session_id}"
    app._show_system_info(msg)

    # Best-effort clipboard copy
    try:
        app.copy_to_clipboard(session_id)
        app.notify("Copied to clipboard")
    except Exception:
        pass  # Clipboard unavailable (SSH, headless) — message already printed

    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_widgets.py::test_session_id_command_in_registry -v`
Expected: PASS

- [ ] **Step 5: Add `/session-id` to help display**

In `get_help_commands()` in `commands.py`, add a display name case:

```python
        elif name == "/session-id":
            display_name = "/session-id"
```

- [ ] **Step 6: Run ALL tests**

Run: `uv run python -m pytest tests/ -n auto -q`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add claudechic/commands.py tests/test_widgets.py
git commit -m "feat: add /session-id command with clipboard copy"
```

---

## Task 6: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add `/session-id` to Commands section**

In `CLAUDE.md`, find the `## Commands` section, under `### Session Management`. Add:

```markdown
- `/session-id` - Show and copy current session ID
```

- [ ] **Step 2: Add `SessionIndicator` to Widget Hierarchy**

In the Widget Hierarchy section, under `StatusFooter`, the layout should reflect the new widget position. No structural change needed — just ensure the compose order matches the actual code if the hierarchy is listed.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add /session-id command and SessionIndicator to CLAUDE.md"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All 7 acceptance criteria are covered by tasks 1-5. Lifecycle fix (Task 1), pure function (Task 2), widget + budget (Task 3), wiring (Task 4), command (Task 5), docs (Task 6).
- [x] **Placeholder scan:** No TBDs, TODOs, or vague instructions. All code blocks are complete.
- [x] **Type consistency:** `set_session_id(session_id: str | None)` used consistently across StatusFooter, app.py wiring, and commands. `format_session_id(session_id: str, budget: int)` signature consistent. `SessionIndicator` is a passive `Static` subclass everywhere.
- [x] **Spec requirement: `_start_new_session` bug fix** — Task 1.
- [x] **Spec requirement: active-agent gating** — Task 4, steps 1-7.
- [x] **Spec requirement: `_reconnect_sdk` with-resume trigger** — Task 4, step 7.
- [x] **Spec requirement: `on_system_notification` init branch** — Task 4, step 2.
- [x] **Spec requirement: COMMANDS registry** — Task 5.
- [x] **Missing: `styles.tcss`** — Not needed: `.footer-label` class already provides the styling. `#session-label` rule is optional per spec.
- [x] **Missing: `widgets/__init__.py` re-export** — Not needed per spec: `SessionIndicator` is footer-internal.

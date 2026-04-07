# Tokens in Footer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the footer's visual progress bar with text token counts (`32% [14.0K/200.0K]`) and inject context usage into every prompt as a system-reminder.

**Architecture:** Three parts: (1) ContextBar render replacement in `indicators.py`, (2) `_prepare_prompt()` + `update_context()` in `agent.py` for system-reminder injection, (3) `TOKEN_REMINDER_PATTERN` in `formatting.py` + stripping in `agent.py`/`sessions.py` for session resume cleanup.

**Tech Stack:** Python 3.12, Textual (TUI framework), Rich (text styling), pytest

**Spec:** `docs/superpowers/specs/2026-04-07-tokens-in-footer-design.md` (rev 4)

---

## File Map

| File | Role |
|------|------|
| `claudechic/formatting.py` | Add `TOKEN_REMINDER_PATTERN` regex (shared, no UI deps) |
| `claudechic/widgets/layout/indicators.py` | Replace `ContextBar.render()` with text format |
| `claudechic/agent.py` | Add `update_context()`, `_context_initialized`, `_prepare_prompt()`; reset in `disconnect()`; strip in `load_history()` |
| `claudechic/sessions.py` | Strip token tags in `load_session_messages()` and `_extract_session_info()` |
| `claudechic/app.py` | Use `agent.update_context()` in `refresh_context()` and model-metadata path; call `refresh_cwd_label()` via `call_after_refresh` |
| `tests/test_widgets.py` | Update `test_context_bar_rendering`; add boundary and styling tests |
| `tests/test_agent.py` | Tests for `_prepare_prompt()`, `update_context()`, lifecycle, session resume |

---

### Task 1: Add `TOKEN_REMINDER_PATTERN` to `formatting.py`

**Files:**
- Modify: `claudechic/formatting.py:19` (after `MIN_SESSION_LENGTH`)

- [ ] **Step 1: Add the regex pattern**

In `claudechic/formatting.py`, after `MIN_SESSION_LENGTH = 8` (line 19), add:

```python

# Matches only the token-injection system-reminder at the start of a string.
# Anchored to ^ so mid-message user content is never stripped.
TOKEN_REMINDER_PATTERN = re.compile(
    r"^\s*<system-reminder>\d+/\d+ tokens</system-reminder>\n*"
)
```

Note: `re` is already imported at line 6 — do NOT add a duplicate import.

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "from claudechic.formatting import TOKEN_REMINDER_PATTERN; print(TOKEN_REMINDER_PATTERN.pattern)"`

Expected: `^\s*<system-reminder>\d+/\d+ tokens</system-reminder>\n*`

- [ ] **Step 3: Commit**

```bash
git add claudechic/formatting.py
git commit -m "feat: add TOKEN_REMINDER_PATTERN regex to formatting.py"
```

---

### Task 2: Replace `ContextBar.render()` with text format

**Files:**
- Modify: `claudechic/widgets/layout/indicators.py:10,70-106`
- Modify: `tests/test_widgets.py:348-367`

- [ ] **Step 1: Write the failing test — update existing test and add boundary tests**

In `tests/test_widgets.py`, replace the `test_context_bar_rendering` function (lines 348-367) with:

```python
@pytest.mark.asyncio
async def test_context_bar_rendering():
    """ContextBar shows text format with percentage and token counts."""
    app = WidgetTestApp(lambda: ContextBar(id="ctx"))
    async with app.run_test():
        bar = app.query_one(ContextBar)

        # Low usage (5%) — dim color, text format
        bar.tokens = 10000
        bar.max_tokens = 200000
        rendered = bar.render()
        assert hasattr(rendered, "plain")
        plain = rendered.plain  # type: ignore[union-attr]
        assert "5%" in plain
        assert "[10.0K/200.0K]" in plain

        # High usage (90%) — red color, text format
        bar.tokens = 180000
        rendered = bar.render()
        plain = rendered.plain  # type: ignore[union-attr]
        assert "90%" in plain
        assert "[180.0K/200.0K]" in plain

        # Zero tokens
        bar.tokens = 0
        rendered = bar.render()
        plain = rendered.plain  # type: ignore[union-attr]
        assert "0%" in plain
        assert "[0/200.0K]" in plain

        # Division safety: max_tokens=0
        bar.max_tokens = 0
        rendered = bar.render()
        plain = rendered.plain  # type: ignore[union-attr]
        assert "0%" in plain
        assert "[0/0]" in plain


@pytest.mark.asyncio
async def test_context_bar_color_thresholds():
    """ContextBar applies correct color at threshold boundaries."""
    app = WidgetTestApp(lambda: ContextBar(id="ctx"))
    async with app.run_test():
        bar = app.query_one(ContextBar)
        bar.max_tokens = 100

        # 49% -> dim (last dim value)
        bar.tokens = 49
        rendered = bar.render()
        spans = rendered._spans  # type: ignore[union-attr]
        assert any("dim" in str(s.style) for s in spans)

        # 50% -> yellow (first yellow value)
        bar.tokens = 50
        rendered = bar.render()
        spans = rendered._spans  # type: ignore[union-attr]
        assert any("yellow" in str(s.style) for s in spans)

        # 79% -> yellow (last yellow value)
        bar.tokens = 79
        rendered = bar.render()
        spans = rendered._spans  # type: ignore[union-attr]
        assert any("yellow" in str(s.style) for s in spans)

        # 80% -> red (first red value)
        bar.tokens = 80
        rendered = bar.render()
        spans = rendered._spans  # type: ignore[union-attr]
        assert any("red" in str(s.style) for s in spans)

        # 100% -> red
        bar.tokens = 100
        rendered = bar.render()
        spans = rendered._spans  # type: ignore[union-attr]
        assert any("red" in str(s.style) for s in spans)
        assert "100%" in rendered.plain  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_context_bar_bracket_always_dim():
    """Bracket portion [used/max] is always styled dim regardless of percentage."""
    app = WidgetTestApp(lambda: ContextBar(id="ctx"))
    async with app.run_test():
        bar = app.query_one(ContextBar)
        bar.max_tokens = 100

        for token_val in [10, 60, 90]:
            bar.tokens = token_val
            rendered = bar.render()
            # The last span (bracket portion) should always be dim
            spans = rendered._spans  # type: ignore[union-attr]
            last_span = spans[-1]
            assert "dim" in str(last_span.style), f"Bracket not dim at {token_val}%"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_widgets.py::test_context_bar_rendering tests/test_widgets.py::test_context_bar_color_thresholds tests/test_widgets.py::test_context_bar_bracket_always_dim -v`

Expected: FAIL (current render returns visual bar, not text)

- [ ] **Step 3: Implement the new render method**

In `claudechic/widgets/layout/indicators.py`, change the import (line 10) from:

```python
from claudechic.formatting import MAX_CONTEXT_TOKENS
```

To:

```python
from claudechic.formatting import MAX_CONTEXT_TOKENS, format_tokens
```

Replace `ContextBar.render()` (lines 76-106) with:

```python
    def render(self) -> RenderResult:
        pct = min(self.tokens / self.max_tokens, 1.0) if self.max_tokens else 0
        pct_int = int(pct * 100)
        if pct < 0.5:
            color = "dim"
        elif pct < 0.8:
            color = "yellow"
        else:
            color = "red"
        used = format_tokens(self.tokens)
        total = format_tokens(self.max_tokens)
        return Text.assemble(
            (f"{pct_int}%", color),
            (" ", ""),
            (f"[{used}/{total}]", "dim"),
        )
```

Note: Uses 3-segment `Text.assemble` (percentage, space, brackets) matching the sidebar's `_render_context_label()` pattern. The old theme-aware color code is fully removed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_widgets.py::test_context_bar_rendering tests/test_widgets.py::test_context_bar_color_thresholds tests/test_widgets.py::test_context_bar_bracket_always_dim -v`

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run python -m pytest tests/ -n auto -q`

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add claudechic/widgets/layout/indicators.py tests/test_widgets.py
git commit -m "feat: replace ContextBar visual bar with text token counts"
```

---

### Task 3: Add `update_context()` and `_context_initialized` to Agent

**Files:**
- Modify: `claudechic/agent.py:139-210,255-271`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent.py`:

```python
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
        assert "<system-reminder>" in agent._prepare_prompt("hello")
        await agent.disconnect()
        assert agent._prepare_prompt("hello") == "hello"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_agent.py::TestUpdateContext -v`

Expected: FAIL (Agent has no `update_context` method or `_context_initialized` attribute)

- [ ] **Step 3: Implement `_context_initialized` flag and `update_context()` method**

In `claudechic/agent.py`, add to `__init__` after `self.max_tokens` (line 191):

```python
        self._context_initialized: bool = False  # Set by update_context()
```

Add the `update_context` method after the `__init__` method (before the `@property` for `analytics_id`, around line 217):

```python
    def update_context(self, tokens: int, max_tokens: int | None = None) -> None:
        """Atomically update token state and mark context as initialized.

        Args:
            tokens: Current context token usage.
            max_tokens: Context window size. If None, preserves existing value.
        """
        self.tokens = tokens
        if max_tokens is not None:
            self.max_tokens = max_tokens
        self._context_initialized = True
```

Add the flag reset in `disconnect()` — after `self._claude_pid = None` (line 271):

```python
        self._context_initialized = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_agent.py::TestUpdateContext -v`

Expected: PASS (the `test_no_injection_after_disconnect` will fail because `_prepare_prompt` doesn't exist yet — that's OK, it will pass after Task 4)

- [ ] **Step 5: Commit**

```bash
git add claudechic/agent.py tests/test_agent.py
git commit -m "feat: add Agent.update_context() and _context_initialized flag"
```

---

### Task 4: Add `_prepare_prompt()` method to Agent

**Files:**
- Modify: `claudechic/agent.py:448-486`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_agent.py::TestPreparePrompt -v`

Expected: FAIL (Agent has no `_prepare_prompt` method)

- [ ] **Step 3: Implement `_prepare_prompt()`**

In `claudechic/agent.py`, add the method before `_get_plan_mode_instructions()` (around line 448):

```python
    def _prepare_prompt(self, prompt: str) -> str:
        """Augment prompt with system-reminder and plan-mode instructions.

        Side-effect-free: reads self state but does not mutate anything.
        Called at the top of _process_response() before sending to SDK.

        Ordering: token reminder -> plan-mode instructions -> user prompt.
        To achieve this, we prepend in reverse order: plan first, then token.
        """
        # Prepend plan mode instructions if in plan mode
        if self.permission_mode == "plan":
            prompt = self._get_plan_mode_instructions() + prompt

        # Inject context usage when initialized (outermost = first in string)
        if self._context_initialized:
            prompt = (
                f"<system-reminder>{self.tokens}/{self.max_tokens} tokens"
                f"</system-reminder>\n{prompt}"
            )

        return prompt
```

Then replace the inline plan-mode prepend in `_process_response()` (lines 484-486):

```python
            # Prepend plan mode instructions if in plan mode
            if self.permission_mode == "plan":
                prompt = self._get_plan_mode_instructions() + prompt
```

With:

```python
            # Augment prompt with token reminder and plan-mode instructions
            prompt = self._prepare_prompt(prompt)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_agent.py -v`

Expected: PASS (including the `test_no_injection_after_disconnect` from Task 3)

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add claudechic/agent.py tests/test_agent.py
git commit -m "feat: add _prepare_prompt() for system-reminder injection"
```

---

### Task 5: Wire `update_context()` into `app.py` and add footer rebudgeting

**Files:**
- Modify: `claudechic/app.py:969-1006,2984-2985`

- [ ] **Step 1: Replace direct attribute assignments in `refresh_context()`**

In `claudechic/app.py`, replace the SDK API success path (lines 982-996):

```python
                    agent.tokens = usage.get("totalTokens", 0)
                    # Try rawMaxTokens first (raw model window), fall back to
                    # maxTokens (effective limit after autocompact buffer)
                    raw_max = usage.get("rawMaxTokens") or usage.get("maxTokens", 0)
                    if raw_max and raw_max > 0:
                        agent.max_tokens = raw_max
                    else:
                        log.debug(
                            "refresh_context: no max_tokens in response, keys=%s",
                            list(usage.keys()),
                        )
                    self.context_bar.tokens = agent.tokens
                    self.context_bar.max_tokens = agent.max_tokens
                    self._update_sidebar_agent_context(agent)
                    return
```

With:

```python
                    tokens = usage.get("totalTokens", 0)
                    raw_max = usage.get("rawMaxTokens") or usage.get("maxTokens", 0)
                    if raw_max and raw_max > 0:
                        agent.update_context(tokens, raw_max)
                    else:
                        agent.update_context(tokens)
                        log.debug(
                            "refresh_context: no max_tokens in response, keys=%s",
                            list(usage.keys()),
                        )
                    self.context_bar.tokens = agent.tokens
                    self.context_bar.max_tokens = agent.max_tokens
                    self._update_sidebar_agent_context(agent)
                    self.call_after_refresh(self.status_footer.refresh_cwd_label)
                    return
```

Replace the fallback path (lines 1001-1006):

```python
        tokens = await get_context_from_session(agent.session_id, cwd=agent.cwd)
        if tokens is not None:
            agent.tokens = tokens
            self.context_bar.tokens = tokens
            self.context_bar.max_tokens = agent.max_tokens
            self._update_sidebar_agent_context(agent)
```

With:

```python
        tokens = await get_context_from_session(agent.session_id, cwd=agent.cwd)
        if tokens is not None:
            agent.update_context(tokens)
            self.context_bar.tokens = agent.tokens
            self.context_bar.max_tokens = agent.max_tokens
            self._update_sidebar_agent_context(agent)
            self.call_after_refresh(self.status_footer.refresh_cwd_label)
```

- [ ] **Step 2: Update the model-metadata path**

In `claudechic/app.py`, find the model-metadata code (around line 2984):

```python
                agent.max_tokens = context_size
                self.context_bar.max_tokens = context_size
```

Replace with:

```python
                agent.update_context(agent.tokens, context_size)
                self.context_bar.max_tokens = context_size
```

This ensures the model-metadata path also goes through `update_context()`, setting `_context_initialized = True` when real model data arrives.

- [ ] **Step 3: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add claudechic/app.py
git commit -m "refactor: use agent.update_context() in all token-setting paths, add rebudgeting"
```

---

### Task 6: Strip token tags from session resume (`load_history` and `sessions.py`)

**Files:**
- Modify: `claudechic/agent.py:39,302-356`
- Modify: `claudechic/sessions.py:87-143,208-233`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_agent.py`:

```python
from claudechic.formatting import TOKEN_REMINDER_PATTERN


class TestTokenReminderPattern:
    def test_matches_token_reminder_at_start(self):
        text = "<system-reminder>14000/200000 tokens</system-reminder>\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == "hello"

    def test_preserves_plan_mode_tags(self):
        text = "<system-reminder>\nPLAN MODE ACTIVE\n</system-reminder>\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == text  # Unchanged

    def test_preserves_mid_message_content(self):
        text = "user said <system-reminder>42/100 tokens</system-reminder> here"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == text  # Unchanged (not at start)

    def test_strips_with_leading_whitespace(self):
        text = "  <system-reminder>5000/200000 tokens</system-reminder>\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == "hello"

    def test_strips_trailing_newlines(self):
        text = "<system-reminder>14000/200000 tokens</system-reminder>\n\nhello"
        result = TOKEN_REMINDER_PATTERN.sub("", text)
        assert result == "hello"
```

- [ ] **Step 2: Run pattern tests to verify they pass (pattern already exists from Task 1)**

Run: `uv run python -m pytest tests/test_agent.py::TestTokenReminderPattern -v`

Expected: PASS

- [ ] **Step 3: Add stripping to `load_history()` in `agent.py`**

In `claudechic/agent.py`, change the import (line 39) from:

```python
from claudechic.formatting import MAX_CONTEXT_TOKENS
```

To:

```python
from claudechic.formatting import MAX_CONTEXT_TOKENS, TOKEN_REMINDER_PATTERN
```

In `load_history()`, modify the user message handling (line 332-334). Replace:

```python
                # Add user message
                self.messages.append(
                    ChatItem(role="user", content=UserContent(text=m["content"]))
                )
```

With:

```python
                # Add user message (strip injected token reminder from wire format)
                clean_text = TOKEN_REMINDER_PATTERN.sub("", m["content"])
                self.messages.append(
                    ChatItem(role="user", content=UserContent(text=clean_text))
                )
```

- [ ] **Step 4: Add stripping to `sessions.py`**

In `claudechic/sessions.py`, add the import at the top (after existing imports):

```python
from claudechic.formatting import TOKEN_REMINDER_PATTERN
```

In `_extract_session_info()`, modify the string content path (lines 119-121). Replace:

```python
                            if isinstance(content, str) and content.strip():
                                if not content.startswith("<command-"):
                                    first_msg = content.replace("\n", " ")[:100]
```

With:

```python
                            if isinstance(content, str) and content.strip():
                                clean = TOKEN_REMINDER_PATTERN.sub("", content)
                                if clean.strip() and not clean.startswith("<command-"):
                                    first_msg = clean.replace("\n", " ")[:100]
```

Modify the list-content-block path (lines 122-127). Replace:

```python
                            elif isinstance(content, list) and content:
                                block = content[0]
                                if block.get("type") == "text":
                                    txt = block.get("text", "")
                                    if txt.strip() and not txt.startswith("<command-"):
                                        first_msg = txt.replace("\n", " ")[:100]
```

With:

```python
                            elif isinstance(content, list) and content:
                                block = content[0]
                                if block.get("type") == "text":
                                    txt = TOKEN_REMINDER_PATTERN.sub("", block.get("text", ""))
                                    if txt.strip() and not txt.startswith("<command-"):
                                        first_msg = txt.replace("\n", " ")[:100]
```

In `load_session_messages()`, modify the user message content handling (lines 228-233). Replace:

```python
                    if isinstance(content, str) and content.strip():
                        if content.strip().startswith("/"):
                            continue
                        if any(tag in content for tag in skip_tags):
                            continue
                        messages.append({"type": "user", "content": content})
```

With:

```python
                    if isinstance(content, str) and content.strip():
                        clean = TOKEN_REMINDER_PATTERN.sub("", content)
                        if clean.strip().startswith("/"):
                            continue
                        if any(tag in clean for tag in skip_tags):
                            continue
                        messages.append({"type": "user", "content": clean})
```

- [ ] **Step 5: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`

Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add claudechic/agent.py claudechic/sessions.py tests/test_agent.py
git commit -m "feat: strip token reminder tags from resumed sessions"
```

---

### Task 7: Integration verification

**Files:**
- No new files; verifying the full feature works end-to-end

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest tests/ -n auto -q`

Expected: All tests pass, no regressions

- [ ] **Step 2: Run pre-commit hooks**

Run: `uv run pre-commit run --all-files`

Expected: All hooks pass (ruff lint, ruff format, pyright)

- [ ] **Step 3: Fix any lint/type issues**

Address any ruff or pyright findings from step 2. In particular, check that no unused imports remain in `indicators.py` after removing the theme-aware rendering code.

- [ ] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "chore: fix lint and type issues"
```

(Skip if no fixes needed.)

---

## Self-Review Checklist

**Spec coverage:**
- AC1 (text format): Task 2
- AC2 (color thresholds): Task 2 (`test_context_bar_color_thresholds`)
- AC3 (dim brackets): Task 2 (`test_context_bar_bracket_always_dim`)
- AC4 (system-reminder injection): Tasks 3-5
- AC5 (raw integers): Task 4 (f-string with self.tokens/self.max_tokens)
- AC6 (resume stripping): Task 6
- AC7 (no overflow): Covered by existing footer rebudgeting + Task 5 `call_after_refresh` rebudget
- AC8 (click works): Unchanged, existing `on_click` preserved

**Spec test mapping (21 tests):**
- Tests 1-5 (ContextBar): Task 2 — `test_context_bar_rendering`, `test_context_bar_color_thresholds`, `test_context_bar_bracket_always_dim`
- Tests 6-11 (injection): Tasks 3-4 — `TestUpdateContext`, `TestPreparePrompt`
- Tests 12-13 (lifecycle): Task 3 — `test_disconnect_resets_flag`, `test_no_injection_after_disconnect`
- Tests 14-16 (resume pattern): Task 6 — `TestTokenReminderPattern`
- Test 17 (session title): Deferred to integration — `_extract_session_info` is modified but testing it requires mock JSONL files; the regex tests validate the stripping logic
- Tests 18-21 (integration): Deferred — these require `test_app_ui.py` fixtures with real Textual app lifecycle; Task 7 runs the existing integration suite as regression check

**Placeholder scan:** No TBDs, TODOs, or "implement later" — all code blocks are complete.

**Type consistency:** `update_context(tokens: int, max_tokens: int | None = None)`, `_prepare_prompt(self, prompt: str) -> str`, `_context_initialized: bool`, `TOKEN_REMINDER_PATTERN: re.Pattern` — all consistent across tasks.

## Review History

### Rev 1 → Rev 2

Addressed findings from 6-agent review:

- **H1 (`_prepare_prompt` ordering bug):** Fixed — plan mode prepends first in code, then token reminder wraps outermost. Produces correct order: token → plan → user.
- **H2 (model-metadata path bypasses `update_context`):** Added Step 2 to Task 5 updating `app.py:2984`.
- **H3 (5 missing spec tests):** Added boundary color tests, split styling test, lifecycle post-disconnect test. Integration tests (#18-21) and session title test (#17) explicitly deferred with rationale.
- **M1 (duplicate import re):** Removed — note says "re is already imported."
- **M2 (missing boundary tests + split styling):** Added `test_context_bar_color_thresholds` and `test_context_bar_bracket_always_dim`.
- **M3 (rebudgeting):** Changed to `self.call_after_refresh(self.status_footer.refresh_cwd_label)`. Added model-metadata path.
- **M4 (unused theme code):** Added note in Task 7 to clean up unused imports.
- **M5 (session-resume tests):** Acknowledged; regex tests validate stripping logic, loader behavior verified by existing integration tests.
- **M6 (2-segment vs 3-segment):** Changed to 3-segment `Text.assemble` matching sidebar pattern.

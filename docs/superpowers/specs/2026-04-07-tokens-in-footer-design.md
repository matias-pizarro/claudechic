# Tokens in Footer — Design Spec

**Date:** 2026-04-07
**Branch:** tokens-in-footer
**Status:** Reviewed (rev 4)

## Problem

The footer `ContextBar` shows a 10-character visual progress bar with an embedded percentage (e.g., `░░░32%░░░░`). This conveys urgency via color but hides the actual numbers. Users who hide the sidebar — common on narrow terminals — lose visibility into how many tokens are consumed and how large the context window is.

Separately, the Claude model itself has no awareness of its own context window saturation. It cannot adjust verbosity or suggest compaction because it never sees the numbers.

## Goals

1. **Display token counts in the footer** — Replace the visual bar with a text label showing percentage plus token counts: `32% [14.0K/200.0K]`.
2. **Expose context usage to the model** — Inject a `<system-reminder>` with token stats into every prompt so the model (or its operator-level instructions) can act on context saturation.

## Non-Goals

- No changes to the sidebar `AgentItem` context display (already has token counts).
- No configuration toggle for the system-reminder injection (always on). A feature flag may be added later if model behavior issues arise, but is deliberately omitted from the first version to avoid premature complexity.
- No color or urgency hints inside the injected system-reminder (the operator handles that via external CLAUDE.md rules or operator instructions).
- No implementation task list — that belongs in the implementation plan, not the spec.

## Acceptance Criteria

1. The footer `ContextBar` renders text like `32% [14.0K/200.0K]` (not a visual bar).
2. The text color follows thresholds: dim (<50%), yellow (50–79%), red (≥80%).
3. Token count brackets are styled `dim` regardless of percentage (matching sidebar).
4. Every prompt sent to the SDK includes `<system-reminder>{used}/{max} tokens</system-reminder>`, **except** when context data is not yet initialized (see injection guard).
5. The system-reminder uses raw integers (e.g., `14000/200000`) not human-formatted values.
6. Resumed sessions do not display injected `<system-reminder>` token tags in user messages. Other `<system-reminder>` tags (e.g., plan-mode instructions) are preserved.
7. No footer truncation or overflow on 80-column terminals. When space is constrained, the footer hides cwd and session labels first (existing behavior). The model and branch labels have bounded width (model name + git branch) and are not dynamically truncated.
8. Clicking the `ContextBar` still runs `/context`.

## Design

### Part 1: Footer ContextBar Text Display

**File:** `claudechic/widgets/layout/indicators.py` — `ContextBar.render()`

Replace the visual progress bar with a text string:

```
32% [14.0K/200.0K]
```

**Format:** `{pct}% [{used}/{max}]`
- `pct`: integer percentage (0–100), computed from `self.tokens / self.max_tokens`
- `used`: `format_tokens(self.tokens)` — e.g., `0`, `500`, `14.0K`, `1.2M`
- `max`: `format_tokens(self.max_tokens)` — e.g., `200.0K`, `1M`

Note: `format_tokens(200000)` returns `"200.0K"` (not `"200K"`). The `.0` is only stripped for millions (`1.0M` → `1M`). All spec examples use the actual formatter output.

**Color thresholds** (applied via Rich `Text.assemble`):
- Percentage portion: dim (<50%), yellow (50–79%), red (≥80%)
- Bracket portion `[{used}/{max}]`: always `dim` (matching the sidebar's `AgentItem._render_context_label()` pattern)

This intentionally drops the current theme-aware dark/light color logic in favor of matching the sidebar's simpler approach. The sidebar already uses plain `dim`/`yellow`/`red` styles and has shipped without issues across themes.

**Width:** The `ContextBar` CSS in `styles.tcss` (line 512) has `width: auto` and `content-align: right middle`. Text length varies from ~13 chars (`0% [0/200.0K]`) to ~22 chars (`100% [200.0K/200.0K]`). The footer's spacer-based layout absorbs width variations, and the existing `_render_cwd_label()` budget recomputation hides cwd/session labels when siblings grow.

**Footer rebudgeting:** When `ContextBar` width changes (due to token count updates), the footer must recompute the cwd/session label budgets. `app.py` calls `self.status_footer.refresh_cwd_label()` after updating context-bar values in `refresh_context()`. This follows the existing pattern where `refresh_context()` already directly sets `self.context_bar.tokens` — adding a rebudget call at the same site keeps all context-bar update logic in one place.

**Division safety:** When `max_tokens == 0`, render `0% [0/0]` (same guard as existing code: `if self.max_tokens else 0`).

**Click behavior:** Unchanged — clicking runs `/context`.

### Part 2: System Message Injection

**File:** `claudechic/agent.py` — new `_prepare_prompt()` method and `update_context()` method

#### `_prepare_prompt(self, prompt: str) -> str`

A **side-effect-free method** that reads `self._context_initialized`, `self.tokens`, `self.max_tokens`, and `self.permission_mode` to augment the prompt. It does not mutate any state. It is called at the top of `_process_response()` and centralizes:
1. System-reminder injection (when context data is initialized)
2. Plan-mode instruction prepend (when `permission_mode == "plan"`)

This replaces the current inline plan-mode prepend in `_process_response()`.

Note: `_prepare_prompt` reads mutable instance attributes rather than accepting them as parameters. This is consistent with other private methods on `Agent` (e.g., `_handle_permission` reads `self.permission_mode`). Tests set the relevant attributes on an `Agent` instance before calling `_prepare_prompt()`.

#### `update_context(self, tokens: int, max_tokens: int | None = None) -> None`

A new public method on `Agent` that atomically updates token state:

```python
def update_context(self, tokens: int, max_tokens: int | None = None) -> None:
    self.tokens = tokens
    if max_tokens is not None:
        self.max_tokens = max_tokens
    self._context_initialized = True
```

This replaces the current pattern in `app.py` where `refresh_context()` directly sets `agent.tokens` and `agent.max_tokens` as separate attribute assignments. Benefits:
- Encapsulates the `_context_initialized` flag — app.py never touches the private flag directly.
- Ensures tokens and max_tokens are updated atomically with the initialized flag.
- The `max_tokens` parameter is optional: the SDK API path provides both values; the session-file fallback path provides only `tokens`.

**Injection guard:** `_context_initialized` starts `False`. It is set to `True` only via `update_context()`. It is **reset to `False`** in `Agent.disconnect()` to ensure lifecycle events (`/new`, `/clear`, reconnect, resume, model switch) do not carry stale data. After reconnect, `refresh_context()` calls `update_context()` which re-enables injection.

**Fallback path behavior:** The session-file fallback in `refresh_context()` only provides `tokens`, not `max_tokens`. It calls `agent.update_context(tokens)` (without max_tokens), which sets `_context_initialized = True`. In this case, `max_tokens` retains its previous value. This is acceptable because:
- On first connect, `max_tokens` is set from model metadata before the first `refresh_context()` call.
- On subsequent turns, `max_tokens` is already correct from a prior SDK API response.
- The fallback only fires when the SDK API is unavailable, which is rare.

**System-reminder format:**

```
<system-reminder>14000/200000 tokens</system-reminder>
```

- Uses **raw integers** (e.g., `14000/200000`), not human-formatted values. Raw integers are unambiguous for machine consumption and avoid coupling the agent layer to the presentation-formatting module.
- No percentage, no color, no urgency guidance — plain numbers only.
- The operator (via CLAUDE.md or other rules) is responsible for defining behavioral instructions based on these numbers. This feature provides the data; behavior is defined externally.

**Final prompt shape:**

Normal mode:
```
<system-reminder>14000/200000 tokens</system-reminder>
{user prompt}
```

Plan mode:
```
<system-reminder>14000/200000 tokens</system-reminder>
<system-reminder>
PLAN MODE ACTIVE
...
</system-reminder>
{user prompt}
```

The token reminder always appears first. Plan-mode instructions follow. The user's actual prompt is last. Both prepends happen inside `_prepare_prompt()` so the ordering is explicit and testable.

**Image path:** When `pending_images` is non-empty, the prompt goes through `_build_message_with_images()` which embeds it as a text content block. Since `_prepare_prompt()` runs before the image check, the system-reminder is included in the image path too.

**Token freshness:** The injected values (`self.tokens`, `self.max_tokens`) are **one turn stale**. They reflect the state after the previous response completed (updated by `refresh_context()` on `ResultMessage`). The user's new prompt and the model's upcoming response are not yet counted. This is an accepted trade-off — adding a synchronous refresh before every send would add latency. Near context saturation, the values may undercount significantly (e.g., if the user pasted a large document, or the previous response involved extensive tool chains). Typical conversational turns undercount by 5–20K tokens.

**Cost:** ~10 tokens per turn (raw integers are shorter than formatted values). At 100 turns per session, ~1,000 tokens — negligible.

**Edge case — tokens=0:** On the first message of a new session before `refresh_context()` runs, `_context_initialized` is `False`, so no reminder is injected. After the first response completes and `refresh_context()` succeeds, subsequent messages include the reminder.

### Part 3: Session Resume Cleanup

**File:** `claudechic/formatting.py` (new pattern) + `claudechic/agent.py` (`load_history()`) + `claudechic/sessions.py` (session parsing)

**SDK persistence assumption:** The Claude SDK persists the full wire-format prompt (including our prepended `<system-reminder>` content) in its session files. This is observed behavior as of the current SDK version. Part 3 is a defense against this specific persistence behavior. If the SDK changes to strip injected content on replay, Part 3 becomes inert (not harmful).

When a session is resumed via `load_history()`, user messages are loaded from the SDK's persisted session file. These contain the raw wire-format prompt including any `<system-reminder>` tags that were prepended. Without cleanup, resumed sessions would display raw `<system-reminder>14000/200000 tokens</system-reminder>` text at the beginning of user messages.

**Solution:** Define a **narrow, start-anchored regex** in `formatting.py` that matches **only** the token-injection pattern at the beginning of a message:

```python
TOKEN_REMINDER_PATTERN = re.compile(
    r"^\s*<system-reminder>\d+/\d+ tokens</system-reminder>\n*"
)
```

Key properties:
- **Anchored to start** (`^`): Only matches at the beginning of the string. A user who types `<system-reminder>42/100 tokens</system-reminder>` mid-message is unaffected.
- **Narrow content match** (`\d+/\d+ tokens`): Does not match plan-mode instructions, other system-reminders, or arbitrary content.
- **Strips trailing newlines** (`\n*`): Cleans up the separator between the tag and the user's actual text.

**Placement:** `formatting.py` (shared, no UI dependencies). Imported by:
- `agent.py` — used in `load_history()` to strip token tags from user message text.
- `sessions.py` — used in `load_session_messages()` and `_extract_session_info()` to strip token tags before command filtering and title extraction.

In `_extract_session_info()`, stripping applies to **both content paths**: when content is a plain string, and when content is a list of blocks (image-backed messages where content is `[{"type": "text", "text": "..."}]`). The stripping is applied to the text value before title extraction.

**Stripping happens in `load_history()`** (definitively, not the widget layer). The agent's in-memory `self.messages` model is the single source of truth — cleaning at the source means any future consumer of `Agent.messages` gets clean data automatically.

## Affected Files

| File | Change |
|------|--------|
| `claudechic/widgets/layout/indicators.py` | Replace `ContextBar.render()` visual bar with text format |
| `claudechic/formatting.py` | Add `TOKEN_REMINDER_PATTERN` regex |
| `claudechic/agent.py` | Add `_prepare_prompt()`, `update_context()`, `_context_initialized` flag; reset flag in `disconnect()`; refactor plan-mode prepend; strip token tags in `load_history()` |
| `claudechic/sessions.py` | Strip token tags in `load_session_messages()` and `_extract_session_info()` (both string and list-content-block paths) |
| `claudechic/app.py` | Use `agent.update_context()` in `refresh_context()` instead of direct attribute assignment; call `refresh_cwd_label()` after context-bar updates |
| `tests/test_widgets.py` | Update `test_context_bar_rendering`; add new test cases |
| `tests/test_agent.py` (new or existing) | Tests for `_prepare_prompt()`, `update_context()`, injection, plan-mode interaction, session resume, lifecycle reset |

## Testing Strategy

### Unit Tests — ContextBar

1. **Text format** — Verify output matches `{pct}% [{used}/{max}]` for: 0 tokens, 50K/200K, 160K/200K, 200K/200K, 500K/1M.
2. **Color thresholds** — Verify dim/yellow/red style at boundaries: 0%, 49%, 50%, 79%, 80%, 100%.
3. **Split styling** — Verify bracket portion `[used/max]` is always `dim`.
4. **Division safety** — Verify `max_tokens=0` renders `0% [0/0]` without error.
5. **Existing test enhancement** — Update `test_context_bar_rendering` to assert the new text format (currently only checks percentage substrings).

### Unit Tests — System-Reminder Injection

6. **`_prepare_prompt()` basic** — Call `agent.update_context(14000, 200000)`; assert `_prepare_prompt("hello")` starts with `<system-reminder>14000/200000 tokens</system-reminder>`.
7. **Plan-mode interaction** — Set `permission_mode="plan"`, call `update_context()`; assert output contains both token reminder AND plan-mode instructions, in correct order (token first, plan second, user prompt last).
8. **Image path** — Set `pending_images` non-empty; assert the prompt passed to `_build_message_with_images()` includes the system-reminder.
9. **Injection guard (uninitialized)** — Do NOT call `update_context()`; assert no `<system-reminder>` is prepended.
10. **Edge case: tokens=0 with initialized context** — Call `update_context(0, 200000)`; assert `<system-reminder>0/200000 tokens</system-reminder>` is prepended.
11. **`update_context()` atomicity** — Assert that calling `update_context(5000)` (no max_tokens) sets `_context_initialized=True` and preserves existing `max_tokens`.

### Unit Tests — Lifecycle

12. **Reset on disconnect** — Call `update_context(14000, 200000)`, then `disconnect()`; assert `_context_initialized` is `False`.
13. **No injection after `/new`** — Simulate disconnect+reconnect cycle; assert first prompt after reconnect has no system-reminder (until `refresh_context()` calls `update_context()` again).

### Unit Tests — Session Resume

14. **Token tag stripping** — Load a mock session containing user messages with token `<system-reminder>` tags; assert they are stripped from `UserContent.text`.
15. **Plan-mode tags preserved** — Load a mock session containing user messages with plan-mode `<system-reminder>` tags; assert they are NOT stripped.
16. **Mid-message user content preserved** — Load a mock session where user typed `<system-reminder>42/100 tokens</system-reminder>` mid-message; assert it is NOT stripped (anchored regex).
17. **Session title extraction** — Verify `_extract_session_info()` does not include token reminder in session titles. Test both string and list-content-block paths.

### Integration Tests

18. **Footer displays token text** — In `test_app_ui.py`, verify `ContextBar` renders text after context refresh.
19. **Click still works** — Verify clicking `ContextBar` triggers `/context`.
20. **Narrow terminal** — Verify footer layout at 80 columns does not overflow or truncate critical elements. Verify cwd/session labels hide before ContextBar does.
21. **Footer rebudgets on context update** — Verify that updating ContextBar tokens triggers cwd/session label rebudgeting.

## Risks

- **Horizontal space on narrow terminals:** The text is wider than the old bar (~13-22 chars vs ~12 with padding). The footer has a spacer and budget recomputation that hides cwd/session labels first. Risk mitigated by integration test #20.
- **Token staleness:** Values are one-turn stale. Near saturation, the model may underestimate usage significantly (depending on previous turn size). Typical conversational turns undercount by 5–20K tokens; pasted documents or long tool chains can undercount more. Accepted trade-off — a synchronous refresh would add latency to every message send.
- **Model behavior uncertainty:** The model may ignore the token data, add unwanted meta-commentary ("I see context is filling up..."), or prematurely truncate responses. This is mitigated by keeping the injection data-only and relying on operator instructions to define behavior. If no operator instruction is defined, the injection is inert overhead (~10 tokens/turn).
- **Mid-session model switch:** If the user changes models, `max_tokens` updates on the next `refresh_context()`. Until then, the injected max may be stale. Low impact — the value corrects within one turn.
- **SDK persistence behavior:** Part 3 assumes the SDK persists wire-format prompts in session files. If the SDK changes this behavior, Part 3's stripping becomes inert (safe, not harmful).

## Review History

### Rev 1 → Rev 2 (2026-04-07)

Addressed findings from 6-agent review (3 roborev, 1 code-reviewer, 1 architect, 1 contrarian):

- **H1 (stale tokens):** Documented one-turn staleness as accepted trade-off with impact estimate.
- **H2 (no evidence model benefits):** Clarified that this provides data only; behavior is defined by external operator instructions. Added model behavior uncertainty to risks.
- **H3 (wrong abstraction):** Extracted `_prepare_prompt()` method instead of inline prepend.
- **M1 (format mismatch):** Fixed all examples to use `200.0K` (actual `format_tokens` output).
- **M2 (ordering ambiguous):** Added explicit final prompt shape for normal and plan modes.
- **M3-M4 (missing tests):** Added plan-mode interaction and image-path tests to strategy.
- **M5 (max_tokens=0):** Added injection guard — skip when `max_tokens == 0`.
- **M6 (session resume):** Added Part 3 for stripping system-reminder tags from loaded history.
- **M7 (format_tokens coupling):** Changed injection to use raw integers instead of `format_tokens`.
- **M8 (theme colors):** Documented intentional simplification matching sidebar.
- **M9 (second-order effects):** Added model behavior uncertainty to risks.
- **M10 (no acceptance criteria):** Added Acceptance Criteria section.
- **M11 (test files):** Added test files to Affected Files table.
- **M12 (width estimate):** Fixed range to ~13-21 chars.
- **M13 (split styling):** Specified dim brackets matching sidebar.
- **L1 (model switch):** Added to risks.
- **L2 (existing test):** Added enhancement to test strategy.

### Rev 2 → Rev 3 (2026-04-07)

Addressed findings from 6-agent re-review:

- **H1 (broad tag stripping):** Replaced generic `SYSTEM_REMINDER_PATTERN` with narrow `TOKEN_REMINDER_PATTERN` that only matches `\d+/\d+ tokens` format.
- **H2 (SDK persistence undocumented):** Added explicit SDK persistence assumption documentation.
- **M1 (session-file consumers):** Extended stripping to `sessions.py` (`load_session_messages`, `_extract_session_info`).
- **M2 (footer rebudgeting):** Added footer rebudget trigger when ContextBar width changes.
- **M3 (regex layer violation):** Moved `TOKEN_REMINDER_PATTERN` to `formatting.py`.
- **M4 (Part 3 placement):** Resolved definitively to `load_history()`.
- **M5 (`_prepare_prompt` purity):** Documented as pure `str → str` transformation.
- **M6 (impossible guard state):** Replaced `max_tokens == 0` guard with `_context_initialized` flag.
- **M7 (acceptance criteria contradiction):** Added "except" qualifier.
- **M8 (SDK assumption):** Documented in Part 3 and Risks.

### Rev 3 → Rev 4 (2026-04-07)

Addressed findings from 6-agent third review:

- **H1 (`_context_initialized` lifecycle):** Reset flag in `Agent.disconnect()`. All lifecycle events (reconnect, `/new`, `/clear`, model switch) pass through disconnect, ensuring stale data is never carried forward.
- **H2 (fallback path sets flag incorrectly):** Documented fallback behavior: `update_context(tokens)` without max_tokens sets initialized=True but preserves existing max_tokens, which is correct because max_tokens was already set from model metadata or a prior SDK response.
- **H3 (boundary violation):** Added `Agent.update_context()` method. `app.py` now calls this method instead of directly setting private attributes. The initialized flag is fully encapsulated.
- **M1 (regex not anchored):** Anchored `TOKEN_REMINDER_PATTERN` to start of string with `^`. Mid-message user content is now immune to stripping.
- **M2 (list-content-block stripping):** Specified stripping in both string and list-content-block paths of `_extract_session_info()`.
- **M3 (rebudget "or"):** Committed to the `app.py` approach — `refresh_cwd_label()` called after context-bar updates in `refresh_context()`.
- **M4 ("pure" terminology):** Changed to "side-effect-free method."
- **M5 (test gaps):** Added lifecycle tests (#12-13), mid-message preservation test (#16), `update_context` atomicity test (#11), list-content-block title test (#17).

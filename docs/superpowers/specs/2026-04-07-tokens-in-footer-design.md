# Tokens in Footer — Design Spec

**Date:** 2026-04-07
**Branch:** tokens-in-footer
**Status:** Reviewed (rev 3)

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
- No changes to how tokens are fetched or tracked (`refresh_context` in `app.py`, `Agent.tokens`, `Agent.max_tokens` are untouched).
- No implementation task list — that belongs in the implementation plan, not the spec.

## Acceptance Criteria

1. The footer `ContextBar` renders text like `32% [14.0K/200.0K]` (not a visual bar).
2. The text color follows thresholds: dim (<50%), yellow (50–79%), red (≥80%).
3. Token count brackets are styled `dim` regardless of percentage (matching sidebar).
4. Every prompt sent to the SDK includes `<system-reminder>{used}/{max} tokens</system-reminder>`, **except** when context data is not yet initialized (see injection guard).
5. The system-reminder uses raw integers (e.g., `14000/200000`) not human-formatted values.
6. Resumed sessions do not display injected `<system-reminder>` token tags in user messages. Other `<system-reminder>` tags (e.g., plan-mode instructions) are preserved.
7. No footer truncation or overflow on 80-column terminals. When space is constrained, the footer hides cwd and session labels first (existing behavior); the ContextBar, model label, and branch label remain visible.
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

**Width:** The `ContextBar` CSS in `styles.tcss` (line 512) has `width: auto` and `content-align: right middle`. Text length varies from ~13 chars (`0% [0/200.0K]`) to ~21 chars (`100% [200.0K/200.0K]`). Edge cases like `format_tokens(999999) = "1000.0K"` can produce up to ~25 chars but are unlikely in practice. The footer's spacer-based layout absorbs width variations, and the existing `_render_cwd_label()` budget recomputation hides cwd/session labels when siblings grow.

**Footer rebudgeting:** When `ContextBar` width changes (due to token count updates), the footer must recompute the cwd/session label budgets. The `ContextBar` widget should trigger `StatusFooter.refresh_cwd_label()` when its rendered width changes, or `app.py` should call `refresh_cwd_label()` after updating context-bar values in `refresh_context()`.

**Division safety:** When `max_tokens == 0`, render `0% [0/0]` (same guard as existing code: `if self.max_tokens else 0`).

**Click behavior:** Unchanged — clicking runs `/context`.

### Part 2: System Message Injection

**File:** `claudechic/agent.py` — new `_prepare_prompt()` method

Extract a dedicated `_prepare_prompt(self, prompt: str) -> str` method that handles all prompt augmentation. This is a **pure transformation** (`str → str`) with no side effects — it reads `self.tokens`, `self.max_tokens`, and `self.permission_mode` but does not mutate state. It is called at the top of `_process_response()` and centralizes:
1. System-reminder injection (when context data is initialized)
2. Plan-mode instruction prepend (when `permission_mode == "plan"`)

This replaces the current inline plan-mode prepend in `_process_response()`.

**System-reminder format:**

```
<system-reminder>14000/200000 tokens</system-reminder>
```

- Uses **raw integers** (e.g., `14000/200000`), not human-formatted values (`14.0K/200.0K`). Raw integers are unambiguous for machine consumption and avoid coupling the agent layer to the presentation-formatting module.
- No percentage, no color, no urgency guidance — plain numbers only.
- The operator (via CLAUDE.md or other rules) is responsible for defining behavioral instructions based on these numbers. This feature provides the data; behavior is defined externally.

**Injection guard:** Add a `_context_initialized: bool` flag to `Agent`, initially `False`. Set to `True` when `refresh_context()` successfully updates token data (via the observer callback). Skip injection when `_context_initialized` is `False`. This avoids injecting the hardcoded default `MAX_CONTEXT_TOKENS` before the actual model's context window is known.

Note: `max_tokens` is initialized to `MAX_CONTEXT_TOKENS` (200,000), never 0, so a `max_tokens == 0` check would protect against an impossible state. The `_context_initialized` flag correctly gates on whether real data has been received.

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

**Solution:** Define a **narrow, specific regex** in `formatting.py` that matches **only** the token-injection pattern:

```python
TOKEN_REMINDER_PATTERN = re.compile(
    r"\n*<system-reminder>\d+/\d+ tokens</system-reminder>\n*"
)
```

This pattern matches `<system-reminder>14000/200000 tokens</system-reminder>` but does **not** match plan-mode instructions, other system-reminders, or user-typed content containing `<system-reminder>` tags. This is intentionally narrower than the generic `SYSTEM_REMINDER_PATTERN` in `widgets/content/tools.py`.

**Placement:** `formatting.py` (shared, no UI dependencies). Imported by:
- `agent.py` — used in `load_history()` to strip token tags from user message text.
- `sessions.py` — used in `load_session_messages()` and `_extract_session_info()` to strip token tags before command filtering and title extraction.

This avoids an inverted dependency (agent → widget layer) and ensures all session-file consumers see clean text.

**Stripping happens in `load_history()`** (definitively, not "or" the widget layer). The agent's in-memory `self.messages` model is the single source of truth — cleaning at the source means any future consumer of `Agent.messages` gets clean data automatically.

## Affected Files

| File | Change |
|------|--------|
| `claudechic/widgets/layout/indicators.py` | Replace `ContextBar.render()` visual bar with text format |
| `claudechic/formatting.py` | Add `TOKEN_REMINDER_PATTERN` regex |
| `claudechic/agent.py` | Add `_prepare_prompt()`, `_context_initialized` flag; refactor plan-mode prepend; strip token tags in `load_history()` |
| `claudechic/sessions.py` | Strip token tags in `load_session_messages()` and `_extract_session_info()` |
| `claudechic/app.py` | Set `_context_initialized = True` after successful `refresh_context()`; call `refresh_cwd_label()` after context-bar updates |
| `tests/test_widgets.py` | Update `test_context_bar_rendering`; add new test cases |
| `tests/test_agent.py` (new or existing) | Tests for `_prepare_prompt()`, injection, plan-mode interaction, session resume |

## Testing Strategy

### Unit Tests — ContextBar

1. **Text format** — Verify output matches `{pct}% [{used}/{max}]` for: 0 tokens, 50K/200K, 160K/200K, 200K/200K, 500K/1M.
2. **Color thresholds** — Verify dim/yellow/red style at boundaries: 0%, 49%, 50%, 79%, 80%, 100%.
3. **Split styling** — Verify bracket portion `[used/max]` is always `dim`.
4. **Division safety** — Verify `max_tokens=0` renders `0% [0/0]` without error.
5. **Existing test enhancement** — Update `test_context_bar_rendering` to assert the new text format (currently only checks percentage substrings).

### Unit Tests — System-Reminder Injection

6. **`_prepare_prompt()` basic** — Set `_context_initialized=True`, `tokens=14000`, `max_tokens=200000`; assert output starts with `<system-reminder>14000/200000 tokens</system-reminder>`.
7. **Plan-mode interaction** — Set `permission_mode="plan"`, `_context_initialized=True`; assert output contains both token reminder AND plan-mode instructions, in correct order (token first, plan second, user prompt last).
8. **Image path** — Set `pending_images` non-empty; assert the prompt passed to `_build_message_with_images()` includes the system-reminder.
9. **Injection guard** — Set `_context_initialized=False`; assert no `<system-reminder>` is prepended.
10. **Edge case: tokens=0 with initialized context** — Set `_context_initialized=True`, `tokens=0`; assert `<system-reminder>0/200000 tokens</system-reminder>` is prepended.

### Unit Tests — Session Resume

11. **Token tag stripping** — Load a mock session containing user messages with token `<system-reminder>` tags; assert they are stripped from `UserContent.text`.
12. **Plan-mode tags preserved** — Load a mock session containing user messages with plan-mode `<system-reminder>` tags; assert they are NOT stripped.
13. **Session title extraction** — Verify `_extract_session_info()` does not include token reminder in session titles.

### Integration Tests

14. **Footer displays token text** — In `test_app_ui.py`, verify `ContextBar` renders text after context refresh.
15. **Click still works** — Verify clicking `ContextBar` triggers `/context`.
16. **Narrow terminal** — Verify footer layout at 80 columns does not overflow or truncate critical elements. Verify cwd/session labels hide before ContextBar does.
17. **Footer rebudgets on context update** — Verify that updating ContextBar tokens triggers cwd/session label rebudgeting.

## Risks

- **Horizontal space on narrow terminals:** The text is wider than the old bar (~13-21 chars vs ~12 with padding). The footer has a spacer and budget recomputation that hides cwd/session labels first. Risk mitigated by integration test #16.
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

- **H1 (broad tag stripping):** Replaced generic `SYSTEM_REMINDER_PATTERN` with narrow `TOKEN_REMINDER_PATTERN` that only matches `\d+/\d+ tokens` format. Plan-mode tags and user content are preserved.
- **H2 (SDK persistence undocumented):** Added explicit SDK persistence assumption documentation.
- **M1 (session-file consumers):** Extended stripping to `sessions.py` (`load_session_messages`, `_extract_session_info`), not just `load_history()`.
- **M2 (footer rebudgeting):** Added footer rebudget trigger when ContextBar width changes.
- **M3 (regex layer violation):** Moved `TOKEN_REMINDER_PATTERN` to `formatting.py` (shared, no UI dependencies).
- **M4 (Part 3 placement):** Resolved definitively to `load_history()` (removed "or").
- **M5 (`_prepare_prompt` purity):** Documented as pure `str → str` transformation with no side effects.
- **M6 (impossible guard state):** Replaced `max_tokens == 0` guard with `_context_initialized` flag set by `refresh_context()`.
- **M7 (acceptance criteria contradiction):** Added "except when context data is not yet initialized" qualifier.
- **M8 (SDK assumption):** Documented in Part 3 and Risks.
- **L1 (narrow terminal priority):** Specified that cwd/session labels hide first; ContextBar remains visible.
- **L4 (width edge cases):** Noted `format_tokens(999999) = "1000.0K"` edge case.
- Added test #12 (plan-mode tags preserved), test #13 (session title extraction), test #17 (footer rebudgeting).

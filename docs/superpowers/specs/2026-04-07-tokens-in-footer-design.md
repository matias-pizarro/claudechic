# Tokens in Footer — Design Spec

**Date:** 2026-04-07
**Branch:** tokens-in-footer
**Status:** Draft

## Problem

The footer `ContextBar` shows a 10-character visual progress bar with an embedded percentage (e.g., `░░░32%░░░░`). This conveys urgency via color but hides the actual numbers. Users who hide the sidebar — common on narrow terminals — lose visibility into how many tokens are consumed and how large the context window is.

Separately, the Claude model itself has no awareness of its own context window saturation. It cannot adjust verbosity or suggest compaction because it never sees the numbers.

## Goals

1. **Display token counts in the footer** — Replace the visual bar with a text label showing percentage plus token counts: `32% [14.0K/200K]`.
2. **Expose context usage to the model** — Inject a `<system-reminder>` with token stats into every prompt so the model (or its operator-level instructions) can act on context saturation.

## Non-Goals

- No changes to the sidebar `AgentItem` context display (already has token counts).
- No configuration toggle for the system-reminder injection (always on).
- No color or urgency hints inside the injected system-reminder (the operator handles that).
- No changes to how tokens are fetched or tracked (`refresh_context` in `app.py`, `Agent.tokens`, `Agent.max_tokens` are untouched).

## Design

### Part 1: Footer ContextBar Text Display

**File:** `claudechic/widgets/layout/indicators.py` — `ContextBar.render()`

Replace the visual progress bar with a text string:

```
32% [14.0K/200K]
```

**Format:** `{pct}% [{used}/{max}]`
- `pct`: integer percentage (0–100), computed from `self.tokens / self.max_tokens`
- `used`: `format_tokens(self.tokens)` — e.g., `0`, `500`, `14.0K`, `1.2M`
- `max`: `format_tokens(self.max_tokens)` — e.g., `200K`, `1M`

**Color thresholds** (applied to the entire text string via Rich style):
- `< 50%` → `dim`
- `50%–79%` → `yellow`
- `≥ 80%` → `red`

These thresholds match the existing visual bar and the sidebar.

**Width:** The `ContextBar` CSS already has `width: auto`. Text length varies from ~12 chars (`0% [0/200K]`) to ~18 chars (`100% [200.0K/200K]`). This is comparable to the old 10-char bar plus padding and does not require CSS changes.

**Rendering implementation:** Replace the current `render()` method (30 lines of bar-drawing logic) with ~10 lines using `format_tokens()` from `claudechic/formatting.py` and `Text.assemble()` from Rich.

**Click behavior:** Unchanged — clicking runs `/context`.

### Part 2: System Message Injection

**File:** `claudechic/agent.py` — `Agent._process_response()`

Prepend a system-reminder to every prompt sent to the SDK:

```
<system-reminder>14.0K/200K tokens</system-reminder>
```

**Format:** `<system-reminder>{used}/{max} tokens</system-reminder>`
- `used`: `format_tokens(self.tokens)`
- `max`: `format_tokens(self.max_tokens)`
- No percentage, no color, no urgency guidance — plain numbers only.

**Injection point:** At the top of `_process_response()`, before the existing plan-mode instruction prepend. The token reminder is always prepended; plan-mode instructions are prepended additionally when in plan mode.

```python
# Always prepend context usage
prompt = f"<system-reminder>{format_tokens(self.tokens)}/{format_tokens(self.max_tokens)} tokens</system-reminder>\n" + prompt
```

**Cost:** ~15 tokens per turn. At 100 turns per session, that is ~1,500 tokens — negligible relative to a 200K window.

**Edge case — tokens=0:** On the first message of a new session, `self.tokens` is 0 and `self.max_tokens` is the default (200K). The injected reminder will read `<system-reminder>0/200K tokens</system-reminder>`. This is accurate and harmless.

## Affected Files

| File | Change |
|------|--------|
| `claudechic/widgets/layout/indicators.py` | Replace `ContextBar.render()` visual bar with text format |
| `claudechic/agent.py` | Add system-reminder prepend in `_process_response()` |

No other files change. No new files, no new dependencies, no configuration changes.

## Testing Strategy

### Unit Tests

1. **`ContextBar.render()` output** — Verify text format matches `{pct}% [{used}/{max}]` for representative values (0 tokens, mid-range, near-max, at-max).
2. **Color thresholds** — Verify dim/yellow/red style is applied at the correct percentage boundaries (0%, 49%, 50%, 79%, 80%, 100%).
3. **System-reminder injection** — Verify `_process_response()` prepends the correct `<system-reminder>` string. Mock `self.tokens` and `self.max_tokens` and assert the prompt passed to `client.query()` starts with the expected reminder.
4. **Edge cases** — tokens=0, max_tokens=0 (division-safe), tokens > max_tokens (caps at 100%).

### Integration Tests

5. **Footer displays token text** — In `test_app_ui.py`, verify the `ContextBar` widget renders text (not a visual bar) after context refresh.
6. **Click still works** — Verify clicking `ContextBar` still triggers `/context`.

## Risks

- **Horizontal space:** The text is slightly wider than the old bar on average (~14 chars vs ~12 chars with padding). The footer already handles variable-width elements via a spacer and budget recomputation. Low risk.
- **`format_tokens` import in `agent.py`:** `agent.py` already imports from `formatting.py` (`MAX_CONTEXT_TOKENS`). Adding `format_tokens` is a trivial addition. No circular dependency risk.

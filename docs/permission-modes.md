# Permission Modes

Canonical set of valid values: `Agent.PERMISSION_MODES` in `claudechic/agent.py`.
This set is enforced by assertions in `set_permission_mode()` and `_set_permission_mode_local()`.

## Mode Contract

| Mode | SDK Value | Footer Label | CSS Class | In Shift+Tab Cycle | Entry | Exit |
|------|-----------|--------------|-----------|---------------------|-------|------|
| `default` | `"default"` | Auto-edit: off | (none) | Yes | Initial / Shift+Tab | Shift+Tab |
| `acceptEdits` | `"acceptEdits"` | Auto-edit: on | `active` | Yes | Shift+Tab | Shift+Tab |
| `plan` | `"plan"` | Plan mode | `plan-mode` | Yes | Shift+Tab | Shift+Tab |
| `planSwarm` | `"plan"` | Plan swarm | `plan-swarm-mode` | No (internal) | `/plan-swarm` | Shift+Tab → default |

## Unknown Mode Handling

Behavior varies by layer:
- **`Agent.set_permission_mode()`**: Asserts against `PERMISSION_MODES` — unknown modes crash (fail-fast).
- **Footer `watch_permission_mode`**: Falls back to "Auto-edit: off" display + `log.warning()` (graceful degradation for UI robustness).
- **`action_cycle_permission_mode`**: `modes.index(current)` raises `ValueError` if current mode is not in the cycle list. Non-cycled modes (planSwarm) exit to "default" via explicit guard.

This asymmetry is intentional: the setter is strict (catches bugs early), the footer is lenient (never crashes the UI), the cycle has explicit fallback for internal modes.

## Wiring Points (ordered by dependency)

Adding a new mode requires changes in this order:

1. **`claudechic/agent.py`** — Add to `PERMISSION_MODES` set + add SDK mapping in `set_permission_mode()`
2. **`claudechic/app.py`** — Add to `action_cycle_permission_mode` modes list + `display` dict (if user-facing)
3. **`claudechic/widgets/layout/footer.py`** — Add entry to `_MODE_DISPLAY` dict
4. **`claudechic/styles.tcss`** — Add CSS class styling
5. **`claudechic/commands.py`** — Add entry command (if applicable, e.g., `/plan-swarm`)
6. **Tests** — Update `tests/test_footer.py` (mode display), `tests/test_agent.py` (SDK mapping), `tests/test_app_ui.py` (cycle), `tests/test_widgets.py` (broader widget coverage)

## Acceptance Criteria for New Mode

A mode addition is complete when:
- [ ] Mode exists in `Agent.PERMISSION_MODES`
- [ ] SDK mapping is defined in `set_permission_mode()`
- [ ] Footer displays correct label and CSS class
- [ ] Shift+Tab cycle includes mode (or explicit bypass for internal modes)
- [ ] Entry and exit paths work (verified by app-level test)
- [ ] All existing tests pass
- [ ] New tests cover the mode's specific behavior

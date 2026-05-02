# Permission Modes

Source of truth: `Agent.PERMISSION_MODES` in `claudechic/agent.py`.

## Mode Contract

| Mode | SDK Value | Footer Label | CSS Class | In Shift+Tab Cycle | Entry |
|------|-----------|--------------|-----------|---------------------|-------|
| `default` | `"default"` | Auto-edit: off | (none) | Yes | Initial / reset |
| `acceptEdits` | `"acceptEdits"` | Auto-edit: on | `active` | Yes | Shift+Tab |
| `plan` | `"plan"` | Plan mode | `plan-mode` | Yes | Shift+Tab |
| `planSwarm` | `"plan"` | Plan swarm | `plan-swarm-mode` | No (internal) | `/plan-swarm` |

## Wiring Points

Adding a new mode requires changes in all of:

1. `claudechic/agent.py` — `PERMISSION_MODES` set + `set_permission_mode()` SDK mapping
2. `claudechic/widgets/layout/footer.py` — `_MODE_DISPLAY` dict entry
3. `claudechic/app.py` — `action_cycle_permission_mode` modes list + `display` dict (if in cycle)
4. `claudechic/styles.tcss` — CSS class styling
5. `claudechic/commands.py` — entry command (if applicable)
6. Tests — `tests/test_footer.py`, `tests/test_agent.py`, `tests/test_app_ui.py`

## Unknown Mode Handling

Unknown modes fall back to "default" display with a `log.warning()` emission.
This is defensive — unknown modes should not reach the footer in normal operation.

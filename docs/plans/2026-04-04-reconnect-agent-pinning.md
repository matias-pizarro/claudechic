# Backlog: Pin _reconnect_sdk to target agent

**Date:** 2026-04-04
**Priority:** Medium
**Origin:** roborev review of footer cwd feature (commits f0a66f5, f494f69)
**Root cause commit:** 93a9667 ("Fix SDK client lifecycle to avoid race conditions", 2026-01-13)

## Problem

`_reconnect_sdk()` captures the agent at entry (`agent = self._agent`) but then calls `_replace_client()`, which reads and writes `self.client` — a property that resolves to `self._agent.client`. If the user switches agents while the reconnect's `await` calls are in-flight (`get_recent_sessions`, `_replace_client`/`ClaudeSDKClient.connect()`), the newly active agent's client can be interrupted/replaced, while the intended reconnecting agent keeps a stale client.

This was introduced in commit `93a9667` which refactored client lifecycle to avoid a different set of race conditions. The irony: the fix for races introduced a new one.

## Current mitigations (partial)

Our cwd feature added guards in commits `56be67a` and `f494f69`:

1. **Footer cwd/branch**: guarded with `if agent is self._agent:` before updating footer state after reconnect completes.
2. **History loading**: `_load_and_display_history` now accepts an explicit `agent` param so reconnect passes the captured agent, not `self._agent`.
3. **Branch refresh**: wrapped in a closure that re-checks `agent is self._agent` after the async git call completes.

These prevent the *symptoms* (wrong cwd/branch/history in footer) but not the *root cause* (wrong client on wrong agent).

## Remaining issues

### 1. `_replace_client` operates on `self._agent`, not the target agent

```python
# Current (app.py ~line 328):
async def _replace_client(self, options: ClaudeAgentOptions) -> None:
    old = self.client          # ← resolves to self._agent.client
    self.client = None         # ← sets self._agent.client = None
    ...
    self.client = new_client   # ← sets self._agent.client = new_client
```

If active agent changed between `old = self.client` and `self.client = new_client`, the new active agent loses its client and the reconnecting agent never gets the new one.

### 2. Rapid reconnect branch staleness

If the same agent reconnects twice rapidly (different cwds), the first branch-refresh task can complete after the second one starts, overwriting the branch with the stale first-cwd result. Our guard checks `agent is self._agent` but not `agent.cwd == new_cwd`.

## Proposed fix

Refactor `_replace_client` to accept a target `Agent`:

```python
async def _replace_client(self, agent: Agent, options: ClaudeAgentOptions) -> None:
    """Safely replace a specific agent's client."""
    old = agent.client
    agent.client = None
    if old:
        try:
            await old.interrupt()
        except Exception:
            pass
    new_client = ClaudeSDKClient(options)
    await new_client.connect()
    agent.client = new_client
```

Then update all callers (`_reconnect_sdk`, `_connect_agent`, etc.) to pass the target agent explicitly. This eliminates the race at the root.

For the rapid-reconnect branch issue, add `agent.cwd == new_cwd` to the guard or cancel prior branch-refresh tasks via a generation counter.

## Scope

- Refactor `_replace_client` signature (all callers)
- Refactor `self.client` property usage in reconnect paths
- Add integration test for agent-switch-during-reconnect scenario
- Estimate: ~2 hours

<!-- Generated: 2026-04-07 | Files scanned: 4 | Token estimate: ~700 -->

# Agent Layer

## Agent (agent.py, 1008 lines)

Core class owning SDK connection, message history, and state.

### Lifecycle

```
Agent(name, cwd)
  → connect(options, resume?)
    → client.connect()
    → FileIndex.refresh()
  → send(prompt, display_as?)
    → _process_response(prompt)
      → _prepare_prompt(prompt)   # augment with token reminder + plan mode
      → client.query(prompt)      # or _build_message_with_images(prompt)
      → async for message: _handle_sdk_message()
  → disconnect()
    → client.disconnect()
    → _context_initialized = False
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `update_context(tokens, max_tokens?)` | Atomically set token state + `_context_initialized = True` |
| `_prepare_prompt(prompt) → str` | Prepend system-reminder + plan-mode instructions |
| `_process_response(prompt)` | Send to SDK, process response stream |
| `_handle_permission(tool, input, ctx)` | Permission flow: auto-approve, plan-mode block, or queue UI prompt |
| `load_history(cwd?)` | Load session messages from file, strip TOKEN_REMINDER_PATTERN |
| `send(prompt, display_as?)` | Record in history + dispatch _process_response |
| `interrupt()` | Cancel current response |

### Context Tracking State

```python
self.tokens: int = 0                     # Current usage (updated by update_context)
self.max_tokens: int = MAX_CONTEXT_TOKENS # Window size (updated by update_context)
self._context_initialized: bool = False   # Gate for system-reminder injection
```

### Prompt Augmentation Order

`_prepare_prompt()` builds the final prompt:
1. Plan-mode instructions prepend (if `permission_mode == "plan"`)
2. Token system-reminder wraps outermost (if `_context_initialized`)

Result: `<system-reminder>T/M tokens</system-reminder>\n<plan-mode>...</plan-mode>\n{user prompt}`

## AgentManager (agent_manager.py, 261 lines)

Coordinates multiple concurrent agents.

| Method | Purpose |
|--------|---------|
| `create_agent(name, cwd)` | Create + connect new agent |
| `switch_to(agent_id)` | Switch active agent |
| `close_agent(agent_id)` | Disconnect + remove |
| `get_active()` | Return current agent |

Events emitted via `AgentManagerObserver`: `on_agent_created`, `on_agent_switched`, `on_agent_closed`.

## Protocols (protocols.py)

```python
class AgentObserver(Protocol):
    def on_text_chunk(agent, text, new_message, parent_id) → None
    def on_tool_use(agent, tool) → None
    def on_tool_result(agent, tool) → None
    def on_complete(agent, result) → None
    def on_status_changed(agent) → None
    def on_prompt_added(agent, request) → None
    def on_permission_mode_changed(agent) → None
    def on_error(agent, message, exception) → None
    ...
```

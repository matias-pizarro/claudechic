<!-- Generated: 2026-04-07 | Files scanned: 78 | Token estimate: ~900 -->

# Architecture

Single-app Python TUI wrapping `claude-agent-sdk` via Textual framework.

## Layers

```
CLI (__main__.py)
  │
  ▼
ChatApp (app.py, 3046 lines) ─── orchestrates everything
  │
  ├── Agent layer (no UI deps)
  │   ├── agent.py ─── SDK client, history, permissions, context tracking, prompt augmentation
  │   ├── agent_manager.py ─── multi-agent coordination, switching, lifecycle
  │   └── protocols.py ─── AgentObserver, AgentManagerObserver, PermissionHandler
  │
  ├── Pure functions (no deps)
  │   ├── formatting.py ─── tool headers, diff rendering, format_tokens, TOKEN_REMINDER_PATTERN
  │   ├── sessions.py ─── session file I/O, listing, token tag stripping
  │   ├── compact.py ─── session compaction, token estimation
  │   ├── file_index.py ─── fuzzy file search via git ls-files
  │   └── usage.py ─── OAuth API for rate limits
  │
  ├── Features (self-contained modules)
  │   ├── features/worktree/ ─── git worktree management
  │   ├── features/diff/ ─── diff review screen + widgets
  │   └── features/roborev/ ─── automated code review
  │
  ├── Screens (full-page navigation)
  │   ├── screens/chat.py ─── main chat UI (default)
  │   ├── screens/diff.py ─── diff review
  │   ├── screens/session.py ─── session browser
  │   └── screens/rewind.py ─── checkpoint rewind
  │
  └── Widgets (Textual components)
      ├── layout/ ─── structural: chat_view, sidebar, footer, indicators
      ├── content/ ─── display: message, tools, diff, todo
      ├── input/ ─── user input: autocomplete, history_search, vi_mode
      ├── primitives/ ─── building blocks: button, collapsible, scroll, spinner
      ├── reports/ ─── in-page: context grid, usage bars
      └── modals/ ─── overlays: profile, process detail
```

## Data Flow

```
User input → ChatApp._handle_prompt()
  → Agent.send(prompt)
    → Agent._prepare_prompt(prompt)  # adds <system-reminder> + plan-mode
    → client.query(augmented_prompt)
    → async for message in client.receive_response():
        → Agent._handle_sdk_message()
          → observer callbacks → UI updates
    → ResultMessage
      → ChatApp.refresh_context()
        → agent.update_context(tokens, max_tokens)
        → ContextBar.tokens = ...
```

## Session Resume Flow

```
Session file (JSONL) → load_session_messages() [strips TOKEN_REMINDER_PATTERN]
  → Agent.load_history() [strips TOKEN_REMINDER_PATTERN]
    → Agent.messages (clean in-memory model)
      → ChatView._render_full() → UI
```

## Key Patterns

- **Observer pattern**: Agent emits events via AgentObserver protocol; ChatApp handles them
- **Prompt augmentation**: `_prepare_prompt()` centralizes all prompt prepends (token reminder + plan mode)
- **Atomic context updates**: `Agent.update_context()` encapsulates tokens + max_tokens + initialized flag
- **Lifecycle safety**: `_context_initialized` resets in `disconnect()` to prevent stale data

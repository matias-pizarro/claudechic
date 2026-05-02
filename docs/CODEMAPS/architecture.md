<!-- Generated: 2026-04-21 | Files scanned: 82 | Token estimate: ~950 -->

# Architecture

Single-app Python TUI wrapping `claude-agent-sdk` via Textual framework.

## Layers

```
CLI (__main__.py)
  │
  ▼
ChatApp (app.py, ~3100 lines) ─── orchestrates everything
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
  │   ├── features/worktree/ ─── git worktree management (create, finish, cleanup, discard)
  │   │   ├── git.py ─── pure git ops: FinishInfo(frozen), validation V2-V8, branch_exists,
  │   │   │              get_local_branches, fast_forward_merge (checkout+rollback),
  │   │   │              get_rebase_finish_prompt (conditional checkout), diagnose_worktree
  │   │   └── commands.py ─── TUI handlers: _handle_finish(base_branch), concurrent guards,
  │   │                       analytics, on_response_complete_finish
  │   ├── features/diff/ ─── diff review screen + widgets
  │   └── features/roborev/ ─── automated code review
  │
  ├── MCP (mcp.py) ─── in-process MCP server for agent control
  │   └── finish_worktree ─── schema {}, base_branch via args.get(), phase-aware re-entry
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

## Worktree Finish Flow

```
/worktree finish [branch]
  → handle_worktree_command() → _handle_finish(app, base_branch)
    → get_finish_info(cwd, base_branch) ── validates V2-V8, sets needs_checkout
    → busy-agent guard (any agent BUSY in target dir?)
    → diagnose_worktree(info) → WorktreeStatus
    → determine_resolution_action(status) → ResolutionAction
      → NONE → cleanup
      → FAST_FORWARD → fast_forward_merge(info) [checkout+rollback if needs_checkout]
      → REBASE → get_rebase_finish_prompt(info, is_non_ancestor) → Claude
      → NO_FF → get_no_ff_finish_prompt(info) → Claude
    → on_response_complete_finish() → re-diagnose → loop
    → finish_cleanup(info) → remove worktree + branch
```

## Key Patterns

- **Observer pattern**: Agent emits events via AgentObserver protocol; ChatApp handles them
- **Prompt augmentation**: `_prepare_prompt()` centralizes all prompt prepends (token reminder + plan mode)
- **Frozen value objects**: `FinishInfo(frozen=True)` — immutable after construction; `FinishState` mutable
- **Phase-aware MCP re-entry**: RESOLUTION re-entry re-diagnoses; CLEANUP re-entry retries with limit
- **needs_checkout flag**: Separates "where to merge" from "do we need git checkout first"
- **Universal busy-agent guard**: Both TUI and MCP check target dir for busy agents before proceeding

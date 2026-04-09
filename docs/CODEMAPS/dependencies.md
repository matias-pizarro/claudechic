<!-- Generated: 2026-04-07 | Files scanned: 78 | Token estimate: ~700 -->

# Internal Dependencies

## Import Graph (key edges only)

```
app.py
  ├── agent.py ─── Agent, ImageAttachment, ToolUse
  ├── agent_manager.py ─── AgentManager
  ├── commands.py ─── handle_command, BARE_WORDS
  ├── formatting.py ─── MAX_CONTEXT_TOKENS, parse_context_size
  ├── sessions.py ─── get_recent_sessions, load_session_messages, get_context_from_session
  ├── mcp.py ─── set_app, create_chic_server
  ├── widgets/ ─── all layout, content, input, report widgets
  └── screens/ ─── ChatScreen, DiffScreen, SessionScreen, RewindScreen

agent.py
  ├── formatting.py ─── MAX_CONTEXT_TOKENS, TOKEN_REMINDER_PATTERN
  ├── sessions.py ─── get_plan_path_for_session
  ├── enums.py ─── AgentStatus, PermissionChoice, ToolName
  ├── permissions.py ─── PermissionRequest
  ├── file_index.py ─── FileIndex
  └── features/worktree/git.py ─── FinishState

sessions.py
  └── formatting.py ─── TOKEN_REMINDER_PATTERN

formatting.py
  └── enums.py ─── ToolName

widgets/layout/indicators.py
  ├── formatting.py ─── MAX_CONTEXT_TOKENS, format_tokens
  ├── profiling.py ─── profile, timed
  └── processes.py ─── BackgroundProcess

widgets/layout/footer.py
  ├── formatting.py ─── format_cwd, format_session_id, MIN_CWD_LENGTH, etc.
  ├── widgets/layout/indicators.py ─── CPUBar, ContextBar, ProcessIndicator
  └── widgets/input/vi_mode.py ─── ViMode

widgets/layout/sidebar.py
  ├── formatting.py ─── MAX_CONTEXT_TOKENS, format_cwd, format_tokens
  └── enums.py ─── AgentStatus
```

## External Dependencies

| Package | Purpose |
|---------|---------|
| `claude_agent_sdk` | SDK client, message types, permission types, stream events |
| `textual` | TUI framework — App, widgets, CSS, screens, reactive |
| `rich` | Text styling, syntax highlighting, markdown rendering |
| `psutil` | CPU usage monitoring (CPUBar) |
| `aiofiles` | Async file I/O for session loading |
| `posthog` | Optional analytics (opt-in) |

## Shared Constants

| Constant | Location | Used by |
|----------|----------|---------|
| `MAX_CONTEXT_TOKENS` | `formatting.py:15` | `agent.py`, `indicators.py`, `sidebar.py`, `app.py` |
| `TOKEN_REMINDER_PATTERN` | `formatting.py:22` | `agent.py`, `sessions.py` |
| `ToolName` | `enums.py` | `agent.py`, `app.py`, `formatting.py`, `compact.py`, `tools.py` |
| `AgentStatus` | `enums.py` | `agent.py`, `app.py`, `sidebar.py`, `chat_view.py` |

<!-- Generated: 2026-04-07 | Files scanned: 30 | Token estimate: ~800 -->

# Widget Map

## Hierarchy

```
ChatApp
└── ChatScreen
    ├── Horizontal #main
    │   ├── Vertical #chat-column
    │   │   ├── ChatView (widgets/layout/chat_view.py, 510 lines)
    │   │   │   ├── ChatMessage ─── user/assistant text blocks
    │   │   │   ├── ToolUseWidget ─── collapsible tool display
    │   │   │   ├── TaskWidget ─── nested Task tool content
    │   │   │   ├── AgentToolWidget ─── sub-agent tool display
    │   │   │   ├── CollapsedTurn ─── compacted old turns
    │   │   │   └── ThinkingIndicator ─── animated spinner
    │   │   └── Vertical #input-container
    │   │       ├── ImageAttachments
    │   │       ├── ChatInput / SelectionPrompt / QuestionPrompt / ModelPrompt
    │   │       └── TextAreaAutoComplete
    │   └── Vertical #right-sidebar
    │       ├── AgentSection ─── agent list with status indicators
    │       ├── TodoPanel ─── task tracking
    │       └── ProcessPanel ─── background process list
    └── StatusFooter (widgets/layout/footer.py, 311 lines)
        ├── ViModeLabel ─── INSERT/NORMAL/VISUAL
        ├── ModelLabel ─── clickable model name
        ├── PermissionModeLabel ─── auto-edit toggle
        ├── ProcessIndicator ─── background process count
        ├── ContextBar ─── token usage with gradient bg
        ├── CPUBar ─── CPU usage percentage
        ├── cwd-label ─── working directory (hidden when narrow)
        ├── session-label ─── session ID (hidden when narrow)
        └── branch-label ─── git branch
```

## Footer ContextBar

`widgets/layout/indicators.py` — `ContextBar` class

Renders token usage as text with gradient background:
```
 32% [14.0K/200.0K]
```

- **Gradient**: `_context_bar_color(pct)` → green (#117733) → orange (#CC7700) → red (#CC3333) → crimson (#661111)
- **Segments**: percentage (fg on bg) + space + brackets (fg_dim on bg) + padding
- **Reactive props**: `tokens`, `max_tokens` — set by `app.py` `refresh_context()`
- **Click**: runs `/context` command
- **Rebudgeting**: width changes trigger `StatusFooter.refresh_cwd_label()` via `call_after_refresh`

## Widget Categories

| Directory | Purpose | Key widgets |
|-----------|---------|-------------|
| `base/` | Protocols, base classes | `ClickableLabel`, `BaseToolWidget`, `ToolWidget` protocol |
| `primitives/` | Building blocks | `Button`, `QuietCollapsible`, `AutoHideScroll`, `Spinner` |
| `content/` | Content display | `ChatMessage`, `ToolUseWidget`, `DiffWidget`, `TodoPanel` |
| `input/` | User input | `TextAreaAutoComplete`, `HistorySearch`, `ViHandler` |
| `layout/` | Structural containers | `ChatView`, `AgentSidebar`, `StatusFooter`, `ContextBar` |
| `reports/` | In-page reports | `ContextReport` (2D grid), `UsageReport`, `UsageBar` |
| `modals/` | Modal overlays | `ProfileModal`, `ProcessModal`, `ProcessDetailModal` |
| `prompts.py` | Prompt overlays | `SelectionPrompt`, `QuestionPrompt`, `ModelPrompt`, `WorktreePrompt` |

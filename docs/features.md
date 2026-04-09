# Features

Beyond multi-agent and styling, Claude Chic includes several quality-of-life features.

## Shell Access

Run shell commands without leaving the UI:

```bash
!git status          # Quick inline command
!ls -la              # Output displayed in chat
```

```bash
!nvim README.md      # Interactive command
!git log             # Drops into native shell
```

<video src="https://github.com/user-attachments/assets/85f3dbe0-9a88-436e-aa9d-c1ba012c1f0e" autoplay loop muted playsinline></video>

Or use the explicit form:

```bash
/shell git diff      # Same as !git diff
/shell -i htop       # Interactive mode (suspends TUI)
/shell               # Opens interactive shell
```

**Inline mode** captures output and displays it in the chat. **Interactive mode** (`-i` flag or no command) suspends the TUI and gives you a real terminal—useful for commands that need interactivity like `vim`, `htop`, or `git rebase -i`.

## Diff Review

Review uncommitted changes before asking Claude to commit:

```bash
/diff                # Compare working tree to HEAD
/diff main           # Compare to specific branch/commit
```

Opens a full-screen diff view with syntax highlighting. You can add comments in the input area—when you submit, Claude sees both the diff and your comments. Press `Escape` to return to chat without sending.

<video src="https://github.com/user-attachments/assets/3c0c262b-2a23-4486-92c4-b97705a0819d" autoplay loop muted playsinline></video>

## Vim Mode

Toggle vi-style keybindings for the input area:

```bash
/vim                 # Toggle vim mode on/off
```

When enabled, the input supports vi normal/insert modes. Setting persists across sessions.

## Background Processes

Claude sometimes runs long-running commands (builds, tests, servers). Track them:

```bash
/processes           # Show modal with all background processes
```

The process panel in the sidebar shows active processes. Click to view details or kill runaway processes.

## Analytics

Claude Chic collects anonymous usage analytics to help improve the project. You can opt out:

```bash
/analytics           # Show current status
/analytics opt-out   # Disable analytics
/analytics opt-in    # Re-enable analytics
```

Analytics are **opt-in by default** for new installations. Data collected includes feature usage patterns (which commands are used) but never conversation content.

## Context Awareness

The footer displays real-time context window usage as text with a color-coded background:

```
 32% [14.0K/200.0K]
```

The background uses a smooth gradient that intensifies with usage:
- **0%** — Deep green (`#117733`)
- **30%** — Orange (`#CC7700`)
- **50%** — Red (`#CC3333`)
- **100%** — Dark crimson (`#661111`)

Click the context bar to run `/context` for detailed token breakdown.

Additionally, every prompt sent to Claude includes a system-reminder with the current token count (e.g., `<system-reminder>14000/200000 tokens</system-reminder>`). This gives the model awareness of context window pressure, allowing operator instructions to guide behavior at different saturation levels.

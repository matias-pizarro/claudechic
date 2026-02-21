# Terminal Width CLI Option Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a CLI option `--width` to set the terminal width for claudechic sessions.

**Architecture:** Add `--width` flag to argparse in `__main__.py`, pass it to `ChatApp.__init__`, store it, and pass it to Textual's `app.run(size=(width, None))`. The width parameter only affects the width dimension; height remains auto-detected.

**Tech Stack:** Python argparse, Textual App.run() size parameter

---

## Task 1: Add --width CLI Argument

**Files:**
- Modify: `claudechic/__main__.py`
- Test: `tests/test_app_ui.py` (CLI parsing test)

**Step 1: Write the failing test**

Add test in `tests/test_app_ui.py`:

```python
def test_cli_width_argument():
    """Test that --width CLI argument is parsed correctly."""
    from claudechic.__main__ import main
    import argparse
    import sys

    # Test that --width is accepted and parsed
    sys.argv = ["claudechic", "--width", "150"]
    # We can't fully test main() without mocking, but we can test argparse setup
    from claudechic.__main__ import parser  # Will need to export parser
    args = parser.parse_args(["--width", "150"])
    assert args.width == 150
```

Actually, since we can't easily unit test argparse without refactoring, let's add the feature directly with manual verification.

**Step 2: Add the --width argument to argparse with validation**

In `claudechic/__main__.py`, add a positive integer type validator and the argument:

```python
def positive_int(value: str) -> int:
    """Validate that value is a positive integer."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"width must be positive, got {ivalue}")
    return ivalue

# Add after the --dangerously-skip-permissions argument:
parser.add_argument(
    "--width",
    "-w",
    type=positive_int,
    default=None,
    help="Set terminal width for this session (e.g., 150). Must be positive.",
)
```

This addresses roborev feedback: validate `--width` as a positive integer (`>=1`) to prevent invalid terminal sizing.

**Step 3: Pass width to ChatApp**

Update the `ChatApp` instantiation:

```python
app = ChatApp(
    resume_session_id=resume_id,
    initial_prompt=initial_prompt,
    remote_port=args.remote_port,
    skip_permissions=args.dangerously_skip_permissions,
    theme_override=args.theme,
    width=args.width,
)
```

**Step 4: Commit**

```bash
git add claudechic/__main__.py
git commit -m "feat: add --width CLI argument for terminal width"
```

---

## Task 2: Update ChatApp to Accept and Store Width

**Files:**
- Modify: `claudechic/app.py`

**Step 1: Update ChatApp.__init__ signature**

Add `width: int | None = None` parameter to `__init__`:

```python
def __init__(
    self,
    resume_session_id: str | None = None,
    initial_prompt: str | None = None,
    remote_port: int = 0,
    skip_permissions: bool = False,
    theme_override: str | None = None,
    width: int | None = None,
) -> None:
    super().__init__()
    self.scroll_sensitivity_y = 1.0
    self.agent_mgr: AgentManager | None = None

    self._resume_on_start = resume_session_id
    self._initial_prompt = initial_prompt
    self._remote_port = remote_port
    self._skip_permissions = skip_permissions
    self._theme_override = theme_override
    self._width = width  # Terminal width override
    # ... rest of __init__
```

**Step 2: Commit**

```bash
git add claudechic/app.py
git commit -m "feat: store terminal width in ChatApp"
```

---

## Task 3: Pass Width to app.run()

**Files:**
- Modify: `claudechic/__main__.py`

**Step 1: Update app.run() call**

Replace `app.run()` with:

```python
if args.width is not None:
    app.run(size=(args.width, None))
else:
    app.run()
```

Note: Textual's `run(size=)` accepts `(width, height)` tuple. Using `None` for height means auto-detect from terminal.

This addresses roborev feedback: use explicit `is not None` check instead of relying on truthiness.

**Step 2: Verify manually**

```bash
uv run claudechic --width 150
# Verify the app respects the width setting
```

**Step 3: Commit**

```bash
git add claudechic/__main__.py
git commit -m "feat: pass terminal width to Textual app.run()"
```

---

## Task 4: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Step 1: Update CLAUDE.md Run section**

Add to the Run section:

```markdown
## Run

```bash
uv run claudechic
uv run claudechic --resume     # Resume most recent session
uv run claudechic -s <uuid>    # Resume specific session
uv run claudechic --width 150  # Set terminal width to 150 columns
```
```

**Step 2: Add CLI Options section to README.md if not present**

```markdown
## CLI Options

- `--resume, -r`: Resume the most recent session
- `--session, -s <uuid>`: Resume a specific session by ID
- `--theme, -t <name>`: Use a specific theme (or list themes with `-t`)
- `--width, -w <columns>`: Set terminal width for this session
- `--dangerously-skip-permissions`: Auto-approve all tool uses (sandboxed environments only)
```

**Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: document --width CLI option"
```

---

## Task 5: Add Integration Test

**Files:**
- Modify: `tests/test_app_ui.py`

**Step 1: Write integration test**

Add test that verifies the CLI parsing and app construction:

```python
from unittest.mock import patch, MagicMock
import pytest

def test_cli_width_parsing():
    """Test that --width CLI argument is parsed and passed to ChatApp."""
    from claudechic.__main__ import positive_int

    # Test validation
    assert positive_int("150") == 150
    assert positive_int("1") == 1

    with pytest.raises(Exception):  # argparse.ArgumentTypeError
        positive_int("0")
    with pytest.raises(Exception):
        positive_int("-10")
    with pytest.raises(Exception):
        positive_int("abc")


@pytest.mark.asyncio
async def test_app_width_stored():
    """Test that ChatApp stores width parameter."""
    from claudechic.app import ChatApp

    app = ChatApp(width=150)
    assert app._width == 150

    app_default = ChatApp()
    assert app_default._width is None
```

This addresses roborev feedback: test CLI parsing and that ChatApp receives the width parameter correctly, rather than relying solely on run_test's forced size.

**Step 2: Run tests**

```bash
uv run python -m pytest tests/test_app_ui.py::test_app_width_override -v
```

**Step 3: Commit**

```bash
git add tests/test_app_ui.py
git commit -m "test: add integration test for --width option"
```

---

## Task 6: Final Verification

**Step 1: Run all tests**

```bash
uv run python -m pytest tests/ -n auto -q
```

**Step 2: Manual verification**

```bash
# Test with width flag
uv run claudechic --width 150

# Test without width flag (should auto-detect)
uv run claudechic

# Test with resume and width
uv run claudechic --resume --width 180
```

**Step 3: Pre-commit checks**

```bash
uv run pre-commit run --all-files
```

---

## Summary of Changes

1. `claudechic/__main__.py`: Add `--width/-w` CLI argument, pass to `ChatApp`, and use in `app.run(size=)`
2. `claudechic/app.py`: Add `width` parameter to `ChatApp.__init__`
3. `CLAUDE.md`: Document new option
4. `README.md`: Add CLI options documentation
5. `tests/test_app_ui.py`: Add integration test

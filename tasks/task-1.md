# Task 1: Build q0t-telegram-mcp

## What we're building

A Telegram MCP server in Python. It exposes Telegram actions as MCP tools so AI assistants (like Claude Code) can interact with Telegram.

## Why not just use the existing Go implementation?

Reference: https://github.com/chaindead/telegram-mcp

It has two bugs:
1. `tg_send` calls `MessagesSaveDraft` instead of actually sending — messages never arrive
2. The MCP tool advertises parameter `to` but the Go struct uses JSON tag `name` — so the parameter is silently ignored and messages always fail

We fix both.

## MCP tools to implement

| Tool | Description | Parameter names |
|------|-------------|-----------------|
| `tg_me` | Get current account info | — |
| `tg_dialogs` | List dialogs | `unread_only: bool`, `limit: int`, `offset: int` |
| `tg_dialog` | Get messages from a dialog | `name: str`, `limit: int`, `offset: int` |
| `tg_send` | Send a message | `name: str`, `text: str` |
| `tg_read` | Mark dialog as read | `name: str` |

`name` is the consistent parameter across all tools that reference a dialog — no `to`, no `dialog_name`, always `name`.

`offset` enables pagination — Telethon supports it natively on both `get_dialogs()` and `get_messages()`.

## Transport

stdio only. This is the standard for local Claude Code integration.
HTTP/SSE is out of scope for v1.

## Invariants that must hold

- `tg_send` uses `telethon.send_message()`, never anything draft-related
- All dialog-referencing parameters are named `name`
- All code is async
- Tests never make real Telegram API calls

## Execution order

Agents have dependencies — do not start a step until its inputs exist:

```
ARCHITECT
    └── produces: docs/architecture.md
            │
            ├── IMPLEMENTER      (needs: docs/architecture.md)
            │       └── produces: src/tg_mcp/
            │
            └── TESTER phase 1   (needs: docs/architecture.md, parallel with IMPLEMENTER)
                    └── produces: tests/ — skeletons only, no implementation inside test functions
                            │
                            └── TESTER phase 2   (needs: src/tg_mcp/ complete)
                                    └── fills in test logic, all tests must pass
                                            │
                                            └── DOCUMENTER  (needs: tests passing)
                                                    └── produces: README.md, docs/, Obsidian notes
```

## Agent tasks

### ARCHITECT
Read your role in `agents/ARCHITECT.md`.

Research the `mcp` Python SDK and `telethon` library, then design the full class architecture.
Write your output to `docs/architecture.md`.

For inspiration, study the reference Go implementation: https://github.com/chaindead/telegram-mcp
It has the right feature set but two bugs to avoid repeating:
1. `tg_send` calls `MessagesSaveDraft` instead of `SendMessage` — messages silently never arrive
2. MCP tool exposes parameter `to` but Go struct has JSON tag `name` — parameter is silently ignored

The design must cover:
- Class diagram
- All classes with methods and signatures
- Data models (UserInfo, Dialog, Message)
- Each MCP tool's parameter contract and what internal method it calls
- Session and auth flow
- Error handling strategy

### IMPLEMENTER
Read your role in `agents/IMPLEMENTER.md`.

Check that `docs/architecture.md` exists before starting. If it doesn't — stop.

Implement the project based on `docs/architecture.md`.
Write code to `src/tg_mcp/`.
`pyproject.toml` already exists — add dependencies if needed but don't restructure it.

### TESTER — phase 1 (parallel with IMPLEMENTER)
Read your role in `agents/TESTER.md`.

Check that `docs/architecture.md` exists before starting. If it doesn't — stop.

Write test skeletons to `tests/`. Do not implement test logic yet — `src/` doesn't exist.

What to do in phase 1:
- Study Telethon docs to understand what each API call returns (real shapes of response objects)
- Write test classes and test function names that reflect every behaviour to verify
- Write a docstring on each test function describing exactly what it tests and what the expected outcome is
- Leave the function body as `pass` or `pytest.mark.skip`
- Write `tests/conftest.py` with mock fixtures based on real Telethon response shapes

### TESTER — phase 2 (after IMPLEMENTER is done)
Check that `src/tg_mcp/` is complete before starting. If it isn't — stop.

Fill in the test logic for all skeletons written in phase 1.

Include a regression test that explicitly asserts `tg_send` never calls anything draft-related,
and that its parameter is named `name`.

Coverage must be above 80% — verify with `pytest --cov=tg_mcp --cov-report=term-missing`.

### DOCUMENTER
Read your role in `agents/DOCUMENTER.md`.

Check that `src/tg_mcp/` is complete and `pytest` passes before starting. If not — stop.

Write `README.md` and add docstrings to all public methods in `src/tg_mcp/`.

Store documentation in two places:
- `docs/` in this project (English)
- Obsidian vault at `/projects/telegram-mcp/` (Russian) — create notes per topic as needed

README must cover:

**Installation & usage (uvx — no repo clone needed):**
```bash
# First-time auth
uvx q0t-telegram-mcp auth --phone +1234567890

# With 2FA
uvx q0t-telegram-mcp auth --phone +1234567890 --password your_2fa_password
```

**Claude Code `.mcp.json`:**
```json
{
  "mcpServers": {
    "telegram": {
      "command": "uvx",
      "args": ["q0t-telegram-mcp"],
      "env": {
        "TG_APP_ID": "your_app_id",
        "TG_API_HASH": "your_api_hash"
      }
    }
  }
}
```

**Required environment variables:**
| Variable | Where to get it |
|----------|----------------|
| `TG_APP_ID` | https://my.telegram.org/apps |
| `TG_API_HASH` | https://my.telegram.org/apps |

**Publishing to PyPI** (for maintainer):
```bash
uv build
uv publish
```

For `uvx` to work, `pyproject.toml` must have:
```toml
[project.scripts]
q0t-telegram-mcp = "tg_mcp:main"
```
Make sure IMPLEMENTER added this.

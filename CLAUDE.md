# q0t-telegram-mcp

A production-quality Telegram MCP server in Python that **actually sends messages**.

## Why This Exists

The reference Go implementation (`@chaindead/telegram-mcp`) has two critical bugs:
1. `tg_send` calls `MessagesSaveDraft` instead of actually sending — messages never arrive
2. Parameter naming inconsistency: tool advertises `to` but struct uses `name` JSON tag

We fix both and build something better.

## Tech Stack

See `pyproject.toml`. Package manager is `uv`.

## Project Structure

```
q0t-telegram-mcp/
├── CLAUDE.md
├── agents/
│   ├── ARCHITECT.md
│   ├── IMPLEMENTER.md
│   ├── TESTER.md
│   └── DOCUMENTER.md
├── docs/
│   └── architecture.md      ← written by ARCHITECT agent
├── src/
│   └── tg_mcp/
│       ├── __init__.py       ← main() entry point, CLI dispatch (click)
│       ├── auth.py           ← interactive auth flow
│       ├── client.py         ← TelegramClientWrapper (Telethon)
│       ├── config.py         ← Config dataclass, env + session path
│       ├── models.py         ← UserInfo, Dialog, Message dataclasses
│       └── server.py         ← FastMCP instance, tool registrations
├── tasks/
│   └── task-1.md
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_client.py
│   ├── test_models.py
│   └── test_server.py
├── pyproject.toml
└── README.md
```

## MCP Tools (Required)

| Tool | Description | Key fix vs reference |
|------|-------------|----------------------|
| `tg_me` | Get current account info | — |
| `tg_dialogs` | List dialogs, optional unread filter | — |
| `tg_dialog` | Get messages from a dialog | — |
| `tg_send` | **Actually send** a message | Use `SendMessage`, not `SaveDraft` |
| `tg_read` | Mark dialog as read | — |

## Agent Workflow

Task definitions and execution order are in `tasks/`.
Each agent reads their role from `agents/<ROLE>.md` and their task from the assigned task file.

## Rules

- Type hints everywhere
- All code is async
- Tests must not make real network calls
- Never commit until all tests pass

## After finishing your task

Every agent must do this before ending the session:
- Update your own `agents/<ROLE>.md` with anything you learned that would help next time (gotchas, useful patterns, decisions made)
- If the project structure changed or something in CLAUDE.md is now outdated, fix it

## Rule for orchestrator (tech lead)

If you directly modify any code — even a small fix — you **must** spawn a DOCUMENTER agent
afterwards to sync the docs. Do not skip this step.

DOCUMENTER's job: ensure `README.md`, `docs/`, and any Obsidian notes reflect the actual
current behaviour of the code. If the code changed, the docs are stale until DOCUMENTER runs.

Example: you fix auth to accept `--app-id` / `--api-hash` CLI args in addition to env vars →
spawn DOCUMENTER → it updates the README auth section to show both options.

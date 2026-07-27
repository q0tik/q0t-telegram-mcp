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
│       ├── check.py          ← `check` subcommand: session liveness
│       ├── client.py         ← TelegramClientWrapper (Telethon)
│       ├── config.py         ← Config dataclass, env + session path
│       ├── models.py         ← UserInfo, Dialog, Message dataclasses
│       └── server.py         ← FastMCP instance, tool registrations
├── tasks/
│   └── task-1.md
├── CHANGELOG.md
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_check.py
│   ├── test_cli.py
│   ├── test_client.py
│   ├── test_config.py
│   ├── test_http_integration.py
│   ├── test_models.py
│   ├── test_readonly.py
│   ├── test_server.py
│   ├── test_server_startup.py
│   ├── test_shared_client.py
│   └── test_transport.py
├── pyproject.toml
└── README.md
```

## MCP Tools

The full list, with parameters and return shapes, lives in `README.md` — keep it there rather
than duplicating it here. The two that carry the project's reason for existing:

| Tool | Description | Key fix vs reference |
|------|-------------|----------------------|
| `tg_send` | **Actually send** a message | Use `SendMessage`, not `SaveDraft` |
| `tg_dialog` | Get messages from a dialog | Parameter names match what the tool advertises |

Read tools (`tg_me`, `tg_dialogs`, `tg_dialog`, `tg_search`, `tg_download`) and write tools
(everything else) are registered separately: `TG_READONLY` omits the writers entirely. When
adding a tool, put it in `READ_TOOLS` or `WRITE_TOOLS` in `server.py` — a test fails if it is
in neither.

## Transports

stdio is the default. `--transport http` serves streamable HTTP so several clients share one
process — and therefore one Telethon session, which is the only safe arrangement (two
processes on one session file give `database is locked`; copies of the file give
`AUTH_KEY_DUPLICATED` and a real logout).

**The trap:** FastMCP enters the lifespan per *MCP session*, not per process, so anything
built inside `lifespan` is built once per connected client. The Telegram client therefore
lives in the module-level `SharedTelegramClient`, not in `lifespan`. Do not move it back.

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

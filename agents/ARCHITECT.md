# Role: Architect

You design software systems before anyone writes code.

Your job is to think, research, and produce a written design that others can implement without guessing. You do not write implementation code.

## How you work

1. Read the task you were given
2. Research any unfamiliar libraries or APIs before designing
3. Make explicit decisions — don't leave things ambiguous
4. Write your output to the file specified in the task

## What good output looks like

- Class names, method signatures, and parameter names are decided and consistent
- Data models are defined (what fields, what types)
- Interfaces between components are clear (who calls whom, with what arguments)
- Error handling strategy is stated, not implied
- Another agent can read your output and implement without asking questions

## After finishing

- Update this file with any research findings, useful patterns, or decisions worth remembering
- Update `CLAUDE.md` if the project structure or tech stack changed from what's written there

---

## Research Findings (2026-03-08)

### MCP Python SDK (`modelcontextprotocol/python-sdk`)

- Library ID for Context7: `/modelcontextprotocol/python-sdk`
- Use `FastMCP` from `mcp.server.fastmcp`; import `Context` from the same module
- Lifespan pattern: decorate an async context manager with `@asynccontextmanager`, yield a typed dataclass, pass to `FastMCP("name", lifespan=fn)`
- Access lifespan context in tools via: `ctx.request_context.lifespan_context`
- Tool context type hint: `ctx: Context[ServerSession, AppContext]` (import `ServerSession` from `mcp.server.session`)
- Tool functions decorated with `@mcp.tool()` — parameters are inferred from the function signature
- `mcp.run()` with no arguments defaults to stdio transport
- MCP SDK v2 moved `ServerRequestContext` vs `ClientRequestContext`; check version if `RequestContext` import fails

### Telethon (`telethon>=1.36.0`)

- Library ID for Context7: `/websites/telethon_dev_en_stable`
- `TelegramClient(session_path_str, api_id, api_hash)` — session path has no extension; Telethon appends `.session`
- `await client.get_dialogs()` returns a `TotalList` of `Dialog` custom objects with these fields:
  - `id` (int, marked entity ID, unique)
  - `name` (str, display name)
  - `title` (str, alias for name)
  - `unread_count` (int)
  - `unread_mentions_count` (int)
  - `is_user`, `is_group`, `is_channel` (bool)
  - `message` (last Message object, may be None)
  - `date` (datetime of last message)
  - `pinned`, `archived`, `folder_id`
  - `entity`, `input_entity`
- `get_dialogs()` does NOT support integer `offset` directly; it uses `offset_date`, `offset_id`, `offset_peer`. For integer-based pagination, fetch all and slice manually.
- `await client.get_messages(entity, limit=N, add_offset=N)` — `add_offset` works for integer pagination
- `Message` object fields (from Telethon custom class, inherits `ChatGetter` and `SenderGetter`):
  - `id` (int)
  - `date` (datetime)
  - `message` (str, the text — note: attribute is `.message` not `.text`, but `.text` is also an alias)
  - `out` (bool, True if outgoing)
  - `sender` — from `SenderGetter`; may need `await m.get_sender()` for full object
  - `sender_id` (int, marked sender ID)
  - Use `telethon.utils.get_display_name(sender)` to get human-readable name
- `await client.send_message(entity, text)` — CORRECT send method (MTProto `SendMessage`)
- `await client.send_read_acknowledge(entity)` with no message arg marks all messages as read
- `get_entity(name)` accepts username strings (`"@username"` or `"username"`), phone numbers, or entity objects

### Reference Go Implementation Bugs Confirmed

Fetched `internal/tg/draft.go` from https://github.com/chaindead/telegram-mcp:
- **Bug 1**: `SendDraft` calls `api.MessagesSaveDraft(...)` — confirmed, never calls `SendMessage`
- **Bug 2**: `serve.go` registers the tool with some parameter naming inconsistency (struct uses `Name` JSON tag)
- The Go reference has `tg_dialogs` with only `Offset` and `OnlyUnread` — our design adds `limit` for full pagination control, which is an improvement

### Key Design Decisions

1. Session path: `Path.cwd() / ".telegram-mcp" / "session"` (no extension) — Telethon creates `session.session` on disk
2. `get_dialogs` with integer offset: fetch all with `limit=None`, filter, then slice `[offset:offset+limit]`
3. `get_messages` with offset: use `add_offset` parameter — Telethon supports this natively
4. Entity resolution: use `client.get_entity(name)` which accepts usernames, phone numbers, and peer objects
5. Error wrapping: Telethon lookup errors become `ValueError("Dialog not found: {name}")` so tools can return `{"error": ...}` gracefully
6. Auth: `SessionPasswordNeededError` triggers 2FA prompt; `getpass.getpass()` for secure password input
7. CLI: `click` with `invoke_without_command=True` pattern for default-to-server behavior
8. `click` must be added to `[project.dependencies]` in `pyproject.toml`

### Gotchas

- `Message.text` and `Message.message` are both valid aliases in Telethon; use `.text` for clarity
- `get_sender()` is async and may make a network call if sender not cached — acceptable for our use case
- `send_read_acknowledge(entity)` with no message marks ALL messages as read (desired behavior for `tg_read`)
- Telethon `Dialog.date` is a `datetime` object, not a string — call `.isoformat()` when serializing
- `TelegramClient` session path: pass the extensionless string; Telethon auto-appends `.session`
- FastMCP tool functions can be `async def` — prefer async for all tools since client calls are async
- `mcp.run()` blocks — call it at end of `run_server()`, not inside an async function

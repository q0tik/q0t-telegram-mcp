# Role: Implementer

You write working code based on a design someone else produced.

You do not invent architecture — if the design is unclear or missing, you stop and say so instead of guessing.

## How you work

1. Read the architecture document specified in your task
2. If something is ambiguous or missing in the design, flag it — don't improvise
3. Implement exactly what is designed, nothing more
4. Run existing tests if they exist; do not break them

## What good output looks like

- Code matches the architecture document exactly (class names, method signatures, parameter names)
- No undocumented deviations from the design
- Code is async where the design specifies async
- Dependencies are declared in pyproject.toml

## After finishing

- Update this file with any patterns, pitfalls, or library quirks you ran into
- Update `CLAUDE.md` if the project structure changed

---

## Notes from implementation (2026-03-08)

### MCP SDK import paths (updated 2026-08-03, mcp 2.0)
- `MCPServer` and `Context` live in `mcp.server.mcpserver`.
- Import as: `from mcp.server.mcpserver import MCPServer, Context`
- **Superseded:** `mcp.server.fastmcp` and the name `FastMCP` were removed in mcp 2.0. Code
  written against them does not import at all. The dependency is pinned to `mcp==2.0.0`.

### Lifespan + AppContext
- The lifespan function must be an `@asynccontextmanager` that `yield`s the context object.
- `AppContext` does NOT need to be frozen — it's just passed through the lifespan context.
- Access the client in tools via: `ctx.request_context.lifespan_context.client`

### Telethon quirks
- `TelegramClient` constructor takes `session` as first arg (a string path, no extension). Telethon appends `.session` automatically.
- `get_dialogs(limit=None)` fetches all dialogs — can be slow for large accounts.
- `send_read_acknowledge(entity)` is the correct method for marking messages read.
- `get_display_name(sender)` from `telethon.utils` returns an empty string for anonymous/deleted accounts — consider converting `""` to `None` as done in client.py.
- `UsernameNotOccupiedError` must be imported from `telethon.errors` to catch it alongside `ValueError`.

### pyproject.toml structure
- The `[project.scripts]` section must come WITHIN the `[project]` table logically but in TOML it can be a separate top-level section. Place it directly after the `dependencies` list for readability.
- `[dependency-groups]` is for dev/test extras — leave it as-is.

### click CLI pattern for default subcommand
- Use `@click.group(invoke_without_command=True)` + `@click.pass_context` on the group.
- Check `ctx.invoked_subcommand is None` to run the default action (MCP server).
- This is the standard pattern since click has no native "default subcommand" support.

---

## Notes from implementation (2026-03-09)

### Adding optional parameters to existing APIs
- When adding optional parameters (`dialog_id`, `username`) alongside an existing required parameter (`name`), make all three optional (`= None`) in both client and server layers.
- Add a `ValueError` guard at the server layer when all three are `None` (not in the client — the client's `_resolve_entity` already raises at the bottom).
- Update every existing test that calls the old mandatory-`name` API; they need keyword argument style or the new signature will reject them.

### Telethon `get_entity()` does NOT accept display names
- `get_entity(str)` interprets the string as a @username or phone number. Passing a human display name like `"Alice Smith"` raises `ValueError` or `UsernameNotOccupiedError`.
- Name-based resolution must use `iter_dialogs()` and scan `dialog.name` — never pass a display name to `get_entity()`.

### Mocking `iter_dialogs()` as an async generator
- `iter_dialogs()` is an async generator; returning a list from `AsyncMock` won't work.
- Define a real `async def _fn(): yield item` inside the fixture and use `MagicMock(side_effect=lambda: _fn())` so each call returns a fresh async generator.
- The conftest needs a dialog with `.name` matching what the tests call with (e.g. `"Alice"`) and a `.entity` attribute for the entity object.

### Keeping conftest fixtures in sync with model changes
- Adding a required field to a frozen dataclass breaks any test that instantiates it without the new field — grep all test files for the dataclass name and update every constructor call.
- The conftest `mock_telegram_dialog` fixture should also grow a `.entity` attribute when `get_dialogs()` starts using `d.entity` — without it, `getattr(d.entity, "username", None)` returns a `MagicMock` (not `None`), which is acceptable but worth noting.

### `_resolve_entity` signature change requires updating all callers
- After changing `_resolve_entity(name: str)` to `_resolve_entity(dialog_id, username, name)`, all three public methods (`get_messages`, `send_message`, `mark_read`) must be updated to pass keyword arguments through.
- Similarly, the server tools must pass all three selector kwargs to the client methods.
- Tests that assert on mock call arguments (e.g. `assert_called_once_with(name="Alice")`) must be updated to match the new call signature.

### Telethon connection state and retries (0.11.1)
- `connection_retries` defaults to 5 and governs the *background auto-reconnect* too: when it runs out, Telethon marks the client disconnected for good and every request raises `Cannot send requests while disconnected`. The server's client uses `-1` (infinite, per `telethon.helpers.retry_range`). Check the installed source under `.venv/lib/python*/site-packages/telethon/network/mtprotosender.py` before relying on retry semantics.
- `TelegramClient.is_connected()` is user intent (`_user_connected`), True during a reconnect. The real link is `client._sender._transport_connected()` (private). `connection_state()` combines both.
- Cross-cutting tool behaviour goes in `_reconnect_first`-style wrappers applied in `register_tools`, not in each tool. `functools.wraps` is enough for MCPServer to derive the same schema and find `ctx`. HTTP-integration fakes are plain `MagicMock`s, so give them every method the wrapper now calls (`ensure_connected`, `connection_state`), or `/health` fails with a JSON-serialisation 500.

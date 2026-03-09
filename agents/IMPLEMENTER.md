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

### FastMCP import paths
- `FastMCP` and `Context` both live in `mcp.server.fastmcp` (not `mcp` directly).
- Import as: `from mcp.server.fastmcp import FastMCP, Context`

### FastMCP lifespan + AppContext
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

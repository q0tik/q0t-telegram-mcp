# Architecture: q0t-telegram-mcp

## 1. Overview

`q0t-telegram-mcp` is a Python MCP server that exposes Telegram operations as MCP tools accessible to AI assistants like Claude Code. It uses `telethon` to communicate with Telegram's MTProto API and the `mcp` Python SDK (`MCPServer`, from `mcp.server.mcpserver`) to handle the MCP protocol over stdio, streamable HTTP, or SSE.

Since 0.10.0 the SDK is `mcp` 2.x. The class formerly known as `FastMCP` — and its module
`mcp.server.fastmcp` — no longer exist upstream; `MCPServer` is the same object under a new
name and takes the same constructor arguments, so `MCPServer("q0t-telegram-mcp",
lifespan=lifespan)` reads exactly as the old call did. What did change is where the HTTP
listen address and the DNS-rebinding policy live (§7) and how often the lifespan is
entered (§7).

### Components

| Component | File | Purpose |
|-----------|------|---------|
| Entry point | `src/tg_mcp/__init__.py` | CLI dispatch: `auth` / `check` subcommands vs MCP server mode |
| MCP server | `src/tg_mcp/server.py` | `MCPServer` instance, conditional tool registration (`TG_READONLY`), stdio/HTTP transports, process-wide Telegram client |
| Telegram client wrapper | `src/tg_mcp/client.py` | Async wrapper around `TelegramClient` |
| Data models | `src/tg_mcp/models.py` | `UserInfo`, `Dialog`, `Message` dataclasses |
| Media grouping | `src/tg_mcp/media.py` | Splits a file list into Telegram-legal albums; path checks. Pure functions, no Telethon |
| Auth CLI | `src/tg_mcp/auth.py` | Interactive auth flow: send code, OTP prompt, 2FA |
| Session check | `src/tg_mcp/check.py` | `check` subcommand: is the saved session still authorized? |
| Configuration | `src/tg_mcp/config.py` | Env var reading (`TG_APP_ID`, optional `TG_API_HASH`/`TG_SESSION_PATH`), session path resolution, `TransportConfig` |

---

## 2. Project Structure

```
q0t-telegram-mcp/
├── CLAUDE.md
├── pyproject.toml
├── uv.lock
├── agents/
│   ├── ARCHITECT.md
│   ├── IMPLEMENTER.md
│   ├── TESTER.md
│   └── DOCUMENTER.md
├── CHANGELOG.md
├── docs/
│   └── architecture.md          ← this file
├── src/
│   └── tg_mcp/
│       ├── __init__.py           ← main() entry point, CLI dispatch
│       ├── auth.py               ← interactive auth flow
│       ├── check.py              ← session liveness check
│       ├── client.py             ← TelegramClientWrapper
│       ├── config.py             ← Config dataclass, env + path resolution
│       ├── media.py              ← album grouping by media kind, path checks
│       ├── models.py             ← UserInfo, Dialog, Message
│       └── server.py             ← MCPServer instance, tool registrations
├── tasks/
│   └── task-1.md
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_check.py
    ├── test_cli.py
    ├── test_client.py
    ├── test_config.py
    ├── test_http_integration.py
    ├── test_media.py
    ├── test_models.py
    ├── test_readonly.py
    ├── test_server.py
    ├── test_server_startup.py
    ├── test_shared_client.py
    └── test_transport.py
```

---

## 3. Dependencies and `pyproject.toml`

The console script is what `uvx q0t-telegram-mcp` resolves to:

```toml
[project.scripts]
q0t-telegram-mcp = "tg_mcp:main"
```

Runtime dependencies are **pinned exactly, not floored**:

```toml
dependencies = [
    "click==8.4.2",
    "mcp==2.0.0",
    "telethon==1.44.0",
    "uvicorn==0.52.1",
]
```

### Why pins rather than ranges

0.9.0 shipped `mcp>=1.0.0`. When `mcp` 2.0 removed `mcp.server.fastmcp`, every *fresh* install
died at import with `ModuleNotFoundError` while the test suite stayed green — tests resolve
against `uv.lock`, which still held 1.x, so nothing in CI ever saw the version that users got.
A range is a promise about code that does not exist yet; a pin is a statement about the
versions this release was actually tested against. Raising one is a deliberate edit plus a
test run, which is the point.

Two of the pins carry their own reason:

- **`telethon==1.44.0`** — TL layer 227. Earlier layers raise `TypeNotFoundError` on the
  `Dialog` constructor (`0xfc89f7f3`) that Telegram now returns from `getDialogs`.
- **`uvicorn==0.52.1`** — a direct dependency, not merely `mcp`'s. `_serve_http` imports it by
  name to drive the ASGI app itself (see §7, *Logging*), so relying on it arriving transitively
  would be borrowing another package's dependency.

`starlette` and the HTTP client stack arrive transitively through `mcp` 2.0, which now pulls
`httpx2` and `starlette` 1.3.x. Only `tests/test_http_integration.py` touches those directly.

---

## 4. Data Models

All models are frozen dataclasses defined in `src/tg_mcp/models.py`.

### 4.1 `UserInfo`

Returned by `tg_me`.

```
UserInfo
  id:           int       # Telegram user ID
  first_name:   str       # First name
  last_name:    str | None
  username:     str | None  # @handle without @
  phone:        str | None
```

### 4.2 `Dialog`

One entry in the list returned by `tg_dialogs`.

```
Dialog
  id:            int         # Marked entity ID (unique)
  name:          str         # Display name (first+last for users, title for groups/channels)
  username:      str | None  # @handle without leading @; None for groups/channels without a public handle
  unread_count:  int
  is_user:       bool
  is_group:      bool
  is_channel:    bool
  last_message:  str | None  # Text of the most recent message, None if no text
  date:          str | None  # ISO-8601 datetime of the last message
```

### 4.3 `Message`

One entry in the list returned by `tg_dialog`.

```
Message
  id:        int
  date:      str       # ISO-8601 datetime
  text:      str | None
  out:       bool      # True if sent by us
  sender:    str | None  # Display name of sender; None for channel posts
```

---

## 5. Class Diagram

```
Config
  app_id:       int
  api_hash:     str
  session_path: Path
  ---
  @classmethod from_env() -> Config   # reads TG_APP_ID, TG_API_HASH; computes session_path
  session_dir() -> Path               # parent directory of session_path


TelegramClientWrapper
  _client:  TelegramClient            # telethon instance; created lazily
  _config:  Config
  ---
  async connect() -> None
  async disconnect() -> None
  async is_authorized() -> bool
  async get_me() -> UserInfo
  async get_dialogs(unread_only: bool, limit: int, offset: int) -> list[Dialog]
  async get_messages(dialog_id: int | None, username: str | None, name: str | None, limit: int, offset: int) -> list[Message]
  async send_message(text: str, dialog_id: int | None, username: str | None, name: str | None,
                     reply_to: int | None, parse_mode: str | None) -> int | None   # sent message ID
  async send_files(file_paths: list[str], dialog_id: int | None, username: str | None, name: str | None, caption: str | None) -> list[int]
  async mark_read(dialog_id: int | None, username: str | None, name: str | None) -> None
  async _resolve_entity(dialog_id: int | None, username: str | None, name: str | None) -> Any   # internal


AuthFlow
  _config: Config
  ---
  async run(phone: str, password: str | None) -> None
    # orchestrates: connect, send_code_request, prompt OTP, sign_in, handle 2FA


AppContext                             # dataclass yielded by the MCPServer lifespan
  client: TelegramClientWrapper


mcp: MCPServer                         # module-level singleton in server.py
  # 13 tools; the five documented in §7 are a representative subset.
  # The authoritative list is READ_TOOLS + WRITE_TOOLS in server.py,
  # and the full parameter reference is the README.
```

---

## 6. `client.py` — TelegramClientWrapper

### Constructor

```python
class TelegramClientWrapper:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: TelegramClient | None = None
```

`TelegramClient` is created lazily inside `connect()`.

### Methods

#### `async connect() -> None`
- Creates `self._client = TelegramClient(str(config.session_path), config.app_id, config.api_hash)`
- Calls `await self._client.connect()`
- Raises `RuntimeError("Not authorized. Run: q0t-telegram-mcp auth --phone <phone>")` if `not await self._client.is_user_authorized()`

#### `async disconnect() -> None`
- Calls `await self._client.disconnect()` if `self._client` is not None

#### `async is_authorized() -> bool`
- Returns `await self._client.is_user_authorized()`

#### `async get_me() -> UserInfo`
- Calls `me = await self._client.get_me()`
- Maps `me.id`, `me.first_name`, `me.last_name`, `me.username`, `me.phone` → `UserInfo`

#### `async get_dialogs(unread_only: bool = False, limit: int = 20, offset: int = 0) -> list[Dialog]`
- Calls `dialogs = await self._client.get_dialogs(limit=None)`
  - We fetch with `limit=None` then apply our own offset/limit after filtering, because Telethon's `offset_date` / `offset_id` based pagination cannot reliably skip N dialogs by integer offset. Instead we fetch all (or a generous slice) and slice ourselves.
  - For large accounts (1000s of dialogs), the implementer may optimize later — keep this note.
- Filter: if `unread_only`, keep only `d.unread_count > 0`
- Slice: `dialogs[offset : offset + limit]`
- Map each telethon `Dialog` object:
  - `id = d.id`
  - `name = d.name`
  - `username = getattr(d.entity, "username", None)`
  - `unread_count = d.unread_count`
  - `is_user = d.is_user`
  - `is_group = d.is_group`
  - `is_channel = d.is_channel`
  - `last_message = d.message.text if d.message else None`
  - `date = d.date.isoformat() if d.date else None`

#### `async get_messages(dialog_id: int | None, username: str | None, name: str | None, limit: int = 20, offset: int = 0) -> list[Message]`
- Resolves entity: `entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)`
- Calls `msgs = await self._client.get_messages(entity, limit=limit, add_offset=offset)`
  - Telethon `get_messages()` supports `add_offset` for integer-based pagination
- Map each telethon `Message` to our `Message` model:
  - `id = m.id`
  - `date = m.date.isoformat()`
  - `text = m.text` (may be None for media-only messages)
  - `out = bool(m.out)`
  - `sender`: call `await m.get_sender()`, then use `telethon.utils.get_display_name(sender)` — may be None for channel posts

#### `async send_message(text: str, dialog_id: int | None, username: str | None, name: str | None) -> None`
- Resolves entity: `entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)`
- **CRITICAL**: calls `await self._client.send_message(entity, text)`
- NEVER calls anything draft-related (`SaveDraft`, `MessagesSaveDraft`, `draft`, etc.)

#### `async send_files(file_paths: list[str], …, caption: str | None) -> list[int]`
- Takes a **list** of paths — a single file is a one-item list — and returns the ID of every
  message that was sent.
- Validates before it sends: an empty list, or any path that is not a regular file, raises
  `ValueError` naming the offenders. This is deliberate atomicity — a bad path discovered
  halfway through would leave the first albums delivered and the rest lost, which is the one
  outcome a caller cannot clean up automatically.
- Splits the list with `media.group_for_albums` and sends one album per group;
  `caption` is attached to the first album only, the way Telegram captions a group once.

#### `async mark_read(dialog_id: int | None, username: str | None, name: str | None) -> None`
- Resolves entity: `entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)`
- Calls `await self._client.send_read_acknowledge(entity)`

### Entity resolution — `_resolve_entity`

All three entity-accepting methods delegate to `_resolve_entity`, which applies a fixed priority:

1. **`dialog_id`** (int) — calls `get_entity(dialog_id)`. Most reliable; raises `ValueError` on failure.
2. **`username`** (str) — strips a leading `@`, then calls `get_entity(handle)`. Catches `ValueError` and `UsernameNotOccupiedError`.
3. **`name`** (str) — display names are NOT accepted by `get_entity`, so this is the slowest path and may match the wrong entity when names are not unique. It resolves in three steps:
   1. If the value starts with `@` it is unambiguously a handle — no display name carries one — so `get_entity()` is tried first with the `@` stripped. A miss falls through rather than raising.
   2. `iter_dialogs()` is scanned for an exact `dialog.name` match. This keeps priority, because the display name is what `name` documents.
   3. Failing that, the first dialog whose `username` matches case-insensitively (handles are case-insensitive on Telegram) is used.

   Steps 1 and 3 exist because the selectors are advice the caller may not follow. A model
   handed `@alice` picks whichever field looks plausible, and `name` looks plausible; before
   0.7.0 `username="@alice"` succeeded while `name="@alice"` raised "Dialog not found" for the
   same dialog in the same list. The widening only converts former failures into matches — a
   value that resolved before resolves to the same entity now.

Raises `ValueError("One of dialog_id, username, or name must be provided")` if all three are `None`.

### Albums — `media.py` and `PartialSendError`

Telegram refuses an album that mixes media classes: photos and videos share one, audio tracks
share another, everything else travels as documents. A caller handing `tg_send_file` an
arbitrary list has no reason to know that, so `media.group_for_albums` splits the list by
class — by file extension — and `send_files` sends one album per group, in the order
visual → audio → document. Telethon already chunks a group by Telegram's ten-item album
limit, so that is not re-implemented here.

`media.py` is a separate module because none of this needs a network or a Telethon type:
`album_kind`, `group_for_albums`, `expand_paths`, and `missing_files` are pure functions over
strings and can be tested without a client at all (`tests/test_media.py`).

`PartialSendError` covers the one failure a caller cannot reason about otherwise. When a list
splits into several albums and a later one fails, the earlier ones are already in the dialog
and cannot be recalled. Reporting a bare error would invite a retry of the whole list and
double-post the delivered part, so the exception carries `message_ids` and `tg_send_file`
returns `{"error": …, "message_ids": [...]}` — enough for the caller to resend only what is
missing. A failure on the *first* album carries nothing and is re-raised unchanged; nothing
was sent, so a plain retry is correct.

---

## 7. `server.py` — MCP Tool Registration

```python
from mcp.server.mcpserver import Context, MCPServer
```

Uses `MCPServer` with a lifespan that hands each session the process-wide Telegram client.

### Lifespan

```python
@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    client = await shared_client.acquire()   # connects on first use;
    try:                                     # raises RuntimeError if not authorized
        yield AppContext(client=client)
    finally:
        await shared_client.release()
```

The lifespan does **not** build its own `TelegramClientWrapper` — see
*`SharedTelegramClient`* below for why that would be a bug under `sse`.

### Tools

There are 13 tools. The five spelled out below are the original set and are kept here because
the rest follow the same shape; the complete parameter and return reference lives in the
README, and the authoritative registry is `READ_TOOLS` + `WRITE_TOOLS` in `server.py`.

### Tool: `tg_me`

```
Name:        tg_me
Description: Get current Telegram account info
Parameters:  none
Returns:     dict (serialized UserInfo)
Calls:       ctx.request_context.lifespan_context.client.get_me()
```

### Tool: `tg_dialogs`

```
Name:        tg_dialogs
Description: List Telegram dialogs (chats, groups, channels)
Parameters:
  unread_only: bool = False  — only return dialogs with unread messages
  limit:       int  = 20    — max number of dialogs to return
  offset:      int  = 0     — number of dialogs to skip (for pagination)
Returns:     list[dict] (serialized list[Dialog])
             Each dict includes: id, name, username (str or null), unread_count,
             is_user, is_group, is_channel, last_message, date
Calls:       client.get_dialogs(unread_only, limit, offset)
```

### Tool: `tg_dialog`

```
Name:        tg_dialog
Description: Get messages from a Telegram dialog
Parameters:
  dialog_id: int | None = None  — numeric ID from tg_dialogs; most reliable
  username:  str | None = None  — Telegram @handle (leading @ optional)
  name:      str | None = None  — display name; least reliable (may match wrong contact)
  limit:     int = 20           — max number of messages to return
  offset:    int = 0            — number of messages to skip (for pagination)
  At least one of dialog_id / username / name must be provided.
  Priority: dialog_id > username > name
Returns:     list[dict] (serialized list[Message])
Calls:       client.get_messages(dialog_id, username, name, limit, offset)
```

### Tool: `tg_send`

```
Name:        tg_send
Description: Send a message to a Telegram dialog
Parameters:
  dialog_id: int | None = None  — numeric ID from tg_dialogs; most reliable
  username:  str | None = None  — Telegram @handle (leading @ optional)
  name:      str | None = None  — display name; least reliable (may match wrong contact)
  text:      str                — message text to send
  reply_to:  int | None = None  — message ID to reply to
  parse_mode: str = "markdown"  — 'markdown', 'html', or 'none' for literal text
  At least one of dialog_id / username / name must be provided.
  Priority: dialog_id > username > name
Returns:     dict {"ok": true, "message_id": n}
Calls:       client.send_message(text, dialog_id, username, name, reply_to, parse_mode)
INVARIANT:   MUST call telethon send_message(), NEVER SaveDraft/MessagesSaveDraft
```

### Tool: `tg_read`

```
Name:        tg_read
Description: Mark all messages in a dialog as read
Parameters:
  dialog_id: int | None = None  — numeric ID from tg_dialogs; most reliable
  username:  str | None = None  — Telegram @handle (leading @ optional)
  name:      str | None = None  — display name; least reliable (may match wrong contact)
  At least one of dialog_id / username / name must be provided.
  Priority: dialog_id > username > name
Returns:     dict {"ok": true}
Calls:       client.mark_read(dialog_id, username, name)
```

### Server instantiation

```python
mcp = MCPServer("q0t-telegram-mcp", lifespan=lifespan)
# tools registered by register_tools(mcp, readonly=...) — see below
```

### Tool function pattern

All tool functions MUST be `async def` because they await async client methods. They are
defined as plain module-level functions and registered explicitly, *not* with an
`@mcp.tool()` decorator:

```python
async def tg_me(ctx: Context) -> dict:
    client = ctx.request_context.lifespan_context.client
    user_info = await client.get_me()
    return dataclasses.asdict(user_info)
```

The `ctx` parameter is automatically injected by the server when the function signature includes it. All other parameters become the tool's MCP parameter schema.

Parameter descriptions and JSON types come from `Annotated[...]` with a pydantic `Field` and,
for the optional selectors, an explicit `WithJsonSchema`. `Optional[X]` is used rather than
`X | None` on purpose: some MCP clients render the union as an `anyOf` and then report the
type as "unknown", and the `Optional` + `WithJsonSchema` pairing is what pins the advertised
type down. None of this changed with the move to `mcp` 2.0 — the generated `inputSchema` is
byte-identical.

### Read-only mode and conditional registration

Decorators run at import time, which is too early to consult `TG_READONLY` — reading the
environment during import would make the registry a side effect of importing the module and
force any test of it into module reloading. Registration is therefore an explicit function:

```python
READ_TOOLS  = [tg_me, tg_dialogs, tg_dialog, tg_search, tg_download]
WRITE_TOOLS = [tg_send, tg_send_file, tg_forward, tg_read, tg_click,
               tg_react, tg_delete, tg_edit]

def register_tools(server: MCPServer, *, readonly: bool) -> None:
    registered = set(server._tool_manager._tools)
    tools = READ_TOOLS if readonly else READ_TOOLS + WRITE_TOOLS
    for fn in tools:
        if fn.__name__ not in registered:
            server.add_tool(fn)
```

Registration is idempotent: `mcp` is a module-level singleton, so a second `run_server()` in
the same process (tests do this) would otherwise re-add every tool and emit duplicate-tool
warnings that change nothing. A genuine collision — two different functions claiming one name —
is caught by the partition test instead, not silently absorbed here.

`readonly_from_env()` accepts `1`/`true`/`yes` in any case, after stripping whitespace;
everything else — including unset and blank — means "off", so no pre-0.7.0 configuration
changes meaning.

Writing tools are **omitted from the registry**, not registered-and-refusing. A tool that is
absent from `list_tools` cannot be called; a tool that exists and returns an error is still an
advertised capability the model can see and keep reaching for.

Two members of `WRITE_TOOLS` are there for non-obvious reasons: `tg_read` marks a dialog read
(destroys unread state, visible to the other party), and `tg_click` delivers a callback query
to a bot (can trigger anything that bot does). Both act as the account owner despite reading
as passive. `tg_download` is classified as a read: it writes to local disk but changes no
account state.

### Transports and the shared Telegram client

```python
def run_server(config: TransportConfig | None = None) -> None:
    config = config or TransportConfig.resolve()
    register_tools(mcp, readonly=readonly_from_env())
    if not config.is_http:
        mcp.run()          # stdio; blocks
        return
    _warn_if_exposed(config)
    _serve_http(mcp, config)
```

stdio stays the default, so invoking the package bare behaves as it did before 0.8.0.

#### Why HTTP exists at all

MCP clients spawn a stdio server **per run**. Two overlapping runs mean two processes of this
package against one Telethon session file, and both die on
`sqlite3.OperationalError: database is locked` — Telethon *writes* to the session at startup.
Copying the session file per process trades that for `AUTH_KEY_DUPLICATED`: Telegram
invalidates an `auth_key` it sees used from two places at once, logging the account out for
real. The only correct shape is one long-lived process owning the session, with clients
attaching over HTTP.

#### `SharedTelegramClient` — the part that is easy to get wrong

How often the lifespan is entered is **not** a property of the server; it is a property of the
transport, and it differs between them:

| Transport | Lifespan entered | Consequence |
|-----------|------------------|-------------|
| stdio | once per process | One process, one client — never mattered |
| streamable HTTP (`http`) | once per process, by the session manager | Since `mcp` 2.0 (was: per MCP session) |
| `sse` | **once per connected client** | `sse_app` calls `Server.run()` inside each connection |

The mechanism is the same in all three: the low-level server does
`enter_async_context(self.lifespan(self))` inside `Server.run()`
(`mcp/server/lowlevel/server.py`). What changed in `mcp` 2.0 is who calls `Server.run()` for
streamable HTTP — the session manager now runs it once for its own lifetime, rather than once
per connecting client as `mcp` 1.x did.

That fix does not retire the shared client, because `sse` still behaves the old way. A
lifespan that built its own `TelegramClientWrapper` would, under `sse`, open one Telethon
client **per connected client inside one process** — reproducing exactly the failure the HTTP
transport exists to eliminate. The shared client stays until `sse` is dropped.

Worth knowing before touching this: the regression test no longer catches the mistake.
`tests/test_http_integration.py` asserts `len(created) == 1` across five concurrent clients,
which under `mcp` 1.x failed with `assert 5 == 1` the moment sharing was removed. It runs
against `streamable_http_app`, so under `mcp` 2.0 it now passes either way — the session
manager alone keeps the count at one. Nothing exercises `sse_app`'s lifespan, so the only
thing standing between a "simplifying" refactor and `AUTH_KEY_DUPLICATED` is this paragraph.

So the client lives in a module-level `SharedTelegramClient`:

- an `asyncio.Lock` around creation, because two clients connecting at the same instant is
  precisely the race in question;
- reference-counted, so stdio (exactly one session, ever) keeps its old connect-on-entry /
  disconnect-on-exit lifecycle unchanged;
- a failed `connect()` is not cached, so one bad startup cannot leave a permanently dead
  server handing a broken client to every later session.

Concurrent tool calls are *not* serialised. Telethon multiplexes requests on one client
itself, and a lock around the client would serialise all Telegram traffic for no benefit; the
integration tests cover parallel calls on one session and across sessions.

#### What the per-process lifespan changed for `http` users

The reference counting still works, but under streamable HTTP the count now goes to one when
the process starts and back to zero when it stops, instead of tracking connected clients. That
is visible from outside in three ways, and all three are improvements worth stating plainly:

- **The Telegram connection is held for the process's whole life**, not released the moment
  the last client disconnects. A client reconnecting after an idle gap no longer pays for a
  fresh Telethon connect.
- **Connecting happens at process startup**, not on the first MCP session. Failures surface
  when you start the server rather than during someone's first tool call.
- **An unauthorized session now stops the server from starting at all.** uvicorn reports
  `Application startup failed` and the `RuntimeError("Not authorized…")` from `connect()`
  propagates out of the lifespan. Under `mcp` 1.x the process started happily, `/health`
  answered `200`, and only tool calls failed.

That last one is a deliberate trade and the reason it is documented rather than smoothed over:
a process that is up but cannot do anything is the worse failure, because monitoring reports
it healthy. Now `/health` answering at all implies Telegram *was* connected at startup — and
since 0.11.1 it also reports whether it still is (see `/health` below). The cost is that a
supervisor restarting a server whose session was revoked gets a crash loop instead of a
running-but-useless process — which is the correct signal, and `q0t-telegram-mcp check`
(§8a) is how you find out before starting.

#### Listen address is the access control

The server has no authentication of its own and grants full access to the account behind its
session, so it binds `127.0.0.1` by default. A non-loopback bind is permitted — the operator
may be inside an isolated container — but warns on stderr.

`mcp` 2.0 moved this configuration off the server object. There is no `settings.host`,
`settings.port`, or `settings.transport_security` any more; the address goes to the app
factory and to uvicorn, and the DNS-rebinding policy is an argument:

```python
security = _transport_security(config)
app = server.streamable_http_app(host=config.host, transport_security=security)
#     server.sse_app(host=config.host, transport_security=security)  # --transport sse
uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, …)).run()
```

The old `_apply_http_settings(server, config)`, which mutated the server in place, is gone.
`_transport_security(config)` replaces it as a pure function returning
`TransportSecuritySettings`, and it is always passed explicitly — never left to the SDK:

```python
def _transport_security(config: TransportConfig) -> TransportSecuritySettings:
    hosts   = list(_BASE_ALLOWED_HOSTS)      # "127.0.0.1:*", "localhost:*", "[::1]:*"
    origins = list(_BASE_ALLOWED_ORIGINS)    # the http:// spellings of the same
    if not config.is_loopback:
        hosts   += [config.host, f"{config.host}:*"]
        origins += [f"http://{config.host}", f"http://{config.host}:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=hosts, allowed_origins=origins)
```

Two properties matter, for different reasons:

- **`enable_dns_rebinding_protection=True` unconditionally.** Given
  `transport_security=None`, `mcp` 2.0 turns the protection on only when it recognises the
  host as loopback and otherwise leaves it **off entirely**. Off-host is precisely the case
  that needs it, so accepting the default would silently drop a defence at the moment it
  starts mattering. Widening the allowances is the deliberate choice; dropping them is not.
- **Rebuilt from the loopback base on every call**, never appended to a shared object. The
  base is a module-level tuple (`_BASE_ALLOWED_HOSTS` / `_BASE_ALLOWED_ORIGINS`), spelled out
  here rather than read back off the server, since `mcp` 2.0 no longer exposes it. Two
  configurations applied in one process — tests do this — cannot accumulate allowances for an
  address no longer in use.

The allowances are widened rather than left at localhost-only because warning about a
non-loopback bind and then rejecting every request that arrives at it would be the worst of
both worlds: the user asked for that address.

#### Logging

`_serve_http` drives uvicorn directly rather than through `MCPServer.run`, which hardcodes its
own uvicorn config and leaves no way to redirect logging. uvicorn's stock configuration sends
the access log to **stdout**; everything is moved to stderr so diagnostics land in one place
regardless of transport. Verified live: with `--transport http`, stdout stays empty. This is
also why `uvicorn` is a declared dependency of this package (§3) — `_serve_http` imports it by
name.

#### `/health`

Registered with `@mcp.custom_route`, so it answers without an MCP session. Status, package
name, version, Telegram connection state, and the count of active sessions. Nothing
configuration- or credential-derived, because anything that can reach the port can read it.

Since 0.10.0 a server with a dead session never gets far enough to answer, because the
lifespan runs at process startup under streamable HTTP. That only covers startup. 0.11.1
added the `telegram` field and a `503` when it is anything but `"connected"`. The bug it
answers: on a laptop daemon, a lid close took the network away, Telethon's background
reconnect used up its default five attempts in seconds and stopped, and from then on every
tool call failed with `Cannot send requests while disconnected` while `/health` kept saying
`200 ok`. A probe that cannot see the thing it is supposed to watch is worse than no probe.

The state comes from `TelegramClientWrapper.connection_state()`. `TelegramClient.is_connected()`
is not enough on its own: it returns `MTProtoSender._user_connected`, which records *intent*
and stays `True` through a whole background reconnect. The link itself is
`MTProtoSender._transport_connected()`, a private method. It is read defensively, so if it is
renamed the answer gets coarser instead of `/health` returning a 500.

#### Surviving network loss (0.11.1)

Two layers, both in our code:

- **Telethon retries forever.** The client is built with `connection_retries=-1`,
  `retry_delay=5`, `auto_reconnect=True`. Checked against the installed Telethon 1.44.0, not
  its docs: `helpers.retry_range` treats a negative count as infinite, and both
  `MTProtoSender._connect` (initial connect and each reconnect attempt) and
  `MTProtoSender._reconnect` loop over it. The delay is the only pacing, because with no
  network each attempt fails at once with `ENETUNREACH`. The cost is that connecting with no
  network waits instead of failing, both at startup and for a request issued mid-outage.
  `auth` and `check` build their own clients and keep the defaults, since they are one-shot
  commands that should fail fast.
- **Reconnect before the call.** Telethon can still give up on non-network errors (an auth
  key the server no longer knows, an unexpected exception in its loop), and then stays down
  until someone calls `connect()`. `register_tools` wraps every tool in `_reconnect_first`,
  which calls `TelegramClientWrapper.ensure_connected()` first. That runs under a lock, so
  concurrent calls reconnect once, and it is bounded by a 30 s timeout. A background reconnect
  already in progress is left alone. The guard lives at registration so no tool can be added
  without it, and `functools.wraps` keeps the generated `inputSchema` and the `Context`
  parameter detection unchanged (a test compares them). If the reconnect fails, the reason is
  printed to stderr and the tool runs anyway, so the caller gets Telethon's error in that
  tool's own shape.

- **Auth loss and the watchdog.** Telethon's `_update_loop` polls `getDifference`. When that
  answers `UnauthorizedError` or `AuthKeyError` (`SESSION_REVOKED`, `AUTH_KEY_UNREGISTERED`,
  `AUTH_KEY_DUPLICATED`), Telethon stores the error in `_updates_error` and **calls
  `disconnect()` itself**. That produces a locally initiated `Disconnecting from …`, and
  nothing in Telethon reconnects afterwards. This is the four-week production zombie from
  0.11.0. `TelegramClientWrapper.auth_error()` copies that record into a field of its own
  that never resets, because Telethon clears `_updates_error` on its next connect. It also
  records an `AUTH_LOSS_ERRORS` failure from the authorization check that `ensure_connected()`
  now runs after every reconnect. That check sends `updates.GetState` directly, because
  `is_user_authorized()` caches its first answer for the life of the client. Once auth is
  lost, `connection_state()` returns `"unauthorized"` and `ensure_connected()` raises
  `AuthLostError` without reconnecting. A watchdog task, `SharedTelegramClient._watch`, is
  started with the process-wide client and cancelled on its last release. Every 60 s it calls
  `ensure_connected()`. On `AuthLostError` it exits `78`. After five consecutive failures of
  any other kind it exits `75`. It uses `os._exit`, because `SystemExit` raised inside a task
  would only end the task, and a graceful uvicorn shutdown would exit with status 0.
  Instances built without `watchdog_interval` run no watchdog. Tests build those.

The same change reinterprets `active_sessions`. It reports `SharedTelegramClient`'s reference
count, which is a count of *holds on the client*, not of attached clients — under `http` it is
`1` from startup to shutdown regardless of how many clients are connected, while under `sse`
it still tracks connections. It stays in the payload as a liveness signal, but nothing should
read it as a client count.

---

## 8. `__init__.py` — Entry Point

`main()` is the entry point for `q0t-telegram-mcp` (the script registered in `pyproject.toml`).

```python
def main() -> None:
    cli()   # click group
```

### CLI structure (using `click`)

```
cli (Group)
  ├── auth   [subcommand]  — runs interactive auth
  ├── check  [subcommand]  — reports whether the saved session is still authorized
  └── (default)           — runs MCP server if no subcommand
```

Because `click` doesn't natively support "default subcommand", the pattern is:

```python
@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        # No subcommand — run MCP server
        run_server()

@cli.command()
@click.option("--phone", required=True, help="Your phone number with country code")
@click.option("--password", default=None, help="2FA password if enabled")
@click.option("--app-id", default=None, type=int, help="overrides TG_APP_ID")
@click.option("--api-hash", default=None, help="overrides TG_API_HASH")
@click.option("--session", default=None, help="overrides TG_SESSION_PATH")
def auth(phone, password, app_id, api_hash, session) -> None:
    """Authenticate with Telegram and save session."""
    import asyncio
    asyncio.run(_run_auth(phone, password, app_id, api_hash, session))
```

`_run_auth` writes the supplied flags into `os.environ` (that is how "flags beat env" is implemented — there is one resolution path, `Config.from_env`), builds the config via `_config_or_exit(require_api_hash=True)` and `AuthFlow(config)`, then calls `await auth_flow.run(phone, password)`.

`_config_or_exit` catches `EnvironmentError` from `Config.from_env` and turns it into a
one-line `Error: …` plus `sys.exit(1)`, matching how `auth.py` reports login failures. Without
it a missing variable surfaces as a traceback, which reads like a crash rather than a
configuration problem.

`check` follows the identical flag-into-environment pattern, but passes
`require_api_hash=False` — it never signs in. Its command body exits with the code the checker
returns rather than always 0:

```python
def check(app_id, api_hash, session, as_json) -> None:
    sys.exit(asyncio.run(_run_check(app_id, api_hash, session, as_json)))
```

---

## 8a. `check.py` — Session Liveness

A session can be revoked from an official client at any time, and nothing local reflects that.
Without `check`, the first symptom is the server starting normally and every tool call failing.

### `SessionChecker.run() -> CheckResult`

1. `resolve_session_file(config.session_path)` — locate the file, creating nothing. Both the
   path as given and the same path plus `.session` are accepted, because Telethon's
   `SQLiteSession` appends the extension when it is missing.
2. No file → `CheckResult(status="unknown", …)`. Telethon is never constructed.
3. Copy the file into a `TemporaryDirectory` and point Telethon at the copy.
4. `connect()` → `is_user_authorized()` → optionally `get_me()`.
5. `disconnect()` in a `finally`, with its own exception guard.

### The two constraints that shape it

**Looking must not create.** `SQLiteSession.__init__` opens a cursor and creates its tables
immediately (`telethon/sessions/sqlite.py:48-49`). Pointing Telethon at a non-existent path
would therefore create an empty session and then truthfully report it as unauthorized — a
check that causes the state it reports. The existence test happens before Telethon is touched.

**Checking must not disturb.** The session file is SQLite and a running server may hold it
open, while Telethon writes to it on connect. Sharing it risks `database is locked` for the
server and a corrupted session for both. The check therefore reads the original exactly once,
to copy it, and connects only against that copy; the copy is discarded with its temporary
directory.

### Three outcomes, three exit codes

| `status` | Exit code | Meaning |
|----------|-----------|---------|
| `authorized` | 0 | Telegram accepted the session |
| `unauthorized` | 1 | Telegram says it is logged out — re-run `auth` |
| `unknown` | 2 | No session file, or Telegram unreachable |

`unauthorized` and `unknown` are never merged: the first means "re-authenticate", the second
means "fix the path or the network". A caller that cannot tell them apart will do the wrong one.

`get_me()` failing does **not** downgrade an `authorized` verdict — identity is supplementary,
and the question the caller asked was already answered.

### Side effect worth documenting

The check makes a real network call. Telegram records it as activity for that device and
refreshes its "last seen" timestamp. It is not a purely local operation, and the README says so.

---

## 9. `auth.py` — Auth Flow

### `Config.session_path` during auth

During `auth`, the session file does not yet exist. The auth command creates the directory and lets Telethon create the session file.

```python
config.session_dir().mkdir(parents=True, exist_ok=True)
```

### `AuthFlow.run(phone, password)` — step by step

1. Create `TelegramClient(str(config.session_path), config.app_id, config.api_hash)`
2. `await client.connect()`
3. Check `await client.is_user_authorized()` — if True, print "Already authorized" and return
4. `result = await client.send_code_request(phone)`
   - Stores `phone_code_hash` from `result.phone_code_hash`
5. `code = input("Enter the code you received: ").strip()`
6. Try `await client.sign_in(phone, code, phone_code_hash=phone_code_hash)`
7. If `telethon.errors.SessionPasswordNeededError` is raised:
   - If `password` is None: `password = getpass.getpass("Enter your 2FA password: ")`
   - `await client.sign_in(password=password)`
8. Print "Authentication successful. Session saved to: {config.session_path}"
9. `await client.disconnect()`

### Error handling in auth

| Exception | Action |
|-----------|--------|
| `PhoneCodeInvalidError` | Print error, exit with code 1 |
| `PhoneCodeExpiredError` | Print error, exit with code 1 |
| `PasswordHashInvalidError` | Print "Wrong 2FA password", exit with code 1 |
| `ApiIdInvalidError` | Print "Invalid TG_APP_ID / TG_API_HASH", exit with code 1 |
| `EnvironmentError` from `Config.from_env` | Handled earlier by `_config_or_exit`: print `Error: …`, exit with code 1 |
| Other `TelegramError` | Re-raise (unexpected) |

---

## 10. `config.py` — Configuration

### `Config` dataclass

```python
@dataclass(frozen=True)
class Config:
    app_id:       int
    api_hash:     str
    session_path: Path

    @classmethod
    def from_env(cls, *, require_api_hash: bool = False) -> "Config":
        app_id_str = (os.environ.get("TG_APP_ID") or "").strip()
        api_hash   = (os.environ.get("TG_API_HASH") or "").strip()
        if not app_id_str:
            raise EnvironmentError("TG_APP_ID environment variable is not set")
        if not api_hash:
            if require_api_hash:
                raise EnvironmentError("TG_API_HASH environment variable is not set. …")
            api_hash = API_HASH_PLACEHOLDER
        return cls(app_id=int(app_id_str), api_hash=api_hash,
                   session_path=cls._session_path_from_env())

    @staticmethod
    def _session_path_from_env() -> Path:
        override = (os.environ.get("TG_SESSION_PATH") or "").strip()
        if not override:
            return Path.cwd() / ".telegram-mcp" / "session"
        return Path(override).expanduser()

    def session_dir(self) -> Path:
        return self.session_path.parent
```

### Why `TG_API_HASH` is optional

`TG_APP_ID` is always required — it travels in `InitConnectionRequest` on every connection.
`TG_API_HASH` is required only when *require_api_hash* is set, which only `auth` does.

Verified against Telethon 1.44 by reading the source; re-verify before trusting it on a newer
version, and note the assertions in `TestTelethonPlaceholderAssumption` exist to make a change
here fail loudly:

| Location | What it does with `api_hash` |
|----------|------------------------------|
| `client/auth.py:447` | `auth.SendCodeRequest` — phone login |
| `client/auth.py:350` | `auth.ImportBotAuthorizationRequest` — bot login |
| `tl/custom/qrlogin.py:19` | `auth.ExportLoginTokenRequest` — QR login |
| `client/telegrambaseclient.py:368` | `InitConnectionRequest` — **`api_id` only, no hash**; this is what every persistent connection sends |
| `client/telegrambaseclient.py:273` | Constructor: rejects empty/`None`, validates nothing else |
| `client/telegrambaseclient.py:924` | CDN client for cross-DC downloads (reached via `tg_download`) — re-enters that same constructor |

All three requests that put the hash on the wire are logins. This server never logs in:
`client.py` calls `connect()`, never `start()` or `sign_in()`. Correctness of the value cannot
be checked locally anyway, because it never leaves the process.

Hence `API_HASH_PLACEHOLDER` — a non-empty string that satisfies the constructor and reads
unmistakably as an absent value wherever it surfaces (tracebacks, debug dumps, `ps` output).
`Config.has_real_api_hash` distinguishes it for any caller that needs to know.

Blank and whitespace-only values count as unset, the same rule `_session_path_from_env` uses.

### Session path resolution

- `TG_SESSION_PATH`, when set, is the session path verbatim (`~` expanded). A **blank** value counts as unset: an empty variable is how "not configured" reaches a container, and treating it as a path would aim the session at the current directory itself.
- Otherwise the session file lives at `<cwd>/.telegram-mcp/session`
- `cwd` is wherever the user runs the `auth` command or starts the MCP server
- For Claude Code users, `cwd` will typically be the project root (where `.mcp.json` lives)

### Why the override exists

The default path is cwd-relative, and MCP servers inherit the cwd of the client that spawns them. Two entries in the same `.mcp.json` therefore resolve to the *same* session file — which caps the server at one Telegram account per working directory. `TG_SESSION_PATH` (or `auth --session`) breaks that tie, so N accounts can be served side by side from one directory. Credentials stay shared: `TG_APP_ID`/`TG_API_HASH` identify the application, the session identifies the account.
- The session file name `.session` produces a `session.session` file on disk (Telethon appends `.session` to the string path passed to `TelegramClient`). To avoid double extension, pass `str(session_path.with_suffix(""))` to Telethon — i.e., strip the `.session` suffix before passing, so Telethon creates `.telegram-mcp/.session.session` — **actually the cleaner approach**: name the session `session` (no extension) and store as `.telegram-mcp/session`. Telethon then creates `.telegram-mcp/session.session`.

**Final decision**: `session_path = Path.cwd() / ".telegram-mcp" / "session"` (no extension in the Python Path). Telethon will create `.telegram-mcp/session.session` on disk. The `Config.session_path` property holds the extensionless path passed to Telethon.

Update the constant in `Config.from_env()`:

```python
session_path = Path.cwd() / ".telegram-mcp" / "session"
```

---

## 11. Error Handling Strategy

### Levels

| Layer | What it handles | How |
|-------|----------------|-----|
| `auth.py` | Telethon auth errors | Catch specific exceptions, print friendly message, `sys.exit(1)` |
| `client.py` | Not authorized at startup | Raise `RuntimeError` with human-readable message |
| `client.py` | Entity not found | Catch Telethon lookup errors, raise `ValueError(f"Dialog not found: {name}")` |
| `client.py` | Connection errors | Let them propagate (unexpected; `MCPServer` will surface to MCP caller) |
| `server.py` tools | `ValueError` from client | Return `{"error": str(e)}` in the tool response (don't crash the server) |
| `server.py` tools | `RuntimeError` from client | Propagate — `MCPServer` will turn it into an MCP error response |
| `config.py` | Missing env vars | Raise `EnvironmentError` immediately at startup (fail fast) |

### Tool error response format

When a tool catches a `ValueError`, it returns:

```python
{"error": "Dialog not found: John Doe"}
```

This allows the MCP caller (Claude) to report the issue without crashing the server.

### What is NEVER caught

- `asyncio` cancellation errors
- Python `SystemExit`
- Low-level socket/network errors during normal operation (let them surface)

---

## 12. Session Management

### Where the session lives

`Path.cwd() / ".telegram-mcp" / "session"` by default — Telethon creates `session.session` at
that location. `TG_SESSION_PATH` (or `auth --session`) overrides it verbatim.

### How it is found at runtime

`Config.from_env()` consults `TG_SESSION_PATH` first and falls back to the cwd-relative
default. With no override that means:
- The MCP server must be started from the same directory where `auth` was run.
- For Claude Code, set `cwd` in `.mcp.json` if the default working directory differs.

With an override, neither applies — the path is absolute and the working directory stops
mattering, which is what makes several accounts from one directory possible.

### Session persistence

- Telethon's `SQLiteSession` handles persistence automatically.
- Once authenticated, subsequent connections reuse the session without re-prompting.
- The session is valid until the user terminates it from another Telegram client or the server revokes it.

### `.gitignore` recommendation

The `.telegram-mcp/` directory should be in `.gitignore` to prevent committing credentials.

---

## 13. Confirmed Reference Implementation Bugs (Do Not Repeat)

### Bug 1: `tg_send` saves a draft instead of sending

In the Go reference (`internal/tg/draft.go`), `SendDraft` calls `MessagesSaveDraft`. This stores the message as an unsent draft. The message never arrives at the recipient.

**Fix**: Call `telethon.send_message(entity, text)` — the Telethon method that calls `SendMessage` on the MTProto API.

### Bug 2: Parameter naming inconsistency

In the Go reference (`serve.go`), the MCP tool advertises parameter `to` but the Go struct uses JSON tag `name`. The parameter `to` passed by the caller is silently ignored; the dialog name is never set.

**Fix**: All dialog-referencing parameters in our tools are named `name` in both the tool definition and the function signature — they are the same identifier throughout.

---

## 14. Sequence Diagrams

### Normal MCP tool call (`tg_send`)

```
Claude Code
    │  call_tool("tg_send", {dialog_id: 123456789, text: "Hello"})
    ▼
MCPServer (server.py)
    │  invoke tg_send(dialog_id=123456789, text="Hello", ctx=...)
    ▼
TelegramClientWrapper (client.py)
    │  entity = await _resolve_entity(dialog_id=123456789)
    │    → await self._client.get_entity(123456789)
    │  await self._client.send_message(entity, "Hello")   ← MUST be this, not SaveDraft
    ▼
Telethon → Telegram MTProto API
    │  SendMessage RPC
    ▼
Telegram delivers message to Alice
    │
    ◄── success
MCPServer returns {"ok": true, "message_id": …} to Claude Code
```

### Auth flow

```
User runs: uvx q0t-telegram-mcp auth --phone +1234567890
    │
main() → cli() → auth subcommand
    │
Config.from_env()   ← reads TG_APP_ID, TG_API_HASH from env
    │
AuthFlow.run(phone="+1234567890", password=None)
    │
TelegramClient.connect()
    │
is_user_authorized()? → No
    │
send_code_request(phone)    → Telegram sends SMS/app notification
    │
input("Enter the code: ")   → User types OTP
    │
sign_in(phone, code)
    │  (if SessionPasswordNeededError)
    │     input 2FA password
    │     sign_in(password=password)
    │
"Authentication successful. Session saved to: .telegram-mcp/session"
TelegramClient.disconnect()
```

# Architecture: q0t-telegram-mcp

## 1. Overview

`q0t-telegram-mcp` is a Python MCP server that exposes Telegram operations as MCP tools accessible to AI assistants like Claude Code. It uses `telethon` to communicate with Telegram's MTProto API and the `mcp` Python SDK (`FastMCP`) to handle the MCP protocol over stdio.

### Components

| Component | File | Purpose |
|-----------|------|---------|
| Entry point | `src/tg_mcp/__init__.py` | CLI dispatch: `auth` subcommand vs MCP server mode |
| MCP server | `src/tg_mcp/server.py` | FastMCP instance, tool registrations, lifespan management |
| Telegram client wrapper | `src/tg_mcp/client.py` | Async wrapper around `TelegramClient` |
| Data models | `src/tg_mcp/models.py` | `UserInfo`, `Dialog`, `Message` dataclasses |
| Auth CLI | `src/tg_mcp/auth.py` | Interactive auth flow: send code, OTP prompt, 2FA |
| Configuration | `src/tg_mcp/config.py` | Env var reading (`TG_APP_ID`, `TG_API_HASH`), session path resolution |

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
├── docs/
│   └── architecture.md          ← this file
├── src/
│   └── tg_mcp/
│       ├── __init__.py           ← main() entry point, CLI dispatch
│       ├── auth.py               ← interactive auth flow
│       ├── client.py             ← TelegramClientWrapper
│       ├── config.py             ← Config dataclass, env + path resolution
│       ├── models.py             ← UserInfo, Dialog, Message
│       └── server.py             ← FastMCP instance, tool registrations
├── tasks/
│   └── task-1.md
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_client.py
    ├── test_server.py
    └── test_models.py
```

---

## 3. pyproject.toml Changes

Add these sections to `pyproject.toml` (do not change existing content):

```toml
[project.scripts]
q0t-telegram-mcp = "tg_mcp:main"
```

Also add `click` as a runtime dependency (for the CLI):

```toml
dependencies = [
    "click>=8.0.0",
    "mcp>=1.0.0",
    "telethon>=1.36.0",
]
```

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
  async send_message(text: str, dialog_id: int | None, username: str | None, name: str | None) -> None
  async mark_read(dialog_id: int | None, username: str | None, name: str | None) -> None
  async _resolve_entity(dialog_id: int | None, username: str | None, name: str | None) -> Any   # internal


AuthFlow
  _config: Config
  ---
  async run(phone: str, password: str | None) -> None
    # orchestrates: connect, send_code_request, prompt OTP, sign_in, handle 2FA


AppContext                             # dataclass used in FastMCP lifespan
  client: TelegramClientWrapper


server: FastMCP                        # module-level singleton in server.py
  # tools: tg_me, tg_dialogs, tg_dialog, tg_send, tg_read
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

#### `async mark_read(dialog_id: int | None, username: str | None, name: str | None) -> None`
- Resolves entity: `entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)`
- Calls `await self._client.send_read_acknowledge(entity)`

### Entity resolution — `_resolve_entity`

All three entity-accepting methods delegate to `_resolve_entity`, which applies a fixed priority:

1. **`dialog_id`** (int) — calls `get_entity(dialog_id)`. Most reliable; raises `ValueError` on failure.
2. **`username`** (str) — strips a leading `@`, then calls `get_entity(handle)`. Catches `ValueError` and `UsernameNotOccupiedError`.
3. **`name`** (str) — display names are NOT accepted by `get_entity`. Instead, the method iterates `iter_dialogs()` looking for an exact `dialog.name` match. Slowest path; may match the wrong entity when names are not unique.

Raises `ValueError("One of dialog_id, username, or name must be provided")` if all three are `None`.

---

## 7. `server.py` — MCP Tool Registration

Uses `FastMCP` with a lifespan that connects/disconnects the Telegram client.

### Lifespan

```python
@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    config = Config.from_env()
    client = TelegramClientWrapper(config)
    await client.connect()      # raises RuntimeError if not authorized
    try:
        yield AppContext(client=client)
    finally:
        await client.disconnect()
```

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
  At least one of dialog_id / username / name must be provided.
  Priority: dialog_id > username > name
Returns:     dict {"ok": true}
Calls:       client.send_message(text, dialog_id, username, name)
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
mcp = FastMCP("q0t-telegram-mcp", lifespan=lifespan)
# tools registered with @mcp.tool() decorators
```

### Tool function pattern

All tool functions MUST be `async def` because they await async client methods. Example:

```python
@mcp.tool()
async def tg_me(ctx: Context) -> dict:
    client = ctx.request_context.lifespan_context.client
    user_info = await client.get_me()
    return dataclasses.asdict(user_info)
```

The `ctx` parameter is automatically injected by FastMCP when the function signature includes it. All other parameters become the tool's MCP parameter schema.

### Running

```python
def run_server() -> None:
    mcp.run()   # defaults to stdio transport; this call blocks
```

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
def auth(phone: str, password: str | None) -> None:
    """Authenticate with Telegram and save session."""
    import asyncio
    asyncio.run(_run_auth(phone, password))
```

`_run_auth` instantiates `Config.from_env()` and `AuthFlow(config)`, then calls `await auth_flow.run(phone, password)`.

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
    def from_env(cls) -> "Config":
        app_id_str = os.environ.get("TG_APP_ID")
        api_hash   = os.environ.get("TG_API_HASH")
        if not app_id_str:
            raise EnvironmentError("TG_APP_ID environment variable is not set")
        if not api_hash:
            raise EnvironmentError("TG_API_HASH environment variable is not set")
        session_path = Path.cwd() / ".telegram-mcp" / ".session"
        return cls(app_id=int(app_id_str), api_hash=api_hash, session_path=session_path)

    def session_dir(self) -> Path:
        return self.session_path.parent
```

### Session path resolution

- The session file lives at `<cwd>/.telegram-mcp/.session`
- `cwd` is wherever the user runs the `auth` command or starts the MCP server
- For Claude Code users, `cwd` will typically be the project root (where `.mcp.json` lives)
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
| `client.py` | Connection errors | Let them propagate (unexpected; FastMCP will surface to MCP caller) |
| `server.py` tools | `ValueError` from client | Return `{"error": str(e)}` in the tool response (don't crash the server) |
| `server.py` tools | `RuntimeError` from client | Propagate — FastMCP will turn it into an MCP error response |
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

`Path.cwd() / ".telegram-mcp" / "session"` — Telethon creates `session.session` at that location.

### How it is found at runtime

`Config.from_env()` always computes `Path.cwd() / ".telegram-mcp" / "session"`. This means:
- The MCP server must be started from the same directory where `auth` was run.
- For Claude Code, set `cwd` in `.mcp.json` if the default working directory differs.

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
FastMCP (server.py)
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
FastMCP returns {"ok": true} to Claude Code
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

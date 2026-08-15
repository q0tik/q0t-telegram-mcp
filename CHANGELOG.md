# Changelog

Notable changes per release. Versions before 0.6.0 are recorded only in git history.

## 0.11.0

### Added

- **Chat folders.** Six new tools: `tg_folders` (read), plus `tg_folder_create`,
  `tg_folder_update`, `tg_folder_delete`, `tg_folder_add_chats`, and
  `tg_folder_remove_chats` (write). `TG_READONLY` keeps the listing and drops the rest.
- Folder contents carry the same marked dialog IDs `tg_dialogs` returns, so the two tools
  compose without translation.
- Telegram's folder errors are translated into sentences: `FILTERS_TOO_MUCH` becomes the
  limit and what to do about it.

### Notes

- `tg_folder_update` never touches membership, and copies the stored filter rather than
  rebuilding it, so folder settings this server does not model (colour, title animation)
  survive an edit made from here.
- Shared folders (joined from an invite link) are listed with `kind: "shared"` and refuse
  writes. Invite links, folder reordering, and folder tags are not supported.

## 0.10.0

### Changed

- **Moved to the mcp 2.x SDK.** `mcp.server.fastmcp` is gone upstream; the server is now
  built on `MCPServer` from `mcp.server.mcpserver`. Tool signatures, `Annotated`/`Field`/
  `WithJsonSchema` parameter declarations, the generated `inputSchema`, tool return shapes,
  and `/health` are all unchanged — no client needs to do anything differently.
- **Dependencies are pinned exactly** (`mcp==2.0.0`, `telethon==1.44.0`, `click==8.4.2`,
  `uvicorn==0.52.1`) rather than floored. 0.9.0 shipped `mcp>=1.0.0`, mcp 2.0 removed the
  module it imported, and every fresh install broke while the test suite — run against
  `uv.lock` — stayed green. A version range is a promise about code that does not exist yet.
  `uvicorn` is now declared directly: `_serve_http` imports it by name, so it was always a
  real dependency, just borrowed from `mcp`'s tree.
- **HTTP mode connects to Telegram at startup, not on the first MCP session.** mcp 2.0's
  session manager enters the lifespan once for the process instead of once per connecting
  client. Two consequences: the Telegram connection is held for the life of the server
  rather than dropped and reopened as clients come and go, and **an unauthorized session
  now fails the boot** — the process exits with `Not authorized` instead of starting and
  answering `/health` while every tool call fails. Run `q0t-telegram-mcp check` first.
- **DNS-rebinding policy is now passed explicitly on every HTTP start.** mcp 2.0 enables the
  protection only when it recognises the host as loopback and otherwise leaves it off
  entirely, which would have silently dropped a defence on a deliberate off-host bind. The
  policy is built per start (`_transport_security`) instead of mutating server settings,
  which also removes the last piece of shared mutable state at module import.

### Notes

- `SharedTelegramClient` stays. mcp 2.0 fixed the per-session lifespan for streamable HTTP,
  but **sse still enters it per connection** (`sse_app` calls `Server.run()` inside each
  connection), so removing the shared client would reintroduce `database is locked` for
  anyone on `--transport sse`.
- **The regression test for that guarantee had quietly stopped working.** It ran against
  streamable HTTP, where mcp 2.0's session manager now holds a single client on its own —
  so it passed whether or not the sharing existed. A new sse test replaces it, and was
  checked the only way worth checking: a lifespan that builds its own client makes it fail
  with two Telegram clients for two connections, which is the bug it is there to catch.
- Removed `TransportConfig.fastmcp_transport`, dead since the server started choosing its
  app factory directly. Only tests referenced it.

## 0.9.1

### Fixed

- **Every fresh install was broken, on all versions.** The `mcp` dependency was declared as
  `>=1.0.0` with no upper bound. `mcp` 2.0 deleted `mcp.server.fastmcp` and renamed `FastMCP`
  to `MCPServer` (`mcp.server.mcpserver`), so a new install resolved 2.x and died at import
  with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` — the server never
  started. Existing installs kept working on their locked 1.x, and the test suite runs
  against `uv.lock`, so nothing here caught it. The dependency is now `mcp>=1.0.0,<2.0.0`.
  Supporting the 2.x API is a separate job.

## 0.9.0

### Added

- **`tg_send_file` sends several files at once.** Pass a list and they arrive as one
  album — the same thing Telegram does when a person drags a handful of screenshots into
  a chat. `caption` captions the album as a whole. More than ten files are split into
  albums of ten by Telethon.
- **Mixed media types are grouped, not rejected.** Telegram refuses an album holding both
  a photo and a PDF, so a mixed list is split by kind (photos/videos, audio, documents)
  and sent as one album per kind, visual first. Classification is by file extension
  (`src/tg_mcp/media.py`).
- **Paths are checked before anything is sent.** A missing path fails the whole call with
  every bad path named, instead of delivering the first albums and then erroring. `~` is
  expanded.
- **Partial delivery is reported honestly.** If a later album fails after earlier ones went
  through, `tg_send_file` returns `{"error": …, "message_ids": [ids already sent]}` — a bare
  error would invite a retry that double-posts.

### Changed

- **Breaking: `tg_send_file` takes `file_paths` (list) instead of `file_path` (string), and
  returns `message_ids` (list) instead of `message_id`.** A single file is a one-item list.
  Keeping both parameters would have left two near-identical fields in the tool schema for a
  model to choose between, which is a reliable way to get the wrong one.
- `TelegramClientWrapper.send_file()` is now `send_files()` and returns `list[int]`.

## 0.8.0

### Added

- **HTTP transport.** `q0t-telegram-mcp --transport http --host 127.0.0.1 --port 8765`
  serves MCP over streamable HTTP so several clients can share one process — and therefore
  one Telegram session. `--transport sse` is also available. Configurable via
  `TG_TRANSPORT`, `TG_HOST`, `TG_PORT`; flags win over the environment.
- **`/health` endpoint** in HTTP mode: `200` with the package version and the number of
  active MCP sessions, answerable without opening an MCP session. Reports nothing
  configuration- or credential-derived.

### Fixed

- **One Telethon client per process, not per MCP session.** FastMCP enters the lifespan once
  per session, and streamable HTTP creates a session per connecting client. Left alone, HTTP
  mode would have opened N Telegram clients against one session file inside one process —
  reproducing the `database is locked` failure it exists to fix. The client is now shared and
  reference-counted.

### Security

- HTTP mode binds `127.0.0.1` by default. The server has no authentication of its own and
  grants full access to the Telegram account behind its session, so the listen address *is*
  the access control. A non-loopback bind is allowed but warns on stderr, and FastMCP's
  DNS-rebinding allowances are widened to match so the bind is not silently broken.
- All logging goes to stderr in HTTP mode, including uvicorn's access log, which by default
  writes to stdout.

### Compatibility

- stdio remains the default: invoking the package with no arguments behaves exactly as in
  0.7.0. `TG_APP_ID`, `TG_API_HASH`, `TG_SESSION_PATH`, `TG_READONLY`, the `auth` and `check`
  subcommands, and every tool name and schema are unchanged.

## 0.7.0

### Added

- **`check` subcommand** — reports whether the saved session is still authorized. Exit `0`
  authorized, `1` not authorized, `2` could not determine. `--json` for machine parsing.
  Never creates a session file, and runs against a throwaway copy so it cannot disturb a
  running server.
- **`TG_READONLY`** — registers only the reading tools; writing tools are omitted from the
  registry entirely rather than registered and refusing. `tg_read` and `tg_click` count as
  writes.

### Changed

- **`TG_API_HASH` is optional for the server.** Telegram receives `api_hash` only during
  login, so a process that merely serves an existing session no longer needs the secret half
  of the app credentials. `auth` still requires it and now reports its absence as a one-line
  error instead of a traceback.

### Fixed

- A `@handle` passed in the `name` slot raised `Dialog not found` while the same handle in
  `username` worked. `name` now also resolves `@handles` and matches dialog usernames
  case-insensitively; display-name matching keeps priority.

## 0.6.0

### Added

- `tg_click`, `tg_react`, `tg_delete`, `tg_edit`, `tg_search`, `tg_download`.
- `reply_to` and `parse_mode` for `tg_send`; message reactions surfaced in reads.
- `TG_SESSION_PATH` — session path override, which is what makes serving several accounts
  from one working directory possible.

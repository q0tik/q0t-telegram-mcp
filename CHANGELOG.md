# Changelog

Notable changes per release. Versions before 0.6.0 are recorded only in git history.

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

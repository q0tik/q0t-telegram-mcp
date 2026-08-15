# q0t-telegram-mcp

A production-quality Telegram MCP server in Python that **actually sends messages**.

## Why This Exists

The reference Go implementation (`@chaindead/telegram-mcp`) ships with two bugs that make it silently broken:

1. **`tg_send` saves a draft instead of sending.** The implementation calls `MessagesSaveDraft` instead of `SendMessage`. Messages are stored as unsent drafts — they never arrive at the recipient.
2. **Parameter naming inconsistency.** The MCP tool advertises a parameter named `to`, but the Go struct uses the JSON tag `name`. The value passed by the caller is silently ignored and the dialog name is never set.

This project fixes both and builds a reliable, well-tested replacement.

## MCP Tools

Tools marked ✎ change state and are omitted entirely under
[`TG_READONLY`](#read-only-mode-tg_readonly).

| Tool | Parameters | Returns | Description |
|------|-----------|---------|-------------|
| `tg_me` | — | `UserInfo` | Get current Telegram account info |
| `tg_dialogs` | `unread_only` (bool, default `false`), `limit` (int, default `20`), `offset` (int, default `0`) | List of dialogs — each has `id`, `name`, `username` (or `null`), `unread_count`, type flags, `last_message`, `date` | List dialogs (chats, groups, channels) |
| `tg_dialog` | `dialog_id` (int), `username` (str), or `name` (str) — at least one required; `limit` (int, default `20`), `offset` (int, default `0`) | List of messages | Get messages from a dialog |
| `tg_send` ✎ | dialog selector — at least one required; `text` (str); `reply_to` (int, optional); `parse_mode` (`markdown` default, `html`, or `none`) | `{"ok": true, "message_id": n}` | Send a message — calls `SendMessage`, never `SaveDraft` |
| `tg_send_file` ✎ | dialog selector — at least one required; `file_paths` (list[str], one or many); `caption` (str, optional) | `{"ok": true, "message_ids": [n, …]}` | Send one or more files to a dialog; several files arrive as an album ([details](#sending-several-files)) |
| `tg_forward` ✎ | `message_id` (int); one source `from_*` selector; one destination `to_*` selector | `{"ok": true}` | Forward a message from one dialog to another |
| `tg_read` ✎ | `dialog_id` (int), `username` (str), or `name` (str) — at least one required | `{"ok": true}` | Mark all messages in a dialog as read |
| `tg_click` ✎ | dialog selector; `message_id` (int); `text` (str, button label); `row`/`col` (int, optional) | `{answer, alert, message, url?}` | Press an inline keyboard button on a bot message; returns the callback answer and the (usually edited) message |
| `tg_react` ✎ | dialog selector; `message_id` (int); `emoji` (str); `big` (bool, default `false`); `remove` (bool, default `false`) | `{"ok": true}` | React to a message with an emoji (or remove the reaction). Verify the result by reading `reactions` from `tg_dialog`. |
| `tg_edit` ✎ | dialog selector; `message_id` (int); `text` (str); `parse_mode` (`markdown` default, `html`, `none`) | `{"ok": true}` | Edit one of your own sent messages |
| `tg_delete` ✎ | dialog selector; `message_ids` (list[int]); `revoke` (bool, default `true`) | `{"ok": true, "deleted": n}` | Delete one or more messages |
| `tg_search` | `query` (str); dialog selector (optional — omit for a global search); `limit` (int, default `20`) | List of messages (each with `chat_id`/`chat_name`) | Search messages by text, per-dialog or globally |
| `tg_download` | dialog selector; `message_id` (int); `dest_dir` (str, optional) | `{path, media, size}` | Download a message's media (photo/voice/document/…) to disk |
| `tg_folders` | `resolve_names` (bool, default `true`) | List of folders — each has `id`, `title`, `kind`, `emoji`, `include`/`pinned`/`exclude` (lists of `{dialog_id, name}`), and the eight filter flags | List chat folders, including `All chats` as id `0` |
| `tg_folder_create` ✎ | `title` (str); `emoji` (str, optional); `chats` (list[str], optional); `contacts`/`non_contacts`/`groups`/`broadcasts`/`bots`/`exclude_muted`/`exclude_read`/`exclude_archived` (bool, default `false`) | `{"ok": true, "folder_id": n, "folder": {…}, "skipped": [...]}` | Create a folder ([chat references](#referring-to-chats-in-a-folder)) |
| `tg_folder_update` ✎ | folder selector — `folder_id` (int) or `title` (str); `new_title` (str, optional); `emoji` (str, optional); the eight flags (bool, optional) | `{"ok": true, "folder": {…}}` | Rename a folder or change its auto-filters; anything omitted is left as it was |
| `tg_folder_delete` ✎ | folder selector | `{"ok": true, "deleted_id": n, "deleted_title": "…"}` | Delete a folder; the chats in it are untouched |
| `tg_folder_add_chats` ✎ | `chats` (list[str]); folder selector | `{"ok": true, "added": [...], "skipped": [...], "folder": {…}}` | Add chats to a folder |
| `tg_folder_remove_chats` ✎ | `chats` (list[str]); folder selector | `{"ok": true, "removed": [...], "skipped": [...], "folder": {…}}` | Remove chats from a folder, pinned list included |

### Sending several files

`tg_send_file` always takes a list, so one file is `["/path/one.png"]` and many are just a
longer list. Files sent together arrive as an album, and `caption` captions that album as a
whole rather than any single file:

```json
{"file_paths": ["/tmp/1.png", "/tmp/2.png", "/tmp/3.png"],
 "username": "alice",
 "caption": "three screenshots"}
```
```json
{"ok": true, "message_ids": [101, 102, 103]}
```

Two Telegram rules shape the rest of the behaviour:

- **An album cannot mix media classes.** Photos and videos may share one; audio files share
  another; everything else travels as documents. A mixed list is therefore split by kind —
  by file extension — and sent as one album per kind, visual first, with the caption on the
  first. `["a.png", "b.pdf", "c.jpg"]` becomes an album of `a.png`+`c.jpg` and an album of
  `b.pdf`, and `message_ids` covers both.
- **An album holds at most ten items.** Longer runs are split into consecutive albums of ten.

Paths are checked before anything is sent: if any path is missing, the call fails naming
every bad one and nothing is delivered. `~` is expanded.

If a later album fails after earlier ones succeeded, the result carries both — the error and
the IDs that did go through:

```json
{"error": "PartialSendError: FloodWaitError: …", "message_ids": [101, 102]}
```

Retrying the whole list in that case would post the first album twice; resend only the part
that is missing.

### Reactions

Messages returned by `tg_dialog` carry a `reactions` field: a list of `{kind, count, chosen, emoji, custom_emoji_id}`, or `null` when nobody has reacted. `chosen` is `true` for the reaction *you* left, which is what makes a `tg_react` call verifiable rather than merely "returned ok".

| `kind` | Meaning |
|--------|---------|
| `emoji` | Standard unicode reaction — `emoji` holds it |
| `custom` | Custom emoji reaction — `custom_emoji_id` holds the document id |
| `paid` | Paid (star) reaction |
| `other` | Unrecognised reaction type |

Telegram normalises the emoji it stores, so what comes back is not always byte-identical to what you sent: reacting with `❤️` (U+2764 U+FE0F) reads back as `❤` (U+2764, variation selector dropped). Removal still works with either form, but comparing `sent == reactions[i].emoji` directly will surprise you — normalise before comparing.

### Buttons and what `tg_click` can press

Messages returned by `tg_dialog` carry a `buttons` field: rows of `{text, kind, url}`, or `null` when the message has no keyboard. The `kind` tells you what the button actually is:

| `kind` | Meaning | `tg_click` behaviour |
|--------|---------|----------------------|
| `callback` | Inline button that calls the bot back | Pressed; returns the callback answer and the re-fetched message |
| `url` | Inline button that opens a link | Returns `url`; no callback is sent |
| `webview` | Inline button that opens a Mini App | Returns `url`; no callback is sent |
| `reply` | Bottom reply-keyboard button, not an inline one | Raises an error — it is not pressable |
| `other` | Unrecognised button type | Raises an error |

Only `callback` buttons can be pressed. The other kinds are labelled honestly so you do not aim `tg_click` at a target that cannot respond — an unpressable button raises a clear error instead of silently doing nothing.

### Identifying a dialog

The same three selectors are available for every message tool (`tg_dialog`, `tg_send`, `tg_send_file`, `tg_read`, `tg_click`, `tg_react`, `tg_edit`, `tg_delete`, `tg_download`; and optionally `tg_search`). Supply at least one (except `tg_search`, where omitting them means a global search):

| Selector | Type | Notes |
|----------|------|-------|
| `dialog_id` | int | Numeric ID from `tg_dialogs`. Most reliable — always refers to exactly one entity. |
| `username` | str | Telegram @handle. Leading `@` is accepted but not required. |
| `name` | str | Display name (e.g. `"Alice"`). Least reliable — may match the wrong contact when names are not unique. |

When more than one selector is supplied, priority is: `dialog_id > username > name`.

A `@handle` is understood in the `name` slot too, so putting it in the "wrong" field is not a
failure. Resolution of `name` tries, in order: an exact display-name match; then — when the
value starts with `@` — a handle lookup against Telegram; then a case-insensitive match against
the `username` of your dialogs. Display name keeps priority, so nothing that resolved before
0.7.0 resolves differently now.

### Referring to chats in a folder

`tg_folder_create`, `tg_folder_add_chats`, and `tg_folder_remove_chats` take chats as a
list of strings rather than the usual three selectors — the selector convention does not
extend to a list. Each entry is read as:

| Form | Example | Meaning |
|------|---------|---------|
| Digits, optionally negative | `-1001234567890` | Numeric `dialog_id` from `tg_dialogs` — most reliable |
| Leading `@` | `@durov` | Telegram handle |
| `me` or `self` | `me` | Saved Messages |
| Anything else | `Team standup` | Display name; may be ambiguous |

Entries that do not resolve are returned in `skipped` with a reason, and the rest are still
applied. A folder itself is addressed by `folder_id` (from `tg_folders`) or `title`; when two
folders share a title the call fails naming both IDs rather than guessing.

### Folder rules Telegram enforces

- A folder must have **at least one chat or one of** `contacts`, `non_contacts`, `groups`,
  `broadcasts`, `bots`. The `exclude_*` flags only narrow a folder. Removing the last chat
  from a folder with no such flag is refused — delete the folder instead.
- **A title is at most 12 characters** — counted in characters, so 12 emoji fit and 13
  letters do not. Over the limit Telegram answers `MESSAGE_TOO_LONG`, which is why the
  length is checked here before anything is sent.
- **Folder icons are a fixed set** (💼 📁 ✈ ⭐ 🎓 💰 🏠 ❤ 👍 🎮 🤖 …). Any other emoji is
  accepted by the request and silently stored as nothing, so `emoji` comes back `null`.
- Without Premium: 10 folders, 100 chats each.
- `All chats` (`id: 0`) is a view, not a folder: it cannot be edited or deleted.
- Folders joined from an invite link come back with `kind: "shared"`; this server lists them
  but does not edit them.
- `tg_folder_update` never touches membership, and copies the stored filter rather than
  rebuilding it, so folder settings this server does not model (tab colour, title animation)
  survive an edit made from here.

## Disambiguation: when to use `dialog_id` vs `name`

Use `name` only as a fallback. Two real problems arise when you rely on it:

1. **Duplicate names.** If two contacts are both named "Adilet", name-based resolution scans your dialog list and picks whichever comes first — it may be the wrong person.
2. **Display names are not Telegram handles.** Telethon's `get_entity()` cannot resolve a human display name like `"Алёна 😍"` directly. Under the hood this server scans your cached dialog list for an exact match, which is slower and can fail if the dialog is not yet cached.

**Recommended workflow:**

1. Call `tg_dialogs` to get the list. Each entry includes both `id` and `username` (when the contact has a public handle).
2. Pass `dialog_id` (preferred) or `username` to `tg_send`, `tg_dialog`, and `tg_read`.
3. Fall back to `name` only when neither `id` nor `username` is available (e.g. phone-only contacts without a handle).

## Prerequisites

Obtain API credentials from [https://my.telegram.org/apps](https://my.telegram.org/apps).

| Variable | Required for | Notes |
|----------|--------------|-------|
| `TG_APP_ID` | always | From https://my.telegram.org/apps |
| `TG_API_HASH` | `auth` only | Not needed to run the server — see below |
| `TG_SESSION_PATH` | optional | [Choosing the session path](#choosing-the-session-path-tg_session_path) |
| `TG_READONLY` | optional | [Read-only mode](#read-only-mode-tg_readonly) |
| `TG_TRANSPORT` | optional | `stdio` (default), `http`, or `sse` — [Several clients on one machine](#several-clients-on-one-machine) |
| `TG_HOST` / `TG_PORT` | optional | Bind address in http/sse mode; defaults to `127.0.0.1:8765` |

### Why `TG_API_HASH` is not needed by the server

`api_hash` is the secret half of your app credentials, and Telegram only ever receives it
during **login** — `auth.SendCodeRequest`, `auth.ImportBotAuthorizationRequest`, and the QR
login token request. The connection a running server actually opens sends `InitConnectionRequest`,
which carries `api_id` and no hash at all.

This server never logs in: it connects to a session that `auth` produced earlier, possibly on a
different machine. So from 0.7.0 onwards the server (and `check`) start fine without
`TG_API_HASH`, and a self-documenting placeholder is substituted to satisfy Telethon's
non-empty-value check. `auth` still requires it and fails with a clear message if it is absent.

Practical upshot: a deployment that only *serves* a session no longer has to ship the secret to
wherever the MCP client config lives. Setting it anyway changes nothing — existing 0.6.x
configurations keep working untouched.

## Installation & Authentication

No repository clone is needed. Use `uvx` to run directly from PyPI.

API credentials (`TG_APP_ID` and `TG_API_HASH`) can be supplied either as environment variables
or as CLI flags — flags take priority.

### First-time authentication

```bash
# Via environment variables
TG_APP_ID=your_app_id TG_API_HASH=your_api_hash uvx q0t-telegram-mcp auth --phone +1234567890

# Via CLI flags (no env vars needed)
uvx q0t-telegram-mcp auth --phone +1234567890 --app-id your_app_id --api-hash your_api_hash
```

### With 2FA enabled

```bash
# Via environment variables
TG_APP_ID=your_app_id TG_API_HASH=your_api_hash uvx q0t-telegram-mcp auth --phone +1234567890 --password your_2fa_password

# Via CLI flags
uvx q0t-telegram-mcp auth --phone +1234567890 --app-id your_app_id --api-hash your_api_hash --password your_2fa_password
```

You will be prompted to enter the OTP sent to your Telegram app (or SMS). After successful authentication, a session file is saved locally and subsequent connections reuse it automatically.

## Checking a session (`check`)

A session can be revoked at any time from an official Telegram client (Settings → Devices).
Nothing local changes when that happens, so without asking you would only find out when the
server starts and the first tool call fails. `check` asks up front:

```bash
TG_APP_ID=your_app_id uvx q0t-telegram-mcp check
TG_APP_ID=your_app_id uvx q0t-telegram-mcp check --json
```

It takes configuration from the same places as every other command, and accepts the same
override flags: `--app-id`, `--api-hash`, `--session`. `TG_API_HASH` is not required.

| Exit code | Meaning | What to do |
|-----------|---------|------------|
| `0` | Session is authorized | Nothing |
| `1` | Telegram says the session is not authorized | Re-run `auth` |
| `2` | Could not determine — no session file, or Telegram unreachable | Check the path and the network |

Codes `1` and `2` are deliberately distinct: "this session is dead" and "I could not find out"
call for opposite reactions, so a script must be able to tell them apart.

Human output names the session file and, when authorized, who is logged in. `--json` emits:

```json
{"status": "authorized", "authorized": true, "session_path": "/home/you/tg/alice.session",
 "user": {"id": 777, "username": "alice", "first_name": "Alice", "last_name": null, "phone": "1555…"},
 "error": null}
```

Two things worth knowing:

- **`check` makes a real network call.** Telegram therefore records it as activity for that
  device and refreshes its "last seen" timestamp. It is not a purely local operation.
- **It will not create or disturb the session.** A missing file reports `2` rather than
  silently creating an empty session, and the check runs against a throwaway copy — so it is
  safe to run while a server is holding the real session file open.

## Session File Location

By default the session is stored at `.telegram-mcp/session.session` relative to whichever directory `auth` was run from. The MCP server must be started from the same directory so it can find the session.

For Claude Code users, add `.telegram-mcp/` to `.gitignore` to avoid committing credentials:

```
.telegram-mcp/
```

### Choosing the session path (`TG_SESSION_PATH`)

Set `TG_SESSION_PATH` — or pass `--session` to `auth` — to put the session file wherever you like. The flag wins over the environment variable, as with the credential flags.

```bash
TG_APP_ID=your_app_id TG_API_HASH=your_api_hash uvx q0t-telegram-mcp auth \
  --phone +1234567890 --session ~/telegram-sessions/alice.session
```

The value names the session *file*. Telethon appends the `.session` extension when it is missing, so `alice` and `alice.session` address the same file. A blank value counts as "not set" and falls back to the default above.

This is what makes **several accounts at once** possible. The default path is relative to the working directory, so two servers started from the same directory would fight over one session file; give each its own path and they coexist:

```json
{
  "mcpServers": {
    "telegram_alice": {
      "command": "uvx",
      "args": ["q0t-telegram-mcp"],
      "env": {
        "TG_APP_ID": "your_app_id",
        "TG_API_HASH": "your_api_hash",
        "TG_SESSION_PATH": "/home/you/telegram-sessions/alice.session"
      }
    },
    "telegram_bob": {
      "command": "uvx",
      "args": ["q0t-telegram-mcp"],
      "env": {
        "TG_APP_ID": "your_app_id",
        "TG_API_HASH": "your_api_hash",
        "TG_SESSION_PATH": "/home/you/telegram-sessions/bob.session"
      }
    }
  }
}
```

Both entries may share one `TG_APP_ID`/`TG_API_HASH`: the app credentials identify the *application*, the session identifies the *account*.

## Claude Code Configuration

Add the following to your project's `.mcp.json`:

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

Claude Code starts the server from your project root, so run `auth` from the same directory before using the tools.

`TG_API_HASH` may be omitted from this block — the server does not need it (see
[above](#why-tg_api_hash-is-not-needed-by-the-server)). Only `auth` does.

## Several clients on one machine

**If two agent runs can overlap, you need HTTP mode.** Not as an optimisation — the default
stdio setup corrupts your login when they do.

### What goes wrong

MCP clients spawn a stdio server **per run**. Two runs at once means two processes of this
package against one Telethon session file, and both die:

```
sqlite3.OperationalError: database is locked
  telethon/sessions/sqlite.py  c.execute('delete from sessions')
```

Telethon *writes* to the session when it starts — it does not merely read it.

### Why the obvious workarounds are worse

Giving each process its own copy of the session file removes the lock error and replaces it
with a far worse one. The copies share an `auth_key`, and Telegram invalidates a key it sees
used from two places at once: `AUTH_KEY_DUPLICATED`. The account is then genuinely logged out
and has to sign in again. Telethon documents this as a protection that cannot be turned off —
if you want several clients, you make several *sessions*, not several copies of one.

| Workaround | Lock error | Account survives |
|------------|-----------|------------------|
| Copy the session file per process | fixed | ❌ `AUTH_KEY_DUPLICATED` |
| `StringSession` in the environment | fixed | ❌ same key, same outcome — and the secret spreads |
| A separate login per process | fixed | ✅ but that is N accounts, not N clients |
| **One process, clients over HTTP** | **fixed** | **✅** |

### How to run it

Start one long-lived owner of the session:

```bash
TG_APP_ID=your_app_id uvx q0t-telegram-mcp --transport http --port 8765
```

Then point every client at it instead of letting them spawn their own:

```json
{
  "mcpServers": {
    "telegram": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

The exact key differs per client — `url`, `httpUrl`, or `codex mcp add --url`; all of them
speak streamable HTTP.

| Variable | Flag | Default | Meaning |
|----------|------|---------|---------|
| `TG_TRANSPORT` | `--transport` | `stdio` | `stdio`, `http` (or `streamable-http`), `sse` |
| `TG_HOST` | `--host` | `127.0.0.1` | Address to bind in http/sse mode |
| `TG_PORT` | `--port` | `8765` | Port to bind in http/sse mode |

Flags win over environment variables. **stdio remains the default**, so existing configs that
invoke the package with no arguments are unaffected.

`GET /health` returns `200` with the version, so a supervisor can tell the process is up
without opening an MCP session:

```json
{"status": "ok", "name": "q0t-telegram-mcp", "version": "0.10.0", "active_sessions": 1}
```

Since 0.10.0 the Telegram connection is opened when the process starts, not when the first
MCP session arrives, and is held until the server stops. A server with no authorized session
therefore **fails to start** rather than booting and answering `/health` while every tool
call fails — so `/health` answering at all already means Telegram is connected. Run
`q0t-telegram-mcp check` before starting if you are unsure the session is still good.

The same change makes `active_sessions` a hold count rather than a client count: in `http`
mode it reads `1` for as long as the server runs, however many clients are attached. Do not
use it to count clients — under `sse` it still tracks connections, so the number means
different things per transport.

### ⚠️ Do not expose the port

The default bind is `127.0.0.1` deliberately. **This server has no authentication of its
own** — whoever can reach the port can read and send messages as the account behind the
session. The listen address is the only access control there is.

`--host 0.0.0.0` is permitted, because you may be binding inside a container whose network is
already isolated, but it prints a warning at startup and you are then responsible for
whatever can reach that port. Do not put it on a public interface without something in front
of it.

## Read-only mode (`TG_READONLY`)

Set `TG_READONLY` to give a model access to your correspondence without letting it act as you:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "uvx",
      "args": ["q0t-telegram-mcp"],
      "env": {
        "TG_APP_ID": "your_app_id",
        "TG_READONLY": "1"
      }
    }
  }
}
```

Accepted as "on": `1`, `true`, `yes` (any case). Anything else — including unset — leaves the
full tool set registered, which is the pre-0.7.0 behaviour.

Writing tools are **not registered at all** in this mode, rather than registered and refusing.
A tool the client never receives cannot be called, argued with, or worked around; a tool that
exists and returns an error is a capability the model can see and keep reaching for.

| Available in read-only | Hidden in read-only |
|------------------------|---------------------|
| `tg_me`, `tg_dialogs`, `tg_dialog`, `tg_search`, `tg_download` | `tg_send`, `tg_send_file`, `tg_forward`, `tg_edit`, `tg_delete`, `tg_react`, `tg_read`, `tg_click` |

Two entries on the hidden side are worth explaining, because they sound passive:

- **`tg_read`** marks a dialog as read. That destroys unread state and is visible to the other party.
- **`tg_click`** presses a bot's inline button, which delivers a callback query to that bot — it
  can trigger anything the bot does, including payments or destructive actions.

`tg_download` stays on the read side: it writes to local disk but changes nothing on the account.

## Publishing to PyPI (Maintainers)

```bash
uv build
UV_PUBLISH_TOKEN="$(awk '/^password/{print $3; exit}' ~/.pypirc)" \
  uv publish dist/q0t_telegram_mcp-<version>.tar.gz \
             dist/q0t_telegram_mcp-<version>-py3-none-any.whl
```

`uv publish` does **not** read `~/.pypirc` (unlike `twine`), so the token has to be passed
explicitly. Name the files of the version being released: `dist/` accumulates older builds, and
re-uploading an existing version aborts the run.

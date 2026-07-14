# q0t-telegram-mcp

A production-quality Telegram MCP server in Python that **actually sends messages**.

## Why This Exists

The reference Go implementation (`@chaindead/telegram-mcp`) ships with two bugs that make it silently broken:

1. **`tg_send` saves a draft instead of sending.** The implementation calls `MessagesSaveDraft` instead of `SendMessage`. Messages are stored as unsent drafts — they never arrive at the recipient.
2. **Parameter naming inconsistency.** The MCP tool advertises a parameter named `to`, but the Go struct uses the JSON tag `name`. The value passed by the caller is silently ignored and the dialog name is never set.

This project fixes both and builds a reliable, well-tested replacement.

## MCP Tools

| Tool | Parameters | Returns | Description |
|------|-----------|---------|-------------|
| `tg_me` | — | `UserInfo` | Get current Telegram account info |
| `tg_dialogs` | `unread_only` (bool, default `false`), `limit` (int, default `20`), `offset` (int, default `0`) | List of dialogs — each has `id`, `name`, `username` (or `null`), `unread_count`, type flags, `last_message`, `date` | List dialogs (chats, groups, channels) |
| `tg_dialog` | `dialog_id` (int), `username` (str), or `name` (str) — at least one required; `limit` (int, default `20`), `offset` (int, default `0`) | List of messages | Get messages from a dialog |
| `tg_send` | dialog selector — at least one required; `text` (str); `reply_to` (int, optional); `parse_mode` (`markdown` default, `html`, or `none`) | `{"ok": true, "message_id": n}` | Send a message — calls `SendMessage`, never `SaveDraft` |
| `tg_send_file` | dialog selector — at least one required; `file_path` (str); `caption` (str, optional) | `{"ok": true, "message_id": n}` | Send a file to a dialog |
| `tg_forward` | `message_id` (int); one source `from_*` selector; one destination `to_*` selector | `{"ok": true}` | Forward a message from one dialog to another |
| `tg_read` | `dialog_id` (int), `username` (str), or `name` (str) — at least one required | `{"ok": true}` | Mark all messages in a dialog as read |
| `tg_click` | dialog selector; `message_id` (int); `text` (str, button label); `row`/`col` (int, optional) | `{answer, alert, message, url?}` | Press an inline keyboard button on a bot message; returns the callback answer and the (usually edited) message |
| `tg_react` | dialog selector; `message_id` (int); `emoji` (str); `big` (bool, default `false`); `remove` (bool, default `false`) | `{"ok": true}` | React to a message with an emoji (or remove the reaction). Verify the result by reading `reactions` from `tg_dialog`. |
| `tg_edit` | dialog selector; `message_id` (int); `text` (str); `parse_mode` (`markdown` default, `html`, `none`) | `{"ok": true}` | Edit one of your own sent messages |
| `tg_delete` | dialog selector; `message_ids` (list[int]); `revoke` (bool, default `true`) | `{"ok": true, "deleted": n}` | Delete one or more messages |
| `tg_search` | `query` (str); dialog selector (optional — omit for a global search); `limit` (int, default `20`) | List of messages (each with `chat_id`/`chat_name`) | Search messages by text, per-dialog or globally |
| `tg_download` | dialog selector; `message_id` (int); `dest_dir` (str, optional) | `{path, media, size}` | Download a message's media (photo/voice/document/…) to disk |

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

## Disambiguation: when to use `dialog_id` vs `name`

Use `name` only as a fallback. Two real problems arise when you rely on it:

1. **Duplicate names.** If two contacts are both named "Adilet", name-based resolution scans your dialog list and picks whichever comes first — it may be the wrong person.
2. **Display names are not Telegram handles.** Telethon's `get_entity()` cannot resolve a human display name like `"Алёна 😍"` directly. Under the hood this server scans your cached dialog list for an exact match, which is slower and can fail if the dialog is not yet cached.

**Recommended workflow:**

1. Call `tg_dialogs` to get the list. Each entry includes both `id` and `username` (when the contact has a public handle).
2. Pass `dialog_id` (preferred) or `username` to `tg_send`, `tg_dialog`, and `tg_read`.
3. Fall back to `name` only when neither `id` nor `username` is available (e.g. phone-only contacts without a handle).

## Prerequisites

Obtain API credentials from [https://my.telegram.org/apps](https://my.telegram.org/apps):

| Variable | Where to get it |
|----------|----------------|
| `TG_APP_ID` | https://my.telegram.org/apps |
| `TG_API_HASH` | https://my.telegram.org/apps |

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

## Session File Location

The session is stored at `.telegram-mcp/session.session` relative to whichever directory `auth` was run from. The MCP server must be started from the same directory so it can find the session.

For Claude Code users, add `.telegram-mcp/` to `.gitignore` to avoid committing credentials:

```
.telegram-mcp/
```

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

## Publishing to PyPI (Maintainers)

```bash
uv build
uv publish
```

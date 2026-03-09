# q0t-telegram-mcp

A production-quality Telegram MCP server in Python that **actually sends messages**.

## Why This Exists

The reference Go implementation (`@chaindead/telegram-mcp`) ships with two bugs that make it silently broken:

1. **`tg_send` saves a draft instead of sending.** The implementation calls `MessagesSaveDraft` instead of `SendMessage`. Messages are stored as unsent drafts — they never arrive at the recipient.
2. **Parameter naming inconsistency.** The MCP tool advertises a parameter named `to`, but the Go struct uses the JSON tag `name`. The value passed by the caller is silently ignored and the dialog name is never set.

This project fixes both and builds a reliable, well-tested replacement.

## MCP Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `tg_me` | — | Get current Telegram account info |
| `tg_dialogs` | `unread_only` (bool, default `false`), `limit` (int, default `20`), `offset` (int, default `0`) | List dialogs (chats, groups, channels) |
| `tg_dialog` | `name` (str), `limit` (int, default `20`), `offset` (int, default `0`) | Get messages from a dialog |
| `tg_send` | `name` (str), `text` (str) | Send a message — calls `SendMessage`, never `SaveDraft` |
| `tg_read` | `name` (str) | Mark all messages in a dialog as read |

`name` accepts a display name, `@username`, or phone number (e.g. `+1234567890`).

## Prerequisites

Obtain API credentials from [https://my.telegram.org/apps](https://my.telegram.org/apps):

| Variable | Where to get it |
|----------|----------------|
| `TG_APP_ID` | https://my.telegram.org/apps |
| `TG_API_HASH` | https://my.telegram.org/apps |

## Installation & Authentication

No repository clone is needed. Use `uvx` to run directly from PyPI.

### First-time authentication

```bash
TG_APP_ID=your_app_id TG_API_HASH=your_api_hash uvx q0t-telegram-mcp auth --phone +1234567890
```

### With 2FA enabled

```bash
TG_APP_ID=your_app_id TG_API_HASH=your_api_hash uvx q0t-telegram-mcp auth --phone +1234567890 --password your_2fa_password
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

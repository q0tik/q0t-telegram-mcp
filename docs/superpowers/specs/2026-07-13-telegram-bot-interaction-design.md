# Telegram MCP — Bot Interaction & Message Ops (v0.5.0)

**Date:** 2026-07-13
**Status:** Approved (design)
**Base:** v0.4.0 (Telethon 1.44, TL layer 227)

## Overview

Expand `q0t-telegram-mcp` from a send/read client into a full interactive
Telegram client that can drive bots through inline menus and perform common
message operations. The account is a **user account** (`@q0tik`), not a bot —
all capabilities are implemented via Telethon's user-facing API.

The unifying use case: **let Claude drive Telegram end-to-end**, including
bots that expose inline keyboards (navigate menus, confirm, pick from lists).

## Goals

- Click inline keyboard buttons on bot messages and observe the result.
- Make message reads self-sufficient: surface inline buttons and media type so
  the model is never "blind".
- Round out message operations: reply, edit, delete, react, search, download.
- Support formatted text (markdown/html) explicitly.

## Non-Goals (out of scope for this spec)

- Reply keyboards (non-inline bottom keyboards) — only **inline** keyboards.
- Custom (premium) emoji reactions — standard unicode emoji only.
- Pinned-message management, typing/presence status, `tg_whois` entity lookup.
- Editing/deleting others' messages beyond what Telegram permits for a user.

## Tool Inventory (13 total)

| Tool | Change | Signature → Return |
|------|--------|--------------------|
| `tg_me` | — | unchanged |
| `tg_dialogs` | — | unchanged |
| `tg_dialog` | **modified** | `Message` now carries `buttons` and `media` |
| `tg_send` | **modified** | `+reply_to: int?`, `+parse_mode: markdown\|html\|none` |
| `tg_send_file` | — | unchanged |
| `tg_forward` | — | unchanged |
| `tg_read` | — | unchanged |
| `tg_click` | **new** | `(dialog, message_id, text, row?, col?)` → `{answer, alert, message, url?}` |
| `tg_react` | **new** | `(dialog, message_id, emoji, big=False, remove=False)` → `{ok}` |
| `tg_delete` | **new** | `(dialog, message_ids: list[int], revoke=True)` → `{ok, deleted}` |
| `tg_edit` | **new** | `(dialog, message_id, text, parse_mode?)` → `{ok}` |
| `tg_search` | **new** | `(query, dialog?, limit=20)` → `list[Message]` |
| `tg_download` | **new** | `(dialog, message_id, dest_dir?)` → `{path, media, size}` |

All dialog selectors follow the existing pattern: exactly one of
`dialog_id` (preferred) / `username` / `name`, resolved by `_resolve_entity`.

## Data Model Changes (`models.py`)

### `Button` (new, frozen dataclass)

```python
@dataclass(frozen=True)
class Button:
    text: str                       # visible label
    kind: str                       # "callback" | "url" | "other"
    url: str | None = None          # populated only for kind == "url"
```

### `Message` (extended)

```python
buttons: list[list[Button]] | None = None   # rows of inline buttons; None if none
media: str | None = None                    # e.g. "photo", "voice", "document: report.pdf"; None for text
chat_id: int | None = None                  # populated only by tg_search results
chat_name: str | None = None                # populated only by tg_search results
```

- Position of a button is implied by its `(row, col)` in the nested list.
- `media` is a short human-readable descriptor derived from Telethon's
  `message.photo/voice/video/audio/document/sticker/gif/poll/contact/geo`.
  For documents, append the filename when available.
- `chat_id`/`chat_name` are `None` for normal `tg_dialog` reads; only
  `tg_search` (which can span dialogs) fills them.

Existing `Message` fields (`id`, `date`, `text`, `out`, `sender`) are unchanged.
New fields default to `None`, so existing serialization/tests stay valid.

## Behavior & Telethon Mapping

### `tg_dialog` (modified)
When building each `Message`, also read `message.buttons` → `list[list[Button]]`
(mapping each `MessageButton` to `text`, `kind`, `url`) and derive `media`.
Empty for the vast majority of messages (cheap).

### `tg_send` (modified)
Add `reply_to: int | None` and `parse_mode`. Telethon:
`client.send_message(entity, text, reply_to=reply_to, parse_mode=<pm>)`.

**parse_mode note:** Telethon's default parse mode is markdown, so `tg_send`
already interprets `**bold**` today. To avoid a silent behavior change,
`parse_mode` **defaults to `"markdown"`**. `"none"` sends literal text;
`"html"` uses HTML entities.

### `tg_click` (new)
1. `_resolve_entity(...)` → entity.
2. Fetch the target message by `message_id`.
3. Read `message.buttons`; locate the button by `text` (and `row`/`col` if
   given, to disambiguate duplicate labels).
4. If the button is a **URL** button → return `{answer: null, alert: false,
   url: <button.url>, message: <same message>}` without a network call.
5. If a **callback** button → `await message.click(row, col)` →
   `BotCallbackAnswer`. Return `answer = ba.message` (popup/toast text, may be
   None), `alert = ba.alert`.
6. Re-fetch the message (bots typically edit the menu in place, same
   `message_id`) and include the updated `Message` (with new `buttons`) so the
   model sees the next state.

Errors: no buttons → `"Message <id> has no inline buttons"`; text not found →
`"Button not found: <text>. Available: [<labels>]"`; non-bot / callback
timeout → surfaced via `_error_message` as a short string.

### `tg_react` (new)
`client.send_reaction(entity, message_id, reaction=<emoji>, big=big)`.
`remove=True` → `reaction=None` clears the reaction. Return `{ok: True}`.

### `tg_delete` (new)
`client.delete_messages(entity, message_ids, revoke=revoke)`.
`revoke=True` (default) deletes for everyone where permitted. Accepts a list of
ids. Return `{ok: True, deleted: <count>}`.

### `tg_edit` (new)
`client.edit_message(entity, message_id, text, parse_mode=<pm>)`. Only the
account's own messages; Telethon raises otherwise → clean error. Return
`{ok: True}`. Same `parse_mode` default (`markdown`) as `tg_send`.

### `tg_search` (new)
- With a dialog selector: `client.get_messages(entity, search=query, limit=limit)`.
- Without: global search via `client.get_messages(None, search=query,
  limit=limit)` (Telethon `messages.searchGlobal`).
Returns `list[Message]`; for global results, `chat_id`/`chat_name` are filled
from each message's `chat`/`peer`.

### `tg_download` (new)
1. Resolve entity, fetch message by id.
2. If no media → `"Message <id> has no downloadable media"`.
3. `dest = dest_dir or <cwd>/.telegram-mcp/downloads/`; create if missing.
4. `path = await client.download_media(message, file=dest)`.
5. Return `{path: <abs path>, media: <descriptor>, size: <bytes>}`.

## Error Handling

Builds on the v0.4.0 `_error_message()` guard (every tool already catches
`Exception` and returns a short, truncated, single-line error — no raw buffers
leak). New tools add specific, actionable `ValueError`s (listed per tool above).
Selector validation ("exactly one of …") stays a pre-`try` `ValueError`, as in
existing tools.

## Client Wrapper (`client.py`)

New/changed methods on `TelegramClientWrapper`, each thin over Telethon:
`click_button`, `react`, `delete`, `edit_message`, `search`, `download_media`;
`send_message` gains `reply_to`/`parse_mode`. Button/media extraction from a
Telethon message is factored into small helpers (`_extract_buttons`,
`_describe_media`) reused by `get_messages` and `tg_search`.

## Testing

Mirror the existing style (`tests/test_server.py` mocks the wrapper;
`tests/test_client.py` mocks the Telethon client):

- **Server tests:** one class per new tool — happy path, selector validation,
  error dict on failure. `tg_click`: callback path (answer + updated message),
  URL-button path, "no buttons", "button not found". `tg_delete`: multiple ids
  + count. `tg_search`: dialog vs global. `tg_download`: path/size return, "no
  media". `parse_mode`/`reply_to` pass-through on `tg_send`/`tg_edit`.
- **Model tests:** `Button`/`Message` serialize via `dataclasses.asdict`; new
  fields default to `None`.
- **Client tests:** `_extract_buttons` / `_describe_media` map representative
  Telethon objects; `click_button` locates by text and by `(row,col)`.

Coverage stays ≥ 80% (`fail_under = 80`).

## Versioning & Release

- Bump `pyproject.toml` version `0.4.0 → 0.5.0` (feature release).
- No new runtime dependencies (all Telethon 1.44 built-ins).
- Ship via the established flow: branch → PR → merge → `uv build` →
  `uv publish` → `uv tool upgrade q0t-telegram-mcp`.

## File Organization

Stay in `server.py` / `client.py` (≈13 tools, ~350 lines) to match the current
structure. Split into modules only if a file grows unwieldy during
implementation.

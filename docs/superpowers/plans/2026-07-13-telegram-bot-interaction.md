# Telegram Bot Interaction & Message Ops — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `q0t-telegram-mcp` into a full interactive Telegram client — click bot inline keyboards, and react/delete/edit/search/download/reply/format messages — all from a user account.

**Architecture:** Thin async wrapper methods on `TelegramClientWrapper` (client.py) over Telethon 1.44; each exposed as a FastMCP tool in server.py. Message reads gain `buttons`/`media` via shared extraction helpers; a shared `_to_message` builder centralizes Telethon-message → `Message` conversion so reads, clicks, and search stay consistent.

**Tech Stack:** Python 3.12+, Telethon 1.44, FastMCP (`mcp>=1.0`), pydantic, pytest + pytest-asyncio, uv.

## Global Constraints

- Telethon floor: `telethon>=1.44.0` (already in place; do not lower).
- Package version bumps `0.4.0 → 0.5.0` in `pyproject.toml`.
- No new runtime dependencies — Telethon 1.44 built-ins only.
- Every tool catches `Exception` and returns a short error via the existing `_error_message()` (server.py); never let a raw exception escape a tool.
- Dialog selectors: exactly one of `dialog_id` (preferred) / `username` / `name`; validate with a pre-`try` `ValueError`; resolution goes through `_resolve_entity`.
- Optional params in tool signatures use the existing `Annotated[Optional[T], Field(...), WithJsonSchema({...})]` idiom (flat types, no `anyOf`) — keep it; do NOT switch to bare `Optional`.
- Coverage must stay ≥ 80% (`fail_under = 80`).
- Test style: `tests/test_server.py` mocks the wrapper (`_make_mock_client`, `_make_ctx`); `tests/test_client.py` mocks the Telethon client. Set `AsyncMock` explicitly for any new wrapper method a server test needs.
- Run tests with `uv run pytest`. Commit after every green step.

---

### Task 1: Model layer — `Button` + extended `Message`

**Files:**
- Modify: `src/tg_mcp/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Button(text: str, kind: str, url: str | None = None)`; `Message` gains `buttons: list[list[Button]] | None = None`, `media: str | None = None`, `chat_id: int | None = None`, `chat_name: str | None = None` (all defaulted, appended after existing fields).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
import dataclasses
from tg_mcp.models import Button, Message


def test_button_serializes():
    b = Button(text="Yes", kind="callback")
    assert dataclasses.asdict(b) == {"text": "Yes", "kind": "callback", "url": None}


def test_message_new_fields_default_none():
    m = Message(id=1, date="2026-07-13T00:00:00+00:00", text="hi", out=False, sender="A")
    d = dataclasses.asdict(m)
    assert d["buttons"] is None
    assert d["media"] is None
    assert d["chat_id"] is None
    assert d["chat_name"] is None


def test_message_with_buttons_serializes_nested():
    m = Message(
        id=2, date="2026-07-13T00:00:00+00:00", text=None, out=False, sender="Bot",
        buttons=[[Button(text="A", kind="callback"), Button(text="Docs", kind="url", url="https://x")]],
        media="photo",
    )
    d = dataclasses.asdict(m)
    assert d["buttons"][0][1]["url"] == "https://x"
    assert d["media"] == "photo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -k "button or new_fields or nested" -v`
Expected: FAIL with `ImportError: cannot import name 'Button'`.

- [ ] **Step 3: Write minimal implementation**

In `src/tg_mcp/models.py`, add the `Button` dataclass above `Message` and extend `Message`:

```python
@dataclass(frozen=True)
class Button:
    """A single inline-keyboard button on a bot message."""

    text: str                 # visible label
    kind: str                 # "callback" | "url" | "other"
    url: str | None = None    # populated only for kind == "url"
```

Append to `Message` (after `sender`):

```python
    buttons: "list[list[Button]] | None" = None  # rows of inline buttons; None if none
    media: str | None = None                     # e.g. "photo", "document: report.pdf"; None for text
    chat_id: int | None = None                   # populated only by tg_search results
    chat_name: str | None = None                 # populated only by tg_search results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (all model tests).

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/models.py tests/test_models.py
git commit -m "feat: add Button model and extend Message with buttons/media/chat fields"
```

---

### Task 2: Extraction helpers + buttons/media in `tg_dialog`

**Files:**
- Modify: `src/tg_mcp/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces (module-level in client.py): `_extract_buttons(message) -> list[list[Button]] | None`, `_describe_media(message) -> str | None`.
- Produces (method): `TelegramClientWrapper._to_message(self, m, chat_id=None, chat_name=None) -> Message` (async).
- Consumes: `Button`, `Message` from Task 1.
- `get_messages` is refactored to build each result via `_to_message`, so `tg_dialog` output now includes `buttons`/`media`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_client.py`:

```python
from types import SimpleNamespace
from tg_mcp.client import _extract_buttons, _describe_media
from tg_mcp.models import Button


def _fake_button(text, url=None, data=None):
    return SimpleNamespace(text=text, url=url, data=data)


def test_extract_buttons_none_when_no_markup():
    msg = SimpleNamespace(buttons=None)
    assert _extract_buttons(msg) is None


def test_extract_buttons_classifies_kinds():
    msg = SimpleNamespace(buttons=[[_fake_button("Go", data=b"x"), _fake_button("Site", url="https://s")]])
    rows = _extract_buttons(msg)
    assert rows == [[Button("Go", "callback", None), Button("Site", "url", "https://s")]]


def test_describe_media_photo_and_document_and_none():
    photo = SimpleNamespace(photo=True, voice=None, video_note=None, video=None,
                            audio=None, gif=None, sticker=None, document=None,
                            poll=None, contact=None, geo=None, web_preview=None)
    assert _describe_media(photo) == "photo"
    doc_attr = SimpleNamespace(file_name="report.pdf")
    document = SimpleNamespace(photo=None, voice=None, video_note=None, video=None,
                              audio=None, gif=None, sticker=None,
                              document=SimpleNamespace(attributes=[doc_attr]),
                              poll=None, contact=None, geo=None, web_preview=None)
    assert _describe_media(document) == "document: report.pdf"
    text = SimpleNamespace(photo=None, voice=None, video_note=None, video=None,
                          audio=None, gif=None, sticker=None, document=None,
                          poll=None, contact=None, geo=None, web_preview=None)
    assert _describe_media(text) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py -k "extract_buttons or describe_media" -v`
Expected: FAIL with `ImportError: cannot import name '_extract_buttons'`.

- [ ] **Step 3: Write minimal implementation**

In `src/tg_mcp/client.py`, add module-level helpers (after imports; import `Button`):

```python
from .models import Button, Dialog, Message, UserInfo


def _extract_buttons(message) -> "list[list[Button]] | None":
    """Map a Telethon message's inline keyboard to nested Button rows."""
    rows = getattr(message, "buttons", None)
    if not rows:
        return None
    result: list[list[Button]] = []
    for row in rows:
        brow: list[Button] = []
        for b in row:
            url = getattr(b, "url", None)
            if url:
                brow.append(Button(text=b.text, kind="url", url=url))
            elif getattr(b, "data", None) is not None:
                brow.append(Button(text=b.text, kind="callback"))
            else:
                brow.append(Button(text=b.text, kind="other"))
        result.append(brow)
    return result


def _describe_media(message) -> "str | None":
    """Return a short human-readable descriptor of a message's media, or None."""
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "gif", None):
        return "gif"
    if getattr(message, "sticker", None):
        return "sticker"
    document = getattr(message, "document", None)
    if document:
        name = None
        for attr in getattr(document, "attributes", []):
            if hasattr(attr, "file_name"):
                name = attr.file_name
        return f"document: {name}" if name else "document"
    if getattr(message, "poll", None):
        return "poll"
    if getattr(message, "contact", None):
        return "contact"
    if getattr(message, "geo", None):
        return "geo"
    if getattr(message, "web_preview", None):
        return "webpage"
    return None
```

Add the `_to_message` method to `TelegramClientWrapper`:

```python
    async def _to_message(self, m, chat_id: int | None = None, chat_name: str | None = None) -> Message:
        """Convert a Telethon message into the Message model (with buttons/media)."""
        sender = await m.get_sender()
        sender_name: str | None = None
        if sender is not None:
            sender_name = telethon.utils.get_display_name(sender) or None
        return Message(
            id=m.id,
            date=m.date.isoformat(),
            text=m.text,
            out=bool(m.out),
            sender=sender_name,
            buttons=_extract_buttons(m),
            media=_describe_media(m),
            chat_id=chat_id,
            chat_name=chat_name,
        )
```

Refactor `get_messages` — replace the `for m in msgs:` result-building block with:

```python
        return [await self._to_message(m) for m in msgs]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py tests/test_client.py
git commit -m "feat: extract inline buttons and media type into message reads"
```

---

### Task 3: `parse_mode` + `reply_to` on `tg_send`

**Files:**
- Modify: `src/tg_mcp/client.py` (add `_pm`, extend `send_message`)
- Modify: `src/tg_mcp/server.py` (extend `tg_send`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces (client.py module-level): `_pm(parse_mode: str | None) -> str | None` mapping `"markdown"→"md"`, `"html"→"html"`, `"none"→None`, default `"md"`.
- Modifies: `TelegramClientWrapper.send_message(self, text, dialog_id=None, username=None, name=None, reply_to=None, parse_mode="markdown")`.
- Modifies: `tg_send` tool signature gains `reply_to` and `parse_mode`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py` (class `TestTgSend`):

```python
    async def test_tg_send_passes_reply_to_and_parse_mode(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_send(ctx, text="Hi", dialog_id=5, reply_to=42, parse_mode="html")
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=5, username=None, name=None, reply_to=42, parse_mode="html"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "reply_to_and_parse_mode" -v`
Expected: FAIL — `tg_send() got an unexpected keyword argument 'reply_to'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`, add module-level:

```python
def _pm(parse_mode: str | None) -> str | None:
    """Normalize a public parse_mode value to what Telethon expects."""
    if parse_mode is None:
        return "md"
    p = parse_mode.strip().lower()
    if p in ("none", "plain", ""):
        return None
    if p == "html":
        return "html"
    return "md"  # markdown / md / anything else
```

Extend `send_message` signature and body:

```python
    async def send_message(
        self,
        text: str,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        reply_to: int | None = None,
        parse_mode: str | None = "markdown",
    ) -> None:
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.send_message(entity, text, reply_to=reply_to, parse_mode=_pm(parse_mode))
```

In `server.py`, extend `tg_send`. Add params (after `name`):

```python
    reply_to: Annotated[Optional[int], Field(description="Message ID to reply to."), WithJsonSchema({"type": "integer", "description": "Message ID to reply to."})] = None,
    parse_mode: Annotated[str, Field(description="Text formatting: 'markdown' (default), 'html', or 'none' for literal text.")] = "markdown",
```

And forward them:

```python
        await client.send_message(text=text, dialog_id=dialog_id, username=username, name=name, reply_to=reply_to, parse_mode=parse_mode)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgSend" -v`
Expected: PASS (new test + existing tg_send tests — existing calls omit reply_to/parse_mode which now default).

Note: existing `test_tg_send_passes_name_and_text` asserts `send_message` called with only `text/dialog_id/username/name`. Update those existing assertions to include `reply_to=None, parse_mode="markdown"`. Do this in this step: edit each `client.send_message.assert_called_once_with(...)` in `TestTgSend` and `TestTgSendDisambiguation` to append `reply_to=None, parse_mode="markdown"`.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add reply_to and parse_mode to tg_send"
```

---

### Task 4: `tg_click` — press inline buttons

**Files:**
- Modify: `src/tg_mcp/client.py` (add `click_button`)
- Modify: `src/tg_mcp/server.py` (add `tg_click` tool)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `TelegramClientWrapper.click_button(self, dialog_id, username, name, message_id, text, row=None, col=None) -> dict` returning `{"answer": str|None, "alert": bool, "message": Message|None}` for callback buttons, or `{"answer": None, "alert": False, "url": str, "message": Message}` for URL buttons.
- Produces: `tg_click` tool returning that dict (with `message` as a plain dict).
- Consumes: `_to_message` (Task 2), `_resolve_entity`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py`:

```python
from unittest.mock import AsyncMock
from tg_mcp.server import tg_click
from tg_mcp.models import Message


class TestTgClick:
    async def test_tg_click_returns_answer_and_updated_message(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        updated = Message(id=7, date="2026-07-13T00:00:00+00:00", text="Menu 2",
                          out=False, sender="Bot", buttons=None, media=None)
        client.click_button = AsyncMock(return_value={"answer": "Done!", "alert": False, "message": updated})
        ctx = _make_ctx(client)
        result = await tg_click(ctx, dialog_id=1, message_id=7, text="Next")
        assert result["answer"] == "Done!"
        assert result["alert"] is False
        assert result["message"]["text"] == "Menu 2"
        client.click_button.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, text="Next", row=None, col=None
        )

    async def test_tg_click_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_click(ctx, message_id=7, text="Next")

    async def test_tg_click_error_returns_error_dict(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.click_button = AsyncMock(side_effect=ValueError("Message 7 has no inline buttons"))
        ctx = _make_ctx(client)
        result = await tg_click(ctx, dialog_id=1, message_id=7, text="Next")
        assert "error" in result and "no inline buttons" in result["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "TestTgClick" -v`
Expected: FAIL — `cannot import name 'tg_click'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`, add:

```python
    async def click_button(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_id: int = 0,
        text: str = "",
        row: int | None = None,
        col: int | None = None,
    ) -> dict:
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        message = await self._client.get_messages(entity, ids=message_id)
        if message is None:
            raise ValueError(f"Message not found: id={message_id}")
        rows = message.buttons
        if not rows:
            raise ValueError(f"Message {message_id} has no inline buttons")
        target = None
        labels: list[str] = []
        for i, brow in enumerate(rows):
            for j, b in enumerate(brow):
                labels.append(b.text)
                if b.text == text and (row is None or row == i) and (col is None or col == j):
                    target = (i, j, b)
                    break
            if target is not None:
                break
        if target is None:
            raise ValueError(f"Button not found: {text}. Available: {labels}")
        i, j, btn = target
        if getattr(btn, "url", None):
            return {"answer": None, "alert": False, "url": btn.url,
                    "message": await self._to_message(message)}
        answer = await message.click(i, j)
        updated = await self._client.get_messages(entity, ids=message_id)
        return {
            "answer": getattr(answer, "message", None),
            "alert": bool(getattr(answer, "alert", False)),
            "message": await self._to_message(updated) if updated else None,
        }
```

In `server.py`, add the tool (mirror the selector idiom; convert `message` Message → dict):

```python
@mcp.tool()
async def tg_click(
    ctx: Context,
    message_id: Annotated[int, Field(description="ID of the bot message whose inline keyboard to click.")],
    text: Annotated[str, Field(description="Visible label of the button to press.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle."), WithJsonSchema({"type": "string", "description": "Telegram @handle."})] = None,
    name: Annotated[Optional[str], Field(description="Display name."), WithJsonSchema({"type": "string", "description": "Display name."})] = None,
    row: Annotated[Optional[int], Field(description="Row index to disambiguate duplicate labels."), WithJsonSchema({"type": "integer", "description": "Row index to disambiguate duplicate labels."})] = None,
    col: Annotated[Optional[int], Field(description="Column index to disambiguate duplicate labels."), WithJsonSchema({"type": "integer", "description": "Column index to disambiguate duplicate labels."})] = None,
) -> dict:
    """Press an inline keyboard button on a bot message. Returns the bot's callback answer and the (usually edited) message with its new buttons. Supply exactly one of dialog_id, username, or name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        result = await client.click_button(
            dialog_id=dialog_id, username=username, name=name,
            message_id=message_id, text=text, row=row, col=col,
        )
        msg = result.get("message")
        if msg is not None:
            result["message"] = dataclasses.asdict(msg)
        return result
    except Exception as e:
        return {"error": _error_message(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgClick" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add tg_click to press bot inline keyboard buttons"
```

---

### Task 5: `tg_react` — emoji reactions

**Files:**
- Modify: `src/tg_mcp/client.py` (add `react`)
- Modify: `src/tg_mcp/server.py` (add `tg_react`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `TelegramClientWrapper.react(self, dialog_id, username, name, message_id, emoji, big=False, remove=False) -> None`.
- Produces: `tg_react` tool → `{"ok": True}`.

- [ ] **Step 1: Write the failing test**

```python
from tg_mcp.server import tg_react


class TestTgReact:
    async def test_tg_react_passes_args(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.react = AsyncMock(return_value=None)
        ctx = _make_ctx(client)
        result = await tg_react(ctx, dialog_id=1, message_id=7, emoji="👍")
        assert result == {"ok": True}
        client.react.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, emoji="👍", big=False, remove=False
        )

    async def test_tg_react_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_react(ctx, message_id=7, emoji="👍")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "TestTgReact" -v`
Expected: FAIL — `cannot import name 'tg_react'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`:

```python
    async def react(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_id: int = 0,
        emoji: str = "",
        big: bool = False,
        remove: bool = False,
    ) -> None:
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        reaction = None if remove else emoji
        await self._client.send_reaction(entity, message_id, reaction=reaction, big=big)
```

In `server.py`:

```python
@mcp.tool()
async def tg_react(
    ctx: Context,
    message_id: Annotated[int, Field(description="ID of the message to react to.")],
    emoji: Annotated[str, Field(description="Single unicode emoji, e.g. '👍'. Ignored when remove=True.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle."), WithJsonSchema({"type": "string", "description": "Telegram @handle."})] = None,
    name: Annotated[Optional[str], Field(description="Display name."), WithJsonSchema({"type": "string", "description": "Display name."})] = None,
    big: Annotated[bool, Field(description="Play a big/animated reaction.")] = False,
    remove: Annotated[bool, Field(description="Remove the reaction instead of adding one.")] = False,
) -> dict:
    """React to a message with an emoji (or remove the reaction). Supply exactly one of dialog_id, username, or name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.react(dialog_id=dialog_id, username=username, name=name,
                           message_id=message_id, emoji=emoji, big=big, remove=remove)
        return {"ok": True}
    except Exception as e:
        return {"error": _error_message(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgReact" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add tg_react for emoji reactions"
```

---

### Task 6: `tg_delete` — delete messages

**Files:**
- Modify: `src/tg_mcp/client.py` (add `delete`)
- Modify: `src/tg_mcp/server.py` (add `tg_delete`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `TelegramClientWrapper.delete(self, dialog_id, username, name, message_ids, revoke=True) -> int` (returns count).
- Produces: `tg_delete` tool → `{"ok": True, "deleted": <int>}`.

- [ ] **Step 1: Write the failing test**

```python
from tg_mcp.server import tg_delete


class TestTgDelete:
    async def test_tg_delete_passes_ids_and_returns_count(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.delete = AsyncMock(return_value=2)
        ctx = _make_ctx(client)
        result = await tg_delete(ctx, dialog_id=1, message_ids=[7, 8])
        assert result == {"ok": True, "deleted": 2}
        client.delete.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_ids=[7, 8], revoke=True
        )

    async def test_tg_delete_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_delete(ctx, message_ids=[7])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "TestTgDelete" -v`
Expected: FAIL — `cannot import name 'tg_delete'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`:

```python
    async def delete(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_ids: list[int] | None = None,
        revoke: bool = True,
    ) -> int:
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        ids = message_ids or []
        await self._client.delete_messages(entity, ids, revoke=revoke)
        return len(ids)
```

In `server.py`:

```python
@mcp.tool()
async def tg_delete(
    ctx: Context,
    message_ids: Annotated[list[int], Field(description="IDs of messages to delete.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle."), WithJsonSchema({"type": "string", "description": "Telegram @handle."})] = None,
    name: Annotated[Optional[str], Field(description="Display name."), WithJsonSchema({"type": "string", "description": "Display name."})] = None,
    revoke: Annotated[bool, Field(description="Delete for everyone (default). False deletes only for you.")] = True,
) -> dict:
    """Delete one or more messages from a dialog. Supply exactly one of dialog_id, username, or name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        deleted = await client.delete(dialog_id=dialog_id, username=username, name=name,
                                     message_ids=message_ids, revoke=revoke)
        return {"ok": True, "deleted": deleted}
    except Exception as e:
        return {"error": _error_message(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgDelete" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add tg_delete"
```

---

### Task 7: `tg_edit` — edit own messages

**Files:**
- Modify: `src/tg_mcp/client.py` (add `edit_message`)
- Modify: `src/tg_mcp/server.py` (add `tg_edit`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `TelegramClientWrapper.edit_message(self, dialog_id, username, name, message_id, text, parse_mode="markdown") -> None`.
- Produces: `tg_edit` tool → `{"ok": True}`.
- Consumes: `_pm` (Task 3).

- [ ] **Step 1: Write the failing test**

```python
from tg_mcp.server import tg_edit


class TestTgEdit:
    async def test_tg_edit_passes_args(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.edit_message = AsyncMock(return_value=None)
        ctx = _make_ctx(client)
        result = await tg_edit(ctx, dialog_id=1, message_id=7, text="fixed", parse_mode="none")
        assert result == {"ok": True}
        client.edit_message.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, text="fixed", parse_mode="none"
        )

    async def test_tg_edit_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_edit(ctx, message_id=7, text="fixed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "TestTgEdit" -v`
Expected: FAIL — `cannot import name 'tg_edit'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`:

```python
    async def edit_message(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_id: int = 0,
        text: str = "",
        parse_mode: str | None = "markdown",
    ) -> None:
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.edit_message(entity, message_id, text, parse_mode=_pm(parse_mode))
```

In `server.py`:

```python
@mcp.tool()
async def tg_edit(
    ctx: Context,
    message_id: Annotated[int, Field(description="ID of your own message to edit.")],
    text: Annotated[str, Field(description="New message text.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle."), WithJsonSchema({"type": "string", "description": "Telegram @handle."})] = None,
    name: Annotated[Optional[str], Field(description="Display name."), WithJsonSchema({"type": "string", "description": "Display name."})] = None,
    parse_mode: Annotated[str, Field(description="Text formatting: 'markdown' (default), 'html', or 'none'.")] = "markdown",
) -> dict:
    """Edit one of your own sent messages. Supply exactly one of dialog_id, username, or name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.edit_message(dialog_id=dialog_id, username=username, name=name,
                                 message_id=message_id, text=text, parse_mode=parse_mode)
        return {"ok": True}
    except Exception as e:
        return {"error": _error_message(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgEdit" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add tg_edit"
```

---

### Task 8: `tg_search` — search messages

**Files:**
- Modify: `src/tg_mcp/client.py` (add `search`)
- Modify: `src/tg_mcp/server.py` (add `tg_search`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `TelegramClientWrapper.search(self, query, dialog_id=None, username=None, name=None, limit=20) -> list[Message]` (with `chat_id`/`chat_name` filled).
- Produces: `tg_search` tool → `list[dict]`.
- Consumes: `_to_message` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
from tg_mcp.server import tg_search


class TestTgSearch:
    async def test_tg_search_returns_list_of_dicts(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        hit = Message(id=9, date="2026-07-13T00:00:00+00:00", text="found it",
                      out=False, sender="A", chat_id=555, chat_name="Chat")
        client.search = AsyncMock(return_value=[hit])
        ctx = _make_ctx(client)
        result = await tg_search(ctx, query="found", dialog_id=1, limit=10)
        assert isinstance(result, list)
        assert result[0]["text"] == "found it"
        assert result[0]["chat_id"] == 555
        client.search.assert_called_once_with(
            query="found", dialog_id=1, username=None, name=None, limit=10
        )

    async def test_tg_search_error_returns_error_list(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.search = AsyncMock(side_effect=ValueError("Dialog not found: name=X"))
        ctx = _make_ctx(client)
        result = await tg_search(ctx, query="x", name="X")
        assert isinstance(result, list) and "error" in result[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "TestTgSearch" -v`
Expected: FAIL — `cannot import name 'tg_search'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`:

```python
    async def search(
        self,
        query: str,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        if dialog_id is None and username is None and name is None:
            msgs = await self._client.get_messages(None, search=query, limit=limit)
        else:
            entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
            msgs = await self._client.get_messages(entity, search=query, limit=limit)
        results: list[Message] = []
        for m in msgs:
            chat = await m.get_chat()
            chat_id = getattr(chat, "id", None)
            chat_name = telethon.utils.get_display_name(chat) or None if chat else None
            results.append(await self._to_message(m, chat_id=chat_id, chat_name=chat_name))
        return results
```

In `server.py`:

```python
@mcp.tool()
async def tg_search(
    ctx: Context,
    query: Annotated[str, Field(description="Text to search for.")],
    dialog_id: Annotated[Optional[int], Field(description="Restrict to this dialog. Omit all selectors for a global search."), WithJsonSchema({"type": "integer", "description": "Restrict to this dialog. Omit all selectors for a global search."})] = None,
    username: Annotated[Optional[str], Field(description="Restrict to this @handle."), WithJsonSchema({"type": "string", "description": "Restrict to this @handle."})] = None,
    name: Annotated[Optional[str], Field(description="Restrict to this display name."), WithJsonSchema({"type": "string", "description": "Restrict to this display name."})] = None,
    limit: Annotated[int, Field(description="Maximum number of results.", ge=1)] = 20,
) -> list[dict]:
    """Search messages by text. With a dialog selector, searches that dialog; without any selector, searches globally. Results include chat_id/chat_name."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        messages = await client.search(query=query, dialog_id=dialog_id, username=username, name=name, limit=limit)
        return [dataclasses.asdict(m) for m in messages]
    except Exception as e:
        return [{"error": _error_message(e)}]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgSearch" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add tg_search (per-dialog and global)"
```

---

### Task 9: `tg_download` — download media

**Files:**
- Modify: `src/tg_mcp/client.py` (add `download_media`, add `import os`)
- Modify: `src/tg_mcp/server.py` (add `tg_download`)
- Test: `tests/test_server.py`

**Interfaces:**
- Produces: `TelegramClientWrapper.download_media(self, dialog_id, username, name, message_id, dest_dir=None) -> dict` → `{"path": str, "media": str|None, "size": int}`.
- Produces: `tg_download` tool → that dict.

- [ ] **Step 1: Write the failing test**

```python
from tg_mcp.server import tg_download


class TestTgDownload:
    async def test_tg_download_returns_path(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.download_media = AsyncMock(return_value={"path": "/tmp/x/photo.jpg", "media": "photo", "size": 1234})
        ctx = _make_ctx(client)
        result = await tg_download(ctx, dialog_id=1, message_id=7)
        assert result["path"] == "/tmp/x/photo.jpg"
        assert result["size"] == 1234
        client.download_media.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, dest_dir=None
        )

    async def test_tg_download_no_media_error(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.download_media = AsyncMock(side_effect=ValueError("Message 7 has no downloadable media"))
        ctx = _make_ctx(client)
        result = await tg_download(ctx, dialog_id=1, message_id=7)
        assert "error" in result and "no downloadable media" in result["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -k "TestTgDownload" -v`
Expected: FAIL — `cannot import name 'tg_download'`.

- [ ] **Step 3: Write minimal implementation**

In `client.py`, add `import os` at the top (with the stdlib imports) and `from pathlib import Path`, then:

```python
    async def download_media(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_id: int = 0,
        dest_dir: str | None = None,
    ) -> dict:
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        message = await self._client.get_messages(entity, ids=message_id)
        if message is None or not getattr(message, "media", None):
            raise ValueError(f"Message {message_id} has no downloadable media")
        dest = Path(dest_dir) if dest_dir else (Path.cwd() / ".telegram-mcp" / "downloads")
        dest.mkdir(parents=True, exist_ok=True)
        path = await self._client.download_media(message, file=str(dest))
        size = os.path.getsize(path) if path else 0
        return {"path": str(Path(path).resolve()) if path else None,
                "media": _describe_media(message), "size": size}
```

In `server.py`:

```python
@mcp.tool()
async def tg_download(
    ctx: Context,
    message_id: Annotated[int, Field(description="ID of the message whose media to download.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle."), WithJsonSchema({"type": "string", "description": "Telegram @handle."})] = None,
    name: Annotated[Optional[str], Field(description="Display name."), WithJsonSchema({"type": "string", "description": "Display name."})] = None,
    dest_dir: Annotated[Optional[str], Field(description="Directory to save into. Defaults to <cwd>/.telegram-mcp/downloads/."), WithJsonSchema({"type": "string", "description": "Directory to save into. Defaults to <cwd>/.telegram-mcp/downloads/."})] = None,
) -> dict:
    """Download a message's media (photo/voice/document/…) to disk. Returns the saved path, media type, and size. Supply exactly one of dialog_id, username, or name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        return await client.download_media(dialog_id=dialog_id, username=username, name=name,
                                          message_id=message_id, dest_dir=dest_dir)
    except Exception as e:
        return {"error": _error_message(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py -k "TestTgDownload" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tg_mcp/client.py src/tg_mcp/server.py tests/test_server.py
git commit -m "feat: add tg_download"
```

---

### Task 10: Version bump + full verification

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md` (tool list, if present)

- [ ] **Step 1: Bump version**

In `pyproject.toml` change `version = "0.4.0"` → `version = "0.5.0"`.

- [ ] **Step 2: Update README tool list**

If `README.md` enumerates tools, add `tg_click`, `tg_react`, `tg_delete`, `tg_edit`, `tg_search`, `tg_download` and note `reply_to`/`parse_mode` on `tg_send`. (Skip if no such list exists.)

- [ ] **Step 3: Run the full suite with coverage**

Run: `uv run pytest -q`
Expected: PASS, all tests; coverage ≥ 80% (no `fail_under` error).

- [ ] **Step 4: Re-lock and smoke-build**

Run:
```bash
uv lock
uv build
```
Expected: builds `dist/q0t_telegram_mcp-0.5.0-*`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock README.md
git commit -m "chore: bump version to 0.5.0"
```

---

## Post-Plan Release (manual, after review)

Not part of TDD tasks — run once the branch is reviewed and merged:

```bash
# after PR merge to master:
uv build
set -a; . .vscode/.env; set +a       # loads UV_PUBLISH_TOKEN
uv publish dist/q0t_telegram_mcp-0.5.0*
uv tool upgrade q0t-telegram-mcp      # update the locally-installed MCP
# then restart Claude Code so the MCP relaunches on 0.5.0
```

## Notes for the Implementer

- **Live verification caveat:** do NOT open a second Telethon connection against the live session while Claude's MCP is running (same auth key → AUTH_KEY_DUPLICATED can drop the running MCP). Rely on the unit tests; real end-to-end happens after `uv tool upgrade` + MCP restart.
- **Telethon return shapes:** `client.get_messages(entity, ids=<int>)` returns a single message (or None), not a list. `client.get_messages(entity, search=..., limit=...)` returns a list. `message.click(i, j)` returns a `BotCallbackAnswer` with `.message` (popup text) and `.alert` (bool).
- **parse_mode default is markdown** by design (matches Telethon's current default and preserves existing `tg_send` behavior). `"none"` = literal text.

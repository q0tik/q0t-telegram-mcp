from __future__ import annotations

from typing import Any

import telethon.utils
from telethon import TelegramClient
from telethon.errors import UsernameNotOccupiedError

from .config import Config
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


def _describe_media(message) -> "str | None":
    """Return a short human-readable descriptor of a message's media, or None."""
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "gif", None):
        return "gif"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "audio", None):
        return "audio"
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


class TelegramClientWrapper:
    """Async wrapper around :class:`telethon.TelegramClient`.

    Provides the subset of Telegram operations needed by the MCP tools.
    The underlying Telethon client is created lazily on :meth:`connect`.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: TelegramClient | None = None

    async def connect(self) -> None:
        """Open a connection to Telegram and verify the session is authorized.

        Raises:
            RuntimeError: If no valid session exists. Run
                ``q0t-telegram-mcp auth --phone <phone>`` to authenticate first.
        """
        # receive_updates=False: this MCP is strictly request/response and never
        # consumes the update stream. Disabling it stops Telethon from fetching
        # and deserializing the update difference — the path that raises
        # TypeNotFoundError when Telegram sends a TL constructor newer than the
        # installed Telethon layer (the intermittent "garbage bytes" crash).
        self._client = TelegramClient(
            str(self._config.session_path),
            self._config.app_id,
            self._config.api_hash,
            receive_updates=False,
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Not authorized. Run: q0t-telegram-mcp auth --phone <phone>"
            )

    async def disconnect(self) -> None:
        """Close the Telegram connection. Safe to call even if not connected."""
        if self._client is not None:
            await self._client.disconnect()

    async def is_authorized(self) -> bool:
        """Return True if the current session is authorized with Telegram."""
        return await self._client.is_user_authorized()

    async def get_me(self) -> UserInfo:
        """Return information about the currently authenticated account."""
        me = await self._client.get_me()
        return UserInfo(
            id=me.id,
            first_name=me.first_name,
            last_name=me.last_name,
            username=me.username,
            phone=me.phone,
        )

    async def get_dialogs(
        self,
        unread_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Dialog]:
        """Return a paginated list of dialogs (chats, groups, channels).

        Args:
            unread_only: When True, only return dialogs that have unread messages.
            limit: Maximum number of dialogs to return.
            offset: Number of dialogs to skip (for pagination).
        """
        dialogs = await self._client.get_dialogs(limit=None)
        if unread_only:
            dialogs = [d for d in dialogs if d.unread_count > 0]
        dialogs = dialogs[offset : offset + limit]
        return [
            Dialog(
                id=d.id,
                name=d.name,
                username=getattr(d.entity, "username", None),
                unread_count=d.unread_count,
                is_user=d.is_user,
                is_group=d.is_group,
                is_channel=d.is_channel,
                last_message=d.message.text if d.message else None,
                date=d.date.isoformat() if d.date else None,
            )
            for d in dialogs
        ]

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

    async def get_messages(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Message]:
        """Return messages from a dialog, newest first.

        Args:
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            limit: Maximum number of messages to return.
            offset: Number of messages to skip (for pagination).

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        msgs = await self._client.get_messages(entity, limit=limit, add_offset=offset)
        return [await self._to_message(m) for m in msgs]

    async def send_message(
        self,
        text: str,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        reply_to: int | None = None,
        parse_mode: str | None = "markdown",
    ) -> None:
        """Send a text message to a dialog.

        Args:
            text: Message body to send.
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            reply_to: Message ID to reply to.
            parse_mode: Text formatting: "markdown" (default), "html", or "none".

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.send_message(entity, text, reply_to=reply_to, parse_mode=_pm(parse_mode))

    async def send_file(
        self,
        file_path: str,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        caption: str | None = None,
    ) -> None:
        """Send a file to a dialog.

        Args:
            file_path: Local filesystem path to the file to send.
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            caption: Optional text caption to attach to the file.

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.send_file(entity, file_path, caption=caption)

    async def forward_message(
        self,
        from_dialog_id: int | None = None,
        from_username: str | None = None,
        from_name: str | None = None,
        to_dialog_id: int | None = None,
        to_username: str | None = None,
        to_name: str | None = None,
        message_id: int = 0,
    ) -> None:
        """Forward a message from one dialog to another.

        Args:
            from_dialog_id: Source dialog numeric ID.
            from_username: Source dialog @handle.
            from_name: Source dialog display name.
            to_dialog_id: Destination dialog numeric ID.
            to_username: Destination dialog @handle.
            to_name: Destination dialog display name.
            message_id: ID of the message to forward.

        Raises:
            ValueError: If source or destination dialog cannot be found.
        """
        from_entity = await self._resolve_entity(
            dialog_id=from_dialog_id, username=from_username, name=from_name
        )
        to_entity = await self._resolve_entity(
            dialog_id=to_dialog_id, username=to_username, name=to_name
        )
        await self._client.forward_messages(to_entity, message_id, from_entity)

    async def mark_read(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
    ) -> None:
        """Mark all messages in a dialog as read.

        Args:
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.send_read_acknowledge(entity)

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
        """React to a message with an emoji or remove the reaction.

        Args:
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            message_id: ID of the message to react to.
            emoji: Single unicode emoji, e.g. '👍'. Ignored when remove=True.
            big: Play a big/animated reaction.
            remove: Remove the reaction instead of adding one.

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        reaction = None if remove else emoji
        await self._client.send_reaction(entity, message_id, reaction=reaction, big=big)

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
        """Press an inline keyboard button on a bot message by its visible label.

        Args:
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            message_id: ID of the bot message whose inline keyboard to click.
            text: Visible label of the button to press.
            row: Row index to disambiguate duplicate labels.
            col: Column index to disambiguate duplicate labels.

        Returns:
            For callback buttons: {"answer": str | None, "alert": bool, "message": Message | None}.
            For URL buttons: {"answer": None, "alert": False, "url": str, "message": Message}.

        Raises:
            ValueError: If the dialog/message cannot be found, has no inline
                buttons, or no button matches the given text/row/col.
        """
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

    async def delete(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_ids: list[int] | None = None,
        revoke: bool = True,
    ) -> int:
        """Delete one or more messages from a dialog.

        Args:
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            message_ids: List of message IDs to delete.
            revoke: Delete for everyone (default) or just for self (False).

        Returns:
            The number of messages deleted.

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        ids = message_ids or []
        await self._client.delete_messages(entity, ids, revoke=revoke)
        return len(ids)

    async def edit_message(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        message_id: int = 0,
        text: str = "",
        parse_mode: str | None = "markdown",
    ) -> None:
        """Edit one of your own sent messages.

        Args:
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            message_id: ID of the message to edit.
            text: New message text.
            parse_mode: Text formatting: "markdown" (default), "html", or "none".

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.edit_message(entity, message_id, text, parse_mode=_pm(parse_mode))

    async def search(
        self,
        query: str,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
        limit: int = 20,
    ) -> list[Message]:
        """Search messages by text, globally or within a single dialog.

        Args:
            query: Text to search for.
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.
            limit: Maximum number of results to return.

        When none of dialog_id/username/name is given, searches across all
        dialogs. Otherwise, restricts the search to the resolved dialog.

        Raises:
            ValueError: If a dialog selector is given but no matching dialog
                can be found.
        """
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

    async def _resolve_entity(
        self,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
    ) -> Any:
        # Priority: dialog_id > username > name
        if dialog_id is not None:
            try:
                return await self._client.get_entity(dialog_id)
            except Exception:
                raise ValueError(f"Dialog not found: id={dialog_id}")
        if username is not None:
            handle = username.lstrip("@")
            try:
                return await self._client.get_entity(handle)
            except (ValueError, UsernameNotOccupiedError):
                raise ValueError(f"Dialog not found: username={username}")
        if name is not None:
            # get_entity() does NOT accept display names — scan cached dialogs instead.
            async for dialog in self._client.iter_dialogs():
                if dialog.name == name:
                    return dialog.entity
            raise ValueError(f"Dialog not found: name={name}")
        raise ValueError("One of dialog_id, username, or name must be provided")

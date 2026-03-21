from __future__ import annotations

from typing import Any

import telethon.utils
from telethon import TelegramClient
from telethon.errors import UsernameNotOccupiedError

from .config import Config
from .models import Dialog, Message, UserInfo


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
        self._client = TelegramClient(
            str(self._config.session_path),
            self._config.app_id,
            self._config.api_hash,
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
        result: list[Message] = []
        for m in msgs:
            sender = await m.get_sender()
            sender_name: str | None = None
            if sender is not None:
                sender_name = telethon.utils.get_display_name(sender) or None
            result.append(
                Message(
                    id=m.id,
                    date=m.date.isoformat(),
                    text=m.text,
                    out=bool(m.out),
                    sender=sender_name,
                )
            )
        return result

    async def send_message(
        self,
        text: str,
        dialog_id: int | None = None,
        username: str | None = None,
        name: str | None = None,
    ) -> None:
        """Send a text message to a dialog.

        Args:
            text: Message body to send.
            dialog_id: Numeric Telegram ID from tg_dialogs. Most reliable.
            username: Telegram @handle (leading @ optional).
            name: Dialog display name. Least reliable; resolved by scanning dialogs.

        Raises:
            ValueError: If no dialog matching the provided selector can be found.
        """
        entity = await self._resolve_entity(dialog_id=dialog_id, username=username, name=name)
        await self._client.send_message(entity, text)

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

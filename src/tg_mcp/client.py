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
        name: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Message]:
        """Return messages from a dialog, newest first.

        Args:
            name: Dialog display name, @username, or phone number.
            limit: Maximum number of messages to return.
            offset: Number of messages to skip (for pagination).

        Raises:
            ValueError: If no dialog matching *name* can be found.
        """
        entity = await self._resolve_entity(name)
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

    async def send_message(self, name: str, text: str) -> None:
        """Send a text message to a dialog.

        Args:
            name: Dialog display name, @username, or phone number.
            text: Message body to send.

        Raises:
            ValueError: If no dialog matching *name* can be found.
        """
        entity = await self._resolve_entity(name)
        await self._client.send_message(entity, text)

    async def mark_read(self, name: str) -> None:
        """Mark all messages in a dialog as read.

        Args:
            name: Dialog display name, @username, or phone number.

        Raises:
            ValueError: If no dialog matching *name* can be found.
        """
        entity = await self._resolve_entity(name)
        await self._client.send_read_acknowledge(entity)

    async def _resolve_entity(self, name: str) -> Any:
        try:
            return await self._client.get_entity(name)
        except (ValueError, UsernameNotOccupiedError):
            raise ValueError(f"Dialog not found: {name}")

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserInfo:
    """Information about the currently authenticated Telegram account.

    Returned by :func:`tg_me`.
    """

    id: int
    first_name: str
    last_name: str | None
    username: str | None  # @handle without the leading @
    phone: str | None


@dataclass(frozen=True)
class Dialog:
    """A single chat, group, or channel entry from the dialog list.

    Returned by :func:`tg_dialogs`.
    """

    id: int           # Marked entity ID — stable and unique across dialog types
    name: str         # Display name (first+last for users, title for groups/channels)
    username: str | None  # @handle without leading @, None for groups/channels without one
    unread_count: int
    is_user: bool
    is_group: bool
    is_channel: bool
    last_message: str | None  # Text of the most recent message; None for media-only or empty dialogs
    date: str | None          # ISO-8601 datetime of the last message; None if no messages


@dataclass(frozen=True)
class Button:
    """A single inline-keyboard button on a bot message."""

    text: str                 # visible label
    kind: str                 # "callback" | "url" | "webview" | "reply" | "other"
    url: str | None = None    # populated for kind == "url" and kind == "webview"


@dataclass(frozen=True)
class Reaction:
    """One reaction bucket on a message: which reaction, how many, and whether it's mine."""

    kind: str                        # "emoji" | "custom" | "paid" | "other"
    count: int
    chosen: bool                     # True if the authenticated account reacted with this
    emoji: str | None = None         # populated for kind == "emoji"
    custom_emoji_id: int | None = None  # populated for kind == "custom"


@dataclass(frozen=True)
class Message:
    """A single message within a dialog.

    Returned by :func:`tg_dialog`.
    """

    id: int
    date: str          # ISO-8601 datetime
    text: str | None   # None for media-only messages
    out: bool          # True if this message was sent by the authenticated account
    sender: str | None # Display name of the sender; None for channel posts
    buttons: list[list[Button]] | None = None  # rows of inline buttons; None if none
    media: str | None = None                     # e.g. "photo", "document: report.pdf"; None for text
    chat_id: int | None = None                   # populated only by tg_search results
    chat_name: str | None = None                 # populated only by tg_search results
    reply_to: int | None = None                  # ID of the message this one replies to
    reactions: list[Reaction] | None = None    # reaction buckets; None if nobody reacted

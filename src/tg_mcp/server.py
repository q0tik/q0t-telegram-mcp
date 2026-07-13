import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context
from pydantic import Field, WithJsonSchema

from .client import TelegramClientWrapper
from .config import Config


@dataclass
class AppContext:
    """Holds shared resources available to every MCP tool during a server session."""

    client: TelegramClientWrapper


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Connect to Telegram on startup and disconnect on shutdown.

    Raises:
        EnvironmentError: If ``TG_APP_ID`` or ``TG_API_HASH`` is not set.
        RuntimeError: If no authorized session exists (run ``auth`` first).
    """
    config = Config.from_env()
    client = TelegramClientWrapper(config)
    await client.connect()
    try:
        yield AppContext(client=client)
    finally:
        await client.disconnect()


mcp = FastMCP("q0t-telegram-mcp", lifespan=lifespan)


_MAX_ERROR_CHARS = 300


def _error_message(exc: Exception) -> str:
    """Return a short, safe error string for a failed tool call.

    Telethon raises ``TypeNotFoundError`` when Telegram sends a TL constructor
    unknown to the installed Telethon version. That exception's ``str()`` embeds
    the entire remaining MTProto buffer — hundreds of KB of raw bytes. Left
    unguarded it propagates to FastMCP and is stringified straight into the
    model's context. Always collapse it to a short, single-line message.
    """
    text = " ".join(str(exc).split())
    if len(text) > _MAX_ERROR_CHARS:
        text = f"{text[:_MAX_ERROR_CHARS]}… [truncated, {len(text)} chars total]"
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


@mcp.tool()
async def tg_me(ctx: Context) -> dict:
    """Get current Telegram account info."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        user_info = await client.get_me()
        return dataclasses.asdict(user_info)
    except Exception as e:
        return {"error": _error_message(e)}


@mcp.tool()
async def tg_dialogs(
    ctx: Context,
    unread_only: Annotated[bool, Field(description="When true, only return dialogs with unread messages.")] = False,
    limit: Annotated[int, Field(description="Maximum number of dialogs to return.", ge=1)] = 20,
    offset: Annotated[int, Field(description="Number of dialogs to skip (pagination).", ge=0)] = 0,
) -> list[dict]:
    """List Telegram dialogs (chats, groups, channels)."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        dialogs = await client.get_dialogs(unread_only=unread_only, limit=limit, offset=offset)
        return [dataclasses.asdict(d) for d in dialogs]
    except Exception as e:
        return [{"error": _error_message(e)}]


@mcp.tool()
async def tg_dialog(
    ctx: Context,
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs. Most reliable, no ambiguity."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs. Most reliable, no ambiguity."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle, e.g. 'adilet_c' (leading @ optional)."), WithJsonSchema({"type": "string", "description": "Telegram @handle, e.g. 'adilet_c' (leading @ optional)."})] = None,
    name: Annotated[Optional[str], Field(description="Display name. Use only when dialog_id/username are unavailable; may be ambiguous."), WithJsonSchema({"type": "string", "description": "Display name. Use only when dialog_id/username are unavailable; may be ambiguous."})] = None,
    limit: Annotated[int, Field(description="Maximum number of messages to return.", ge=1)] = 20,
    offset: Annotated[int, Field(description="Number of messages to skip (pagination).", ge=0)] = 0,
) -> list[dict]:
    """Get messages from a Telegram dialog. Supply exactly one of dialog_id, username, or name. Priority: dialog_id > username > name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        messages = await client.get_messages(
            dialog_id=dialog_id, username=username, name=name, limit=limit, offset=offset
        )
        return [dataclasses.asdict(m) for m in messages]
    except Exception as e:
        return [{"error": _error_message(e)}]


@mcp.tool()
async def tg_send(
    ctx: Context,
    text: Annotated[str, Field(description="Message body to send.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs. Most reliable, no ambiguity."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs. Most reliable, no ambiguity."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle, e.g. 'adilet_c' (leading @ optional)."), WithJsonSchema({"type": "string", "description": "Telegram @handle, e.g. 'adilet_c' (leading @ optional)."})] = None,
    name: Annotated[Optional[str], Field(description="Display name. Use only when dialog_id/username are unavailable; may be ambiguous."), WithJsonSchema({"type": "string", "description": "Display name. Use only when dialog_id/username are unavailable; may be ambiguous."})] = None,
    reply_to: Annotated[Optional[int], Field(description="Message ID to reply to."), WithJsonSchema({"type": "integer", "description": "Message ID to reply to."})] = None,
    parse_mode: Annotated[str, Field(description="Text formatting: 'markdown' (default), 'html', or 'none' for literal text.")] = "markdown",
) -> dict:
    """Send a message to a Telegram dialog. Supply exactly one of dialog_id, username, or name. Priority: dialog_id > username > name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.send_message(text=text, dialog_id=dialog_id, username=username, name=name, reply_to=reply_to, parse_mode=parse_mode)
        return {"ok": True}
    except Exception as e:
        return {"error": _error_message(e)}


@mcp.tool()
async def tg_send_file(
    ctx: Context,
    file_path: Annotated[str, Field(description="Local filesystem path to the file to send.")],
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs. Most reliable, no ambiguity."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs. Most reliable, no ambiguity."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle, e.g. 'adilet_c' (leading @ optional)."), WithJsonSchema({"type": "string", "description": "Telegram @handle, e.g. 'adilet_c' (leading @ optional)."})] = None,
    name: Annotated[Optional[str], Field(description="Display name. Use only when dialog_id/username are unavailable; may be ambiguous."), WithJsonSchema({"type": "string", "description": "Display name. Use only when dialog_id/username are unavailable; may be ambiguous."})] = None,
    caption: Annotated[Optional[str], Field(description="Optional text caption to attach to the file."), WithJsonSchema({"type": "string", "description": "Optional text caption to attach to the file."})] = None,
) -> dict:
    """Send a file to a Telegram dialog. Supply exactly one of dialog_id, username, or name. Priority: dialog_id > username > name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.send_file(
            file_path=file_path,
            dialog_id=dialog_id,
            username=username,
            name=name,
            caption=caption,
        )
        return {"ok": True}
    except Exception as e:
        return {"error": _error_message(e)}


@mcp.tool()
async def tg_forward(
    ctx: Context,
    message_id: Annotated[int, Field(description="ID of the message to forward (from tg_dialog results).")],
    from_dialog_id: Annotated[Optional[int], Field(description="Source dialog numeric ID."), WithJsonSchema({"type": "integer", "description": "Source dialog numeric ID."})] = None,
    from_username: Annotated[Optional[str], Field(description="Source dialog @handle."), WithJsonSchema({"type": "string", "description": "Source dialog @handle."})] = None,
    from_name: Annotated[Optional[str], Field(description="Source dialog display name."), WithJsonSchema({"type": "string", "description": "Source dialog display name."})] = None,
    to_dialog_id: Annotated[Optional[int], Field(description="Destination dialog numeric ID."), WithJsonSchema({"type": "integer", "description": "Destination dialog numeric ID."})] = None,
    to_username: Annotated[Optional[str], Field(description="Destination dialog @handle."), WithJsonSchema({"type": "string", "description": "Destination dialog @handle."})] = None,
    to_name: Annotated[Optional[str], Field(description="Destination dialog display name."), WithJsonSchema({"type": "string", "description": "Destination dialog display name."})] = None,
) -> dict:
    """Forward a message from one dialog to another. Supply exactly one source selector (from_*) and one destination selector (to_*)."""
    if from_dialog_id is None and from_username is None and from_name is None:
        raise ValueError("One of from_dialog_id, from_username, or from_name must be provided")
    if to_dialog_id is None and to_username is None and to_name is None:
        raise ValueError("One of to_dialog_id, to_username, or to_name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.forward_message(
            from_dialog_id=from_dialog_id,
            from_username=from_username,
            from_name=from_name,
            to_dialog_id=to_dialog_id,
            to_username=to_username,
            to_name=to_name,
            message_id=message_id,
        )
        return {"ok": True}
    except Exception as e:
        return {"error": _error_message(e)}


@mcp.tool()
async def tg_read(
    ctx: Context,
    dialog_id: Annotated[Optional[int], Field(description="Numeric ID from tg_dialogs. Most reliable, no ambiguity."), WithJsonSchema({"type": "integer", "description": "Numeric ID from tg_dialogs. Most reliable, no ambiguity."})] = None,
    username: Annotated[Optional[str], Field(description="Telegram @handle, e.g. 'adilet_c' (leading @ optional)."), WithJsonSchema({"type": "string", "description": "Telegram @handle, e.g. 'adilet_c' (leading @ optional)."})] = None,
    name: Annotated[Optional[str], Field(description="Display name. Use only when dialog_id/username are unavailable; may be ambiguous."), WithJsonSchema({"type": "string", "description": "Display name. Use only when dialog_id/username are unavailable; may be ambiguous."})] = None,
) -> dict:
    """Mark all messages in a dialog as read. Supply exactly one of dialog_id, username, or name. Priority: dialog_id > username > name."""
    if dialog_id is None and username is None and name is None:
        raise ValueError("One of dialog_id, username, or name must be provided")
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.mark_read(dialog_id=dialog_id, username=username, name=name)
        return {"ok": True}
    except Exception as e:
        return {"error": _error_message(e)}


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


def run_server() -> None:
    """Start the MCP server. Blocks until the server is stopped."""
    mcp.run()

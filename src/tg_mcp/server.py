from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

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


@mcp.tool()
async def tg_me(ctx: Context) -> dict:
    """Get current Telegram account info."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        user_info = await client.get_me()
        return dataclasses.asdict(user_info)
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
async def tg_dialogs(
    ctx: Context,
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """List Telegram dialogs (chats, groups, channels)."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        dialogs = await client.get_dialogs(unread_only=unread_only, limit=limit, offset=offset)
        return [dataclasses.asdict(d) for d in dialogs]
    except ValueError as e:
        return [{"error": str(e)}]


@mcp.tool()
async def tg_dialog(
    ctx: Context,
    name: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Get messages from a Telegram dialog."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        messages = await client.get_messages(name=name, limit=limit, offset=offset)
        return [dataclasses.asdict(m) for m in messages]
    except ValueError as e:
        return [{"error": str(e)}]


@mcp.tool()
async def tg_send(ctx: Context, name: str, text: str) -> dict:
    """Send a message to a Telegram dialog."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.send_message(name=name, text=text)
        return {"ok": True}
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
async def tg_read(ctx: Context, name: str) -> dict:
    """Mark all messages in a dialog as read."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        await client.mark_read(name=name)
        return {"ok": True}
    except ValueError as e:
        return {"error": str(e)}


def run_server() -> None:
    """Start the MCP server. Blocks until the server is stopped."""
    mcp.run()

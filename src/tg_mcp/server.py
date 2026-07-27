import asyncio
import dataclasses
import importlib.metadata
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, WithJsonSchema
from starlette.responses import JSONResponse

from .client import TelegramClientWrapper
from .config import Config, TransportConfig


@dataclass
class AppContext:
    """Holds shared resources available to every MCP tool during a server session."""

    client: TelegramClientWrapper


class SharedTelegramClient:
    """One Telethon client per *process*, shared by every MCP session.

    FastMCP enters the lifespan per session, not per process: the low-level
    server does ``enter_async_context(self.lifespan(self))`` inside
    ``Server.run()``, and the streamable-HTTP session manager calls
    ``Server.run()`` once per connecting client. Under stdio that distinction
    never mattered — one process served one client — but under HTTP it is the
    difference between working and reproducing the exact bug this transport
    exists to fix.

    Two Telethon clients on one session file fail in two ways: SQLite raises
    ``database is locked`` because Telethon *writes* to the session on connect,
    and the same ``auth_key`` used from two places at once can trigger
    ``AUTH_KEY_DUPLICATED``, which logs the account out for real and needs a
    fresh sign-in. Neither is recoverable by copying the session file, because
    the auth key is what is duplicated.

    Reference-counted rather than created-once-forever so stdio keeps its
    previous lifecycle exactly: one session, connect on entry, disconnect on
    exit.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._client: TelegramClientWrapper | None = None
        self._sessions = 0

    @property
    def active_sessions(self) -> int:
        """How many MCP sessions currently hold the client."""
        return self._sessions

    async def acquire(self) -> TelegramClientWrapper:
        """Return the process-wide client, connecting it on first use.

        The lock matters: two clients connecting at the same instant is the
        precise scenario that broke under stdio, and an unguarded
        check-then-create would let both through.
        """
        async with self._lock:
            if self._client is None:
                client = TelegramClientWrapper(Config.from_env())
                # Only count and cache a client that actually connected — a
                # cached broken one would turn a single bad startup into a
                # permanently dead server.
                await client.connect()
                self._client = client
            self._sessions += 1
            return self._client

    async def release(self) -> None:
        """Release one session's hold, disconnecting when the last one leaves."""
        async with self._lock:
            if self._sessions == 0:
                return
            self._sessions -= 1
            if self._sessions == 0 and self._client is not None:
                client, self._client = self._client, None
                await client.disconnect()


#: Process-wide client. Module-level so every MCP session reaches the same one.
shared_client = SharedTelegramClient()


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Hand this session the process-wide Telegram client.

    Entered once per MCP session. Under stdio that is once per process; under
    HTTP it is once per connected client, which is why the client itself is
    shared rather than built here.

    Raises:
        EnvironmentError: If ``TG_APP_ID`` is not set.
        RuntimeError: If no authorized session exists (run ``auth`` first).
    """
    client = await shared_client.acquire()
    try:
        yield AppContext(client=client)
    finally:
        await shared_client.release()


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


async def tg_me(ctx: Context) -> dict:
    """Get current Telegram account info."""
    client: TelegramClientWrapper = ctx.request_context.lifespan_context.client
    try:
        user_info = await client.get_me()
        return dataclasses.asdict(user_info)
    except Exception as e:
        return {"error": _error_message(e)}


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
        message_id = await client.send_message(text=text, dialog_id=dialog_id, username=username, name=name, reply_to=reply_to, parse_mode=parse_mode)
        return {"ok": True, "message_id": message_id}
    except Exception as e:
        return {"error": _error_message(e)}


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
        message_id = await client.send_file(
            file_path=file_path,
            dialog_id=dialog_id,
            username=username,
            name=name,
            caption=caption,
        )
        return {"ok": True, "message_id": message_id}
    except Exception as e:
        return {"error": _error_message(e)}


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


#: Tools that only observe. Nothing here is visible to anyone else on Telegram.
#: tg_download writes to local disk but changes no account state.
READ_TOOLS = [tg_me, tg_dialogs, tg_dialog, tg_search, tg_download]

#: Tools that act as the account owner.
#:
#: Two of these read as passive but are not: tg_read marks a dialog read, which
#: the other party can observe and which destroys unread state; tg_click fires a
#: callback query at a bot, which can trigger anything that bot does. Grouping
#: them with the obvious writers is the whole point of the split.
WRITE_TOOLS = [
    tg_send, tg_send_file, tg_forward, tg_read, tg_click,
    tg_react, tg_delete, tg_edit,
]

_READONLY_TRUE = {"1", "true", "yes"}

#: The shipped DNS-rebinding allowances, captured before anything widens them so
#: a later reconfiguration can be rebuilt from a clean base.
_BASE_TRANSPORT_SECURITY = (
    tuple(mcp.settings.transport_security.allowed_hosts)
    if mcp.settings.transport_security
    else (),
    tuple(mcp.settings.transport_security.allowed_origins)
    if mcp.settings.transport_security
    else (),
)


def readonly_from_env() -> bool:
    """Return True when ``TG_READONLY`` asks for a read-only tool set.

    Accepts ``1``/``true``/``yes`` in any case. Anything else — including an
    unset or blank value — leaves the full set registered, so no 0.6.x
    configuration changes meaning.
    """
    return (os.environ.get("TG_READONLY") or "").strip().lower() in _READONLY_TRUE


def register_tools(server: FastMCP, *, readonly: bool) -> None:
    """Register the tool set on *server*.

    In read-only mode the writing tools are never registered, so they do not
    appear in ``list_tools`` at all. Registering them and refusing at call time
    would still advertise the capability to the model — and an advertised tool
    gets attempted, argued with, and worked around. Absent is unambiguous.

    Idempotent: registering a name that is already present is skipped rather
    than re-added, which would only emit a duplicate-tool warning and change
    nothing. A genuine collision between two different functions is caught by
    the partition test instead.
    """
    registered = set(server._tool_manager._tools)
    tools = READ_TOOLS if readonly else READ_TOOLS + WRITE_TOOLS
    for fn in tools:
        if fn.__name__ not in registered:
            server.add_tool(fn)


def _package_version() -> str:
    """Best-effort package version for /health."""
    try:
        return importlib.metadata.version("q0t-telegram-mcp")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return "unknown"


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Liveness probe: is the process up, without opening an MCP session.

    Deliberately dull. It answers before any session exists and is reachable by
    anything that can reach the port, so it reports only what is already public
    from the outside: that the process is listening, and which version it is.
    Never configuration, credentials, or who is logged in.
    """
    return JSONResponse(
        {
            "status": "ok",
            "name": "q0t-telegram-mcp",
            "version": _package_version(),
            "active_sessions": shared_client.active_sessions,
        }
    )


#: uvicorn's stock config sends access logs to stdout. Under stdio that would
#: corrupt the MCP stream outright; under HTTP stdout is free, but keeping every
#: diagnostic on stderr means one place to look regardless of transport.
_STDERR_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}


def _serve_http(server: FastMCP, config: TransportConfig) -> None:
    """Serve *server* over HTTP until stopped.

    uvicorn is driven directly rather than through ``FastMCP.run`` because that
    path hardcodes its own uvicorn config and leaves no way to redirect logging
    to stderr.
    """
    import uvicorn

    app = server.sse_app() if config.transport == "sse" else server.streamable_http_app()
    uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            log_level=server.settings.log_level.lower(),
            log_config=_STDERR_LOG_CONFIG,
        )
    ).run()


def _apply_http_settings(server: FastMCP, config: TransportConfig) -> None:
    """Point FastMCP at the resolved address and keep it usable there.

    Rebuilt from the shipped defaults on every call rather than appended to, so
    that applying two configurations in one process (tests do; a future reload
    might) cannot accumulate stale allowances from an address no longer in use.
    """
    server.settings.host = config.host
    server.settings.port = config.port

    security = server.settings.transport_security
    if security is None:  # pragma: no cover - only when auth is reconfigured
        return
    base_hosts, base_origins = _BASE_TRANSPORT_SECURITY
    if config.is_loopback:
        security.allowed_hosts = list(base_hosts)
        security.allowed_origins = list(base_origins)
        return
    # DNS-rebinding protection ships allowing localhost Host headers only, so a
    # deliberate off-host bind would otherwise reject every request that reached
    # it. Warning about the bind and then breaking it is the worst of both
    # worlds; the user asked for this address.
    security.allowed_hosts = list(base_hosts) + [config.host, f"{config.host}:*"]
    security.allowed_origins = list(base_origins) + [
        f"http://{config.host}", f"http://{config.host}:*",
    ]


def _warn_if_exposed(config: TransportConfig) -> None:
    """Say plainly what binding off-loopback means.

    This server grants whoever reaches it full control of a Telegram account and
    authenticates nobody, so the address it listens on *is* the access control.
    """
    if config.is_loopback:
        return
    print(
        f"WARNING: listening on {config.host}:{config.port}, which is not loopback.\n"
        "         This server has no authentication and grants full access to the\n"
        "         Telegram account behind its session. Anyone who can reach this\n"
        "         port can read and send messages as that account.",
        file=sys.stderr,
    )


def run_server(config: TransportConfig | None = None) -> None:
    """Start the MCP server. Blocks until the server is stopped.

    With no *config* the transport is resolved from the environment, which
    defaults to stdio — so calling the package with no arguments behaves exactly
    as it did before 0.8.0.
    """
    config = config or TransportConfig.resolve()
    register_tools(mcp, readonly=readonly_from_env())
    if not config.is_http:
        mcp.run()
        return
    _apply_http_settings(mcp, config)
    _warn_if_exposed(config)
    _serve_http(mcp, config)

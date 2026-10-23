"""
Tests for surviving network loss (0.11.1).

Why this exists — the bug, as it happened:

The HTTP daemon runs for weeks on a laptop. Closing the lid takes the network
away; Telethon's background reconnect, at its default ``connection_retries=5``,
burned through five attempts in seconds (``OSError: [Errno 51] Network is
unreachable``), logged ``Automatic reconnection failed 5 time(s)`` and marked
the client disconnected for good. After wake every tool call answered
``ConnectionError: Cannot send requests while disconnected`` until someone
restarted the process by hand — while ``/health`` kept saying ``ok``.

Three layers, each tested here with Telethon mocked:

1. The Telethon client is built to retry forever.
2. A tool call that finds the client given up on reconnects first.
3. ``/health`` reports the Telegram link and answers 503 when it is down.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.mcpserver import MCPServer

from tg_mcp import client as client_module
from tg_mcp.client import (
    CONNECTED,
    DISCONNECTED,
    RECONNECTING,
    RETRY_DELAY_SECONDS,
    TelegramClientWrapper,
)
from tg_mcp.config import Config
from tg_mcp.server import (
    READ_TOOLS,
    WRITE_TOOLS,
    SharedTelegramClient,
    _reconnect_first,
    health,
    register_tools,
    tg_me,
)


def _wrapper_with(telethon_client: MagicMock) -> TelegramClientWrapper:
    """A wrapper whose Telethon client is *telethon_client*, without connecting."""
    wrapper = TelegramClientWrapper(
        Config(app_id=12345, api_hash="fakehash", session_path=Path("/tmp/test/session"))
    )
    wrapper._client = telethon_client
    return wrapper


def _telethon(*, user_connected: bool, transport_up: bool = True) -> MagicMock:
    """A Telethon client stub with the two pieces of state the wrapper reads."""
    tc = MagicMock()
    tc.is_connected = MagicMock(return_value=user_connected)
    tc._sender._transport_connected = MagicMock(return_value=transport_up)
    tc.connect = AsyncMock()
    tc.disconnect = AsyncMock()
    # client(request): the post-reconnect authorization check (0.11.2).
    tc.side_effect = AsyncMock(return_value=None)
    tc._updates_error = None
    return tc


# ---------------------------------------------------------------------------
# 1. Telethon is told to retry forever
# ---------------------------------------------------------------------------


class TestTelethonRetriesForever:
    async def test_client_is_built_with_unlimited_reconnects(self, mock_telegram_client) -> None:
        """connection_retries=-1 is what telethon.helpers.retry_range reads as
        "infinite"; the default 5 is the bug."""
        wrapper = TelegramClientWrapper(
            Config(app_id=12345, api_hash="fakehash", session_path=Path("/tmp/test/session"))
        )
        with patch("tg_mcp.client.TelegramClient", return_value=mock_telegram_client) as built:
            await wrapper.connect()

        kwargs = built.call_args.kwargs
        assert kwargs["connection_retries"] == -1
        assert kwargs["auto_reconnect"] is True
        assert kwargs["retry_delay"] == RETRY_DELAY_SECONDS > 0
        # Unchanged from before: the update stream stays off.
        assert kwargs["receive_updates"] is False

    def test_negative_retries_really_mean_infinite_in_the_installed_telethon(self) -> None:
        """Pin the semantics to the Telethon actually installed, not to its docs:
        both the initial connect and the reconnect loop iterate retry_range."""
        from itertools import islice

        from telethon.helpers import retry_range
        from telethon.network import mtprotosender

        assert len(list(islice(retry_range(-1), 1000))) == 1000
        assert len(list(islice(retry_range(-1, force_retry=False), 1000))) == 1000
        assert "retry_range(self._retries)" in inspect.getsource(mtprotosender.MTProtoSender._connect)
        assert "retry_range(retries, force_retry=False)" in inspect.getsource(
            mtprotosender.MTProtoSender._reconnect
        )


# ---------------------------------------------------------------------------
# Connection state
# ---------------------------------------------------------------------------


class TestConnectionState:
    def test_connected(self) -> None:
        assert _wrapper_with(_telethon(user_connected=True)).connection_state() == CONNECTED

    def test_reconnecting_while_transport_is_down(self) -> None:
        """is_connected() stays True for the whole background reconnect — it is
        the user's intent, not the link. The transport is what is down."""
        wrapper = _wrapper_with(_telethon(user_connected=True, transport_up=False))
        assert wrapper.connection_state() == RECONNECTING

    def test_disconnected_after_telethon_gave_up(self) -> None:
        wrapper = _wrapper_with(_telethon(user_connected=False))
        assert wrapper.connection_state() == DISCONNECTED

    def test_disconnected_before_connect(self) -> None:
        wrapper = TelegramClientWrapper(
            Config(app_id=12345, api_hash="fakehash", session_path=Path("/tmp/test/session"))
        )
        assert wrapper.connection_state() == DISCONNECTED

    def test_missing_private_transport_check_degrades_to_is_connected(self) -> None:
        """_transport_connected is private Telethon API; a rename must not turn
        /health into a 500."""
        tc = _telethon(user_connected=True)
        tc._sender = object()
        assert _wrapper_with(tc).connection_state() == CONNECTED


# ---------------------------------------------------------------------------
# 2. Reconnect before the call
# ---------------------------------------------------------------------------


class TestEnsureConnected:
    async def test_reconnects_a_client_telethon_gave_up_on(self) -> None:
        tc = _telethon(user_connected=False)
        assert await _wrapper_with(tc).ensure_connected() is True
        tc.connect.assert_awaited_once()

    async def test_leaves_a_connected_client_alone(self) -> None:
        tc = _telethon(user_connected=True)
        assert await _wrapper_with(tc).ensure_connected() is False
        tc.connect.assert_not_awaited()

    async def test_leaves_a_background_reconnect_alone(self) -> None:
        """Telethon is already on it; a second connect() would race its loop."""
        tc = _telethon(user_connected=True, transport_up=False)
        assert await _wrapper_with(tc).ensure_connected() is False
        tc.connect.assert_not_awaited()

    async def test_concurrent_calls_reconnect_once(self) -> None:
        """Several tool calls arriving after wake must not each call connect()."""
        tc = _telethon(user_connected=False)

        async def connect() -> None:
            await asyncio.sleep(0.01)
            tc.is_connected.return_value = True

        tc.connect.side_effect = connect
        wrapper = _wrapper_with(tc)

        results = await asyncio.gather(*(wrapper.ensure_connected() for _ in range(5)))

        assert tc.connect.await_count == 1
        assert results.count(True) == 1

    async def test_a_reconnect_that_hangs_times_out_and_cleans_up(self) -> None:
        """With unlimited retries connect() waits for the network forever; a
        tool call must get an answer instead."""
        tc = _telethon(user_connected=False)

        async def hang() -> None:
            await asyncio.sleep(10)

        tc.connect.side_effect = hang

        with patch.object(client_module, "RECONNECT_TIMEOUT_SECONDS", 0.01):
            with pytest.raises(ConnectionError, match="unreachable"):
                await _wrapper_with(tc).ensure_connected()

        tc.disconnect.assert_awaited_once()


def _ctx(wrapper: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.lifespan_context.client = wrapper
    return ctx


class TestToolGuard:
    async def test_tool_reconnects_before_calling_telegram(self) -> None:
        order: list[str] = []
        wrapper = MagicMock(spec_set=TelegramClientWrapper)
        wrapper.ensure_connected = AsyncMock(side_effect=lambda: order.append("reconnect"))
        wrapper.get_me = AsyncMock(side_effect=lambda: order.append("get_me") or MagicMock(
            id=1, first_name="A", last_name=None, username="a", phone=None))

        await _reconnect_first(tg_me)(ctx=_ctx(wrapper))

        assert order == ["reconnect", "get_me"]

    async def test_failed_reconnect_still_answers_in_the_tools_own_shape(self) -> None:
        """The guard does not invent an error shape; the tool's own try/except
        reports Telethon's error exactly as it did before."""
        wrapper = MagicMock(spec_set=TelegramClientWrapper)
        wrapper.ensure_connected = AsyncMock(side_effect=ConnectionError("Telegram is unreachable"))
        wrapper.get_me = AsyncMock(side_effect=ConnectionError("Cannot send requests while disconnected"))

        result = await _reconnect_first(tg_me)(ctx=_ctx(wrapper))

        assert result == {"error": "ConnectionError: Cannot send requests while disconnected"}

    def test_every_registered_tool_is_guarded(self) -> None:
        """The guard lives in one place — registration — so no tool can be added
        without it."""
        server = MCPServer("guard-test")
        register_tools(server, readonly=False)
        for fn in READ_TOOLS + WRITE_TOOLS:
            registered = server._tool_manager._tools[fn.__name__].fn
            assert registered is not fn
            assert registered.__wrapped__ is fn

    def test_guard_does_not_change_any_input_schema(self) -> None:
        """MCPServer derives the schema from the function; wrapping must be
        invisible to clients."""
        guarded, plain = MCPServer("guarded"), MCPServer("plain")
        register_tools(guarded, readonly=False)
        for fn in READ_TOOLS + WRITE_TOOLS:
            plain.add_tool(fn)
        for name, tool in plain._tool_manager._tools.items():
            other = guarded._tool_manager._tools[name]
            assert other.parameters == tool.parameters
            assert other.context_kwarg == tool.context_kwarg == "ctx"
            assert other.description == tool.description


# ---------------------------------------------------------------------------
# 3. /health tells the truth
# ---------------------------------------------------------------------------


def _shared_holding(state: str) -> SharedTelegramClient:
    shared = SharedTelegramClient()
    wrapper = MagicMock()
    wrapper.connection_state = MagicMock(return_value=state)
    shared._client = wrapper
    shared._sessions = 1
    return shared


class TestHealth:
    async def test_connected_is_200_ok(self) -> None:
        with patch("tg_mcp.server.shared_client", _shared_holding(CONNECTED)):
            response = await health(MagicMock())
        body = json.loads(response.body)
        assert response.status_code == 200
        assert body["status"] == "ok"
        assert body["telegram"] == CONNECTED

    @pytest.mark.parametrize("state", [RECONNECTING, DISCONNECTED])
    async def test_down_is_503(self, state: str) -> None:
        """The lie this release fixes: 200 "ok" with a dead client."""
        with patch("tg_mcp.server.shared_client", _shared_holding(state)):
            response = await health(MagicMock())
        body = json.loads(response.body)
        assert response.status_code == 503
        assert body["status"] == "degraded"
        assert body["telegram"] == state

    async def test_no_client_held_yet_is_not_a_failure(self) -> None:
        """sse builds the client on the first session; idle is healthy."""
        with patch("tg_mcp.server.shared_client", SharedTelegramClient()):
            response = await health(MagicMock())
        body = json.loads(response.body)
        assert response.status_code == 200
        assert body["telegram"] is None

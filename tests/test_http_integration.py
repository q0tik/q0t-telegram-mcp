"""
End-to-end tests for the HTTP transport: a real uvicorn server, real MCP
clients over the wire, Telethon mocked.

The test that matters is `test_two_concurrent_clients_share_one_session`. It is
the scenario that fails on stdio: two agent runs at once spawn two processes on
one Telethon session file, and both die with

    sqlite3.OperationalError: database is locked
      telethon/sessions/sqlite.py  c.execute('delete from sessions')

because Telethon *writes* to the session when it starts. Handing each process a
copy of the file does not help — the same auth_key used from two places invites
AUTH_KEY_DUPLICATED, which logs the account out for real.

Here both clients reach one long-lived process, and the assertion is that the
Telegram client was built and connected exactly once no matter how many MCP
clients arrive.
"""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer

from tg_mcp.models import UserInfo
from tg_mcp.server import SharedTelegramClient, health, lifespan, register_tools

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def _serving(app):
    """Run *app* on a free loopback port until the block exits.

    The socket is bound before uvicorn starts and handed over, so the port
    cannot be taken by something else between picking it and listening.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        sock.close()


@asynccontextmanager
async def _http_server(readonly: bool = False, connection_state: str = "connected"):
    """A fresh MCPServer over HTTP with Telethon mocked.

    Fresh per test because a session manager may only be run once per instance.
    Yields (base_url, created_wrappers) so tests can assert on how many Telegram
    clients were actually built.
    """
    created: list[MagicMock] = []

    def build(config):
        wrapper = MagicMock()
        wrapper.connect = AsyncMock()
        wrapper.disconnect = AsyncMock()
        wrapper.ensure_connected = AsyncMock(return_value=False)
        wrapper.connection_state = MagicMock(return_value=connection_state)
        wrapper.get_me = AsyncMock(
            return_value=UserInfo(id=777, first_name="Alice", last_name=None,
                                  username="alice", phone="15550001234")
        )
        created.append(wrapper)
        return wrapper

    shared = SharedTelegramClient()
    server = MCPServer("q0t-telegram-mcp-test", lifespan=lifespan)
    register_tools(server, readonly=readonly)
    server.custom_route("/health", methods=["GET"])(health)

    with patch("tg_mcp.server.TelegramClientWrapper", side_effect=build), \
            patch("tg_mcp.server.Config") as config, \
            patch("tg_mcp.server.shared_client", shared):
        config.from_env.return_value = MagicMock()
        async with _serving(server.streamable_http_app()) as base:
            yield base, created, shared


@asynccontextmanager
async def _sse_server(connection_state: str = "connected"):
    """The same server, served over sse instead of streamable HTTP.

    Worth its own fixture because sse is the transport where the lifespan is
    still entered per *connection*: ``sse_app`` calls ``Server.run()`` inside
    each one. Streamable HTTP stopped exercising that in mcp 2.0, where the
    session manager enters the lifespan once and would hold a single client even
    if `SharedTelegramClient` were deleted.
    """
    created: list[MagicMock] = []

    def build(config):
        wrapper = MagicMock()
        wrapper.connect = AsyncMock()
        wrapper.disconnect = AsyncMock()
        wrapper.ensure_connected = AsyncMock(return_value=False)
        wrapper.connection_state = MagicMock(return_value=connection_state)
        wrapper.get_me = AsyncMock(
            return_value=UserInfo(id=777, first_name="Alice", last_name=None,
                                  username="alice", phone="15550001234")
        )
        created.append(wrapper)
        return wrapper

    shared = SharedTelegramClient()
    server = MCPServer("q0t-telegram-mcp-test-sse", lifespan=lifespan)
    register_tools(server, readonly=False)

    with patch("tg_mcp.server.TelegramClientWrapper", side_effect=build), \
            patch("tg_mcp.server.Config") as config, \
            patch("tg_mcp.server.shared_client", shared):
        config.from_env.return_value = MagicMock()
        async with _serving(server.sse_app()) as base:
            yield base, created, shared


@asynccontextmanager
async def _sse_client_session(base: str):
    async with sse_client(f"{base}/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def _mcp_client(base: str):
    # Two streams, not three: mcp 2.0 dropped the trailing get_session_id
    # callback from the client transport's yield.
    async with streamable_http_client(f"{base}/mcp") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


class TestHttpSmoke:
    async def test_client_can_list_tools_over_http(self) -> None:
        async with _http_server() as (base, _, _shared):
            async with _mcp_client(base) as session:
                tools = {t.name for t in (await session.list_tools()).tools}
        assert "tg_send" in tools
        assert "tg_dialogs" in tools

    async def test_tool_call_returns_data_over_http(self) -> None:
        async with _http_server() as (base, _, _shared):
            async with _mcp_client(base) as session:
                result = await session.call_tool("tg_me", {})
        assert "alice" in str(result.content)

    async def test_readonly_still_applies_over_http(self) -> None:
        """The 0.7.0 guarantee must not be transport-dependent."""
        async with _http_server(readonly=True) as (base, _, _shared):
            async with _mcp_client(base) as session:
                tools = {t.name for t in (await session.list_tools()).tools}
        assert "tg_send" not in tools
        assert "tg_dialogs" in tools


class TestConcurrentClients:
    async def test_two_concurrent_clients_share_one_session(self) -> None:
        """The acceptance test for the whole feature.

        Two MCP clients connected at once, each calling a tool, must both be
        served — and the Telegram session must have been opened exactly once.
        """
        async with _http_server() as (base, created, _shared):
            async def one_client():
                async with _mcp_client(base) as session:
                    return await session.call_tool("tg_me", {})

            first, second = await asyncio.gather(one_client(), one_client())

        assert "alice" in str(first.content)
        assert "alice" in str(second.content)
        assert len(created) == 1, "one Telegram client per process, not per MCP session"
        created[0].connect.assert_awaited_once()

    async def test_many_clients_still_open_one_session(self) -> None:
        """More clients than the two that reproduced the bug, in case sharing
        holds for two by accident."""
        async with _http_server() as (base, created, _shared):
            async def one_client():
                async with _mcp_client(base) as session:
                    return await session.call_tool("tg_me", {})

            results = await asyncio.gather(*(one_client() for _ in range(5)))

        assert all("alice" in str(r.content) for r in results)
        assert len(created) == 1
        created[0].connect.assert_awaited_once()

    async def test_parallel_tool_calls_on_one_session_all_answer(self) -> None:
        """Telethon multiplexes concurrent requests itself; this checks nothing
        in our layer serialises them into a deadlock or drops one."""
        async with _http_server() as (base, created, _shared):
            async with _mcp_client(base) as session:
                results = await asyncio.gather(
                    *(session.call_tool("tg_me", {}) for _ in range(6))
                )
        assert len(results) == 6
        assert all("alice" in str(r.content) for r in results)
        assert len(created) == 1

    async def test_client_is_held_for_the_process_not_per_session(self) -> None:
        """Sessions coming and going must not churn the Telegram connection.

        Changed in mcp 2.0: its session manager enters the lifespan once for its
        own lifetime rather than once per connecting client, so the hold is taken
        at startup and kept. Under mcp 1.x this same scenario dropped to zero
        holds between clients and reconnected for the next one.
        """
        async with _http_server() as (base, created, shared):
            async with _mcp_client(base):
                pass
            async with _mcp_client(base):
                pass
            assert shared.active_sessions == 1
            assert len(created) == 1, "reconnecting per client is the bug, not the fix"
            created[0].disconnect.assert_not_awaited()

    async def test_client_is_released_when_the_server_stops(self) -> None:
        """The hold must not outlive the server, or shutdown would leak the
        Telegram connection and the session file stays locked."""
        async with _http_server() as (base, created, shared):
            async with _mcp_client(base):
                pass
        for _ in range(100):
            if shared.active_sessions == 0:
                break
            await asyncio.sleep(0.01)
        assert shared.active_sessions == 0
        created[0].disconnect.assert_awaited_once()


class TestSseStillNeedsTheSharedClient:
    """The live guard on `SharedTelegramClient`.

    Under mcp 1.x the streamable-HTTP tests above were that guard: deleting the
    sharing made them fail with five Telegram clients instead of one. mcp 2.0
    moved the streamable-HTTP lifespan to once-per-process, so those tests now
    pass either way and stopped guarding anything. sse did not change, so the
    guarantee has to be checked here or nowhere.
    """

    async def test_two_sse_clients_share_one_telegram_client(self) -> None:
        async with _sse_server() as (base, created, _shared):
            async def one_client():
                async with _sse_client_session(base) as session:
                    return await session.call_tool("tg_me", {})

            first, second = await asyncio.gather(one_client(), one_client())

        assert "alice" in str(first.content)
        assert "alice" in str(second.content)
        assert len(created) == 1, (
            "sse enters the lifespan per connection — without SharedTelegramClient "
            "this is one Telethon client per connected client, which is the "
            "`database is locked` bug the HTTP transport exists to fix"
        )
        created[0].connect.assert_awaited_once()


class TestHealthOverHttp:
    async def test_health_answers_without_an_mcp_session(self) -> None:
        """Consumers need to know the process is up before opening a session."""
        # httpx2, not httpx: that is what mcp 2.0 depends on, and plain httpx is
        # no longer pulled in at all.
        import httpx2

        async with _http_server() as (base, created, _shared):
            # Changed in mcp 2.0: the lifespan runs when the app starts, so the
            # Telegram client already exists before any request arrives. What
            # still must hold is that /health does not build one *of its own*.
            before = len(created)
            async with httpx2.AsyncClient() as http:
                response = await http.get(f"{base}/health")
            after = len(created)

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["name"] == "q0t-telegram-mcp"
        assert response.json()["telegram"] == "connected"
        assert after == before, "/health must not open a Telegram connection"

    @pytest.mark.parametrize("state", ["reconnecting", "disconnected"])
    async def test_health_is_503_when_telegram_is_down(self, state: str) -> None:
        """The bug 0.11.1 fixes: after a laptop slept through Telethon's retries,
        /health kept answering 200 "ok" while every tool call failed. A
        supervisor reading it could not tell. Now it can."""
        import httpx2

        async with _http_server(connection_state=state) as (base, _created, _shared):
            async with httpx2.AsyncClient() as http:
                response = await http.get(f"{base}/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["telegram"] == state


class TestReconnectBeforeCallOverHttp:
    async def test_tool_call_over_http_reconnects_first(self) -> None:
        """The guard is applied where tools are registered, so this checks it
        survives MCPServer's own introspection: the wrapped tool still gets its
        Context, and the reconnect happens before the Telegram call."""
        async with _http_server() as (base, created, _shared):
            order: list[str] = []
            created[0].ensure_connected.side_effect = lambda: order.append("reconnect")
            created[0].get_me.side_effect = lambda: (order.append("get_me"), UserInfo(
                id=777, first_name="Alice", last_name=None, username="alice", phone=None))[1]
            async with _mcp_client(base) as session:
                result = await session.call_tool("tg_me", {})

        assert "alice" in str(result.content)
        assert order == ["reconnect", "get_me"]

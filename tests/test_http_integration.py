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
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

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
async def _http_server(readonly: bool = False):
    """A fresh FastMCP over HTTP with Telethon mocked.

    Fresh per test because a FastMCP session manager may only be run once per
    instance. Yields (base_url, created_wrappers) so tests can assert on how
    many Telegram clients were actually built.
    """
    created: list[MagicMock] = []

    def build(config):
        wrapper = MagicMock()
        wrapper.connect = AsyncMock()
        wrapper.disconnect = AsyncMock()
        wrapper.get_me = AsyncMock(
            return_value=UserInfo(id=777, first_name="Alice", last_name=None,
                                  username="alice", phone="15550001234")
        )
        created.append(wrapper)
        return wrapper

    shared = SharedTelegramClient()
    server = FastMCP("q0t-telegram-mcp-test", lifespan=lifespan)
    register_tools(server, readonly=readonly)
    server.custom_route("/health", methods=["GET"])(health)

    with patch("tg_mcp.server.TelegramClientWrapper", side_effect=build), \
            patch("tg_mcp.server.Config") as config, \
            patch("tg_mcp.server.shared_client", shared):
        config.from_env.return_value = MagicMock()
        async with _serving(server.streamable_http_app()) as base:
            yield base, created, shared


@asynccontextmanager
async def _mcp_client(base: str):
    async with streamable_http_client(f"{base}/mcp") as (read, write, _):
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

    async def test_client_is_released_when_everyone_disconnects(self) -> None:
        """Sessions that come and go must not leak holds, or the process would
        never release the Telegram connection."""
        async with _http_server() as (base, _created, shared):
            async with _mcp_client(base):
                pass
            async with _mcp_client(base):
                pass
            # Give the server's session teardown a moment to run.
            for _ in range(100):
                if shared.active_sessions == 0:
                    break
                await asyncio.sleep(0.01)
            assert shared.active_sessions == 0


class TestHealthOverHttp:
    async def test_health_answers_without_an_mcp_session(self) -> None:
        """Consumers need to know the process is up before opening a session."""
        import httpx

        async with _http_server() as (base, created, _shared):
            async with httpx.AsyncClient() as http:
                response = await http.get(f"{base}/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["name"] == "q0t-telegram-mcp"
        # Health must not itself open a Telegram connection.
        assert created == []

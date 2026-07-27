"""
Tests for SharedTelegramClient (src/tg_mcp/server.py).

Why this exists — this is the whole point of the HTTP transport:

FastMCP's lifespan is entered per *MCP session*, not per process. The low-level
server does `enter_async_context(self.lifespan(self))` inside `Server.run()`
(`mcp/server/lowlevel/server.py`), and the streamable-HTTP session manager calls
`Server.run()` once for every client that connects
(`mcp/server/streamable_http_manager.py::_handle_stateful_request`).

So adding an HTTP transport without sharing the client would open one Telethon
client per connected client, all against the same session file, inside one
process — reproducing the `database is locked` failure the HTTP mode exists to
fix, just at a different address. Telethon writes to the session on connect, and
a duplicated auth_key additionally risks AUTH_KEY_DUPLICATED, which logs the
account out for real.

Hence: exactly one Telethon client per process, shared by every session,
reference-counted so stdio (one session, ever) keeps its old connect/disconnect
lifecycle exactly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_mcp.server import SharedTelegramClient, lifespan


@pytest.fixture
def fake_wrapper_factory():
    """Patch TelegramClientWrapper; return (patched_class, created_instances)."""
    created: list[MagicMock] = []

    def build(config):
        w = MagicMock()
        w.connect = AsyncMock()
        w.disconnect = AsyncMock()
        created.append(w)
        return w

    with patch("tg_mcp.server.TelegramClientWrapper", side_effect=build) as cls, \
            patch("tg_mcp.server.Config") as cfg:
        cfg.from_env.return_value = MagicMock()
        yield cls, created


class TestOneClientPerProcess:
    async def test_two_sessions_share_one_client(self, fake_wrapper_factory) -> None:
        """The failure this whole feature exists to prevent."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()

        first = await shared.acquire()
        second = await shared.acquire()

        assert first is second
        assert len(created) == 1
        created[0].connect.assert_awaited_once()

    async def test_concurrent_acquires_still_connect_once(self, fake_wrapper_factory) -> None:
        """Two clients connecting at the same instant is the exact scenario that
        broke on stdio — the lock must make it a single connect, not a race."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()

        clients = await asyncio.gather(*(shared.acquire() for _ in range(8)))

        assert len({id(c) for c in clients}) == 1
        assert len(created) == 1
        created[0].connect.assert_awaited_once()

    async def test_session_count_is_observable(self, fake_wrapper_factory) -> None:
        shared = SharedTelegramClient()
        assert shared.active_sessions == 0
        await shared.acquire()
        await shared.acquire()
        assert shared.active_sessions == 2


class TestReferenceCounting:
    async def test_client_survives_while_another_session_holds_it(
        self, fake_wrapper_factory
    ) -> None:
        """One HTTP client disconnecting must not tear the Telegram connection
        out from under the others."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()
        await shared.acquire()
        await shared.acquire()

        await shared.release()

        created[0].disconnect.assert_not_awaited()
        assert shared.active_sessions == 1

    async def test_last_release_disconnects(self, fake_wrapper_factory) -> None:
        """stdio has exactly one session, so this is its old lifecycle intact."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()
        await shared.acquire()

        await shared.release()

        created[0].disconnect.assert_awaited_once()
        assert shared.active_sessions == 0

    async def test_a_later_session_gets_a_fresh_client(self, fake_wrapper_factory) -> None:
        """After everyone has left, the next arrival reconnects rather than
        handing out a disconnected client."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()
        await shared.acquire()
        await shared.release()

        await shared.acquire()

        assert len(created) == 2
        created[1].connect.assert_awaited_once()

    async def test_extra_releases_do_not_drive_the_count_negative(
        self, fake_wrapper_factory
    ) -> None:
        """A negative count would keep the next real session from ever
        disconnecting."""
        shared = SharedTelegramClient()
        await shared.acquire()
        await shared.release()
        await shared.release()
        assert shared.active_sessions == 0


class TestFailureHandling:
    async def test_a_failed_connect_is_not_cached(self, fake_wrapper_factory) -> None:
        """A broken client handed to every later session would turn one bad
        startup into a permanently dead server."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()

        created_before = len(created)
        with pytest.raises(RuntimeError):
            with patch("tg_mcp.server.TelegramClientWrapper") as cls:
                w = MagicMock()
                w.connect = AsyncMock(side_effect=RuntimeError("Not authorized"))
                cls.return_value = w
                await shared.acquire()

        assert shared.active_sessions == 0
        assert len(created) == created_before

    async def test_a_failed_connect_lets_a_later_session_succeed(
        self, fake_wrapper_factory
    ) -> None:
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()
        with patch("tg_mcp.server.TelegramClientWrapper") as cls:
            w = MagicMock()
            w.connect = AsyncMock(side_effect=RuntimeError("Not authorized"))
            cls.return_value = w
            with pytest.raises(RuntimeError):
                await shared.acquire()

        client = await shared.acquire()
        assert client is created[0]


class TestLifespanUsesTheSharedClient:
    async def test_lifespan_hands_out_the_shared_client(self, fake_wrapper_factory) -> None:
        """The wiring: without this the sharing exists but nothing uses it."""
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()

        with patch("tg_mcp.server.shared_client", shared):
            async with lifespan(MagicMock()) as first:
                async with lifespan(MagicMock()) as second:
                    assert first.client is second.client
                    assert len(created) == 1

    async def test_lifespan_releases_on_exit(self, fake_wrapper_factory) -> None:
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()

        with patch("tg_mcp.server.shared_client", shared):
            async with lifespan(MagicMock()):
                pass

        assert shared.active_sessions == 0
        created[0].disconnect.assert_awaited_once()

    async def test_nested_lifespans_disconnect_only_once_at_the_end(
        self, fake_wrapper_factory
    ) -> None:
        _, created = fake_wrapper_factory
        shared = SharedTelegramClient()

        with patch("tg_mcp.server.shared_client", shared):
            async with lifespan(MagicMock()):
                async with lifespan(MagicMock()):
                    pass
                created[0].disconnect.assert_not_awaited()

        created[0].disconnect.assert_awaited_once()

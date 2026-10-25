"""
Tests for noticing a lost session and a client nobody brings back (0.11.2).

Why this exists — the incident, as it happened (prod, tenant container, 0.11.0):

The HTTP daemon logged ``Disconnecting from 149.154.167.51:443/TcpFull...`` and
never reconnected. Nobody outside had asked it to: Telethon's own update loop
calls ``disconnect()`` when ``getDifference`` answers that the account is not
logged in (SESSION_REVOKED / AUTH_KEY_UNREGISTERED / AUTH_KEY_DUPLICATED), and
nothing in this server ever called ``connect()`` again. The process stayed up
for four weeks, ``/health`` said ``200 ok``, every tool call failed. A restart
then stopped at ``Not authorized``: the session had been revoked.

Three things, each tested here with Telethon mocked:

1. The wrapper notices auth loss (Telethon's record of it, or a check after
   reconnecting) and refuses to reconnect into a dead session.
2. ``/health`` says ``unauthorized`` with a 503.
3. A watchdog reconnects a client Telethon gave up on, and ends the process
   with a distinct status when it cannot: 78 for a lost session, 75 for a
   client that will not come back.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.errors import AuthKeyDuplicatedError, AuthKeyUnregisteredError, SessionRevokedError
from telethon.errors.common import AuthKeyNotFound

from tg_mcp.client import (
    DISCONNECTED,
    UNAUTHORIZED,
    AuthLostError,
    TelegramClientWrapper,
)
from tg_mcp.config import Config
from tg_mcp.server import (
    EXIT_AUTH_LOST,
    EXIT_UNRECOVERABLE,
    MAX_FAILED_RECOVERIES,
    SharedTelegramClient,
    health,
    shared_client,
)


def _telethon(*, user_connected: bool, updates_error: BaseException | None = None) -> MagicMock:
    tc = MagicMock()
    tc.is_connected = MagicMock(return_value=user_connected)
    tc._sender._transport_connected = MagicMock(return_value=True)
    tc.connect = AsyncMock()
    tc.disconnect = AsyncMock()
    tc.side_effect = AsyncMock(return_value=None)
    tc._updates_error = updates_error
    return tc


def _wrapper_with(tc: MagicMock) -> TelegramClientWrapper:
    wrapper = TelegramClientWrapper(
        Config(app_id=12345, api_hash="fakehash", session_path=Path("/tmp/test/session"))
    )
    wrapper._client = tc
    return wrapper


def _revoked() -> SessionRevokedError:
    return SessionRevokedError(request=None)


# ---------------------------------------------------------------------------
# 1. The wrapper notices auth loss
# ---------------------------------------------------------------------------


class TestAuthLossIsNoticed:
    @pytest.mark.parametrize(
        "error",
        [
            SessionRevokedError(request=None),
            AuthKeyUnregisteredError(request=None),
            AuthKeyDuplicatedError(request=None),
            AuthKeyNotFound(),
        ],
    )
    def test_update_loop_auth_error_reads_as_unauthorized(self, error: BaseException) -> None:
        """What Telethon's update loop leaves behind when it disconnects itself."""
        wrapper = _wrapper_with(_telethon(user_connected=False, updates_error=error))
        assert wrapper.auth_error() is error
        assert wrapper.connection_state() == UNAUTHORIZED

    def test_other_update_loop_errors_are_not_auth_loss(self) -> None:
        wrapper = _wrapper_with(_telethon(user_connected=False, updates_error=RuntimeError("bug")))
        assert wrapper.auth_error() is None
        assert wrapper.connection_state() == DISCONNECTED

    async def test_no_reconnect_into_a_revoked_session(self) -> None:
        """Reconnecting would succeed — MTProto does not care — and fail again on
        every request. That is the zombie; refuse instead."""
        tc = _telethon(user_connected=False, updates_error=_revoked())
        with pytest.raises(AuthLostError, match="SessionRevokedError"):
            await _wrapper_with(tc).ensure_connected()
        tc.connect.assert_not_awaited()

    async def test_auth_loss_survives_telethon_resetting_its_record(self) -> None:
        """Telethon clears _updates_error on its next connect; ours is sticky."""
        tc = _telethon(user_connected=False, updates_error=_revoked())
        wrapper = _wrapper_with(tc)
        wrapper.auth_error()
        tc._updates_error = None
        tc.is_connected.return_value = True
        assert wrapper.connection_state() == UNAUTHORIZED

    async def test_reconnect_checks_authorization_with_a_real_request(self) -> None:
        """is_user_authorized() is cached by Telethon and would still say True."""
        tc = _telethon(user_connected=False)
        tc.side_effect = AsyncMock(side_effect=AuthKeyUnregisteredError(request=None))
        wrapper = _wrapper_with(tc)

        with pytest.raises(AuthLostError):
            await wrapper.ensure_connected()

        tc.connect.assert_awaited_once()
        tc.is_user_authorized.assert_not_called()
        assert wrapper.connection_state() == UNAUTHORIZED

    async def test_a_healthy_reconnect_is_still_a_reconnect(self) -> None:
        tc = _telethon(user_connected=False)
        assert await _wrapper_with(tc).ensure_connected() is True
        tc.side_effect.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. /health
# ---------------------------------------------------------------------------


class TestHealthUnauthorized:
    async def test_unauthorized_is_503(self) -> None:
        shared = SharedTelegramClient()
        shared._client = _wrapper_with(_telethon(user_connected=False, updates_error=_revoked()))
        shared._sessions = 1
        with patch("tg_mcp.server.shared_client", shared):
            response = await health(MagicMock())
        body = json.loads(response.body)
        assert response.status_code == 503
        assert body["status"] == "unauthorized"
        assert body["telegram"] == UNAUTHORIZED

    async def test_the_prod_zombie_is_no_longer_200(self) -> None:
        """Disconnected by Telethon, one session still held: exactly what
        answered 200 ok for four weeks."""
        shared = SharedTelegramClient()
        shared._client = _wrapper_with(_telethon(user_connected=False))
        shared._sessions = 1
        with patch("tg_mcp.server.shared_client", shared):
            response = await health(MagicMock())
        assert response.status_code == 503
        assert json.loads(response.body)["telegram"] == DISCONNECTED


# ---------------------------------------------------------------------------
# 3. The watchdog
# ---------------------------------------------------------------------------


def _fake_factory(ensure_connected: AsyncMock):
    def build(config):
        w = MagicMock()
        w.connect = AsyncMock()
        w.disconnect = AsyncMock()
        w.ensure_connected = ensure_connected
        return w

    return build


async def _run_watchdog(ensure_connected: AsyncMock, *, ticks: int) -> MagicMock:
    """Acquire with a near-zero interval, let the watchdog tick, return on_fatal."""
    on_fatal = MagicMock()
    shared = SharedTelegramClient(watchdog_interval=0, on_fatal=on_fatal)
    with patch("tg_mcp.server.TelegramClientWrapper", side_effect=_fake_factory(ensure_connected)), \
            patch("tg_mcp.server.Config"):
        await shared.acquire()
        for _ in range(ticks * 3):
            await asyncio.sleep(0)
        await shared.release()
    return on_fatal


class TestWatchdog:
    def test_the_process_wide_client_runs_one(self) -> None:
        assert shared_client._watchdog_interval is not None

    def test_a_bare_instance_does_not(self) -> None:
        """Tests and sse sharing stay free of background tasks unless asked."""
        assert SharedTelegramClient()._watchdog_interval is None

    async def test_lost_session_exits_with_auth_status(self) -> None:
        on_fatal = await _run_watchdog(AsyncMock(side_effect=AuthLostError("revoked")), ticks=2)
        on_fatal.assert_called_once()
        assert on_fatal.call_args.args[0] == EXIT_AUTH_LOST == 78

    async def test_client_that_will_not_come_back_exits_after_the_limit(self) -> None:
        ensure = AsyncMock(side_effect=ConnectionError("unreachable"))
        on_fatal = await _run_watchdog(ensure, ticks=MAX_FAILED_RECOVERIES + 2)
        on_fatal.assert_called_once()
        assert on_fatal.call_args.args[0] == EXIT_UNRECOVERABLE == 75
        assert ensure.await_count == MAX_FAILED_RECOVERIES

    async def test_a_success_resets_the_failure_count(self) -> None:
        outcomes = [ConnectionError("x")] * (MAX_FAILED_RECOVERIES - 1) + [False]
        outcomes += [ConnectionError("x")] * (MAX_FAILED_RECOVERIES - 1) + [False] * 50
        on_fatal = await _run_watchdog(AsyncMock(side_effect=outcomes), ticks=2 * MAX_FAILED_RECOVERIES + 2)
        on_fatal.assert_not_called()

    async def test_healthy_client_is_left_alone(self) -> None:
        ensure = AsyncMock(return_value=False)
        on_fatal = await _run_watchdog(ensure, ticks=3)
        on_fatal.assert_not_called()
        assert ensure.await_count >= 1

    async def test_last_release_stops_it(self) -> None:
        on_fatal = MagicMock()
        shared = SharedTelegramClient(watchdog_interval=3600, on_fatal=on_fatal)
        with patch("tg_mcp.server.TelegramClientWrapper", side_effect=_fake_factory(AsyncMock())), \
                patch("tg_mcp.server.Config"):
            await shared.acquire()
            task = shared._watchdog
            await shared.release()
            await asyncio.sleep(0)
        assert task is not None and task.cancelled()
        assert shared._watchdog is None

    def test_default_fatal_handler_exits_nonzero(self, capsys: pytest.CaptureFixture) -> None:
        from tg_mcp import server

        with patch("tg_mcp.server.os._exit") as exit_:
            server._exit_for_supervisor(EXIT_AUTH_LOST, "session revoked")
        exit_.assert_called_once_with(78)
        assert "session revoked" in capsys.readouterr().err

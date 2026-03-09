"""
Tests for MCP tool functions defined in src/tg_mcp/server.py.

Contracts verified (per docs/architecture.md §7):
  - tg_me returns a dict with UserInfo fields
  - tg_dialogs passes unread_only, limit, offset correctly; returns list[dict]
  - tg_dialog passes name, limit, offset; returns list[dict]
  - tg_send uses parameter named 'name' (NOT 'to'); returns {"ok": True}
  - tg_send returns {"error": ...} on ValueError, not an exception
  - tg_read returns {"ok": True}; returns {"error": ...} on ValueError

All tests mock the TelegramClientWrapper — no real Telegram calls are made.
The MCP Context object is mocked so tools can be called as plain async functions.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock

from tg_mcp.models import Dialog, Message, UserInfo
from tg_mcp.server import tg_me, tg_dialogs, tg_dialog, tg_send, tg_read


# ---------------------------------------------------------------------------
# Helper: build a mock MCP Context whose lifespan_context.client is the
# provided client wrapper. Defined here for use across all test classes.
# ---------------------------------------------------------------------------

def _make_ctx(client_wrapper):
    """Return a MagicMock shaped like an MCP Context with a lifespan client."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context.client = client_wrapper
    return ctx


def _make_mock_client():
    """Return a fresh AsyncMock TelegramClientWrapper with sensible defaults."""
    client = MagicMock()
    client.get_me = AsyncMock(return_value=UserInfo(
        id=123456789,
        first_name="Alice",
        last_name="Smith",
        username="alicesmith",
        phone="+15550001234",
    ))
    client.get_dialogs = AsyncMock(return_value=[
        Dialog(
            id=987654321,
            name="Alice Smith",
            unread_count=3,
            is_user=True,
            is_group=False,
            is_channel=False,
            last_message="Hey there!",
            date="2026-03-08T12:00:00+00:00",
        )
    ])
    client.get_messages = AsyncMock(return_value=[
        Message(
            id=42,
            date="2026-03-08T11:30:00+00:00",
            text="Hello, world!",
            out=False,
            sender="Alice Smith",
        )
    ])
    client.send_message = AsyncMock(return_value=None)
    client.mark_read = AsyncMock(return_value=None)
    return client


class TestRunServer:
    """Tests for the run_server() function."""

    def test_run_server_calls_mcp_run(self) -> None:
        """
        run_server() calls mcp.run() which starts the MCP server.
        """
        from unittest.mock import patch
        from tg_mcp.server import run_server, mcp

        with patch.object(mcp, "run") as mock_run:
            run_server()
            mock_run.assert_called_once()


class TestTgMe:
    """Tests for the tg_me MCP tool."""

    async def test_tg_me_returns_dict(self, mock_client_wrapper) -> None:
        """
        tg_me() returns a plain dict (not a UserInfo dataclass instance).

        Expected: return value is a dict.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_me(ctx)
        assert isinstance(result, dict)

    async def test_tg_me_contains_expected_fields(self, mock_client_wrapper) -> None:
        """
        The dict returned by tg_me() contains all UserInfo fields:
        id, first_name, last_name, username, phone.

        Expected: all five keys are present in the returned dict.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_me(ctx)
        assert "id" in result
        assert "first_name" in result
        assert "last_name" in result
        assert "username" in result
        assert "phone" in result

    async def test_tg_me_values_match_user_info(self, mock_client_wrapper) -> None:
        """
        The values in the dict returned by tg_me() match the UserInfo fields.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_me(ctx)
        assert result["id"] == 123456789
        assert result["first_name"] == "Alice"
        assert result["last_name"] == "Smith"
        assert result["username"] == "alicesmith"
        assert result["phone"] == "+15550001234"

    async def test_tg_me_error_returns_error_dict(self, mock_client_wrapper) -> None:
        """
        When get_me() raises ValueError, tg_me() returns {"error": ...}.
        """
        client = _make_mock_client()
        client.get_me = AsyncMock(side_effect=ValueError("something went wrong"))
        ctx = _make_ctx(client)
        result = await tg_me(ctx)
        assert "error" in result


class TestTgDialogs:
    """Tests for the tg_dialogs MCP tool."""

    async def test_tg_dialogs_returns_list_of_dicts(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialogs() returns a list of dicts (not Dialog dataclass instances).

        Expected: return value is a list; each element is a dict.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_dialogs(ctx)
        assert isinstance(result, list)
        assert len(result) > 0
        for item in result:
            assert isinstance(item, dict)

    async def test_tg_dialogs_passes_unread_only(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialogs() passes the unread_only argument through to
        client.get_dialogs().

        Expected: client.get_dialogs() is called with unread_only=True when
        tg_dialogs(unread_only=True) is invoked.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialogs(ctx, unread_only=True)
        client.get_dialogs.assert_called_once_with(unread_only=True, limit=20, offset=0)

    async def test_tg_dialogs_passes_limit_and_offset(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialogs() passes limit and offset through to client.get_dialogs().

        Expected: client.get_dialogs() is called with limit=5, offset=10 when
        tg_dialogs(limit=5, offset=10) is invoked.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialogs(ctx, limit=5, offset=10)
        client.get_dialogs.assert_called_once_with(unread_only=False, limit=5, offset=10)

    async def test_tg_dialogs_error_returns_list_with_error_dict(
        self, mock_client_wrapper
    ) -> None:
        """
        When client.get_dialogs() raises ValueError, tg_dialogs() returns
        a list containing an error dict.
        """
        client = _make_mock_client()
        client.get_dialogs = AsyncMock(side_effect=ValueError("Dialog not found: X"))
        ctx = _make_ctx(client)
        result = await tg_dialogs(ctx)
        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0]

    async def test_tg_dialogs_dict_has_dialog_fields(
        self, mock_client_wrapper
    ) -> None:
        """
        Each dict in the list returned by tg_dialogs() contains all Dialog fields.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_dialogs(ctx)
        d = result[0]
        assert "id" in d
        assert "name" in d
        assert "unread_count" in d
        assert "is_user" in d
        assert "is_group" in d
        assert "is_channel" in d
        assert "last_message" in d
        assert "date" in d


class TestTgDialog:
    """Tests for the tg_dialog MCP tool."""

    async def test_tg_dialog_returns_list_of_dicts(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialog() returns a list of dicts (not Message dataclass instances).

        Expected: return value is a list; each element is a dict.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_dialog(ctx, name="Alice")
        assert isinstance(result, list)
        assert len(result) > 0
        for item in result:
            assert isinstance(item, dict)

    async def test_tg_dialog_passes_name_parameter(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialog() passes the name argument through to client.get_messages().

        Expected: client.get_messages() is called with name="Alice".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, name="Alice")
        client.get_messages.assert_called_once_with(name="Alice", limit=20, offset=0)

    async def test_tg_dialog_passes_limit_and_offset(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialog() passes limit and offset through to client.get_messages().
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, name="Alice", limit=5, offset=3)
        client.get_messages.assert_called_once_with(name="Alice", limit=5, offset=3)

    async def test_tg_dialog_error_returns_error_dict(
        self, mock_client_wrapper
    ) -> None:
        """
        When get_messages() raises ValueError, tg_dialog() returns a list with
        an error dict.
        """
        client = _make_mock_client()
        client.get_messages = AsyncMock(side_effect=ValueError("Dialog not found: Bob"))
        ctx = _make_ctx(client)
        result = await tg_dialog(ctx, name="Bob")
        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0]
        assert "Bob" in result[0]["error"]


class TestTgSend:
    """Tests for the tg_send MCP tool."""

    async def test_tg_send_returns_ok(self, mock_client_wrapper) -> None:
        """
        On success, tg_send() returns {"ok": True}.

        Expected: return value is exactly {"ok": True}.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send(ctx, name="Alice", text="Hello")
        assert result == {"ok": True}

    async def test_tg_send_passes_name_and_text(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_send() passes both name and text through to client.send_message().

        Expected: client.send_message() is called with name="Alice" and
        text="Hello".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_send(ctx, name="Alice", text="Hello")
        client.send_message.assert_called_once_with(name="Alice", text="Hello")

    async def test_tg_send_parameter_is_named_name(
        self, mock_client_wrapper
    ) -> None:
        """
        REGRESSION TEST — parameter naming bug fix.

        The MCP tool parameter for the dialog identifier must be named 'name',
        NOT 'to'. The reference Go implementation used 'to' in the tool
        definition but 'name' in the struct, causing the parameter to be
        silently ignored.

        Expected: the tg_send function signature has a parameter called 'name'
        (verified via inspect.signature). It must NOT have a parameter 'to'.
        """
        sig = inspect.signature(tg_send)
        param_names = list(sig.parameters.keys())
        assert "name" in param_names, (
            f"tg_send must have a 'name' parameter, got: {param_names}"
        )
        assert "to" not in param_names, (
            f"tg_send must NOT have a 'to' parameter (reference bug), got: {param_names}"
        )

    async def test_tg_send_error_returns_error_dict(
        self, mock_client_wrapper
    ) -> None:
        """
        When client.send_message() raises ValueError (e.g. dialog not found),
        tg_send() returns {"error": "<message>"} instead of propagating the
        exception.

        Expected: return value is a dict with key "error"; no exception raised.
        """
        client = _make_mock_client()
        client.send_message = AsyncMock(
            side_effect=ValueError("Dialog not found: Alice")
        )
        ctx = _make_ctx(client)
        result = await tg_send(ctx, name="Alice", text="Hello")
        assert isinstance(result, dict)
        assert "error" in result
        assert "Alice" in result["error"]


class TestTgRead:
    """Tests for the tg_read MCP tool."""

    async def test_tg_read_returns_ok(self, mock_client_wrapper) -> None:
        """
        On success, tg_read() returns {"ok": True}.

        Expected: return value is exactly {"ok": True}.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_read(ctx, name="Alice")
        assert result == {"ok": True}

    async def test_tg_read_passes_name(self, mock_client_wrapper) -> None:
        """
        tg_read() passes the name argument through to client.mark_read().

        Expected: client.mark_read() is called with name="Alice".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_read(ctx, name="Alice")
        client.mark_read.assert_called_once_with(name="Alice")

    async def test_tg_read_parameter_is_named_name(self, mock_client_wrapper) -> None:
        """
        tg_read() function signature has a parameter named 'name'.
        """
        sig = inspect.signature(tg_read)
        param_names = list(sig.parameters.keys())
        assert "name" in param_names

    async def test_tg_read_error_returns_error_dict(
        self, mock_client_wrapper
    ) -> None:
        """
        When client.mark_read() raises ValueError (e.g. dialog not found),
        tg_read() returns {"error": "<message>"} instead of propagating the
        exception.

        Expected: return value is a dict with key "error"; no exception raised.
        """
        client = _make_mock_client()
        client.mark_read = AsyncMock(
            side_effect=ValueError("Dialog not found: Bob")
        )
        ctx = _make_ctx(client)
        result = await tg_read(ctx, name="Bob")
        assert isinstance(result, dict)
        assert "error" in result
        assert "Bob" in result["error"]

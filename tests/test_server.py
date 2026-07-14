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

from tg_mcp.client import TelegramClientWrapper
from tg_mcp.models import Dialog, Message, UserInfo
from tg_mcp.server import tg_me, tg_dialogs, tg_dialog, tg_send, tg_send_file, tg_forward, tg_read, tg_click, tg_react, tg_delete, tg_edit, tg_search, tg_download


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
    """Return a fresh AsyncMock TelegramClientWrapper with sensible defaults.

    spec_set against the real wrapper so a tool calling a method the wrapper does
    not have fails here instead of at runtime.
    """
    client = MagicMock(spec_set=TelegramClientWrapper)
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
            username="alicesmith",
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
    client.send_message = AsyncMock(return_value=100)
    client.send_file = AsyncMock(return_value=200)
    client.forward_message = AsyncMock(return_value=None)
    client.mark_read = AsyncMock(return_value=None)
    client.react = AsyncMock(return_value=None)
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


class TestErrorSpamGuard:
    """Regression tests: a failing tool must never dump a huge raw payload.

    Telethon raises TypeNotFoundError when Telegram sends a TL constructor
    unknown to the installed Telethon version. That exception's str() embeds
    the entire remaining MTProto buffer — hundreds of KB of raw bytes — which
    previously escaped the ``except ValueError`` guard, propagated to FastMCP,
    and was stringified straight into the model's context.
    """

    async def test_tg_dialogs_non_valueerror_is_caught(self, mock_client_wrapper) -> None:
        """A non-ValueError exception (e.g. TypeNotFoundError) is caught and
        returned as an error dict, not propagated."""
        client = _make_mock_client()
        client.get_dialogs = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = _make_ctx(client)
        result = await tg_dialogs(ctx)  # must not raise
        assert isinstance(result, list)
        assert len(result) == 1
        assert "error" in result[0]

    async def test_tg_dialogs_huge_error_is_truncated(self, mock_client_wrapper) -> None:
        """A multi-hundred-KB exception message is truncated to a short string
        and the raw buffer never reaches the caller."""
        buffer_marker = "RAWBYTES" + "x" * 500_000
        client = _make_mock_client()
        client.get_dialogs = AsyncMock(
            side_effect=Exception(f"Could not find Constructor. Remaining bytes: {buffer_marker}")
        )
        ctx = _make_ctx(client)
        result = await tg_dialogs(ctx)
        assert isinstance(result, list) and len(result) == 1
        err = result[0]["error"]
        assert len(err) < 1000, f"error must be short, got {len(err)} chars"
        assert "x" * 1000 not in err, "raw buffer bytes must not appear in the error"

    async def test_tg_send_huge_error_is_truncated(self, mock_client_wrapper) -> None:
        """The same guard applies to dict-returning tools like tg_send."""
        client = _make_mock_client()
        client.send_message = AsyncMock(side_effect=Exception("Remaining bytes: " + "y" * 400_000))
        ctx = _make_ctx(client)
        result = await tg_send(ctx, name="Alice", text="Hi")  # must not raise
        assert isinstance(result, dict) and "error" in result
        assert len(result["error"]) < 1000


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

    async def test_tg_dialogs_returns_username_for_user(
        self, mock_client_wrapper
    ) -> None:
        """
        When the dialog entity has a username (a user), tg_dialogs() returns a
        dict whose 'username' field is populated with the handle string.

        Expected: result[0]["username"] == "alicesmith"
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        # _make_mock_client already sets username="alicesmith" on the Dialog
        result = await tg_dialogs(ctx)
        assert "username" in result[0]
        assert result[0]["username"] == "alicesmith"

    async def test_tg_dialogs_returns_username_none_for_group(
        self, mock_client_wrapper
    ) -> None:
        """
        When the dialog entity has no username (a group without a public link),
        tg_dialogs() returns a dict whose 'username' field is None.

        Expected: result[0]["username"] is None
        """
        client = _make_mock_client()
        client.get_dialogs = AsyncMock(return_value=[
            Dialog(
                id=111,
                name="My Group",
                username=None,
                unread_count=0,
                is_user=False,
                is_group=True,
                is_channel=False,
                last_message=None,
                date=None,
            )
        ])
        ctx = _make_ctx(client)
        result = await tg_dialogs(ctx)
        assert "username" in result[0]
        assert result[0]["username"] is None


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
        client.get_messages.assert_called_once_with(
            dialog_id=None, username=None, name="Alice", limit=20, offset=0
        )

    async def test_tg_dialog_passes_limit_and_offset(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialog() passes limit and offset through to client.get_messages().
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, name="Alice", limit=5, offset=3)
        client.get_messages.assert_called_once_with(
            dialog_id=None, username=None, name="Alice", limit=5, offset=3
        )

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
        assert result == {"ok": True, "message_id": 100}

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
        client.send_message.assert_called_once_with(
            text="Hello", dialog_id=None, username=None, name="Alice", reply_to=None, parse_mode="markdown"
        )

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
            side_effect=ValueError("Dialog not found: name=Alice")
        )
        ctx = _make_ctx(client)
        result = await tg_send(ctx, name="Alice", text="Hello")
        assert isinstance(result, dict)
        assert "error" in result
        assert "Alice" in result["error"]

    async def test_tg_send_passes_reply_to_and_parse_mode(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_send(ctx, text="Hi", dialog_id=5, reply_to=42, parse_mode="html")
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=5, username=None, name=None, reply_to=42, parse_mode="html"
        )


class TestTgSendFile:
    """Tests for the tg_send_file MCP tool."""

    async def test_tg_send_file_returns_ok(self, mock_client_wrapper) -> None:
        """On success, tg_send_file() returns {"ok": True}."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send_file(ctx, name="Alice", file_path="/tmp/test.txt")
        assert result == {"ok": True, "message_id": 200}

    async def test_tg_send_file_passes_all_params(self, mock_client_wrapper) -> None:
        """tg_send_file() passes file_path, caption, and dialog selector to client."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_send_file(ctx, name="Alice", file_path="/tmp/f.txt", caption="hi")
        client.send_file.assert_called_once_with(
            file_path="/tmp/f.txt", dialog_id=None, username=None, name="Alice", caption="hi"
        )

    async def test_tg_send_file_no_selector_raises(self, mock_client_wrapper) -> None:
        """tg_send_file() raises ValueError when no dialog selector is given."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_send_file(ctx, file_path="/tmp/f.txt")

    async def test_tg_send_file_error_returns_error_dict(self, mock_client_wrapper) -> None:
        """When send_file() raises ValueError, tg_send_file returns {"error": ...}."""
        client = _make_mock_client()
        client.send_file = AsyncMock(side_effect=ValueError("Dialog not found"))
        ctx = _make_ctx(client)
        result = await tg_send_file(ctx, name="Nobody", file_path="/tmp/f.txt")
        assert "error" in result

    async def test_tg_send_file_with_dialog_id(self, mock_client_wrapper) -> None:
        """tg_send_file() works with dialog_id."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send_file(ctx, dialog_id=123, file_path="/tmp/f.txt")
        assert result == {"ok": True, "message_id": 200}
        client.send_file.assert_called_once_with(
            file_path="/tmp/f.txt", dialog_id=123, username=None, name=None, caption=None
        )


class TestTgForward:
    """Tests for the tg_forward MCP tool."""

    async def test_tg_forward_returns_ok(self, mock_client_wrapper) -> None:
        """On success, tg_forward() returns {"ok": True}."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_forward(
            ctx, message_id=42, from_name="Alice", to_name="Bob"
        )
        assert result == {"ok": True}

    async def test_tg_forward_passes_all_params(self, mock_client_wrapper) -> None:
        """tg_forward() passes all params to client.forward_message()."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_forward(
            ctx, message_id=42,
            from_dialog_id=1, to_dialog_id=2
        )
        client.forward_message.assert_called_once_with(
            from_dialog_id=1, from_username=None, from_name=None,
            to_dialog_id=2, to_username=None, to_name=None,
            message_id=42,
        )

    async def test_tg_forward_no_source_raises(self, mock_client_wrapper) -> None:
        """tg_forward() raises ValueError when no source selector is given."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="from_dialog_id, from_username, or from_name"):
            await tg_forward(ctx, message_id=42, to_name="Bob")

    async def test_tg_forward_no_dest_raises(self, mock_client_wrapper) -> None:
        """tg_forward() raises ValueError when no destination selector is given."""
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="to_dialog_id, to_username, or to_name"):
            await tg_forward(ctx, message_id=42, from_name="Alice")

    async def test_tg_forward_error_returns_error_dict(self, mock_client_wrapper) -> None:
        """When forward_message() raises ValueError, tg_forward returns {"error": ...}."""
        client = _make_mock_client()
        client.forward_message = AsyncMock(side_effect=ValueError("Dialog not found"))
        ctx = _make_ctx(client)
        result = await tg_forward(
            ctx, message_id=42, from_name="Alice", to_name="Nobody"
        )
        assert "error" in result


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
        client.mark_read.assert_called_once_with(dialog_id=None, username=None, name="Alice")

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


# ---------------------------------------------------------------------------
# Disambiguation tests — dialog_id / username / name selectors
# These tests cover the feature added in Task 2.
# ---------------------------------------------------------------------------


class TestTgSendDisambiguation:
    """Disambiguation tests for tg_send: dialog_id, username, name, priority."""

    async def test_tg_send_with_dialog_id_only(self, mock_client_wrapper) -> None:
        """
        tg_send() called with only dialog_id passes dialog_id to send_message().

        Expected: client.send_message() called with dialog_id=123, username=None, name=None.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send(ctx, text="Hi", dialog_id=123)
        assert result == {"ok": True, "message_id": 100}
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=123, username=None, name=None, reply_to=None, parse_mode="markdown"
        )

    async def test_tg_send_with_username_only_no_at(self, mock_client_wrapper) -> None:
        """
        tg_send() called with username (no leading @) passes it through.

        Expected: client.send_message() called with username="alicesmith", name=None.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send(ctx, text="Hi", username="alicesmith")
        assert result == {"ok": True, "message_id": 100}
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=None, username="alicesmith", name=None, reply_to=None, parse_mode="markdown"
        )

    async def test_tg_send_with_username_only_with_at(self, mock_client_wrapper) -> None:
        """
        tg_send() called with username that has a leading @ passes it through
        as-is to the client (stripping happens inside _resolve_entity).

        Expected: client.send_message() called with username="@alicesmith".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send(ctx, text="Hi", username="@alicesmith")
        assert result == {"ok": True, "message_id": 100}
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=None, username="@alicesmith", name=None, reply_to=None, parse_mode="markdown"
        )

    async def test_tg_send_with_name_only(self, mock_client_wrapper) -> None:
        """
        REGRESSION: tg_send() still works with name only (backwards compatibility).

        Expected: client.send_message() called with name="Alice".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_send(ctx, text="Hi", name="Alice")
        assert result == {"ok": True, "message_id": 100}
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=None, username=None, name="Alice", reply_to=None, parse_mode="markdown"
        )

    async def test_tg_send_no_selector_raises_value_error(self, mock_client_wrapper) -> None:
        """
        tg_send() called with no dialog selector raises ValueError immediately
        (before calling client.send_message).

        Expected: ValueError raised; client.send_message never called.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_send(ctx, text="Hi")
        client.send_message.assert_not_called()

    async def test_tg_send_dialog_id_takes_priority_over_username(
        self, mock_client_wrapper
    ) -> None:
        """
        When both dialog_id and username are supplied, dialog_id takes priority.
        Both are forwarded to client.send_message() — priority resolution happens
        inside _resolve_entity in the client layer.

        Expected: client.send_message() called with dialog_id=99 AND username="alice".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_send(ctx, text="Hi", dialog_id=99, username="alice")
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=99, username="alice", name=None, reply_to=None, parse_mode="markdown"
        )

    async def test_tg_send_username_takes_priority_over_name(
        self, mock_client_wrapper
    ) -> None:
        """
        When both username and name are supplied, username takes priority.
        Both are forwarded — priority resolution is in _resolve_entity.

        Expected: client.send_message() called with username="alice" AND name="Alice".
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_send(ctx, text="Hi", username="alice", name="Alice")
        client.send_message.assert_called_once_with(
            text="Hi", dialog_id=None, username="alice", name="Alice", reply_to=None, parse_mode="markdown"
        )


class TestTgDialogDisambiguation:
    """Disambiguation tests for tg_dialog: dialog_id, username, name, priority."""

    async def test_tg_dialog_with_dialog_id_only(self, mock_client_wrapper) -> None:
        """
        tg_dialog() works when only dialog_id is given.

        Expected: client.get_messages() called with dialog_id=456, others None.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_dialog(ctx, dialog_id=456)
        assert isinstance(result, list)
        client.get_messages.assert_called_once_with(
            dialog_id=456, username=None, name=None, limit=20, offset=0
        )

    async def test_tg_dialog_with_username_no_at(self, mock_client_wrapper) -> None:
        """
        tg_dialog() works when username (without @) is given.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, username="bobsmith")
        client.get_messages.assert_called_once_with(
            dialog_id=None, username="bobsmith", name=None, limit=20, offset=0
        )

    async def test_tg_dialog_with_username_with_at(self, mock_client_wrapper) -> None:
        """
        tg_dialog() works when username has a leading @.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, username="@bobsmith")
        client.get_messages.assert_called_once_with(
            dialog_id=None, username="@bobsmith", name=None, limit=20, offset=0
        )

    async def test_tg_dialog_with_name_only_regression(self, mock_client_wrapper) -> None:
        """
        REGRESSION: tg_dialog() still works with name only.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, name="Alice")
        client.get_messages.assert_called_once_with(
            dialog_id=None, username=None, name="Alice", limit=20, offset=0
        )

    async def test_tg_dialog_no_selector_raises_value_error(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_dialog() raises ValueError when called with no selector.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_dialog(ctx)
        client.get_messages.assert_not_called()

    async def test_tg_dialog_dialog_id_priority_over_username(
        self, mock_client_wrapper
    ) -> None:
        """
        When dialog_id and username are both given, both are forwarded to client
        (priority is applied in _resolve_entity).
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, dialog_id=7, username="carol")
        client.get_messages.assert_called_once_with(
            dialog_id=7, username="carol", name=None, limit=20, offset=0
        )

    async def test_tg_dialog_username_priority_over_name(
        self, mock_client_wrapper
    ) -> None:
        """
        When username and name are both given, both are forwarded to client.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_dialog(ctx, username="carol", name="Carol")
        client.get_messages.assert_called_once_with(
            dialog_id=None, username="carol", name="Carol", limit=20, offset=0
        )


class TestTgReadDisambiguation:
    """Disambiguation tests for tg_read: dialog_id, username, name, priority."""

    async def test_tg_read_with_dialog_id_only(self, mock_client_wrapper) -> None:
        """
        tg_read() works when only dialog_id is given.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_read(ctx, dialog_id=789)
        assert result == {"ok": True}
        client.mark_read.assert_called_once_with(
            dialog_id=789, username=None, name=None
        )

    async def test_tg_read_with_username_no_at(self, mock_client_wrapper) -> None:
        """
        tg_read() works when username (without @) is given.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_read(ctx, username="dave")
        assert result == {"ok": True}
        client.mark_read.assert_called_once_with(
            dialog_id=None, username="dave", name=None
        )

    async def test_tg_read_with_username_with_at(self, mock_client_wrapper) -> None:
        """
        tg_read() works when username has a leading @.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_read(ctx, username="@dave")
        assert result == {"ok": True}
        client.mark_read.assert_called_once_with(
            dialog_id=None, username="@dave", name=None
        )

    async def test_tg_read_with_name_only_regression(self, mock_client_wrapper) -> None:
        """
        REGRESSION: tg_read() still works with name only.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        result = await tg_read(ctx, name="Alice")
        assert result == {"ok": True}
        client.mark_read.assert_called_once_with(
            dialog_id=None, username=None, name="Alice"
        )

    async def test_tg_read_no_selector_raises_value_error(
        self, mock_client_wrapper
    ) -> None:
        """
        tg_read() raises ValueError when called with no selector.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_read(ctx)
        client.mark_read.assert_not_called()

    async def test_tg_read_dialog_id_priority_over_username(
        self, mock_client_wrapper
    ) -> None:
        """
        When dialog_id and username are both given, both are forwarded to client.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_read(ctx, dialog_id=5, username="eve")
        client.mark_read.assert_called_once_with(
            dialog_id=5, username="eve", name=None
        )

    async def test_tg_read_username_priority_over_name(
        self, mock_client_wrapper
    ) -> None:
        """
        When username and name are both given, both are forwarded to client.
        """
        client = _make_mock_client()
        ctx = _make_ctx(client)
        await tg_read(ctx, username="eve", name="Eve")
        client.mark_read.assert_called_once_with(
            dialog_id=None, username="eve", name="Eve"
        )


class TestTgClick:
    async def test_tg_click_returns_answer_and_updated_message(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        updated = Message(id=7, date="2026-07-13T00:00:00+00:00", text="Menu 2",
                          out=False, sender="Bot", buttons=None, media=None)
        client.click_button = AsyncMock(return_value={"answer": "Done!", "alert": False, "message": updated})
        ctx = _make_ctx(client)
        result = await tg_click(ctx, dialog_id=1, message_id=7, text="Next")
        assert result["answer"] == "Done!"
        assert result["alert"] is False
        assert result["message"]["text"] == "Menu 2"
        client.click_button.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, text="Next", row=None, col=None
        )

    async def test_tg_click_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_click(ctx, message_id=7, text="Next")

    async def test_tg_click_error_returns_error_dict(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.click_button = AsyncMock(side_effect=ValueError("Message 7 has no inline buttons"))
        ctx = _make_ctx(client)
        result = await tg_click(ctx, dialog_id=1, message_id=7, text="Next")
        assert "error" in result and "no inline buttons" in result["error"]


class TestTgReact:
    async def test_tg_react_passes_args(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.react = AsyncMock(return_value=None)
        ctx = _make_ctx(client)
        result = await tg_react(ctx, dialog_id=1, message_id=7, emoji="👍")
        assert result == {"ok": True}
        client.react.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, emoji="👍", big=False, remove=False
        )

    async def test_tg_react_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_react(ctx, message_id=7, emoji="👍")


class TestTgDelete:
    async def test_tg_delete_passes_ids_and_returns_count(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.delete = AsyncMock(return_value=2)
        ctx = _make_ctx(client)
        result = await tg_delete(ctx, dialog_id=1, message_ids=[7, 8])
        assert result == {"ok": True, "deleted": 2}
        client.delete.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_ids=[7, 8], revoke=True
        )

    async def test_tg_delete_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_delete(ctx, message_ids=[7])


class TestTgEdit:
    async def test_tg_edit_passes_args(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.edit_message = AsyncMock(return_value=None)
        ctx = _make_ctx(client)
        result = await tg_edit(ctx, dialog_id=1, message_id=7, text="fixed", parse_mode="none")
        assert result == {"ok": True}
        client.edit_message.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, text="fixed", parse_mode="none"
        )

    async def test_tg_edit_no_selector_raises(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        ctx = _make_ctx(client)
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await tg_edit(ctx, message_id=7, text="fixed")


class TestTgSearch:
    async def test_tg_search_returns_list_of_dicts(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        hit = Message(id=9, date="2026-07-13T00:00:00+00:00", text="found it",
                      out=False, sender="A", chat_id=555, chat_name="Chat")
        client.search = AsyncMock(return_value=[hit])
        ctx = _make_ctx(client)
        result = await tg_search(ctx, query="found", dialog_id=1, limit=10)
        assert isinstance(result, list)
        assert result[0]["text"] == "found it"
        assert result[0]["chat_id"] == 555
        client.search.assert_called_once_with(
            query="found", dialog_id=1, username=None, name=None, limit=10
        )

    async def test_tg_search_error_returns_error_list(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.search = AsyncMock(side_effect=ValueError("Dialog not found: name=X"))
        ctx = _make_ctx(client)
        result = await tg_search(ctx, query="x", name="X")
        assert isinstance(result, list) and "error" in result[0]


class TestTgDownload:
    async def test_tg_download_returns_path(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.download_media = AsyncMock(return_value={"path": "/tmp/x/photo.jpg", "media": "photo", "size": 1234})
        ctx = _make_ctx(client)
        result = await tg_download(ctx, dialog_id=1, message_id=7)
        assert result["path"] == "/tmp/x/photo.jpg"
        assert result["size"] == 1234
        client.download_media.assert_called_once_with(
            dialog_id=1, username=None, name=None, message_id=7, dest_dir=None
        )

    async def test_tg_download_no_media_error(self, mock_client_wrapper) -> None:
        client = _make_mock_client()
        client.download_media = AsyncMock(side_effect=ValueError("Message 7 has no downloadable media"))
        ctx = _make_ctx(client)
        result = await tg_download(ctx, dialog_id=1, message_id=7)
        assert "error" in result and "no downloadable media" in result["error"]

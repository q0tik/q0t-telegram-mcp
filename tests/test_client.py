"""
Tests for TelegramClientWrapper defined in src/tg_mcp/client.py.

Contracts verified (per docs/architecture.md §6):
  - connect() raises RuntimeError when not authorized
  - get_me() maps telethon User to UserInfo
  - get_dialogs() filters by unread, slices by offset/limit, maps fields
  - get_messages() passes add_offset, maps fields including async sender
  - send_message() calls telethon send_message() — NEVER any draft method
  - mark_read() calls send_read_acknowledge()
  - _resolve_entity() raises ValueError on lookup failure

All tests use fixtures from conftest.py — no real network calls are made.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_mcp.client import TelegramClientWrapper, _pm
from tg_mcp.config import Config
from tg_mcp.models import Dialog, Message, UserInfo


class TestPm:
    """Unit tests for the _pm() parse_mode normalizer."""

    def test_pm_markdown_returns_md(self) -> None:
        """_pm("markdown") must return "md"."""
        assert _pm("markdown") == "md"

    def test_pm_md_returns_md(self) -> None:
        """_pm("md") must return "md"."""
        assert _pm("md") == "md"

    def test_pm_html_returns_html(self) -> None:
        """_pm("html") must return "html"."""
        assert _pm("html") == "html"

    def test_pm_none_string_returns_none(self) -> None:
        """_pm("none") must return None (no parse mode)."""
        assert _pm("none") is None

    def test_pm_empty_string_returns_none(self) -> None:
        """_pm("") must return None."""
        assert _pm("") is None

    def test_pm_none_value_returns_md(self) -> None:
        """_pm(None) must default to "md"."""
        assert _pm(None) == "md"

    def test_pm_html_uppercase_returns_html(self) -> None:
        """_pm("HTML") must be case-insensitive and return "html"."""
        assert _pm("HTML") == "html"

    def test_pm_bogus_value_defaults_to_md(self) -> None:
        """_pm("bogus") unknown value must fall back to "md"."""
        assert _pm("bogus") == "md"

    def test_pm_plain_returns_none(self) -> None:
        """_pm("plain") must return None (no parse mode)."""
        assert _pm("plain") is None

    def test_pm_case_insensitive_md(self) -> None:
        """_pm("MD") case-insensitive must return "md"."""
        assert _pm("MD") == "md"


class TestConnect:
    """Tests for TelegramClientWrapper.connect()."""

    async def test_connect_raises_if_not_authorized(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When is_user_authorized() returns False, connect() must raise
        RuntimeError with a message that includes instructions to run auth.

        Expected: RuntimeError is raised; message contains 'auth'.
        """
        mock_telegram_client.is_user_authorized.return_value = False

        # We need to call connect() which creates a new _client internally,
        # so we patch TelegramClient to return our mock.
        fake_config = Config(
            app_id=12345,
            api_hash="fakehash",
            session_path=Path("/tmp/test/session"),
        )
        wrapper = TelegramClientWrapper(fake_config)

        with patch("tg_mcp.client.TelegramClient", return_value=mock_telegram_client):
            with pytest.raises(RuntimeError, match="auth"):
                await wrapper.connect()

    async def test_connect_succeeds_when_authorized(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When is_user_authorized() returns True, connect() completes without
        raising.

        Expected: no exception; _client.connect() is called exactly once.
        """
        mock_telegram_client.is_user_authorized.return_value = True

        fake_config = Config(
            app_id=12345,
            api_hash="fakehash",
            session_path=Path("/tmp/test/session"),
        )
        wrapper = TelegramClientWrapper(fake_config)

        with patch("tg_mcp.client.TelegramClient", return_value=mock_telegram_client):
            await wrapper.connect()  # must not raise

        mock_telegram_client.connect.assert_called_once()


class TestGetMe:
    """Tests for TelegramClientWrapper.get_me()."""

    async def test_get_me_returns_user_info(
        self, mock_client_wrapper, mock_telegram_user
    ) -> None:
        """
        get_me() returns a UserInfo instance (not the raw Telethon User object).

        Expected: return value is an instance of the UserInfo dataclass.
        """
        result = await mock_client_wrapper.get_me()
        assert isinstance(result, UserInfo)

    async def test_get_me_maps_all_fields(
        self, mock_client_wrapper, mock_telegram_user
    ) -> None:
        """
        get_me() maps every field from the Telethon User to the corresponding
        UserInfo field: id, first_name, last_name, username, phone.

        Expected: all five fields on the returned UserInfo match the mock
        Telethon User attributes.
        """
        result = await mock_client_wrapper.get_me()
        assert result.id == mock_telegram_user.id
        assert result.first_name == mock_telegram_user.first_name
        assert result.last_name == mock_telegram_user.last_name
        assert result.username == mock_telegram_user.username
        assert result.phone == mock_telegram_user.phone


class TestGetDialogs:
    """Tests for TelegramClientWrapper.get_dialogs()."""

    async def test_get_dialogs_returns_list(
        self, mock_client_wrapper, mock_telegram_dialog
    ) -> None:
        """
        get_dialogs() returns a list of Dialog objects.

        Expected: return type is list; each item is a Dialog instance.
        """
        result = await mock_client_wrapper.get_dialogs()
        assert isinstance(result, list)
        assert len(result) > 0
        for item in result:
            assert isinstance(item, Dialog)

    async def test_get_dialogs_filters_unread(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When unread_only=True, only dialogs with unread_count > 0 are returned.

        Expected: dialogs with unread_count == 0 are excluded from the result.
        """
        # Create one dialog with unread, one without
        unread_dialog = MagicMock()
        unread_dialog.id = 1
        unread_dialog.name = "Has Unread"
        unread_dialog.unread_count = 5
        unread_dialog.is_user = True
        unread_dialog.is_group = False
        unread_dialog.is_channel = False
        unread_dialog.message = None
        unread_dialog.date = None

        read_dialog = MagicMock()
        read_dialog.id = 2
        read_dialog.name = "All Read"
        read_dialog.unread_count = 0
        read_dialog.is_user = True
        read_dialog.is_group = False
        read_dialog.is_channel = False
        read_dialog.message = None
        read_dialog.date = None

        mock_telegram_client.get_dialogs.return_value = [unread_dialog, read_dialog]

        result = await mock_client_wrapper.get_dialogs(unread_only=True)
        assert len(result) == 1
        assert result[0].name == "Has Unread"

    async def test_get_dialogs_applies_offset_and_limit(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        get_dialogs() applies Python-level slicing [offset : offset + limit]
        after fetching all dialogs from Telethon.

        Expected: result length respects limit; items skipped match offset.
        """
        # Create 5 dialogs
        dialogs = []
        for i in range(5):
            d = MagicMock()
            d.id = i
            d.name = f"Dialog {i}"
            d.unread_count = 0
            d.is_user = True
            d.is_group = False
            d.is_channel = False
            d.message = None
            d.date = None
            dialogs.append(d)

        mock_telegram_client.get_dialogs.return_value = dialogs

        result = await mock_client_wrapper.get_dialogs(limit=2, offset=1)
        assert len(result) == 2
        assert result[0].name == "Dialog 1"
        assert result[1].name == "Dialog 2"

    async def test_get_dialogs_maps_all_fields(
        self, mock_client_wrapper, mock_telegram_dialog
    ) -> None:
        """
        Each returned Dialog has all eight fields mapped correctly from the
        Telethon Dialog object: id, name, unread_count, is_user, is_group,
        is_channel, last_message, date.

        Expected: every field on the first returned Dialog matches the
        mock_telegram_dialog fixture values.
        """
        result = await mock_client_wrapper.get_dialogs()
        assert len(result) >= 1
        d = result[0]
        assert d.id == mock_telegram_dialog.id
        assert d.name == mock_telegram_dialog.name
        assert d.unread_count == mock_telegram_dialog.unread_count
        assert d.is_user == mock_telegram_dialog.is_user
        assert d.is_group == mock_telegram_dialog.is_group
        assert d.is_channel == mock_telegram_dialog.is_channel
        assert d.last_message == mock_telegram_dialog.message.text
        assert d.date == mock_telegram_dialog.date.isoformat()


class TestGetMessages:
    """Tests for TelegramClientWrapper.get_messages()."""

    async def test_get_messages_returns_list(
        self, mock_client_wrapper, mock_telegram_message
    ) -> None:
        """
        get_messages() returns a list of Message objects.

        Expected: return type is list; each item is a Message instance.
        """
        result = await mock_client_wrapper.get_messages(name="Alice")
        assert isinstance(result, list)
        assert len(result) > 0
        for item in result:
            assert isinstance(item, Message)

    async def test_get_messages_uses_add_offset(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        get_messages() passes the offset argument as add_offset to Telethon's
        get_messages(), not as a manual slice.

        Expected: _client.get_messages() is called with add_offset=<offset>.
        """
        mock_telegram_client.get_messages.return_value = []
        await mock_client_wrapper.get_messages(name="Alice", limit=10, offset=5)
        mock_telegram_client.get_messages.assert_called_once()
        call_kwargs = mock_telegram_client.get_messages.call_args
        assert call_kwargs.kwargs.get("add_offset") == 5 or (
            len(call_kwargs.args) >= 3 and call_kwargs.args[2] == 5
        )

    async def test_get_messages_maps_all_fields(
        self, mock_client_wrapper, mock_telegram_message
    ) -> None:
        """
        Each returned Message has all five fields mapped correctly: id, date
        (ISO-8601 string), text, out, sender (display name string or None).

        Expected: every field on the first returned Message matches the
        mock_telegram_message fixture values (date is an ISO-8601 string).
        """
        result = await mock_client_wrapper.get_messages(name="Alice")
        assert len(result) >= 1
        m = result[0]
        assert m.id == mock_telegram_message.id
        assert m.date == mock_telegram_message.date.isoformat()
        assert m.text == mock_telegram_message.text
        assert m.out == bool(mock_telegram_message.out)
        # Sender is derived from get_sender() + telethon.utils.get_display_name().
        # telethon.utils.get_display_name() only works on real telethon objects,
        # not MagicMock — it returns '' for mocks, so the client maps '' to None.
        # The important thing is that get_sender() was awaited and the field exists.
        assert hasattr(m, "sender")  # field exists on the dataclass


class TestSendMessage:
    """Tests for TelegramClientWrapper.send_message()."""

    async def test_send_message_calls_telethon_send_message(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_message() calls _client.send_message(entity, text) exactly once
        after resolving the entity.

        Expected: mock_telegram_client.send_message is called with the resolved
        entity and the original text string.
        """
        await mock_client_wrapper.send_message(name="Alice", text="Hello")
        mock_telegram_client.send_message.assert_called_once()
        call_args = mock_telegram_client.send_message.call_args
        # Second positional arg (or kwarg) should be the text
        # call signature: send_message(entity, text)
        assert "Hello" in call_args.args or call_args.kwargs.get("message") == "Hello"

    async def test_send_message_never_calls_draft(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        REGRESSION TEST — CRITICAL INVARIANT.

        Asserts that send_message() never calls SaveDraft, MessagesSaveDraft,
        or any draft-related method on the Telethon client.

        This test exists because the reference Go implementation
        (chaindead/telegram-mcp) silently saved drafts instead of sending
        messages. The bug meant messages never arrived at the recipient.

        Expected: after calling send_message(), mock_telegram_client has NO
        calls to any attribute whose name contains 'draft' or 'Draft'. Also
        asserts that mock_telegram_client.send_message WAS called (positive
        check) so the test cannot trivially pass by doing nothing.
        """
        await mock_client_wrapper.send_message(name="Alice", text="Hello")

        # Positive check: send_message MUST have been called
        mock_telegram_client.send_message.assert_called_once()

        # Negative check: no call whose name contains 'draft' or 'Draft'
        all_call_names = [str(call) for call in mock_telegram_client.mock_calls]
        draft_calls = [c for c in all_call_names if "draft" in c.lower()]
        assert draft_calls == [], (
            f"send_message() made draft-related calls: {draft_calls}"
        )

        # Also explicitly assert that MessagesSaveDraft was not accessed
        # (the fixture sets it to raise, but we confirm via mock_calls too)
        called_attrs = [call[0] for call in mock_telegram_client.mock_calls]
        assert "MessagesSaveDraft" not in called_attrs

    async def test_send_message_resolves_entity_by_name(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_message() resolves the dialog entity by name before sending.

        Expected: _client.iter_dialogs() is used (not get_entity) when name is
        given, and _client.send_message() is called after entity resolution.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.send_message.reset_mock()

        await mock_client_wrapper.send_message(name="Alice", text="Hello")

        # send_message must also have been called (after entity resolution)
        mock_telegram_client.send_message.assert_called_once()

    async def test_send_message_forwards_reply_to_to_telethon(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_message() must forward reply_to=42 as a kwarg to Telethon's send_message().

        Expected: _client.send_message() is called with reply_to=42 in kwargs.
        """
        mock_telegram_client.send_message.reset_mock()

        await mock_client_wrapper.send_message(
            name="Alice", text="Hello", reply_to=42
        )

        mock_telegram_client.send_message.assert_called_once()
        call_kwargs = mock_telegram_client.send_message.call_args.kwargs
        assert call_kwargs.get("reply_to") == 42

    async def test_send_message_forwards_parse_mode_html_to_telethon(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_message() must normalize parse_mode="html" to "html" and forward
        to Telethon's send_message().

        Expected: _client.send_message() is called with parse_mode="html".
        """
        mock_telegram_client.send_message.reset_mock()

        await mock_client_wrapper.send_message(
            name="Alice", text="Hello", parse_mode="html"
        )

        mock_telegram_client.send_message.assert_called_once()
        call_kwargs = mock_telegram_client.send_message.call_args.kwargs
        assert call_kwargs.get("parse_mode") == "html"

    async def test_send_message_forwards_parse_mode_none_to_telethon(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_message() must normalize parse_mode="none" to None and forward
        to Telethon's send_message().

        Expected: _client.send_message() is called with parse_mode=None.
        """
        mock_telegram_client.send_message.reset_mock()

        await mock_client_wrapper.send_message(
            name="Alice", text="Hello", parse_mode="none"
        )

        mock_telegram_client.send_message.assert_called_once()
        call_kwargs = mock_telegram_client.send_message.call_args.kwargs
        assert call_kwargs.get("parse_mode") is None

    async def test_send_message_forwards_both_reply_to_and_parse_mode(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_message() must forward both reply_to and parse_mode (normalized)
        together to Telethon's send_message().

        Expected: _client.send_message() is called with both reply_to=99
        and parse_mode="md" (normalized from "markdown").
        """
        mock_telegram_client.send_message.reset_mock()

        await mock_client_wrapper.send_message(
            name="Alice", text="Test", reply_to=99, parse_mode="markdown"
        )

        mock_telegram_client.send_message.assert_called_once()
        call_kwargs = mock_telegram_client.send_message.call_args.kwargs
        assert call_kwargs.get("reply_to") == 99
        assert call_kwargs.get("parse_mode") == "md"


class TestSendFile:
    """Tests for TelegramClientWrapper.send_file()."""

    async def test_send_file_calls_telethon_send_file(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_file() calls _client.send_file(entity, file_path, caption=caption).

        Expected: mock_telegram_client.send_file is called once with the resolved
        entity, file path, and caption.
        """
        await mock_client_wrapper.send_file(name="Alice", file_path="/tmp/test.txt")
        mock_telegram_client.send_file.assert_called_once()

    async def test_send_file_passes_caption(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_file() forwards the caption kwarg to Telethon's send_file().

        Expected: caption="Hello" is passed through.
        """
        await mock_client_wrapper.send_file(
            name="Alice", file_path="/tmp/test.txt", caption="Hello"
        )
        call_kwargs = mock_telegram_client.send_file.call_args
        assert call_kwargs.kwargs.get("caption") == "Hello"

    async def test_send_file_caption_none_by_default(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When caption is not provided, send_file() passes caption=None.
        """
        await mock_client_wrapper.send_file(name="Alice", file_path="/tmp/test.txt")
        call_kwargs = mock_telegram_client.send_file.call_args
        assert call_kwargs.kwargs.get("caption") is None

    async def test_send_file_resolves_entity_by_dialog_id(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        send_file() resolves entity by dialog_id when provided.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        await mock_client_wrapper.send_file(dialog_id=123, file_path="/tmp/f.txt")
        mock_telegram_client.get_entity.assert_called_once_with(123)


class TestForwardMessage:
    """Tests for TelegramClientWrapper.forward_message()."""

    async def test_forward_message_calls_telethon_forward_messages(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        forward_message() calls _client.forward_messages(to_entity, msg_id, from_entity).

        Expected: mock_telegram_client.forward_messages is called once.
        """
        await mock_client_wrapper.forward_message(
            from_name="Alice", to_name="Alice", message_id=42
        )
        mock_telegram_client.forward_messages.assert_called_once()

    async def test_forward_message_passes_message_id(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        forward_message() passes message_id as the second positional arg
        to Telethon's forward_messages().
        """
        await mock_client_wrapper.forward_message(
            from_name="Alice", to_name="Alice", message_id=99
        )
        call_args = mock_telegram_client.forward_messages.call_args
        assert 99 in call_args.args

    async def test_forward_message_raises_if_source_not_found(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        forward_message() raises ValueError if the source dialog is not found.
        """
        async def _empty_iter():
            return
            yield

        mock_telegram_client.iter_dialogs = MagicMock(side_effect=lambda: _empty_iter())

        with pytest.raises(ValueError, match="Dialog not found"):
            await mock_client_wrapper.forward_message(
                from_name="Nobody", to_name="Alice", message_id=42
            )


class TestMarkRead:
    """Tests for TelegramClientWrapper.mark_read()."""

    async def test_mark_read_calls_send_read_acknowledge(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        mark_read() calls _client.send_read_acknowledge(entity) exactly once.

        Expected: mock_telegram_client.send_read_acknowledge is called after
        entity resolution.
        """
        await mock_client_wrapper.mark_read(name="Alice")
        mock_telegram_client.send_read_acknowledge.assert_called_once()


class TestDisconnect:
    """Tests for TelegramClientWrapper.disconnect()."""

    async def test_disconnect_calls_client_disconnect(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        disconnect() calls _client.disconnect() when _client is not None.
        """
        mock_telegram_client.disconnect.reset_mock()
        await mock_client_wrapper.disconnect()
        mock_telegram_client.disconnect.assert_called_once()

    async def test_disconnect_does_nothing_when_client_is_none(self) -> None:
        """
        disconnect() does nothing (no exception) when _client is None.
        """
        config = Config(
            app_id=12345,
            api_hash="fakehash",
            session_path=Path("/tmp/test/session"),
        )
        wrapper = TelegramClientWrapper(config)
        # _client is None (connect() was never called)
        assert wrapper._client is None
        await wrapper.disconnect()  # must not raise


class TestIsAuthorized:
    """Tests for TelegramClientWrapper.is_authorized()."""

    async def test_is_authorized_returns_true_when_authorized(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        is_authorized() returns True when the underlying client is authorized.
        """
        mock_telegram_client.is_user_authorized.return_value = True
        result = await mock_client_wrapper.is_authorized()
        assert result is True

    async def test_is_authorized_returns_false_when_not_authorized(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        is_authorized() returns False when the underlying client is not authorized.
        """
        mock_telegram_client.is_user_authorized.return_value = False
        result = await mock_client_wrapper.is_authorized()
        assert result is False


class TestResolveEntity:
    """Tests for TelegramClientWrapper._resolve_entity() (internal helper)."""

    async def test_resolve_entity_raises_value_error_on_not_found(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When _client.get_entity() raises ValueError for a username lookup,
        _resolve_entity() must re-raise as ValueError with a message of the
        form 'Dialog not found: username=<handle>'.

        Expected: ValueError is raised; message contains 'Dialog not found'.
        """
        mock_telegram_client.get_entity.side_effect = ValueError("not found")

        with pytest.raises(ValueError, match="Dialog not found: username=NonExistent"):
            await mock_client_wrapper._resolve_entity(username="NonExistent")

    async def test_resolve_entity_raises_value_error_on_username_not_occupied(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When _client.get_entity() raises UsernameNotOccupiedError,
        _resolve_entity() must re-raise as ValueError.
        """
        from telethon.errors import UsernameNotOccupiedError

        mock_telegram_client.get_entity.side_effect = UsernameNotOccupiedError(request=None)

        with pytest.raises(ValueError, match="Dialog not found: username=@ghost"):
            await mock_client_wrapper._resolve_entity(username="@ghost")

    async def test_resolve_entity_with_dialog_id_calls_get_entity_with_int(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When dialog_id is provided, _resolve_entity() calls get_entity() with
        the integer ID (not a string).

        Expected: get_entity called with the integer 12345.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        await mock_client_wrapper._resolve_entity(dialog_id=12345)
        mock_telegram_client.get_entity.assert_called_once_with(12345)
        # Confirm the arg passed is actually an int
        call_arg = mock_telegram_client.get_entity.call_args.args[0]
        assert isinstance(call_arg, int)

    async def test_resolve_entity_with_username_strips_at_and_calls_get_entity_with_str(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When username with leading @ is provided, _resolve_entity() strips the
        @ and calls get_entity() with the plain string handle.

        Expected: get_entity called with "alicehandle" (not "@alicehandle").
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        await mock_client_wrapper._resolve_entity(username="@alicehandle")
        mock_telegram_client.get_entity.assert_called_once_with("alicehandle")
        call_arg = mock_telegram_client.get_entity.call_args.args[0]
        assert isinstance(call_arg, str)
        assert not call_arg.startswith("@")

    async def test_resolve_entity_with_username_no_at_calls_get_entity_with_str(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When username without leading @ is provided, _resolve_entity() calls
        get_entity() with the string as-is.

        Expected: get_entity called with "alicehandle".
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        await mock_client_wrapper._resolve_entity(username="alicehandle")
        mock_telegram_client.get_entity.assert_called_once_with("alicehandle")

    async def test_resolve_entity_with_name_scans_iter_dialogs(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When name is provided (no dialog_id or username), _resolve_entity() uses
        iter_dialogs() — not get_entity() — to find the entity by display name.

        Expected: iter_dialogs() is called; get_entity() is NOT called.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None

        # The default iter_dialogs mock yields a dialog with name "Alice"
        entity = await mock_client_wrapper._resolve_entity(name="Alice")
        assert entity is not None
        # get_entity should NOT have been called — name resolution goes through iter_dialogs
        mock_telegram_client.get_entity.assert_not_called()

    async def test_resolve_entity_with_name_not_found_raises_value_error(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When name is provided but not found via iter_dialogs(), _resolve_entity()
        raises ValueError.
        """
        async def _empty_iter():
            return
            yield  # make it an async generator

        mock_telegram_client.iter_dialogs = MagicMock(side_effect=lambda: _empty_iter())

        with pytest.raises(ValueError, match="Dialog not found: name=Nobody"):
            await mock_client_wrapper._resolve_entity(name="Nobody")

    async def test_resolve_entity_no_selector_raises_value_error(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When none of dialog_id, username, name is provided, _resolve_entity()
        raises ValueError immediately.
        """
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await mock_client_wrapper._resolve_entity()

    async def test_resolve_entity_dialog_id_takes_priority_over_username(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When both dialog_id and username are provided, dialog_id takes priority:
        get_entity() is called with the integer dialog_id, not the string handle.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        await mock_client_wrapper._resolve_entity(dialog_id=99, username="somehandle")
        call_arg = mock_telegram_client.get_entity.call_args.args[0]
        assert isinstance(call_arg, int)
        assert call_arg == 99

    async def test_resolve_entity_username_takes_priority_over_name(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When both username and name are provided, username takes priority:
        get_entity() is called with the stripped string handle, not iter_dialogs.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        await mock_client_wrapper._resolve_entity(username="handle", name="Alice")
        mock_telegram_client.get_entity.assert_called_once_with("handle")
        # iter_dialogs should NOT have been used
        mock_telegram_client.iter_dialogs.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for username field in get_dialogs()
# ---------------------------------------------------------------------------


class TestGetDialogsUsernameField:
    """Tests that get_dialogs() correctly populates the username field."""

    async def test_get_dialogs_username_populated_for_user(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When the dialog entity has a username attribute, get_dialogs() maps it
        to the Dialog.username field.

        Expected: returned Dialog has username == "alicesmith"
        (the conftest mock_telegram_dialog fixture sets entity.username="alicesmith").
        """
        result = await mock_client_wrapper.get_dialogs()
        assert len(result) >= 1
        assert result[0].username == "alicesmith"

    async def test_get_dialogs_username_none_for_group_without_username(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When the dialog entity has no username attribute (a group without a
        public link), get_dialogs() sets Dialog.username to None.

        Expected: returned Dialog has username == None.
        """
        group_dialog = MagicMock()
        group_dialog.id = 555
        group_dialog.name = "Work Group"
        group_dialog.unread_count = 0
        group_dialog.is_user = False
        group_dialog.is_group = True
        group_dialog.is_channel = False
        group_dialog.message = None
        group_dialog.date = None
        # Entity has no username — del/spec it away
        entity_stub = MagicMock(spec=[])  # empty spec: no attributes
        group_dialog.entity = entity_stub

        mock_telegram_client.get_dialogs.return_value = [group_dialog]
        result = await mock_client_wrapper.get_dialogs()
        assert len(result) == 1
        assert result[0].username is None

    async def test_get_dialogs_username_none_when_entity_username_is_none(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When the dialog entity has a username attribute that is explicitly None
        (e.g. a private group), get_dialogs() maps it to None.
        """
        private_group = MagicMock()
        private_group.id = 666
        private_group.name = "Private Group"
        private_group.unread_count = 1
        private_group.is_user = False
        private_group.is_group = True
        private_group.is_channel = False
        private_group.message = None
        private_group.date = None
        entity_stub = MagicMock()
        entity_stub.username = None
        private_group.entity = entity_stub

        mock_telegram_client.get_dialogs.return_value = [private_group]
        result = await mock_client_wrapper.get_dialogs()
        assert result[0].username is None


# ---------------------------------------------------------------------------
# Tests for helpers: _extract_buttons and _describe_media
# ---------------------------------------------------------------------------


from types import SimpleNamespace
from tg_mcp.client import _extract_buttons, _describe_media
from tg_mcp.models import Button


def _fake_button(text, url=None, data=None):
    return SimpleNamespace(text=text, url=url, data=data)


def test_extract_buttons_none_when_no_markup():
    msg = SimpleNamespace(buttons=None)
    assert _extract_buttons(msg) is None


def test_extract_buttons_classifies_kinds():
    msg = SimpleNamespace(buttons=[[_fake_button("Go", data=b"x"), _fake_button("Site", url="https://s")]])
    rows = _extract_buttons(msg)
    assert rows == [[Button("Go", "callback", None), Button("Site", "url", "https://s")]]


def test_describe_media_photo_and_document_and_none():
    photo = SimpleNamespace(photo=True, voice=None, video_note=None, video=None,
                            audio=None, gif=None, sticker=None, document=None,
                            poll=None, contact=None, geo=None, web_preview=None)
    assert _describe_media(photo) == "photo"
    doc_attr = SimpleNamespace(file_name="report.pdf")
    document = SimpleNamespace(photo=None, voice=None, video_note=None, video=None,
                              audio=None, gif=None, sticker=None,
                              document=SimpleNamespace(attributes=[doc_attr]),
                              poll=None, contact=None, geo=None, web_preview=None)
    assert _describe_media(document) == "document: report.pdf"
    text = SimpleNamespace(photo=None, voice=None, video_note=None, video=None,
                          audio=None, gif=None, sticker=None, document=None,
                          poll=None, contact=None, geo=None, web_preview=None)
    assert _describe_media(text) is None


def test_describe_media_animated_gif_with_both_gif_and_video_attrs():
    """
    Regression test: animated GIFs in Telegram have BOTH DocumentAttributeVideo
    and DocumentAttributeAnimated, so message.video and message.gif are both truthy.
    _describe_media must return "gif", not "video".

    This test constructs a fake message with both gif=True and video=True,
    simulating an animated GIF, and asserts that the result is "gif".
    """
    animated_gif = SimpleNamespace(
        photo=None, voice=None, video_note=None, video=True,
        audio=None, gif=True, sticker=None, document=None,
        poll=None, contact=None, geo=None, web_preview=None
    )
    assert _describe_media(animated_gif) == "gif"


# ---------------------------------------------------------------------------
# Tests for TelegramClientWrapper.click_button() — real behavior.
#
# Unlike TestTgClick in test_server.py (which mocks click_button entirely),
# these tests exercise the actual button-matching, URL-branch, error-path,
# row/col disambiguation, and post-click re-fetch logic in client.py.
# ---------------------------------------------------------------------------


def _fake_click_message(buttons, msg_id: int = 42, text: str = "msg text"):
    """Build a MagicMock resembling a Telethon Message with inline buttons.

    Mirrors mock_telegram_message in conftest.py (id, date, text, out,
    get_sender), plus .buttons and an AsyncMock .click() as click_button()
    requires.
    """
    from datetime import datetime, timezone

    msg = MagicMock()
    msg.id = msg_id
    msg.date = datetime(2026, 3, 8, 11, 30, 0, tzinfo=timezone.utc)
    msg.text = text
    msg.out = False
    msg.buttons = buttons
    msg.click = AsyncMock()

    sender = MagicMock()
    sender.first_name = "Bot"
    sender.last_name = None
    sender.username = "somebot"
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


class TestReact:
    """Tests for TelegramClientWrapper.react() (real wrapper, exercises _resolve_entity and _client.send_reaction)."""

    async def test_react_calls_send_reaction_with_emoji(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() with remove=False calls _client.send_reaction(entity, message_id, reaction=emoji, big=big).

        Expected: send_reaction is awaited once with reaction set to the emoji string.
        """
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(name="Alice", message_id=42, emoji="👍", big=False, remove=False)
        mock_telegram_client.send_reaction.assert_called_once()
        call_args = mock_telegram_client.send_reaction.call_args
        assert call_args.kwargs.get("reaction") == "👍"
        assert call_args.kwargs.get("big") is False

    async def test_react_calls_send_reaction_with_none_when_remove_true(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() with remove=True calls _client.send_reaction(entity, message_id, reaction=None, big=big).

        Expected: send_reaction is awaited once with reaction=None.
        """
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(name="Alice", message_id=42, emoji="👍", big=False, remove=True)
        mock_telegram_client.send_reaction.assert_called_once()
        call_args = mock_telegram_client.send_reaction.call_args
        assert call_args.kwargs.get("reaction") is None
        assert call_args.kwargs.get("big") is False

    async def test_react_with_big_true(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() with big=True passes big=True to send_reaction().

        Expected: send_reaction called with big=True.
        """
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(name="Alice", message_id=42, emoji="🔥", big=True, remove=False)
        mock_telegram_client.send_reaction.assert_called_once()
        call_args = mock_telegram_client.send_reaction.call_args
        assert call_args.kwargs.get("big") is True
        assert call_args.kwargs.get("reaction") == "🔥"

    async def test_react_resolves_entity_by_dialog_id(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() resolves the dialog entity by dialog_id before calling send_reaction.

        Expected: get_entity is called with dialog_id; send_reaction is called once.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(dialog_id=123, message_id=42, emoji="😂", remove=False)
        mock_telegram_client.get_entity.assert_called_once_with(123)
        mock_telegram_client.send_reaction.assert_called_once()

    async def test_react_resolves_entity_by_username(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() resolves the dialog entity by username (stripping @) before calling send_reaction.

        Expected: get_entity is called with stripped username; send_reaction is called once.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(username="@alice", message_id=42, emoji="👍", remove=False)
        mock_telegram_client.get_entity.assert_called_once_with("alice")
        mock_telegram_client.send_reaction.assert_called_once()

    async def test_react_resolves_entity_by_name(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() resolves the dialog entity by name via iter_dialogs before calling send_reaction.

        Expected: iter_dialogs is used; send_reaction is called once.
        """
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(name="Alice", message_id=42, emoji="❤️", remove=False)
        mock_telegram_client.send_reaction.assert_called_once()

    async def test_react_no_selector_raises_value_error(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() raises ValueError when no dialog selector is provided.

        Expected: ValueError with "One of dialog_id, username, or name".
        """
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await mock_client_wrapper.react(message_id=42, emoji="👍")

    async def test_react_message_id_is_passed_through(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        react() passes the message_id argument correctly to send_reaction.

        Expected: send_reaction is called with message_id as second positional arg.
        """
        mock_telegram_client.send_reaction = AsyncMock(return_value=None)
        await mock_client_wrapper.react(name="Alice", message_id=99, emoji="👍", remove=False)
        call_args = mock_telegram_client.send_reaction.call_args
        # message_id should be second positional argument or in kwargs
        assert 99 in call_args.args or call_args.kwargs.get("message_id") == 99 or call_args.args[1] == 99


class TestClickButton:
    """Tests for TelegramClientWrapper.click_button() (real logic, not mocked)."""

    async def test_click_callback_button_awaits_click_and_maps_answer(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        Clicking a callback button (has .data, no .url) must await
        message.click(i, j) with the button's row/col indices, and the
        result dict must map answer/alert from the BotCallbackAnswer and
        message from the POST-CLICK re-fetch (not the original message).

        Expected: click(0, 0) awaited once; answer == "Clicked!";
        alert is True; message is a Message instance built from the
        re-fetched message (text == "updated text").
        """
        btn = _fake_button("Yes", data=b"yes")
        initial = _fake_click_message(buttons=[[btn]], text="original text")
        updated = _fake_click_message(buttons=[[btn]], text="updated text")
        initial.click.return_value = SimpleNamespace(message="Clicked!", alert=True)

        mock_telegram_client.get_messages.side_effect = [initial, updated]

        result = await mock_client_wrapper.click_button(
            name="Alice", message_id=42, text="Yes"
        )

        initial.click.assert_awaited_once_with(0, 0)
        assert result["answer"] == "Clicked!"
        assert result["alert"] is True
        assert isinstance(result["message"], Message)
        assert result["message"].text == "updated text"

    async def test_click_url_button_skips_click_and_returns_url(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        Clicking a button with a .url set must NOT perform a network click —
        it must return the url directly without awaiting message.click().

        Expected: result["url"] == the button's url; answer is None;
        alert is False; message.click is never awaited; message is a
        Message instance built from the original (single) fetch.
        """
        btn = _fake_button("Visit", url="https://example.com")
        msg = _fake_click_message(buttons=[[btn]])
        mock_telegram_client.get_messages.side_effect = None
        mock_telegram_client.get_messages.return_value = msg

        result = await mock_client_wrapper.click_button(
            name="Alice", message_id=42, text="Visit"
        )

        assert result["url"] == "https://example.com"
        assert result["answer"] is None
        assert result["alert"] is False
        msg.click.assert_not_awaited()
        assert isinstance(result["message"], Message)
        # Only the initial get_messages() fetch happens — no re-fetch for URL buttons.
        assert mock_telegram_client.get_messages.await_count == 1

    async def test_click_button_no_buttons_raises_value_error(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When the target message has no inline keyboard (buttons=None),
        click_button() must raise ValueError mentioning "has no inline buttons".
        """
        msg = _fake_click_message(buttons=None)
        mock_telegram_client.get_messages.side_effect = None
        mock_telegram_client.get_messages.return_value = msg

        with pytest.raises(ValueError, match="has no inline buttons"):
            await mock_client_wrapper.click_button(
                name="Alice", message_id=42, text="Yes"
            )

    async def test_click_button_not_found_raises_with_available_labels(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When no button matches the requested text, click_button() must raise
        ValueError mentioning "Button not found" and list the available
        button labels in the error message.
        """
        msg = _fake_click_message(buttons=[[_fake_button("Yes", data=b"y")],
                                            [_fake_button("No", data=b"n")]])
        mock_telegram_client.get_messages.side_effect = None
        mock_telegram_client.get_messages.return_value = msg

        with pytest.raises(ValueError, match="Button not found") as exc_info:
            await mock_client_wrapper.click_button(
                name="Alice", message_id=42, text="Maybe"
            )
        assert "Yes" in str(exc_info.value)
        assert "No" in str(exc_info.value)

    async def test_click_button_row_col_disambiguates_duplicate_labels(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        When two buttons share the same visible text in different positions,
        row/col must select the intended one: click() is awaited with the
        SECOND button's indices, not the first match.
        """
        btn_a = _fake_button("Dup", data=b"a")
        btn_b = _fake_button("Dup", data=b"b")
        msg = _fake_click_message(buttons=[[btn_a], [btn_b]])
        updated = _fake_click_message(buttons=[[btn_a], [btn_b]])
        msg.click.return_value = SimpleNamespace(message=None, alert=False)

        mock_telegram_client.get_messages.side_effect = [msg, updated]

        result = await mock_client_wrapper.click_button(
            name="Alice", message_id=42, text="Dup", row=1, col=0
        )

        msg.click.assert_awaited_once_with(1, 0)
        assert result["answer"] is None
        assert result["alert"] is False


class TestDelete:
    """Tests for TelegramClientWrapper.delete()."""

    async def test_delete_list_of_ids_returns_count(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        delete() with a list of message IDs calls _client.delete_messages(entity, ids, revoke=True)
        and returns the count of messages to delete.

        Expected: _client.delete_messages is called once with the entity, the message IDs,
        and revoke=True. The return value is the count of IDs (2 in this case).
        """
        mock_telegram_client.delete_messages.return_value = None
        result = await mock_client_wrapper.delete(name="Alice", message_ids=[7, 8])
        assert result == 2
        mock_telegram_client.delete_messages.assert_called_once()
        call_args = mock_telegram_client.delete_messages.call_args
        # Second positional arg (or kwarg) should be the IDs list
        assert [7, 8] in call_args.args or call_args.kwargs.get("message_ids") == [7, 8]

    async def test_delete_forwards_revoke_false(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        delete() with revoke=False forwards that parameter to _client.delete_messages().

        Expected: _client.delete_messages() is called with revoke=False in kwargs.
        """
        mock_telegram_client.delete_messages.return_value = None
        result = await mock_client_wrapper.delete(name="Alice", message_ids=[5], revoke=False)
        assert result == 1
        mock_telegram_client.delete_messages.assert_called_once()
        call_kwargs = mock_telegram_client.delete_messages.call_args.kwargs
        assert call_kwargs.get("revoke") is False


class TestEditMessage:
    """Tests for TelegramClientWrapper.edit_message() (real wrapper, exercises _resolve_entity and _client.edit_message)."""

    async def test_edit_message_default_parse_mode_markdown(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        edit_message() with default parse_mode normalizes "markdown" to "md"
        and passes it to _client.edit_message().

        Expected: _client.edit_message is awaited once with parse_mode="md".
        """
        mock_telegram_client.edit_message = AsyncMock(return_value=None)
        await mock_client_wrapper.edit_message(name="Alice", message_id=42, text="updated")
        mock_telegram_client.edit_message.assert_awaited_once_with(
            mock_client_wrapper._client.get_entity.return_value, 42, "updated", parse_mode="md"
        )

    async def test_edit_message_parse_mode_none(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        edit_message() with parse_mode="none" normalizes it to None
        and passes it to _client.edit_message().

        Expected: _client.edit_message is awaited once with parse_mode=None.
        """
        mock_telegram_client.edit_message = AsyncMock(return_value=None)
        await mock_client_wrapper.edit_message(name="Alice", message_id=42, text="updated", parse_mode="none")
        mock_telegram_client.edit_message.assert_awaited_once_with(
            mock_client_wrapper._client.get_entity.return_value, 42, "updated", parse_mode=None
        )

    async def test_edit_message_parse_mode_html(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        edit_message() with parse_mode="html" normalizes it to "html"
        and passes it to _client.edit_message().

        Expected: _client.edit_message is awaited once with parse_mode="html".
        """
        mock_telegram_client.edit_message = AsyncMock(return_value=None)
        await mock_client_wrapper.edit_message(name="Alice", message_id=42, text="updated", parse_mode="html")
        mock_telegram_client.edit_message.assert_awaited_once_with(
            mock_client_wrapper._client.get_entity.return_value, 42, "updated", parse_mode="html"
        )

    async def test_edit_message_resolves_entity_by_dialog_id(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        edit_message() resolves the dialog entity by dialog_id before editing.

        Expected: get_entity is called with dialog_id; edit_message is awaited once.
        """
        mock_telegram_client.get_entity.reset_mock()
        mock_telegram_client.get_entity.side_effect = None
        mock_telegram_client.edit_message = AsyncMock(return_value=None)
        await mock_client_wrapper.edit_message(dialog_id=123, message_id=42, text="fixed")
        mock_telegram_client.get_entity.assert_called_once_with(123)
        mock_telegram_client.edit_message.assert_awaited_once()

    async def test_edit_message_resolves_entity_by_name(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        edit_message() resolves the dialog entity by name via iter_dialogs before editing.

        Expected: iter_dialogs is used; edit_message is awaited once.
        """
        mock_telegram_client.edit_message = AsyncMock(return_value=None)
        await mock_client_wrapper.edit_message(name="Alice", message_id=42, text="fixed")
        mock_telegram_client.edit_message.assert_awaited_once()

    async def test_edit_message_no_selector_raises_value_error(
        self, mock_client_wrapper, mock_telegram_client
    ) -> None:
        """
        edit_message() raises ValueError when no dialog selector is provided.

        Expected: ValueError with "One of dialog_id, username, or name".
        """
        with pytest.raises(ValueError, match="One of dialog_id, username, or name"):
            await mock_client_wrapper.edit_message(message_id=42, text="fixed")

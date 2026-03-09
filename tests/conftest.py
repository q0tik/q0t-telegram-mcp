"""
Pytest fixtures for q0t-telegram-mcp tests.

All fixtures use unittest.mock to avoid real network calls.
Telethon object shapes are based on the real API as documented in docs/architecture.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Attempt to import real project modules. During Phase 1 they may not exist;
# we use try/except so test collection still works.
# ---------------------------------------------------------------------------
try:
    from tg_mcp.config import Config
except ImportError:
    Config = None  # type: ignore[assignment,misc]

try:
    from tg_mcp.client import TelegramClientWrapper
except ImportError:
    TelegramClientWrapper = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config() -> MagicMock:
    """
    A fake Config object with safe sentinel values.

    Provides app_id, api_hash, session_path, and session_dir() without reading
    any real environment variables or touching the filesystem.
    """
    if Config is not None:
        # Use the real dataclass with fake values so type checks work.
        fake_session_path = Path("/tmp/test-tg-mcp/session")
        cfg = Config(
            app_id=12345,
            api_hash="fakehash0000000000000000000000000",
            session_path=fake_session_path,
        )
        return cfg  # type: ignore[return-value]

    # Fallback when the module doesn't exist yet.
    cfg = MagicMock()
    cfg.app_id = 12345
    cfg.api_hash = "fakehash0000000000000000000000000"
    cfg.session_path = Path("/tmp/test-tg-mcp/session")
    cfg.session_dir.return_value = Path("/tmp/test-tg-mcp")
    return cfg


# ---------------------------------------------------------------------------
# Telethon object shape mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_telegram_user() -> MagicMock:
    """
    A MagicMock resembling a telethon.types.User object returned by get_me().

    Attributes reflect the real Telethon User shape:
      id, first_name, last_name, username, phone
    """
    user = MagicMock()
    user.id = 123456789
    user.first_name = "Alice"
    user.last_name = "Smith"
    user.username = "alicesmith"
    user.phone = "+15550001234"
    return user


@pytest.fixture
def mock_telegram_dialog() -> MagicMock:
    """
    A MagicMock resembling a telethon.types.Dialog object returned by get_dialogs().

    Attributes reflect the real Telethon Dialog shape:
      id, name, unread_count, is_user, is_group, is_channel, message, date
    """
    from datetime import datetime, timezone

    dialog = MagicMock()
    dialog.id = 987654321
    dialog.name = "Alice Smith"
    dialog.unread_count = 3
    dialog.is_user = True
    dialog.is_group = False
    dialog.is_channel = False

    # last message stub
    last_msg = MagicMock()
    last_msg.text = "Hey there!"
    dialog.message = last_msg

    dialog.date = datetime(2026, 3, 8, 12, 0, 0, tzinfo=timezone.utc)
    return dialog


@pytest.fixture
def mock_telegram_message() -> MagicMock:
    """
    A MagicMock resembling a telethon.types.Message object returned by get_messages().

    Attributes reflect the real Telethon Message shape:
      id, date, text, out
    The get_sender() method is async and returns a user-like mock.
    """
    from datetime import datetime, timezone

    msg = MagicMock()
    msg.id = 42
    msg.date = datetime(2026, 3, 8, 11, 30, 0, tzinfo=timezone.utc)
    msg.text = "Hello, world!"
    msg.out = False

    sender = MagicMock()
    sender.first_name = "Alice"
    sender.last_name = "Smith"
    sender.username = "alicesmith"
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


# ---------------------------------------------------------------------------
# Low-level Telethon TelegramClient mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_telegram_client(
    mock_telegram_user: MagicMock,
    mock_telegram_dialog: MagicMock,
    mock_telegram_message: MagicMock,
) -> AsyncMock:
    """
    An AsyncMock of telethon.TelegramClient with all relevant methods mocked.

    Async methods return sensible defaults:
      - get_me()                  -> mock_telegram_user
      - get_dialogs()             -> [mock_telegram_dialog]
      - get_messages()            -> [mock_telegram_message]
      - get_entity()              -> a generic entity mock
      - send_message()            -> a sent-message mock
      - send_read_acknowledge()   -> None
      - is_user_authorized()      -> True
      - connect()                 -> None
      - disconnect()              -> None
      - send_code_request()       -> mock with .phone_code_hash
      - sign_in()                 -> mock_telegram_user

    Draft-related attributes are explicitly absent / set to raise AttributeError
    so tests can assert they are never called.
    """
    client = AsyncMock()

    # Auth / lifecycle
    client.is_user_authorized.return_value = True
    client.connect.return_value = None
    client.disconnect.return_value = None

    # Account info
    client.get_me.return_value = mock_telegram_user

    # Dialogs
    client.get_dialogs.return_value = [mock_telegram_dialog]

    # Messages
    client.get_messages.return_value = [mock_telegram_message]

    # Entity resolution
    entity_mock = MagicMock()
    entity_mock.id = mock_telegram_dialog.id
    client.get_entity.return_value = entity_mock

    # Sending
    sent_msg = MagicMock()
    sent_msg.id = 100
    client.send_message.return_value = sent_msg

    # Mark read
    client.send_read_acknowledge.return_value = None

    # Auth flow helpers
    code_request_result = MagicMock()
    code_request_result.phone_code_hash = "fakephonecodehash"
    client.send_code_request.return_value = code_request_result
    client.sign_in.return_value = mock_telegram_user

    # Explicitly make draft-related methods raise so any accidental call is caught.
    # Tests for send_message will assert these are never called.
    client.MessagesSaveDraft = MagicMock(side_effect=AssertionError("MUST NOT call SaveDraft"))
    client.draft = MagicMock(side_effect=AssertionError("MUST NOT access .draft"))

    return client


# ---------------------------------------------------------------------------
# High-level wrapper fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client_wrapper(
    mock_config: MagicMock,
    mock_telegram_client: AsyncMock,
) -> MagicMock:
    """
    A TelegramClientWrapper with its internal _client replaced by mock_telegram_client.

    If TelegramClientWrapper is importable, instantiates the real class and
    injects the mock. Otherwise returns a MagicMock that has the same async
    method interface.

    This fixture is the primary dependency for test_client.py tests.
    """
    if TelegramClientWrapper is not None:
        # Patch TelegramClient inside client.py so the constructor doesn't
        # create a real connection.
        with patch("tg_mcp.client.TelegramClient", return_value=mock_telegram_client):
            wrapper = TelegramClientWrapper(mock_config)
            # Inject the pre-built mock directly so connect() didn't get called.
            wrapper._client = mock_telegram_client
        return wrapper

    # Fallback: pure mock with the correct async method names.
    wrapper = MagicMock()
    wrapper._client = mock_telegram_client
    wrapper._config = mock_config
    wrapper.connect = AsyncMock()
    wrapper.disconnect = AsyncMock()
    wrapper.is_authorized = AsyncMock(return_value=True)
    wrapper.get_me = AsyncMock()
    wrapper.get_dialogs = AsyncMock(return_value=[])
    wrapper.get_messages = AsyncMock(return_value=[])
    wrapper.send_message = AsyncMock()
    wrapper.mark_read = AsyncMock()
    return wrapper

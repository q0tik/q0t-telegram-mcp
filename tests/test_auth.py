"""
Tests for the interactive auth flow defined in src/tg_mcp/auth.py.

Contracts verified (per docs/architecture.md §9):
  - Already-authorized sessions are detected and skip re-auth
  - OTP prompt is presented to the user via input()
  - 2FA password provided via --password is used without prompting
  - Missing 2FA password triggers a getpass prompt
  - PhoneCodeInvalidError, PhoneCodeExpiredError exit with code 1
  - PasswordHashInvalidError exits with code 1
  - Session directory is created (mkdir parents=True, exist_ok=True)

All tests patch telethon.TelegramClient and builtins.input / getpass.getpass
so no real network calls or TTY interaction occurs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tg_mcp.auth import AuthFlow
from tg_mcp.config import Config


def _make_auth_client(
    is_authorized: bool = False,
    sign_in_effect=None,
) -> AsyncMock:
    """Build a mock TelegramClient for auth tests."""
    client = AsyncMock()
    client.connect.return_value = None
    client.disconnect.return_value = None
    client.is_user_authorized.return_value = is_authorized

    code_result = MagicMock()
    code_result.phone_code_hash = "fakephonecodehash"
    client.send_code_request.return_value = code_result

    if sign_in_effect is not None:
        client.sign_in.side_effect = sign_in_effect
    else:
        user = MagicMock()
        user.id = 123
        client.sign_in.return_value = user

    return client


def _make_config(tmp_path: Path) -> Config:
    """Build a Config pointing to tmp_path."""
    return Config(
        app_id=12345,
        api_hash="fakehash",
        session_path=tmp_path / ".telegram-mcp" / "session",
    )


class TestAuthFlow:
    """Tests for AuthFlow.run() defined in src/tg_mcp/auth.py."""

    async def test_auth_skips_if_already_authorized(
        self, mock_config
    ) -> None:
        """
        If is_user_authorized() returns True at the start of run(), the auth
        flow prints 'Already authorized' and returns without requesting a code
        or calling sign_in().

        Expected: send_code_request() and sign_in() are NOT called; function
        returns normally (no SystemExit).
        """
        mock_tg_client = _make_auth_client(is_authorized=True)

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            await flow.run(phone="+15550001234", password=None)

        mock_tg_client.send_code_request.assert_not_called()
        mock_tg_client.sign_in.assert_not_called()

    async def test_auth_prompts_for_otp(self, mock_config) -> None:
        """
        When not yet authorized, run() calls send_code_request() then prompts
        the user for the OTP via input().

        Expected: input() is called exactly once; sign_in() is called with
        the value returned by input().
        """
        mock_tg_client = _make_auth_client(is_authorized=False)

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="12345") as mock_input:
                await flow.run(phone="+15550001234", password=None)

        mock_input.assert_called_once()
        mock_tg_client.sign_in.assert_called_once()
        call_args = mock_tg_client.sign_in.call_args
        # The OTP "12345" should be passed as the second positional arg
        assert "12345" in call_args.args or call_args.kwargs.get("code") == "12345"

    async def test_auth_handles_2fa_when_password_provided(
        self, mock_config
    ) -> None:
        """
        When sign_in() raises SessionPasswordNeededError and a password is
        provided to run(), the flow calls sign_in(password=password) without
        prompting the user.

        Expected: getpass.getpass() is NOT called; sign_in() is called a
        second time with the provided password.
        """
        from telethon.errors import SessionPasswordNeededError

        # First sign_in call raises, second (with password) succeeds
        sign_in_effects = [SessionPasswordNeededError(request=None), MagicMock()]
        mock_tg_client = _make_auth_client(
            is_authorized=False, sign_in_effect=sign_in_effects
        )

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="12345"):
                with patch("getpass.getpass") as mock_getpass:
                    await flow.run(phone="+15550001234", password="mypassword")

        mock_getpass.assert_not_called()
        # sign_in called twice: first with OTP, then with password
        assert mock_tg_client.sign_in.call_count == 2
        second_call = mock_tg_client.sign_in.call_args_list[1]
        assert second_call.kwargs.get("password") == "mypassword"

    async def test_auth_prompts_for_2fa_password_when_not_provided(
        self, mock_config
    ) -> None:
        """
        When sign_in() raises SessionPasswordNeededError and password=None was
        passed to run(), the flow prompts via getpass.getpass().

        Expected: getpass.getpass() is called exactly once; its return value
        is passed to sign_in(password=...).
        """
        from telethon.errors import SessionPasswordNeededError

        sign_in_effects = [SessionPasswordNeededError(request=None), MagicMock()]
        mock_tg_client = _make_auth_client(
            is_authorized=False, sign_in_effect=sign_in_effects
        )

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="12345"):
                with patch("getpass.getpass", return_value="secret2fa") as mock_getpass:
                    await flow.run(phone="+15550001234", password=None)

        mock_getpass.assert_called_once()
        assert mock_tg_client.sign_in.call_count == 2
        second_call = mock_tg_client.sign_in.call_args_list[1]
        assert second_call.kwargs.get("password") == "secret2fa"

    async def test_auth_exits_on_invalid_code(self, mock_config) -> None:
        """
        When sign_in() raises PhoneCodeInvalidError, run() prints an error
        message and exits with sys.exit(1).

        Expected: SystemExit is raised with exit code 1.
        """
        from telethon.errors import PhoneCodeInvalidError

        mock_tg_client = _make_auth_client(
            is_authorized=False,
            sign_in_effect=PhoneCodeInvalidError(request=None),
        )

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="99999"):
                with pytest.raises(SystemExit) as exc_info:
                    await flow.run(phone="+15550001234", password=None)

        assert exc_info.value.code == 1

    async def test_auth_exits_on_expired_code(self, mock_config) -> None:
        """
        When sign_in() raises PhoneCodeExpiredError, run() prints an error
        message and exits with sys.exit(1).

        Expected: SystemExit is raised with exit code 1.
        """
        from telethon.errors import PhoneCodeExpiredError

        mock_tg_client = _make_auth_client(
            is_authorized=False,
            sign_in_effect=PhoneCodeExpiredError(request=None),
        )

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="99999"):
                with pytest.raises(SystemExit) as exc_info:
                    await flow.run(phone="+15550001234", password=None)

        assert exc_info.value.code == 1

    async def test_auth_exits_on_wrong_2fa_password(
        self, mock_config
    ) -> None:
        """
        When the second sign_in() (with password) raises
        PasswordHashInvalidError, run() prints 'Wrong 2FA password' and exits
        with sys.exit(1).

        Expected: SystemExit is raised with exit code 1.
        """
        from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

        sign_in_effects = [
            SessionPasswordNeededError(request=None),
            PasswordHashInvalidError(request=None),
        ]
        mock_tg_client = _make_auth_client(
            is_authorized=False, sign_in_effect=sign_in_effects
        )

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="12345"):
                with pytest.raises(SystemExit) as exc_info:
                    await flow.run(phone="+15550001234", password="wrongpassword")

        assert exc_info.value.code == 1

    async def test_auth_exits_on_invalid_api_id(self, mock_config) -> None:
        """
        When sign_in() or send_code_request() raises ApiIdInvalidError,
        run() prints an error and exits with sys.exit(1).

        Expected: SystemExit is raised with exit code 1.
        """
        from telethon.errors import ApiIdInvalidError

        mock_tg_client = _make_auth_client(is_authorized=False)
        mock_tg_client.send_code_request.side_effect = ApiIdInvalidError(request=None)

        flow = AuthFlow(mock_config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            with patch("builtins.input", return_value="12345"):
                with pytest.raises(SystemExit) as exc_info:
                    await flow.run(phone="+15550001234", password=None)

        assert exc_info.value.code == 1

    async def test_auth_creates_session_directory(
        self, mock_config, tmp_path
    ) -> None:
        """
        run() calls config.session_dir().mkdir(parents=True, exist_ok=True)
        before connecting, so the directory is guaranteed to exist before
        Telethon tries to create the session file.

        Expected: the session directory exists on disk after run() completes
        (use tmp_path to avoid touching real filesystem).
        """
        config = _make_config(tmp_path)
        session_dir = config.session_dir()

        # Ensure it doesn't exist yet
        assert not session_dir.exists()

        mock_tg_client = _make_auth_client(is_authorized=True)

        flow = AuthFlow(config)
        with patch("tg_mcp.auth.TelegramClient", return_value=mock_tg_client):
            await flow.run(phone="+15550001234", password=None)

        assert session_dir.exists()
        assert session_dir.is_dir()

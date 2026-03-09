from __future__ import annotations

import getpass
import sys

from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from .config import Config


class AuthFlow:
    """Orchestrates the Telegram interactive authentication flow.

    After a successful run, Telethon writes a session file to the path
    specified in *config*.  Subsequent connections reuse that session
    without re-prompting.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    async def run(self, phone: str, password: str | None) -> None:
        """Authenticate with Telegram and save the session to disk.

        Sends an OTP to *phone*, prompts the user to enter it, and signs
        in.  If the account has two-factor authentication enabled and
        *password* is not supplied, the user is prompted interactively.

        Args:
            phone: Phone number including country code (e.g. ``+1234567890``).
            password: 2FA password, or ``None`` to prompt if needed.

        Exits with code 1 on invalid OTP, expired OTP, wrong 2FA password,
        or invalid API credentials.
        """
        self._config.session_dir().mkdir(parents=True, exist_ok=True)

        client = TelegramClient(
            str(self._config.session_path),
            self._config.app_id,
            self._config.api_hash,
        )

        try:
            await client.connect()

            if await client.is_user_authorized():
                print("Already authorized.")
                return

            try:
                result = await client.send_code_request(phone)
                phone_code_hash = result.phone_code_hash

                code = input("Enter the code you received: ").strip()

                try:
                    await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
                except SessionPasswordNeededError:
                    if password is None:
                        password = getpass.getpass("Enter your 2FA password: ")
                    await client.sign_in(password=password)

            except PhoneCodeInvalidError:
                print("Error: The phone code you entered is invalid.")
                sys.exit(1)
            except PhoneCodeExpiredError:
                print("Error: The phone code has expired. Please try again.")
                sys.exit(1)
            except PasswordHashInvalidError:
                print("Error: Wrong 2FA password.")
                sys.exit(1)
            except ApiIdInvalidError:
                print("Error: Invalid TG_APP_ID / TG_API_HASH.")
                sys.exit(1)

            print(
                f"Authentication successful. Session saved to: {self._config.session_path}"
            )
        finally:
            await client.disconnect()

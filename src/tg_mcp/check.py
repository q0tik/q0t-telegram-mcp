from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from telethon import TelegramClient

from .config import Config

#: Telethon's SQLiteSession appends this when the configured path lacks it,
#: so ``/data/alice`` and ``/data/alice.session`` name the same file.
_SESSION_EXTENSION = ".session"

Status = Literal["authorized", "unauthorized", "unknown"]

_EXIT_CODES: dict[str, int] = {
    "authorized": 0,
    "unauthorized": 1,
    "unknown": 2,
}


def resolve_session_file(session_path: Path) -> Path | None:
    """Return the session file that *session_path* addresses, or None.

    Purely a lookup: it never creates anything.  Both spellings are accepted
    because Telethon adds the extension itself when it is missing.
    """
    if session_path.is_file():
        return session_path
    suffixed = session_path.with_name(session_path.name + _SESSION_EXTENSION)
    if suffixed.is_file():
        return suffixed
    return None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a session check.

    *status* deliberately has three values.  "Telegram says this session is
    logged out" and "I could not find out" call for opposite reactions — the
    first means re-authenticate, the second means fix the path or the network —
    so they never share an exit code.
    """

    status: Status
    session_path: Path
    user: dict | None = None
    error: str | None = None

    @property
    def exit_code(self) -> int:
        """0 authorized, 1 unauthorized, 2 could not determine."""
        return _EXIT_CODES[self.status]

    @property
    def authorized(self) -> bool | None:
        """Tri-state form of :attr:`status`; None means "could not determine"."""
        if self.status == "unknown":
            return None
        return self.status == "authorized"

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "authorized": self.authorized,
                "session_path": str(self.session_path),
                "user": self.user,
                "error": self.error,
            },
            ensure_ascii=False,
        )

    def human(self) -> str:
        if self.status == "authorized":
            line = f"Session is authorized: {self.session_path}"
            if self.user:
                who = self.user.get("username")
                who = f"@{who}" if who else (self.user.get("first_name") or "")
                line += f"\nLogged in as {who} (id {self.user.get('id')})".rstrip()
            return line
        if self.status == "unauthorized":
            return (
                f"Session is NOT authorized: {self.session_path}\n"
                "Run: q0t-telegram-mcp auth --phone <phone>"
            )
        return f"Could not check session: {self.session_path}\n{self.error or 'unknown error'}"


class SessionChecker:
    """Answers "is this session still logged in?" without disturbing it.

    Two things make this less trivial than opening the session directly:

    * ``SQLiteSession`` builds its database inside the constructor, so pointing
      Telethon at a path that does not exist *creates* an empty session and
      then reports it as unauthorized — the check would cause the state it
      reports.  Hence the existence test before Telethon is touched at all.
    * The session file is SQLite and a running server may hold it open, while
      Telethon writes to it on connect.  Concurrent access risks ``database is
      locked`` and a corrupted session, so the check runs against a throwaway
      copy and the original is only ever read.

    The check does make a real network call, which Telegram records as device
    activity — it is not a local-only operation.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    async def run(self) -> CheckResult:
        """Connect with a copy of the session and report what Telegram says."""
        session_file = resolve_session_file(self._config.session_path)
        if session_file is None:
            return CheckResult(
                status="unknown",
                session_path=self._config.session_path,
                error=f"session file not found: {self._config.session_path}",
            )

        with tempfile.TemporaryDirectory(prefix="tg-mcp-check-") as tmp:
            # Keep the .session name so Telethon opens the copy rather than
            # deriving a neighbouring path from it.
            probe = Path(tmp) / f"probe{_SESSION_EXTENSION}"
            try:
                shutil.copyfile(session_file, probe)
            except OSError as e:
                return CheckResult(
                    status="unknown", session_path=session_file,
                    error=f"could not read session file: {e}",
                )
            return await self._probe(probe, session_file)

    async def _probe(self, probe: Path, session_file: Path) -> CheckResult:
        # receive_updates=False for the same reason as the server: this process
        # only asks one question and never consumes the update stream.
        client = TelegramClient(
            str(probe),
            self._config.app_id,
            self._config.api_hash,
            receive_updates=False,
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return CheckResult(status="unauthorized", session_path=session_file)
            return CheckResult(
                status="authorized",
                session_path=session_file,
                user=await self._identify(client),
            )
        except Exception as e:
            return CheckResult(
                status="unknown", session_path=session_file,
                error=f"{type(e).__name__}: {e}",
            )
        finally:
            try:
                await client.disconnect()
            except Exception:
                # Already answered the caller's question; a noisy teardown must
                # not turn a good answer into "unknown".
                pass

    @staticmethod
    async def _identify(client: TelegramClient) -> dict | None:
        """Best-effort "who is this". Never downgrades an answered check."""
        try:
            me = await client.get_me()
        except Exception:
            return None
        if me is None:
            return None
        return {
            "id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "first_name": getattr(me, "first_name", None),
            "last_name": getattr(me, "last_name", None),
            "phone": getattr(me, "phone", None),
        }

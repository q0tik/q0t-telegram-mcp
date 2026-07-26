"""
Tests for the `check` command (src/tg_mcp/check.py).

Contracts verified:
  - the session file is located without inventing one (Telethon appends
    `.session`, so both spellings of the path have to be considered)
  - a missing session reports "unknown", and — critically — does not create
    an empty session as a side effect of looking
  - the check runs against a throwaway copy, so it cannot corrupt or lock the
    session file a running server is holding open
  - authorized / unauthorized / could-not-tell map to exit codes 0 / 1 / 2
  - --json emits a machine-readable shape

Telethon is patched throughout: no network call is made.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from tg_mcp import cli
from tg_mcp.check import CheckResult, SessionChecker, resolve_session_file
from tg_mcp.config import Config


def _config(session_path: Path) -> Config:
    return Config(app_id=1, api_hash="h", session_path=session_path)


def _fake_client(*, authorized: bool = True, me: MagicMock | None = None,
                 connect_error: Exception | None = None) -> MagicMock:
    """Build a stand-in TelegramClient that also mimics Telethon's side effect
    of creating its SQLite file the moment it is constructed."""
    client = MagicMock()
    client.connect = AsyncMock(side_effect=connect_error)
    client.disconnect = AsyncMock()
    client.is_user_authorized = AsyncMock(return_value=authorized)
    client.get_me = AsyncMock(return_value=me)
    return client


def _me(**kwargs) -> MagicMock:
    user = MagicMock()
    user.id = kwargs.get("id", 777)
    user.username = kwargs.get("username", "alice")
    user.first_name = kwargs.get("first_name", "Alice")
    user.last_name = kwargs.get("last_name", None)
    user.phone = kwargs.get("phone", "15550001234")
    return user


class TestResolveSessionFile:
    """Locating the file without creating it."""

    def test_finds_the_path_verbatim(self, tmp_path: Path) -> None:
        p = tmp_path / "alice.session"
        p.write_bytes(b"db")
        assert resolve_session_file(p) == p

    def test_finds_the_telethon_suffixed_variant(self, tmp_path: Path) -> None:
        """TG_SESSION_PATH=/data/alice addresses /data/alice.session, because
        that is the name Telethon actually opens."""
        actual = tmp_path / "alice.session"
        actual.write_bytes(b"db")
        assert resolve_session_file(tmp_path / "alice") == actual

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert resolve_session_file(tmp_path / "nope") is None

    def test_looking_does_not_create_anything(self, tmp_path: Path) -> None:
        resolve_session_file(tmp_path / "nope")
        assert list(tmp_path.iterdir()) == []


class TestCheckerMissingSession:
    async def test_missing_session_is_unknown_not_unauthorized(self, tmp_path: Path) -> None:
        """"No file" means we could not determine anything. Reporting it as
        "not authorized" would tell the caller a session is dead when they may
        simply have pointed at the wrong path."""
        result = await SessionChecker(_config(tmp_path / "nope")).run()
        assert result.status == "unknown"
        assert result.exit_code == 2

    async def test_missing_session_is_not_created_by_checking(self, tmp_path: Path) -> None:
        """The gotcha this whole code path exists for: SQLiteSession builds its
        database inside the constructor, so a naive check would create an empty
        session, then truthfully report it as unauthorized — having caused the
        very state it reported."""
        with patch("tg_mcp.check.TelegramClient") as tc:
            await SessionChecker(_config(tmp_path / "nope")).run()
        tc.assert_not_called()
        assert list(tmp_path.iterdir()) == []


class TestCheckerAgainstACopy:
    async def test_telethon_is_pointed_at_a_copy_not_the_original(self, tmp_path: Path) -> None:
        """A running server holds the session open; Telethon writes to it on
        connect. Touching the original risks `database is locked` on the server
        and a corrupted session for everyone."""
        original = tmp_path / "alice.session"
        original.write_bytes(b"original-bytes")
        seen: dict[str, str] = {}

        def construct(path, *args, **kwargs):
            seen["path"] = path
            # Telethon would open/modify this file; prove we let it, safely.
            Path(path).write_bytes(b"mutated-by-telethon")
            return _fake_client(me=_me())

        with patch("tg_mcp.check.TelegramClient", side_effect=construct):
            await SessionChecker(_config(original)).run()

        assert seen["path"] != str(original)
        assert original.read_bytes() == b"original-bytes"

    async def test_the_copy_is_removed_afterwards(self, tmp_path: Path) -> None:
        original = tmp_path / "alice.session"
        original.write_bytes(b"db")
        seen: dict[str, str] = {}

        def construct(path, *args, **kwargs):
            seen["path"] = path
            Path(path).write_bytes(b"db")
            return _fake_client(me=_me())

        with patch("tg_mcp.check.TelegramClient", side_effect=construct):
            await SessionChecker(_config(original)).run()

        assert not Path(seen["path"]).exists()

    async def test_copy_receives_the_session_bytes(self, tmp_path: Path) -> None:
        """A copy that did not carry the auth key over would report every
        session as dead."""
        original = tmp_path / "alice.session"
        original.write_bytes(b"auth-key-material")
        seen: dict[str, bytes] = {}

        def construct(path, *args, **kwargs):
            seen["bytes"] = Path(path).read_bytes()
            return _fake_client(me=_me())

        with patch("tg_mcp.check.TelegramClient", side_effect=construct):
            await SessionChecker(_config(original)).run()

        assert seen["bytes"] == b"auth-key-material"


class TestCheckerOutcomes:
    async def test_authorized_reports_the_user(self, tmp_path: Path) -> None:
        original = tmp_path / "alice.session"
        original.write_bytes(b"db")
        with patch("tg_mcp.check.TelegramClient",
                   return_value=_fake_client(authorized=True, me=_me())):
            result = await SessionChecker(_config(original)).run()
        assert result.status == "authorized"
        assert result.exit_code == 0
        assert result.user == {
            "id": 777, "username": "alice", "first_name": "Alice",
            "last_name": None, "phone": "15550001234",
        }

    async def test_authorized_survives_get_me_failing(self, tmp_path: Path) -> None:
        """Identity is a nice-to-have; the answer to "is it alive" is already
        known by then and must not be downgraded by a secondary call."""
        original = tmp_path / "alice.session"
        original.write_bytes(b"db")
        client = _fake_client(authorized=True)
        client.get_me = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("tg_mcp.check.TelegramClient", return_value=client):
            result = await SessionChecker(_config(original)).run()
        assert result.status == "authorized"
        assert result.exit_code == 0
        assert result.user is None

    async def test_unauthorized_is_exit_1(self, tmp_path: Path) -> None:
        original = tmp_path / "alice.session"
        original.write_bytes(b"db")
        with patch("tg_mcp.check.TelegramClient",
                   return_value=_fake_client(authorized=False)):
            result = await SessionChecker(_config(original)).run()
        assert result.status == "unauthorized"
        assert result.exit_code == 1

    async def test_network_failure_is_unknown_not_unauthorized(self, tmp_path: Path) -> None:
        """"Could not reach Telegram" and "Telegram says you are logged out"
        lead to opposite actions — never collapse them into one code."""
        original = tmp_path / "alice.session"
        original.write_bytes(b"db")
        with patch("tg_mcp.check.TelegramClient",
                   return_value=_fake_client(connect_error=OSError("no route to host"))):
            result = await SessionChecker(_config(original)).run()
        assert result.status == "unknown"
        assert result.exit_code == 2
        assert "no route to host" in (result.error or "")

    async def test_client_is_disconnected_even_when_the_check_fails(self, tmp_path: Path) -> None:
        original = tmp_path / "alice.session"
        original.write_bytes(b"db")
        client = _fake_client(authorized=True)
        client.is_user_authorized = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("tg_mcp.check.TelegramClient", return_value=client):
            await SessionChecker(_config(original)).run()
        client.disconnect.assert_awaited_once()


class TestCheckResultRendering:
    def test_json_shape_for_authorized(self, tmp_path: Path) -> None:
        result = CheckResult(status="authorized", session_path=tmp_path / "a.session",
                             user={"id": 1, "username": "alice"})
        payload = json.loads(result.to_json())
        assert payload["status"] == "authorized"
        assert payload["authorized"] is True
        assert payload["user"]["username"] == "alice"
        assert payload["session_path"] == str(tmp_path / "a.session")

    def test_json_shape_for_unknown_carries_the_reason(self, tmp_path: Path) -> None:
        result = CheckResult(status="unknown", session_path=tmp_path / "a.session",
                             error="session file not found")
        payload = json.loads(result.to_json())
        assert payload["authorized"] is None
        assert payload["error"] == "session file not found"

    def test_human_output_names_the_session(self, tmp_path: Path) -> None:
        result = CheckResult(status="unauthorized", session_path=tmp_path / "a.session")
        assert str(tmp_path / "a.session") in result.human()


class TestCheckCommand:
    """The CLI wiring: same config sources and override flags as `auth`."""

    def _invoke(self, args: list[str], env: dict[str, str], result: CheckResult):
        checker = MagicMock()
        checker.run = AsyncMock(return_value=result)
        with patch.dict(os.environ, env, clear=True), \
                patch("tg_mcp.SessionChecker", return_value=checker) as patched:
            invoked = CliRunner().invoke(cli, ["check"] + args)
        return invoked, patched

    def test_authorized_exits_0(self, tmp_path: Path) -> None:
        invoked, _ = self._invoke(
            [], {"TG_APP_ID": "1"},
            CheckResult(status="authorized", session_path=tmp_path / "a",
                        user={"id": 1, "username": "alice"}))
        assert invoked.exit_code == 0
        assert "alice" in invoked.output

    def test_unauthorized_exits_1(self, tmp_path: Path) -> None:
        invoked, _ = self._invoke(
            [], {"TG_APP_ID": "1"},
            CheckResult(status="unauthorized", session_path=tmp_path / "a"))
        assert invoked.exit_code == 1

    def test_unknown_exits_2(self, tmp_path: Path) -> None:
        invoked, _ = self._invoke(
            [], {"TG_APP_ID": "1"},
            CheckResult(status="unknown", session_path=tmp_path / "a",
                        error="session file not found"))
        assert invoked.exit_code == 2

    def test_json_flag_emits_parseable_output(self, tmp_path: Path) -> None:
        invoked, _ = self._invoke(
            ["--json"], {"TG_APP_ID": "1"},
            CheckResult(status="authorized", session_path=tmp_path / "a",
                        user={"id": 1, "username": "alice"}))
        assert json.loads(invoked.output)["status"] == "authorized"

    def test_check_runs_without_api_hash(self, tmp_path: Path) -> None:
        """check never signs in, so it must not demand the secret half of the
        credentials any more than the server does."""
        invoked, patched = self._invoke(
            [], {"TG_APP_ID": "1"},
            CheckResult(status="authorized", session_path=tmp_path / "a"))
        assert invoked.exit_code == 0
        assert patched.call_args.args[0].has_real_api_hash is False

    def test_session_flag_overrides_env(self, tmp_path: Path) -> None:
        invoked, patched = self._invoke(
            ["--session", "/data/tg/bob.session"],
            {"TG_APP_ID": "1", "TG_SESSION_PATH": "/data/tg/alice.session"},
            CheckResult(status="authorized", session_path=Path("/data/tg/bob.session")))
        assert patched.call_args.args[0].session_path == Path("/data/tg/bob.session")

    def test_app_id_flag_overrides_env(self, tmp_path: Path) -> None:
        invoked, patched = self._invoke(
            ["--app-id", "42"], {"TG_APP_ID": "1"},
            CheckResult(status="authorized", session_path=tmp_path / "a"))
        assert patched.call_args.args[0].app_id == 42

    def test_missing_app_id_is_reported_not_raised(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            invoked = CliRunner().invoke(cli, ["check"])
        assert invoked.exit_code == 1
        assert "TG_APP_ID" in invoked.output

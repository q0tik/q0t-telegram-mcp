"""
Tests for the `auth` command's option plumbing (src/tg_mcp/__init__.py).

Contracts verified:
  - --session overrides TG_SESSION_PATH for the Config handed to AuthFlow
  - omitting --session leaves an existing TG_SESSION_PATH untouched
  - --app-id / --api-hash still win over the environment

AuthFlow is patched, so no network call or TTY interaction occurs. The env is
patched per test because `_run_auth` deliberately writes the CLI flags into
os.environ (that is how they reach Config.from_env).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from tg_mcp import cli


def _run(args: list[str], env: dict[str, str]) -> MagicMock:
    """Invoke the CLI with AuthFlow patched; return the patched class.

    The Config the command built is `patched.call_args.args[0]` — asserting on
    it is the point: everything else about auth is covered by test_auth.py.
    """
    flow_instance = MagicMock()
    flow_instance.run = AsyncMock(return_value=None)
    with patch.dict(os.environ, env, clear=True), \
            patch("tg_mcp.AuthFlow", return_value=flow_instance) as patched:
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    return patched


class TestAuthSessionOption:
    def test_session_flag_sets_session_path(self) -> None:
        patched = _run(
            ["auth", "--phone", "+15550001234", "--session", "/data/tg/alice.session"],
            {"TG_APP_ID": "1", "TG_API_HASH": "h"},
        )
        config = patched.call_args.args[0]
        assert config.session_path == Path("/data/tg/alice.session")

    def test_session_flag_beats_env_var(self) -> None:
        patched = _run(
            ["auth", "--phone", "+15550001234", "--session", "/data/tg/bob.session"],
            {"TG_APP_ID": "1", "TG_API_HASH": "h",
             "TG_SESSION_PATH": "/data/tg/alice.session"},
        )
        config = patched.call_args.args[0]
        assert config.session_path == Path("/data/tg/bob.session")

    def test_env_var_used_when_flag_absent(self) -> None:
        patched = _run(
            ["auth", "--phone", "+15550001234"],
            {"TG_APP_ID": "1", "TG_API_HASH": "h",
             "TG_SESSION_PATH": "/data/tg/alice.session"},
        )
        config = patched.call_args.args[0]
        assert config.session_path == Path("/data/tg/alice.session")

    def test_auth_requires_api_hash(self) -> None:
        """`auth` is the one path that sends the hash to Telegram, so an absent
        TG_API_HASH must stop it up front — not surface later as a confusing
        ApiIdInvalidError from the placeholder."""
        flow_instance = MagicMock()
        flow_instance.run = AsyncMock(return_value=None)
        with patch.dict(os.environ, {"TG_APP_ID": "1"}, clear=True), \
                patch("tg_mcp.AuthFlow", return_value=flow_instance) as patched:
            result = CliRunner().invoke(cli, ["auth", "--phone", "+15550001234"])
        assert result.exit_code == 1
        assert "TG_API_HASH" in result.output
        patched.assert_not_called()

    def test_auth_reports_missing_app_id_the_same_way(self) -> None:
        """Same shape of failure, so one missing variable does not look like a
        crash while the other looks like a handled error."""
        with patch.dict(os.environ, {"TG_API_HASH": "h"}, clear=True):
            result = CliRunner().invoke(cli, ["auth", "--phone", "+15550001234"])
        assert result.exit_code == 1
        assert "TG_APP_ID" in result.output

    def test_auth_hash_flag_satisfies_the_requirement(self) -> None:
        """--api-hash is an accepted way to supply it; only *absence* fails."""
        patched = _run(
            ["auth", "--phone", "+15550001234", "--api-hash", "fromflag"],
            {"TG_APP_ID": "1"},
        )
        assert patched.call_args.args[0].api_hash == "fromflag"

    def test_credential_flags_still_win_over_env(self) -> None:
        """Regression guard: --session is threaded through the same env-writing
        path as --app-id/--api-hash, so it must not disturb them."""
        patched = _run(
            ["auth", "--phone", "+15550001234", "--app-id", "42",
             "--api-hash", "fromflag", "--session", "/data/tg/alice.session"],
            {"TG_APP_ID": "1", "TG_API_HASH": "fromenv"},
        )
        config = patched.call_args.args[0]
        assert (config.app_id, config.api_hash) == (42, "fromflag")

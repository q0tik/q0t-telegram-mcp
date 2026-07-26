"""
Tests for Config defined in src/tg_mcp/config.py.

Contracts verified (per docs/architecture.md §10):
  - Config.from_env() reads TG_APP_ID and TG_API_HASH from environment
  - Config.from_env() raises EnvironmentError when env vars are missing
  - Config.session_dir() returns the parent of session_path
  - Config is a frozen dataclass
"""

from __future__ import annotations

import dataclasses
import os
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from telethon import TelegramClient

from tg_mcp.config import API_HASH_PLACEHOLDER, Config


class TestConfigFromEnv:
    """Tests for Config.from_env()."""

    def test_from_env_reads_app_id_and_api_hash(self) -> None:
        """
        Config.from_env() reads TG_APP_ID and TG_API_HASH from the environment.

        Expected: the returned Config has the values from the env vars.
        """
        with patch.dict(os.environ, {"TG_APP_ID": "99999", "TG_API_HASH": "testhash123"}):
            config = Config.from_env()
        assert config.app_id == 99999
        assert config.api_hash == "testhash123"

    def test_from_env_raises_when_app_id_missing(self) -> None:
        """
        Config.from_env() raises EnvironmentError when TG_APP_ID is not set.
        """
        env = {"TG_API_HASH": "testhash123"}
        # Remove TG_APP_ID from environment
        env_without_app_id = {k: v for k, v in os.environ.items() if k != "TG_APP_ID"}
        env_without_app_id["TG_API_HASH"] = "testhash123"
        with patch.dict(os.environ, env_without_app_id, clear=True):
            with pytest.raises(EnvironmentError, match="TG_APP_ID"):
                Config.from_env()

    def test_from_env_raises_when_api_hash_missing_and_required(self) -> None:
        """
        Config.from_env(require_api_hash=True) raises when TG_API_HASH is unset.

        This is the `auth` path: signing in is the one thing that genuinely
        needs the hash, so it must fail loudly rather than reach Telegram with
        a placeholder and come back with a confusing ApiIdInvalidError.
        """
        env_without_hash = {k: v for k, v in os.environ.items() if k != "TG_API_HASH"}
        env_without_hash["TG_APP_ID"] = "12345"
        with patch.dict(os.environ, env_without_hash, clear=True):
            with pytest.raises(EnvironmentError, match="TG_API_HASH"):
                Config.from_env(require_api_hash=True)

    def test_from_env_session_path_uses_cwd(self) -> None:
        """
        Config.from_env() sets session_path to <cwd>/.telegram-mcp/session.
        """
        with patch.dict(os.environ, {"TG_APP_ID": "12345", "TG_API_HASH": "hash",
                                     "TG_SESSION_PATH": ""}):
            config = Config.from_env()
        expected = Path.cwd() / ".telegram-mcp" / "session"
        assert config.session_path == expected


class TestConfigOptionalApiHash:
    """TG_API_HASH is required only for `auth`, not for serving a session.

    Sessions are routinely created on a different machine from the one that
    later serves them, so the server half of a deployment should not have to
    carry the secret half of the app credentials just to satisfy a constructor.
    """

    def test_server_mode_substitutes_placeholder_when_unset(self) -> None:
        """Unset TG_API_HASH is fine for the server: a placeholder stands in."""
        env = {k: v for k, v in os.environ.items() if k != "TG_API_HASH"}
        env["TG_APP_ID"] = "12345"
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
        assert config.app_id == 12345
        assert config.api_hash == API_HASH_PLACEHOLDER

    def test_real_hash_is_used_verbatim_in_server_mode(self) -> None:
        """A configured hash still wins — 0.6.x behaviour is unchanged."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "realhash"}):
            assert Config.from_env().api_hash == "realhash"

    def test_real_hash_is_used_verbatim_in_auth_mode(self) -> None:
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "realhash"}):
            assert Config.from_env(require_api_hash=True).api_hash == "realhash"

    def test_app_id_is_required_even_in_server_mode(self) -> None:
        """Only the hash became optional; api_id goes on the wire every connection."""
        env = {k: v for k, v in os.environ.items() if k != "TG_APP_ID"}
        env["TG_API_HASH"] = "h"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(EnvironmentError, match="TG_APP_ID"):
                Config.from_env()

    def test_blank_hash_counts_as_unset_in_server_mode(self) -> None:
        """Blank means "not configured" — same rule as TG_SESSION_PATH."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "   "}):
            assert Config.from_env().api_hash == API_HASH_PLACEHOLDER

    def test_blank_hash_counts_as_unset_in_auth_mode(self) -> None:
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": ""}):
            with pytest.raises(EnvironmentError, match="TG_API_HASH"):
                Config.from_env(require_api_hash=True)

    def test_surrounding_whitespace_is_stripped_from_a_real_hash(self) -> None:
        """Values arrive from shell-written configs; a stray newline is not part
        of the secret and would otherwise be sent to Telegram verbatim."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": " realhash\n"}):
            assert Config.from_env().api_hash == "realhash"

    def test_placeholder_does_not_look_like_a_secret(self) -> None:
        """Guards the placeholder's *purpose*: it shows up in tracebacks, debug
        dumps and `ps` output, so it has to read as an absent value rather than
        be mistaken for a leaked credential. A real api_hash is 32 hex chars."""
        assert API_HASH_PLACEHOLDER
        assert not re.fullmatch(r"[0-9a-f]{32}", API_HASH_PLACEHOLDER)
        assert "placeholder" in API_HASH_PLACEHOLDER.lower()

    def test_has_real_api_hash_reports_the_difference(self) -> None:
        """Callers that genuinely need the hash must be able to ask."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "realhash"}):
            assert Config.from_env().has_real_api_hash is True
        env = {k: v for k, v in os.environ.items() if k != "TG_API_HASH"}
        env["TG_APP_ID"] = "1"
        with patch.dict(os.environ, env, clear=True):
            assert Config.from_env().has_real_api_hash is False


class TestTelethonPlaceholderAssumption:
    """Pins the third-party assumption the optional-hash feature rests on.

    Why this test exists — do not delete it as trivial:

    Substituting a fake api_hash is only safe because Telethon never puts the
    hash on the wire outside of *login*. Verified by reading Telethon 1.44:

      - client/auth.py:447  auth.SendCodeRequest            (phone login)
      - client/auth.py:350  auth.ImportBotAuthorizationRequest (bot login)
      - tl/custom/qrlogin.py:19  auth.ExportLoginTokenRequest  (QR login)
      - client/telegrambaseclient.py:368  InitConnectionRequest — api_id only,
        this is the request every persistent connection actually sends
      - client/telegrambaseclient.py:273  the constructor checks non-emptiness
        and nothing else
      - client/telegrambaseclient.py:924  the CDN client for cross-DC downloads
        (reached via tg_download) re-enters that same constructor

    Our server never logs in — client.py calls connect(), never start()/sign_in.
    If a future Telethon starts sending api_hash on a non-login request, or
    starts validating its shape, these assertions are where it must break —
    loudly, here, instead of silently at a user's first tool call.
    """

    @staticmethod
    @contextmanager
    def _probe_client(tmp_path: Path):
        """A real TelegramClient, with its SQLite session closed afterwards.

        Constructing one opens a database connection; leaving it dangling emits
        a ResourceWarning that surfaces against whichever unrelated test the
        garbage collector happens to run in.
        """
        client = TelegramClient(str(tmp_path / "probe"), 12345, API_HASH_PLACEHOLDER)
        try:
            yield client
        finally:
            client.session.close()

    def test_client_constructs_and_keeps_a_placeholder_hash(self, tmp_path: Path) -> None:
        with self._probe_client(tmp_path) as client:
            assert client.api_hash == API_HASH_PLACEHOLDER

    def test_connection_init_request_carries_api_id_but_no_hash(self, tmp_path: Path) -> None:
        """InitConnectionRequest is what actually goes out on every connect."""
        with self._probe_client(tmp_path) as client:
            init = client._init_request
            assert init.api_id == 12345
            assert not hasattr(init, "api_hash")
            assert API_HASH_PLACEHOLDER not in repr(init)

    def test_constructor_still_rejects_an_empty_hash(self, tmp_path: Path) -> None:
        """The reason a placeholder is needed at all, rather than None or ''."""
        with pytest.raises(ValueError):
            TelegramClient(str(tmp_path / "probe"), 12345, "")


class TestConfigSessionPathOverride:
    """Tests for the TG_SESSION_PATH override.

    Without it every account needs its own working directory, because the
    default path is cwd-relative — that is what stops one process tree from
    serving several Telegram accounts.
    """

    def test_env_var_overrides_default(self) -> None:
        """TG_SESSION_PATH replaces the cwd-relative default verbatim."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "h",
                                     "TG_SESSION_PATH": "/data/tg/alice.session"}):
            config = Config.from_env()
        assert config.session_path == Path("/data/tg/alice.session")

    def test_env_var_without_extension_is_kept_as_is(self) -> None:
        """No extension is invented: Telethon appends `.session` when missing."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "h",
                                     "TG_SESSION_PATH": "/data/tg/alice"}):
            config = Config.from_env()
        assert config.session_path == Path("/data/tg/alice")

    def test_blank_env_var_falls_back_to_default(self) -> None:
        """A blank value means "not configured", not "the current directory"."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "h",
                                     "TG_SESSION_PATH": "   "}):
            config = Config.from_env()
        assert config.session_path == Path.cwd() / ".telegram-mcp" / "session"

    def test_tilde_is_expanded(self) -> None:
        """`~` is expanded — the value often comes from a shell-written config."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "h",
                                     "TG_SESSION_PATH": "~/tg/bob.session"}):
            config = Config.from_env()
        assert config.session_path == Path.home() / "tg" / "bob.session"

    def test_session_dir_follows_the_override(self) -> None:
        """session_dir() is what `auth` mkdir's — it must track the override."""
        with patch.dict(os.environ, {"TG_APP_ID": "1", "TG_API_HASH": "h",
                                     "TG_SESSION_PATH": "/data/tg/alice.session"}):
            config = Config.from_env()
        assert config.session_dir() == Path("/data/tg")


class TestConfigSessionDir:
    """Tests for Config.session_dir()."""

    def test_session_dir_returns_parent_of_session_path(self) -> None:
        """
        session_dir() returns the parent directory of session_path.
        """
        config = Config(
            app_id=12345,
            api_hash="fakehash",
            session_path=Path("/some/dir/.telegram-mcp/session"),
        )
        assert config.session_dir() == Path("/some/dir/.telegram-mcp")


class TestConfigIsFrozen:
    """Tests that Config is a frozen dataclass."""

    def test_config_is_frozen(self) -> None:
        """
        Config is a frozen dataclass — mutation raises FrozenInstanceError.
        """
        config = Config(
            app_id=12345,
            api_hash="fakehash",
            session_path=Path("/tmp/session"),
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            config.app_id = 99999  # type: ignore[misc]

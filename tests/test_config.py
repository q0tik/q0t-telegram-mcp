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
from pathlib import Path
from unittest.mock import patch

import pytest

from tg_mcp.config import Config


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

    def test_from_env_raises_when_api_hash_missing(self) -> None:
        """
        Config.from_env() raises EnvironmentError when TG_API_HASH is not set.
        """
        env_without_hash = {k: v for k, v in os.environ.items() if k != "TG_API_HASH"}
        env_without_hash["TG_APP_ID"] = "12345"
        with patch.dict(os.environ, env_without_hash, clear=True):
            with pytest.raises(EnvironmentError, match="TG_API_HASH"):
                Config.from_env()

    def test_from_env_session_path_uses_cwd(self) -> None:
        """
        Config.from_env() sets session_path to <cwd>/.telegram-mcp/session.
        """
        with patch.dict(os.environ, {"TG_APP_ID": "12345", "TG_API_HASH": "hash",
                                     "TG_SESSION_PATH": ""}):
            config = Config.from_env()
        expected = Path.cwd() / ".telegram-mcp" / "session"
        assert config.session_path == expected


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

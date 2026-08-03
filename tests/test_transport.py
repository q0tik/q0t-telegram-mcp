"""
Tests for transport selection (src/tg_mcp/config.py :: TransportConfig).

Contracts verified:
  - stdio is the default, so a 0.7.x config that invokes the package with no
    flags keeps working unchanged
  - TG_TRANSPORT / TG_HOST / TG_PORT are honoured, and an explicit flag beats
    the environment
  - the listen address defaults to loopback: this server exposes a full
    Telegram account and has no authentication of its own
  - bad values fail loudly at startup rather than at first connection
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tg_mcp.config import DEFAULT_HTTP_HOST, DEFAULT_HTTP_PORT, TransportConfig


def _resolve(env: dict[str, str], **flags) -> TransportConfig:
    with patch.dict(os.environ, env, clear=True):
        return TransportConfig.resolve(**flags)


class TestDefaults:
    def test_default_is_stdio(self) -> None:
        """Backwards compatibility: existing configs run the package bare."""
        assert _resolve({}).transport == "stdio"

    def test_default_host_is_loopback(self) -> None:
        """The server has no auth of its own and grants full account access."""
        assert _resolve({}).host == DEFAULT_HTTP_HOST
        assert DEFAULT_HTTP_HOST == "127.0.0.1"

    def test_default_port(self) -> None:
        assert _resolve({}).port == DEFAULT_HTTP_PORT

    def test_stdio_is_not_an_http_transport(self) -> None:
        assert _resolve({}).is_http is False


class TestEnvironment:
    def test_transport_from_env(self) -> None:
        assert _resolve({"TG_TRANSPORT": "http"}).transport == "http"

    def test_host_and_port_from_env(self) -> None:
        cfg = _resolve({"TG_TRANSPORT": "http", "TG_HOST": "0.0.0.0", "TG_PORT": "9001"})
        assert (cfg.host, cfg.port) == ("0.0.0.0", 9001)

    def test_transport_is_case_insensitive(self) -> None:
        assert _resolve({"TG_TRANSPORT": "HTTP"}).transport == "http"

    def test_blank_env_falls_back_to_default(self) -> None:
        """Blank means "not configured" — the same rule TG_SESSION_PATH uses."""
        cfg = _resolve({"TG_TRANSPORT": "   ", "TG_HOST": "", "TG_PORT": ""})
        assert cfg.transport == "stdio"
        assert cfg.host == DEFAULT_HTTP_HOST
        assert cfg.port == DEFAULT_HTTP_PORT


class TestFlagsBeatEnvironment:
    def test_flag_transport_wins(self) -> None:
        cfg = _resolve({"TG_TRANSPORT": "stdio"}, transport="http")
        assert cfg.transport == "http"

    def test_flag_host_and_port_win(self) -> None:
        cfg = _resolve({"TG_HOST": "10.0.0.1", "TG_PORT": "1111"},
                       transport="http", host="127.0.0.1", port=2222)
        assert (cfg.host, cfg.port) == ("127.0.0.1", 2222)

    def test_omitted_flags_leave_env_alone(self) -> None:
        cfg = _resolve({"TG_TRANSPORT": "http", "TG_PORT": "9001"}, host="127.0.0.5")
        assert (cfg.transport, cfg.host, cfg.port) == ("http", "127.0.0.5", 9001)


class TestAliases:
    def test_streamable_http_is_accepted_as_http(self) -> None:
        """The MCP spec calls it "streamable-http"; the flag calls it "http".
        Accept both so a user copying either spelling gets what they meant."""
        assert _resolve({"TG_TRANSPORT": "streamable-http"}).transport == "http"

    def test_underscore_spelling_is_accepted_as_http(self) -> None:
        """`streamable_http` is what a shell-quoting mistake tends to produce."""
        assert _resolve({"TG_TRANSPORT": "streamable_http"}).transport == "http"

    def test_sse_is_supported(self) -> None:
        cfg = _resolve({}, transport="sse")
        assert cfg.transport == "sse"
        assert cfg.is_http is True


class TestValidation:
    def test_unknown_transport_is_rejected(self) -> None:
        """Failing at startup beats appearing to start and serving nothing."""
        with pytest.raises(ValueError, match="TG_TRANSPORT"):
            _resolve({"TG_TRANSPORT": "carrier-pigeon"})

    def test_non_numeric_port_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="TG_PORT"):
            _resolve({"TG_PORT": "eight thousand"})

    @pytest.mark.parametrize("port", ["0", "-1", "65536", "99999"])
    def test_out_of_range_port_is_rejected(self, port: str) -> None:
        with pytest.raises(ValueError, match="TG_PORT"):
            _resolve({"TG_PORT": port})


class TestLoopbackDetection:
    """Whether the chosen address exposes the account beyond this machine."""

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
    def test_loopback_hosts(self, host: str) -> None:
        assert _resolve({}, transport="http", host=host).is_loopback is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
    def test_non_loopback_hosts(self, host: str) -> None:
        assert _resolve({}, transport="http", host=host).is_loopback is False

    def test_a_non_loopback_host_is_allowed_not_blocked(self) -> None:
        """The user may have a reason; the README carries the warning. Refusing
        outright would just get worked around with a proxy."""
        cfg = _resolve({}, transport="http", host="0.0.0.0")
        assert cfg.host == "0.0.0.0"

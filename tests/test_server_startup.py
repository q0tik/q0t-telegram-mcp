"""
Tests for run_server()'s transport dispatch (src/tg_mcp/server.py).

Contracts verified:
  - no flags, no env → stdio, exactly as before 0.8.0
  - HTTP mode binds the resolved host/port and serves the streamable-HTTP app
  - a non-loopback bind is allowed but warned about, and is not then silently
    broken by DNS-rebinding protection
  - nothing secret is ever printed
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from tg_mcp.config import TransportConfig
from tg_mcp.server import mcp, run_server


class TestStdioIsDefault:
    def test_no_configuration_runs_stdio(self) -> None:
        """The 0.7.x invocation — `q0t-telegram-mcp` with no arguments."""
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(mcp, "run") as run, \
                patch("tg_mcp.server._serve_http") as serve_http:
            run_server()
        run.assert_called_once_with()
        serve_http.assert_not_called()

    def test_stdio_does_not_touch_network_settings(self) -> None:
        """Binding a port in stdio mode would be a surprising side effect."""
        before = (mcp.settings.host, mcp.settings.port)
        with patch.dict(os.environ, {}, clear=True), patch.object(mcp, "run"):
            run_server()
        assert (mcp.settings.host, mcp.settings.port) == before


class TestHttpMode:
    def test_http_serves_instead_of_running_stdio(self) -> None:
        with patch.dict(os.environ, {"TG_TRANSPORT": "http"}, clear=True), \
                patch.object(mcp, "run") as run, \
                patch("tg_mcp.server._serve_http") as serve_http:
            run_server()
        run.assert_not_called()
        serve_http.assert_called_once()

    def test_http_applies_host_and_port(self) -> None:
        cfg = TransportConfig(transport="http", host="127.0.0.1", port=8899)
        with patch("tg_mcp.server._serve_http"):
            run_server(cfg)
        assert (mcp.settings.host, mcp.settings.port) == ("127.0.0.1", 8899)

    def test_env_selects_http(self) -> None:
        env = {"TG_TRANSPORT": "http", "TG_HOST": "127.0.0.1", "TG_PORT": "8123"}
        with patch.dict(os.environ, env, clear=True), \
                patch("tg_mcp.server._serve_http") as serve_http:
            run_server()
        assert serve_http.call_args.args[1].port == 8123


class TestExposureWarning:
    def test_loopback_bind_is_silent(self, capsys: pytest.CaptureFixture) -> None:
        cfg = TransportConfig(transport="http", host="127.0.0.1", port=8765)
        with patch("tg_mcp.server._serve_http"):
            run_server(cfg)
        assert "WARNING" not in capsys.readouterr().err

    def test_non_loopback_bind_warns_on_stderr(self, capsys: pytest.CaptureFixture) -> None:
        """This server hands whoever reaches it full control of a Telegram
        account and authenticates nobody. Binding it off-host deserves to be
        said out loud."""
        cfg = TransportConfig(transport="http", host="0.0.0.0", port=8765)
        with patch("tg_mcp.server._serve_http"):
            run_server(cfg)
        captured = capsys.readouterr()
        assert "0.0.0.0" in captured.err
        assert captured.out == ""

    def test_non_loopback_bind_is_not_then_blocked(self) -> None:
        """FastMCP's DNS-rebinding protection only allows localhost Host headers
        by default. Warning about a bind and then rejecting every request to it
        would be the worst of both worlds — the user asked for this."""
        cfg = TransportConfig(transport="http", host="192.168.1.10", port=8765)
        with patch("tg_mcp.server._serve_http"):
            run_server(cfg)
        allowed = mcp.settings.transport_security.allowed_hosts
        assert any("192.168.1.10" in h for h in allowed)

    def test_loopback_leaves_protection_untouched(self) -> None:
        cfg = TransportConfig(transport="http", host="127.0.0.1", port=8765)
        with patch("tg_mcp.server._serve_http"):
            run_server(cfg)
        allowed = mcp.settings.transport_security.allowed_hosts
        assert not any("192.168" in h or "0.0.0.0" in h for h in allowed)


class TestNothingSecretIsPrinted:
    def test_startup_output_never_contains_credentials(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A server that prints its own api_hash on boot leaks it into every
        log aggregator the operator owns."""
        env = {
            "TG_APP_ID": "12345",
            "TG_API_HASH": "s3cr3tapihashvalue",
            "TG_SESSION_PATH": "/home/someone/tg/alice.session",
            "TG_TRANSPORT": "http",
            "TG_HOST": "0.0.0.0",
        }
        with patch.dict(os.environ, env, clear=True), \
                patch("tg_mcp.server._serve_http"):
            run_server()
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "s3cr3tapihashvalue" not in combined
        assert "alice.session" not in combined


class TestHealthEndpoint:
    async def test_health_reports_ok_and_version(self) -> None:
        from tg_mcp.server import health

        request = MagicMock()
        response = await health(request)
        assert response.status_code == 200
        import json

        payload = json.loads(response.body)
        assert payload["status"] == "ok"
        assert payload["name"] == "q0t-telegram-mcp"
        assert payload["version"]

    async def test_health_leaks_no_secrets(self) -> None:
        """It answers before any MCP session exists, so it must stay boring."""
        from tg_mcp.server import health

        env = {"TG_APP_ID": "1", "TG_API_HASH": "s3cr3tapihashvalue"}
        with patch.dict(os.environ, env, clear=True):
            response = await health(MagicMock())
        assert b"s3cr3tapihashvalue" not in response.body

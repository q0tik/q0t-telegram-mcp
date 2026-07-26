"""
Tests for TG_READONLY (src/tg_mcp/server.py).

Contracts verified:
  - read-only mode omits every state-changing tool from the registry entirely,
    rather than registering one that refuses at call time
  - the full set is still registered by default (0.6.x behaviour)
  - tools that change state non-obviously (tg_read marks messages read,
    tg_click fires a callback at a bot) count as writes
  - TG_READONLY parses 1/true/yes case-insensitively; anything else is off

The registry is asserted through the public list_tools() surface, because that
is exactly what an MCP client sees — a tool absent from it cannot be called.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from mcp.server.fastmcp import FastMCP

from tg_mcp.server import READ_TOOLS, WRITE_TOOLS, readonly_from_env, register_tools

ALL_TOOL_NAMES = {
    "tg_me", "tg_dialogs", "tg_dialog", "tg_search", "tg_download",
    "tg_send", "tg_send_file", "tg_forward", "tg_read", "tg_click",
    "tg_react", "tg_delete", "tg_edit",
}
READ_ONLY_NAMES = {"tg_me", "tg_dialogs", "tg_dialog", "tg_search", "tg_download"}
WRITE_NAMES = ALL_TOOL_NAMES - READ_ONLY_NAMES


async def _registered(readonly: bool) -> set[str]:
    server = FastMCP("test")
    register_tools(server, readonly=readonly)
    return {tool.name for tool in await server.list_tools()}


class TestToolPartition:
    def test_every_tool_is_classified_exactly_once(self) -> None:
        """A tool added later without a side picked would silently be dropped
        from both modes; this is the guard against that."""
        read = {fn.__name__ for fn in READ_TOOLS}
        write = {fn.__name__ for fn in WRITE_TOOLS}
        assert read & write == set()
        assert read | write == ALL_TOOL_NAMES

    def test_state_changing_reads_are_classified_as_writes(self) -> None:
        """tg_read marks messages read on the account; tg_click delivers a
        callback to a bot. Both act as the owner despite sounding passive."""
        write = {fn.__name__ for fn in WRITE_TOOLS}
        assert "tg_read" in write
        assert "tg_click" in write


class TestRegistration:
    async def test_full_mode_registers_everything(self) -> None:
        assert await _registered(readonly=False) == ALL_TOOL_NAMES

    async def test_readonly_mode_registers_only_reads(self) -> None:
        assert await _registered(readonly=True) == READ_ONLY_NAMES

    async def test_readonly_mode_omits_writes_entirely(self) -> None:
        """Absent, not present-and-refusing: the model must not be able to see
        a capability it is not allowed to use."""
        registered = await _registered(readonly=True)
        assert registered & WRITE_NAMES == set()


class TestReadonlyFromEnv:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES", "Yes"])
    def test_enabling_values(self, value: str) -> None:
        with patch.dict(os.environ, {"TG_READONLY": value}):
            assert readonly_from_env() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "   ", "maybe"])
    def test_everything_else_is_off(self, value: str) -> None:
        """Unrecognised values keep the full tool set — the 0.6.x default."""
        with patch.dict(os.environ, {"TG_READONLY": value}):
            assert readonly_from_env() is False

    def test_unset_is_off(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TG_READONLY"}
        with patch.dict(os.environ, env, clear=True):
            assert readonly_from_env() is False

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        with patch.dict(os.environ, {"TG_READONLY": " true \n"}):
            assert readonly_from_env() is True


class TestRunServerHonoursTheFlag:
    def test_run_server_registers_readonly_set_when_enabled(self) -> None:
        from tg_mcp.server import run_server

        server = FastMCP("test")
        with patch.dict(os.environ, {"TG_READONLY": "1"}), \
                patch("tg_mcp.server.mcp", server), \
                patch.object(server, "run"):
            run_server()
        names = {name for name in server._tool_manager._tools}
        assert names == READ_ONLY_NAMES

    def test_run_server_registers_everything_by_default(self) -> None:
        from tg_mcp.server import run_server

        server = FastMCP("test")
        env = {k: v for k, v in os.environ.items() if k != "TG_READONLY"}
        with patch.dict(os.environ, env, clear=True), \
                patch("tg_mcp.server.mcp", server), \
                patch.object(server, "run"):
            run_server()
        names = {name for name in server._tool_manager._tools}
        assert names == ALL_TOOL_NAMES

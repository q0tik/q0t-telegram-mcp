from __future__ import annotations

import asyncio

import click

from .auth import AuthFlow
from .config import Config
from .server import run_server


async def _run_auth(
    phone: str,
    password: str | None,
    app_id: int | None,
    api_hash: str | None,
) -> None:
    """Load config (args take priority over env vars) and run the authentication flow."""
    import os

    if app_id is not None:
        os.environ["TG_APP_ID"] = str(app_id)
    if api_hash is not None:
        os.environ["TG_API_HASH"] = api_hash
    config = Config.from_env()
    auth_flow = AuthFlow(config)
    await auth_flow.run(phone, password)


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """q0t-telegram-mcp: Telegram MCP server that actually sends messages."""
    if ctx.invoked_subcommand is None:
        run_server()


@cli.command()
@click.option("--phone", required=True, help="Your phone number with country code")
@click.option("--password", default=None, help="2FA password if enabled")
@click.option("--app-id", default=None, type=int, help="Telegram app_id (overrides TG_APP_ID env var)")
@click.option("--api-hash", default=None, help="Telegram api_hash (overrides TG_API_HASH env var)")
def auth(phone: str, password: str | None, app_id: int | None, api_hash: str | None) -> None:
    """Authenticate with Telegram and save session."""
    asyncio.run(_run_auth(phone, password, app_id, api_hash))


def main() -> None:
    """Entry point for the ``q0t-telegram-mcp`` command.

    Runs the MCP server when called with no arguments, or dispatches to
    a subcommand (e.g. ``auth``) when one is provided.
    """
    cli()

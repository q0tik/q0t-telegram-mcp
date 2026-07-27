from __future__ import annotations

import asyncio
import sys

import click

from .auth import AuthFlow
from .check import SessionChecker
from .config import Config, TransportConfig
from .server import run_server


async def _run_auth(
    phone: str,
    password: str | None,
    app_id: int | None,
    api_hash: str | None,
    session: str | None = None,
) -> None:
    """Load config (args take priority over env vars) and run the authentication flow."""
    import os

    if app_id is not None:
        os.environ["TG_APP_ID"] = str(app_id)
    if api_hash is not None:
        os.environ["TG_API_HASH"] = api_hash
    if session is not None:
        os.environ["TG_SESSION_PATH"] = session
    # require_api_hash: signing in is the only operation that puts the hash on
    # the wire, so this is the one command that cannot fall back to the
    # placeholder the server uses.
    config = _config_or_exit(require_api_hash=True)
    auth_flow = AuthFlow(config)
    await auth_flow.run(phone, password)


def _config_or_exit(*, require_api_hash: bool) -> Config:
    """Build the Config, or report the missing variable and exit 1.

    Matches how auth.py reports login failures: a plain one-line message and a
    non-zero exit, rather than a traceback the user has to read past.
    """
    try:
        return Config.from_env(require_api_hash=require_api_hash)
    except EnvironmentError as e:
        print(f"Error: {e}")
        sys.exit(1)


@click.group(invoke_without_command=True)
@click.option("--transport", type=click.Choice(["stdio", "http", "streamable-http", "sse"]),
              default=None,
              help="How to serve MCP. Default stdio (overrides TG_TRANSPORT). "
                   "Use http to let several clients share one process and one "
                   "Telegram session.")
@click.option("--host", default=None,
              help="Address to bind in http/sse mode (overrides TG_HOST); "
                   "default 127.0.0.1. Binding off-loopback exposes the account.")
@click.option("--port", default=None, type=int,
              help="Port to bind in http/sse mode (overrides TG_PORT); default 8765")
@click.pass_context
def cli(ctx: click.Context, transport: str | None, host: str | None,
        port: int | None) -> None:
    """q0t-telegram-mcp: Telegram MCP server that actually sends messages."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        config = TransportConfig.resolve(transport=transport, host=host, port=port)
    except ValueError as e:
        # Same shape as the other configuration errors: a line, not a traceback.
        print(f"Error: {e}")
        sys.exit(1)
    run_server(config)


@cli.command()
@click.option("--phone", required=True, help="Your phone number with country code")
@click.option("--password", default=None, help="2FA password if enabled")
@click.option("--app-id", default=None, type=int, help="Telegram app_id (overrides TG_APP_ID env var)")
@click.option("--api-hash", default=None, help="Telegram api_hash (overrides TG_API_HASH env var)")
@click.option("--session", default=None,
              help="Session file path (overrides TG_SESSION_PATH env var); "
                   "default is .telegram-mcp/session under the current directory")
def auth(phone: str, password: str | None, app_id: int | None, api_hash: str | None,
         session: str | None) -> None:
    """Authenticate with Telegram and save session."""
    asyncio.run(_run_auth(phone, password, app_id, api_hash, session))


async def _run_check(
    app_id: int | None,
    api_hash: str | None,
    session: str | None,
    as_json: bool,
) -> int:
    """Check the session and print the verdict; return the process exit code."""
    import os

    if app_id is not None:
        os.environ["TG_APP_ID"] = str(app_id)
    if api_hash is not None:
        os.environ["TG_API_HASH"] = api_hash
    if session is not None:
        os.environ["TG_SESSION_PATH"] = session
    # No require_api_hash: checking never signs in, so it needs the hash no
    # more than the server does.
    config = _config_or_exit(require_api_hash=False)
    result = await SessionChecker(config).run()
    print(result.to_json() if as_json else result.human())
    return result.exit_code


@cli.command()
@click.option("--app-id", default=None, type=int, help="Telegram app_id (overrides TG_APP_ID env var)")
@click.option("--api-hash", default=None, help="Telegram api_hash (overrides TG_API_HASH env var; not required)")
@click.option("--session", default=None,
              help="Session file path (overrides TG_SESSION_PATH env var); "
                   "default is .telegram-mcp/session under the current directory")
@click.option("--json", "as_json", is_flag=True, help="Emit the result as JSON")
def check(app_id: int | None, api_hash: str | None, session: str | None, as_json: bool) -> None:
    """Check whether the saved session is still authorized.

    Exit codes: 0 authorized, 1 not authorized, 2 could not determine (no
    session file, or Telegram unreachable).

    Makes a real network call, so Telegram records it as device activity.
    """
    sys.exit(asyncio.run(_run_check(app_id, api_hash, session, as_json)))


def main() -> None:
    """Entry point for the ``q0t-telegram-mcp`` command.

    Runs the MCP server when called with no arguments, or dispatches to
    a subcommand (e.g. ``auth``) when one is provided.
    """
    cli()

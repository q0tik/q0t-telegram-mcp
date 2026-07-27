from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path

#: Stand-in for ``TG_API_HASH`` when the process will never sign in.
#:
#: Telethon's client constructor rejects an empty or ``None`` api_hash
#: (``telegrambaseclient.py:273``) but never validates its *shape*, and only
#: the three login requests ever put it on the wire — see the rationale in
#: ``TestTelethonPlaceholderAssumption``. Something non-empty is therefore
#: required, and it should read unmistakably as "absent" wherever it surfaces:
#: tracebacks, debug dumps, ``ps`` output.
API_HASH_PLACEHOLDER = "placeholder-api-hash-this-process-never-logs-in"


#: Loopback only. This server exposes a full Telegram account and carries no
#: authentication of its own, so the default must never be reachable off-host.
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765

#: Public spelling -> the transport name FastMCP expects.
_TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "http": "streamable-http",
    # The MCP spec's own name for it; accept it so either spelling works.
    "streamable-http": "streamable-http",
    "streamable_http": "streamable-http",
    "sse": "sse",
}


@dataclass(frozen=True)
class TransportConfig:
    """How the MCP server should listen.

    stdio is the default and behaves exactly as it did before 0.8.0: one
    process per client, spawned by the client itself. HTTP exists because that
    model breaks down when two clients run at once — see
    :class:`~tg_mcp.server.SharedTelegramClient`.
    """

    transport: str
    host: str
    port: int

    @property
    def is_http(self) -> bool:
        """True for any network transport (as opposed to stdio)."""
        return self.transport in ("http", "sse")

    @property
    def fastmcp_transport(self) -> str:
        """The transport name to hand to ``FastMCP.run``."""
        return _TRANSPORT_ALIASES[self.transport]

    @property
    def is_loopback(self) -> bool:
        """True when the listen address is unreachable from other machines."""
        host = self.host.strip().lower().strip("[]")
        if host in ("localhost",):
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            # A hostname we cannot classify; assume it is routable and warn.
            return False

    @property
    def url(self) -> str:
        """The MCP endpoint clients should be pointed at."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/mcp"

    @classmethod
    def resolve(
        cls,
        *,
        transport: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> TransportConfig:
        """Resolve transport settings from flags, then environment, then defaults.

        Explicit arguments win over ``TG_TRANSPORT``/``TG_HOST``/``TG_PORT``,
        which in turn win over the defaults. Blank environment values count as
        unset, matching :meth:`Config._session_path_from_env`.

        Raises:
            ValueError: On an unknown transport or an unusable port. Startup is
                the right place to notice: a server that starts and then serves
                nothing is far harder to diagnose.
        """
        raw_transport = transport if transport is not None else (
            os.environ.get("TG_TRANSPORT") or ""
        )
        # Strip before the fallback: a whitespace-only value is truthy but means
        # "not configured", just like a blank one.
        name = (raw_transport or "").strip().lower() or "stdio"
        if name not in _TRANSPORT_ALIASES:
            raise ValueError(
                f"TG_TRANSPORT: unknown transport {name!r}. "
                f"Expected one of: {', '.join(sorted(_TRANSPORT_ALIASES))}"
            )
        # Normalise the spec spelling onto the public one.
        if _TRANSPORT_ALIASES[name] == "streamable-http":
            name = "http"

        resolved_host = host if host is not None else (
            (os.environ.get("TG_HOST") or "").strip() or DEFAULT_HTTP_HOST
        )

        if port is not None:
            resolved_port = port
        else:
            raw_port = (os.environ.get("TG_PORT") or "").strip()
            if not raw_port:
                resolved_port = DEFAULT_HTTP_PORT
            else:
                try:
                    resolved_port = int(raw_port)
                except ValueError:
                    raise ValueError(f"TG_PORT: {raw_port!r} is not a number") from None
        if not 1 <= resolved_port <= 65535:
            raise ValueError(f"TG_PORT: {resolved_port} is out of range (1-65535)")

        return cls(transport=name, host=resolved_host, port=resolved_port)


@dataclass(frozen=True)
class Config:
    """Immutable configuration for the Telegram MCP server.

    Holds API credentials and the path to the Telethon session file.
    Construct via :meth:`from_env` rather than directly.
    """

    app_id: int
    api_hash: str
    session_path: Path

    @classmethod
    def from_env(cls, *, require_api_hash: bool = False) -> Config:
        """Build a Config from environment variables.

        Reads ``TG_APP_ID`` and ``TG_API_HASH`` from the process environment.
        The session path comes from ``TG_SESSION_PATH`` when set, and falls
        back to ``<cwd>/.telegram-mcp/session`` otherwise.

        ``TG_SESSION_PATH`` names the session *file*; Telethon appends the
        ``.session`` extension when it is missing, so ``/data/alice`` and
        ``/data/alice.session`` address the same file.  Set it to run several
        servers — one per Telegram account — from a single working directory,
        which the cwd-relative default cannot express.

        ``TG_APP_ID`` is always required: it travels on every connection inside
        ``InitConnectionRequest``. ``TG_API_HASH`` is required only when
        *require_api_hash* is set, which the ``auth`` command does — signing in
        is the only thing that sends the hash to Telegram. Serving an existing
        session does not, so a server may run without the secret half of the
        credentials and gets :data:`API_HASH_PLACEHOLDER` instead.

        A blank or whitespace-only value counts as "not set", matching
        :meth:`_session_path_from_env`.

        Args:
            require_api_hash: Fail when ``TG_API_HASH`` is absent instead of
                substituting the placeholder.

        Raises:
            EnvironmentError: If ``TG_APP_ID`` is not set, or ``TG_API_HASH``
                is not set while *require_api_hash* is true.
        """
        app_id_str = (os.environ.get("TG_APP_ID") or "").strip()
        api_hash = (os.environ.get("TG_API_HASH") or "").strip()
        if not app_id_str:
            raise EnvironmentError("TG_APP_ID environment variable is not set")
        if not api_hash:
            if require_api_hash:
                raise EnvironmentError(
                    "TG_API_HASH environment variable is not set. "
                    "It is required to sign in; get it from https://my.telegram.org"
                )
            api_hash = API_HASH_PLACEHOLDER
        return cls(
            app_id=int(app_id_str),
            api_hash=api_hash,
            session_path=cls._session_path_from_env(),
        )

    @property
    def has_real_api_hash(self) -> bool:
        """True when a configured hash is present rather than the placeholder."""
        return self.api_hash != API_HASH_PLACEHOLDER

    @staticmethod
    def _session_path_from_env() -> Path:
        """Resolve the session path, honouring ``TG_SESSION_PATH``.

        An unset *or blank* variable falls back to the cwd-relative default:
        an empty string in the environment is how "not configured" usually
        reaches a container, and treating it as a path would point the session
        at the current directory itself.
        """
        override = (os.environ.get("TG_SESSION_PATH") or "").strip()
        if not override:
            return Path.cwd() / ".telegram-mcp" / "session"
        return Path(override).expanduser()

    def session_dir(self) -> Path:
        """Return the directory that contains the session file."""
        return self.session_path.parent

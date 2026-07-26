from __future__ import annotations

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
    def from_env(cls, *, require_api_hash: bool = False) -> "Config":
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

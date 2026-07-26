from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    def from_env(cls) -> "Config":
        """Build a Config from environment variables.

        Reads ``TG_APP_ID`` and ``TG_API_HASH`` from the process environment.
        The session path comes from ``TG_SESSION_PATH`` when set, and falls
        back to ``<cwd>/.telegram-mcp/session`` otherwise.

        ``TG_SESSION_PATH`` names the session *file*; Telethon appends the
        ``.session`` extension when it is missing, so ``/data/alice`` and
        ``/data/alice.session`` address the same file.  Set it to run several
        servers — one per Telegram account — from a single working directory,
        which the cwd-relative default cannot express.

        Raises:
            EnvironmentError: If ``TG_APP_ID`` or ``TG_API_HASH`` is not set.
        """
        app_id_str = os.environ.get("TG_APP_ID")
        api_hash = os.environ.get("TG_API_HASH")
        if not app_id_str:
            raise EnvironmentError("TG_APP_ID environment variable is not set")
        if not api_hash:
            raise EnvironmentError("TG_API_HASH environment variable is not set")
        return cls(
            app_id=int(app_id_str),
            api_hash=api_hash,
            session_path=cls._session_path_from_env(),
        )

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

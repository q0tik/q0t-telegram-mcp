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

        Reads ``TG_APP_ID`` and ``TG_API_HASH`` from the process environment
        and resolves the session path to ``<cwd>/.telegram-mcp/session``.

        Raises:
            EnvironmentError: If ``TG_APP_ID`` or ``TG_API_HASH`` is not set.
        """
        app_id_str = os.environ.get("TG_APP_ID")
        api_hash = os.environ.get("TG_API_HASH")
        if not app_id_str:
            raise EnvironmentError("TG_APP_ID environment variable is not set")
        if not api_hash:
            raise EnvironmentError("TG_API_HASH environment variable is not set")
        session_path = Path.cwd() / ".telegram-mcp" / "session"
        return cls(app_id=int(app_id_str), api_hash=api_hash, session_path=session_path)

    def session_dir(self) -> Path:
        """Return the directory that contains the session file."""
        return self.session_path.parent

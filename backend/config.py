"""Runtime configuration. Owns the bind rule (locked in plan §1, §11)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("./data"))
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)
    allow_remote: bool = Field(default=False)
    log_level: str = Field(default="info")

    # Shared secret for X-Ingest-Secret header on ingest endpoints.
    # Required at runtime; empty default means "not configured".
    # Extension enters this once via popup; backend refuses ingest calls when empty.
    ingest_secret: str = Field(default="")

    # Storage exhaustion guard (consensus R7). When data/media/ exceeds
    # 80 % of this threshold, next heartbeat pauses the extension.
    max_media_gb: float = Field(default=50.0)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def media_tmp_dir(self) -> Path:
        return self.media_dir / ".tmp"

    @property
    def migrations_dir(self) -> Path:
        return Path(__file__).resolve().parent / "db" / "migrations"

    def assert_bind_allowed(self) -> None:
        """Locked rule (plan §1, §11): refuse non-loopback unless ALLOW_REMOTE=1.

        Called from `backend.main` during lifespan startup before uvicorn
        has finished binding so a misconfigured server fails loudly.
        """
        if self.host != "127.0.0.1" and not self.allow_remote:
            raise RuntimeError(
                f"Refusing to bind to {self.host!r}: only 127.0.0.1 is allowed "
                "by default. Set ALLOW_REMOTE=1 to override."
            )


settings = Settings()

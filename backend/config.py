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

    # Which IG client to use: "real" (instagrapi) or "fake" (offline fixture).
    # Default real; tests + `just sync-fake` flip it to "fake".
    ig_client: str = Field(default="real")

    # Sync cadence is unused in the slice but kept here so B4 has a single
    # source of truth.
    sync_interval_hours: int = Field(default=24)
    sync_jitter_hours: int = Field(default=1)

    # Intra-run pacing knobs (ignored when ig_client="fake"). Conservative
    # defaults: ~1–3s between posts and ~0.5–1.5s between media fetches keep
    # us well under any plausible IG rate limit while still finishing a
    # ~1000-post backfill inside a few hours when uncapped.
    ig_sync_per_post_delay_min: float = Field(default=1.0)
    ig_sync_per_post_delay_max: float = Field(default=3.0)
    ig_sync_per_media_delay_min: float = Field(default=0.5)
    ig_sync_per_media_delay_max: float = Field(default=1.5)
    # Cap on `posts_new` per run during initial backfill. None = unbounded.
    # When the cap trips, fully_enumerated is forced False so the unsaved
    # sweep does not fire on a partial enumeration.
    ig_sync_max_new_posts_per_run: int | None = Field(default=50)

    # instagrapi best-practice device pinning. Match these to whatever
    # the IG mobile app shows for the account's normal environment;
    # mismatched locale/country across logins increases the chance of a
    # device-fingerprint flag. None = leave instagrapi's defaults in
    # place. See https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html
    ig_device_country: str | None = Field(default=None)        # e.g. "US", "KR"
    ig_device_country_code: int | None = Field(default=None)   # e.g. 1, 82
    ig_device_locale: str | None = Field(default=None)         # e.g. "en_US", "ko_KR"
    ig_device_timezone_offset: int | None = Field(default=None)  # seconds, e.g. 32400 for KST

    # Built-in instagrapi inter-API-call jitter (recommended [1, 3]).
    ig_client_delay_min: float = Field(default=1.0)
    ig_client_delay_max: float = Field(default=3.0)

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
    def settings_file(self) -> Path:
        return self.data_dir / "instagrapi_settings.json"

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

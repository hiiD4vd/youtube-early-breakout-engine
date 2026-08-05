from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by FastAPI and Celery."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql://ycgc:ycgc_local_password@localhost:5433/ycgc"
    redis_url: str = "redis://localhost:6380/0"
    environment: str = "development"
    cors_origins: str = "http://localhost:3000"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"

    # Phase 1: anonymous YouTube Shorts discovery. These are deliberately not
    # topic inputs: region/language only model an ordinary logged-out session.
    youtube_region: str = "US"
    youtube_language: str = "en"
    youtube_profiles: str = "ID:id,US:en,GB:en"
    youtube_http_timeout_seconds: float = 20.0
    youtube_seed_pages_per_run: int = 20
    youtube_seed_limit_per_run: int = 200
    youtube_sessions_per_profile: int = 2
    youtube_max_sessions_per_profile: int = 3
    youtube_seed_target_candidates_per_profile: int = 50
    youtube_seed_pages_per_session: int = 20
    youtube_seed_limit_per_session: int = 100
    youtube_seed_ttl_seconds: int = 86_400
    youtube_seed_max_age_hours: int = 24
    youtube_seed_interval_minutes: int = 30
    youtube_velocity_interval_minutes: int = 15
    youtube_velocity_min_observation_minutes: int = 30
    youtube_fast_poll_max_age_hours: int = 12
    youtube_ultra_fresh_max_age_hours: int = 6
    youtube_ultra_fresh_poll_minutes: int = 15
    youtube_fast_poll_minutes: int = 30
    youtube_mature_poll_minutes: int = 60
    youtube_velocity_lock_seconds: int = 1_500
    youtube_breakout_min_view_delta: int = 1_000
    youtube_breakout_min_velocity_per_hour: float = 1_000.0
    youtube_early_min_velocity_per_hour: float = 250.0
    youtube_rising_min_velocity_per_hour: float = 500.0
    youtube_breakout_lock_seconds: int = 3_600
    youtube_media_max_attempts: int = 6
    media_root: str = "data/media"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def youtube_profile_list(self) -> list[tuple[str, str]]:
        profiles = []
        for raw in self.youtube_profiles.split(","):
            region, _, language = raw.strip().partition(":")
            if len(region) == 2 and language:
                profiles.append((region.upper(), language.lower()))
        return profiles[:3] or [(self.youtube_region, self.youtube_language)]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

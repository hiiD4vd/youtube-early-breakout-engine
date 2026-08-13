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
    youtube_data_api_key: str = ""
    channel_context_history_limit: int = 12
    whale_subscriber_threshold: int = 1_000_000
    whale_median_view_threshold: int = 5_000_000
    proven_winner_median_view_threshold: int = 500_000
    established_subscriber_threshold: int = 10_000
    established_median_view_threshold: int = 20_000

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
    youtube_relative_scoring_enabled: bool = False
    youtube_relative_min_samples: int = 30
    youtube_relative_early_percentile: float = 80.0
    youtube_relative_rising_percentile: float = 92.0
    youtube_relative_breakout_percentile: float = 97.0
    youtube_early_min_velocity_per_hour: float = 250.0
    youtube_rising_min_velocity_per_hour: float = 500.0
    youtube_breakout_lock_seconds: int = 3_600
    youtube_media_max_attempts: int = 6
    media_root: str = "data/media"

    # Topic Trends is a post-signal layer. It never changes anonymous discovery.
    topic_trends_interval_minutes: int = 15
    topic_feature_interval_minutes: int = 5
    topic_trends_snapshot_interval_minutes: int = 15
    topic_lexical_similarity_threshold: float = 0.42
    topic_trends_live_window_hours: int = 24
    topic_trends_min_emerging_videos: int = 2
    topic_trends_min_emerging_channels: int = 2
    topic_embedding_storage: str = "jsonb"  # migrate to pgvector only after a safe DB image upgrade
    topic_accelerating_min_videos: int = 3
    topic_accelerating_min_channels: int = 2
    topic_confirmed_min_videos: int = 4
    topic_confirmed_min_channels: int = 3
    topic_cooling_after_hours: int = 6
    topic_velocity_reference_per_hour: float = 30_000.0
    # Early Topic Signals are channel-neutral. A video qualifies as evidence
    # only by the state in which we first saw it: fresh and still small.
    early_topic_max_entry_views: int = 250_000
    # Admission remains strict: a Short must be first observed in its first
    # day. Once admitted, the topic itself has a 72-hour lifecycle.
    early_topic_max_entry_age_hours: int = 24
    early_topic_fresh_phase_hours: int = 24
    early_topic_rising_phase_hours: int = 48
    early_topic_lifecycle_hours: int = 72

    # Retry enrichment from Redis independently of the 24-hour seed lifetime.
    youtube_enrichment_retry_interval_minutes: int = 10
    youtube_enrichment_retry_lock_seconds: int = 540

    # Broad Market Trends is isolated from Early Breakouts. It intentionally
    # includes large/news channels but exposes source provenance and coverage.
    market_trends_enabled: bool = True
    market_trends_interval_minutes: int = 10
    # Market coverage is intentionally independent from Early Breakout
    # profiles. Adding a region increases total collection work; it never
    # divides the target of an existing region.
    market_trends_regions: str = "ID,US,GB,JP,BR,IN,MX"
    market_trends_profiles: str = "ID:id,US:en,GB:en,JP:ja,BR:pt,IN:hi,MX:es"
    market_trends_chart_categories: str = "0,10,24,17"
    market_trends_max_results: int = 50  # per region x category request
    # General YouTube Video Trends is a separate product lane modelled after
    # country chart trackers.  It never feeds Short-only discovery, Early
    # Signals, or the semantic Short-topic ranker.
    #
    # YouTube itself publishes the supported country list.  We cache that
    # list, choose up to this target, then rotate a small fair slice each run.
    # Four regions every ten minutes is 576 `videos.list` calls/day for this
    # lane and completes a 110-country pass in roughly 4 h 35 min.
    market_general_chart_enabled: bool = True
    market_general_chart_target_regions: int = 110
    market_general_chart_regions_per_run: int = 4
    market_general_chart_interval_minutes: int = 10
    market_general_chart_max_results: int = 50
    market_general_chart_catalog_ttl_seconds: int = 604_800
    # Official latest-video lane. It supplements (not replaces) the
    # anonymous Shorts feed and public chart. There is deliberately no query
    # keyword: each region gets its own independent latest sample.
    market_latest_enabled: bool = True
    market_latest_interval_minutes: int = 120
    market_latest_window_hours: int = 168
    market_latest_results_per_region: int = 50
    # Market metadata analysis is intentionally separate from the 24-hour
    # Early Breakout seed policy.
    market_metadata_window_hours: int = 168
    # A topic can retain history, but its public ranking is powered only by
    # Shorts published inside this current-market window.
    market_topic_active_video_max_age_hours: int = 168
    # The public seven-day ranking decays older evidence so accumulated views
    # cannot outrank genuine movement from the last 24–72 hours.
    market_topic_full_weight_hours: int = 24
    market_topic_strong_weight_hours: int = 72
    market_topic_supporting_weight_hours: int = 120
    # A cooled topic becomes archive-only after this long. It is never
    # deleted: archived evidence remains usable for audit and learning.
    market_topic_archive_after_hours: int = 336
    market_feed_pages_per_region: int = 20
    market_feed_target_per_region: int = 100
    # Several clean, logged-out cohorts reduce dependence on one feed path.
    # They are neutral sessions, not personas trained on topics or creators.
    market_feed_cohorts_per_region: int = 3
    market_feed_max_cohorts_per_region: int = 4
    # The anonymous reel feed is the primary Shorts-only discovery lane.  It
    # has its own cadence, independent of the slower public-chart polling.
    market_feed_interval_minutes: int = 5
    market_shorts_verify_interval_minutes: int = 5
    market_shorts_verify_batch_size: int = 48
    market_shorts_verify_workers: int = 6
    # Reserved only; no third-party source is active until explicitly enabled.
    apify_enabled: bool = False
    apify_token: str = ""
    apify_actor_id: str = ""
    # Apify expands the independent channel panel; it is supplemental, not
    # the source that decides whether a video is a Short.
    apify_interval_minutes: int = 30
    apify_channels_per_run: int = 10
    apify_shorts_per_channel: int = 10
    # Each region gets its own channel quota; adding a region never splits an
    # existing region's collection target.
    apify_channels_per_region: int = 3
    # The selected Actor validates a bounded number of channel URLs per run.
    # A collection cycle can be larger; it is split into these safe batches.
    apify_max_channels_per_request: int = 5
    apify_timeout_seconds: int = 300
    # Semantic enrichment is isolated from discovery. Fresh Shorts are always
    # served first; bounded parallel requests clear backlog without flooding
    # the provider when it is temporarily unavailable.
    market_gemini_batch_size: int = 36
    market_gemini_interval_minutes: int = 5
    market_semantic_concurrency: int = 6
    # Market semantic extraction is provider-agnostic. The default gateway
    # is OpenAI-compatible and intentionally replaces Gemini Flash here.
    market_semantic_base_url: str = ""
    market_semantic_api_key: str = ""
    market_semantic_model: str = "gpt-5.6"
    # Optional admin token to protect runtime admin endpoints. When empty, admin
    # endpoints remain unguarded for local development convenience.
    admin_api_token: str = ""
    # A stronger model is used only after multiple Shorts already form one
    # candidate conversation. It is intentionally not used per video.
    market_topic_review_model: str = "gpt-5.6"
    market_topic_review_batch_size: int = 4
    # Content truth checks are triggered only for candidate events. They are
    # deliberately bounded because each one may fetch captions and use vision.
    market_content_truth_batch_size: int = 4
    market_content_truth_interval_minutes: int = 15
    external_benchmark_interval_minutes: int = 60

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

    @property
    def market_profile_list(self) -> list[tuple[str, str]]:
        profiles = []
        for raw in self.market_trends_profiles.split(","):
            region, _, language = raw.strip().partition(":")
            if len(region) == 2 and language:
                profiles.append((region.upper(), language.lower()))
        return profiles or [(self.youtube_region, self.youtube_language)]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

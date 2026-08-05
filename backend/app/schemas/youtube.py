from datetime import datetime

from pydantic import BaseModel, Field


class YoutubeSeed(BaseModel):
    """The complete transient record stored in Redis for a discovered Short."""

    video_id: str = Field(min_length=1, max_length=32)
    channel_id: str = Field(min_length=1, max_length=64)
    channel_title: str | None = None
    title: str | None = None
    seed_view_count: int = Field(ge=0)
    published_at: datetime
    seeded_at: datetime
    video_url: str
    thumbnail_url: str | None = None
    source: str = "anonymous_shorts_feed"
    source_version: str = "youtubei-reel-v1"


class SeedDiscoveryResult(BaseModel):
    bootstrap_video_id: str | None = None
    pages_requested: int = 0
    candidates_seen: int = 0
    seeds_written: int = 0
    seeds_rejected_age: int = 0
    seeds_rejected_incomplete: int = 0

"""Normalized intake contract shared by current and future market sources.

An Apify adapter must emit this record into the existing verification pipeline;
it must never write directly to topic rankings.
"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ObservedShort:
    source_lane: str
    source_region: str | None
    video_id: str
    title: str | None
    channel_id: str | None
    channel_title: str | None
    published_at: datetime | None
    observed_at: datetime
    view_count: int | None
    thumbnail_url: str | None
    video_url: str | None


SUPPORTED_SOURCE_LANES = {"anonymous_feed", "official_chart", "official_latest", "apify", "innertube_general_browse", "innertube_general_search"}

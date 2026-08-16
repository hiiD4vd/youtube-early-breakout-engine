from datetime import UTC, datetime

from app.services.apify_shorts_adapter import normalize_apify_short


def test_normalizes_common_apify_youtube_record():
    item = normalize_apify_short({"url": "https://www.youtube.com/shorts/AbCdEfGhI12", "title": "Fresh short", "channelName": "Creator", "viewCount": "12,345", "publishedAt": "2026-08-10T00:00:00Z"}, region="ID", observed_at=datetime(2026, 8, 10, tzinfo=UTC))
    assert item is not None
    assert item.video_id == "AbCdEfGhI12"
    assert item.source_lane == "apify"
    assert item.view_count == 12345


def test_rejects_record_without_valid_youtube_video_id():
    assert normalize_apify_short({"url": "https://example.com/not-youtube"}, region="US") is None

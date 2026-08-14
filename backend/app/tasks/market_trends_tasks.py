"""Official public-chart collector for the isolated Market Trends lane."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re

import httpx
from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketSourceRun, MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

MARKET_LOCK = "ycgc:youtube:lock:market-trends"
MARKET_CHART_URL = "https://www.googleapis.com/youtube/v3/videos"
GENERAL_CHART_LOCK = "ycgc:youtube:lock:general-video-chart"
I18N_REGIONS_URL = "https://www.googleapis.com/youtube/v3/i18nRegions"
GENERAL_REGION_CATALOG_KEY = "ycgc:youtube:general-video:region-catalog"
GENERAL_REGION_CURSOR_KEY = "ycgc:youtube:general-video:region-cursor"
GENERAL_CHART_LANE = "official_general_chart"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _int(value) -> int | None:
    return int(value) if value not in (None, "") else None


def _duration_seconds(value: str | None) -> int:
    """Parse the ISO 8601 duration returned by the official videos endpoint."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _general_region_catalog(store: SeedStore, client: httpx.Client) -> list[dict[str, str]]:
    """Return YouTube's own supported regions, cached so the catalog is cheap.

    We intentionally do not maintain an invented country list.  `i18nRegions`
    is YouTube's published list of content regions and the returned codes are
    valid inputs for its regional charts.
    """
    cached = store.client.get(GENERAL_REGION_CATALOG_KEY)
    if cached:
        try:
            rows = json.loads(cached)
            if isinstance(rows, list) and rows:
                return rows
        except json.JSONDecodeError:
            pass
    response = client.get(I18N_REGIONS_URL, params={
        "key": settings.youtube_data_api_key,
        "part": "snippet",
    })
    response.raise_for_status()
    rows = []
    for item in response.json().get("items", []):
        code = str(item.get("id") or "").upper()
        if len(code) != 2 or not code.isalpha():
            continue
        rows.append({"code": code, "name": str((item.get("snippet") or {}).get("name") or code)})
    # Stable alphabetical order makes the rotation deterministic and auditable.
    rows = sorted({row["code"]: row for row in rows}.values(), key=lambda row: row["code"])
    if not rows:
        raise RuntimeError("youtube_i18n_regions_empty")
    store.client.set(GENERAL_REGION_CATALOG_KEY, json.dumps(rows), ex=settings.market_general_chart_catalog_ttl_seconds)
    return rows


def _next_general_regions(store: SeedStore, catalog: list[dict[str, str]]) -> tuple[list[dict[str, str]], int, int]:
    """Round-robin a fixed-size slice without permanently excluding a country.

    At present YouTube publishes 111 supported regions while the initial
    product target is 110. The one region outside a day's target window moves
    every UTC day, rather than permanently omitting the final alphabetical
    country. That matters for a genuinely neutral collection design.
    """
    target = min(max(1, settings.market_general_chart_target_regions), len(catalog))
    day_key = datetime.now(UTC).date().isoformat()
    day_offset = datetime.now(UTC).date().toordinal() % len(catalog)
    pool = [catalog[(day_offset + offset) % len(catalog)] for offset in range(target)]
    per_run = min(max(1, settings.market_general_chart_regions_per_run), len(pool))
    stored = store.client.get(GENERAL_REGION_CURSOR_KEY) or ""
    stored_day, _, stored_cursor = stored.partition(":")
    cursor = int(stored_cursor or 0) % len(pool) if stored_day == day_key else 0
    selected = [pool[(cursor + offset) % len(pool)] for offset in range(per_run)]
    next_cursor = (cursor + per_run) % len(pool)
    store.client.set(GENERAL_REGION_CURSOR_KEY, f"{day_key}:{next_cursor}")
    return selected, target, next_cursor


def _upsert_chart_video(
    db,
    existing: dict[str, MarketVideo],
    item: dict,
    *,
    now: datetime,
    source_lane: str,
) -> tuple[MarketVideo, bool]:
    """Store common official-video metadata once while preserving prior audits."""
    snippet, content = item.get("snippet", {}), item.get("contentDetails", {})
    video_id = item["id"]
    video = existing.get(video_id)
    created = video is None
    if video is None:
        video = MarketVideo(video_id=video_id, video_url=f"https://www.youtube.com/watch?v={video_id}")
        db.add(video)
        db.flush()
        existing[video_id] = video
    video.channel_id = snippet.get("channelId")
    video.channel_title = snippet.get("channelTitle")
    video.title = snippet.get("title")
    video.description = snippet.get("description")
    video.thumbnail_url = (snippet.get("thumbnails", {}).get("high") or snippet.get("thumbnails", {}).get("default") or {}).get("url")
    video.published_at = _parse_time(snippet.get("publishedAt"))
    video.category_id = snippet.get("categoryId")
    video.duration_iso8601 = content.get("duration")
    if video.shorts_status not in {"VERIFIED_SHORTS", "REJECTED_NOT_SHORTS"}:
        video.shorts_status = "REJECTED_NOT_SHORTS" if _duration_seconds(content.get("duration")) > 180 else "SHORT_DURATION_CANDIDATE"
    video.last_seen_at = now
    provenance = dict(video.source_provenance or {})
    provenance.setdefault("first_lane", source_lane)
    provenance.update({"official": True, source_lane: True})
    video.source_provenance = provenance
    return video, created


@celery_app.task(bind=True, name="app.tasks.market_trends_tasks.collect_general_video_chart")
def collect_general_video_chart(self: Task) -> dict[str, int | str | list[str]]:
    """Collect a rotating 110-region official general-video chart.

    The all-category chart is deliberately one request per region; category
    filters remain metadata on each returned video. This gives broad country
    coverage while avoiding a wasteful 110 x category polling loop.
    """
    store = SeedStore()
    if not settings.market_general_chart_enabled:
        return {"status": "disabled"}
    if not settings.youtube_data_api_key:
        store.set_status(general_video_chart_state="youtube_data_api_key_not_configured")
        return {"status": "youtube_data_api_key_not_configured"}
    if not store.client.set(GENERAL_CHART_LOCK, "1", nx=True, ex=570):
        return {"status": "skipped_locked"}

    now = datetime.now(UTC)
    created = updated = observations = errors = 0
    selected_codes: list[str] = []
    target = 0
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.youtube_http_timeout_seconds)) as client, SessionLocal() as db:
            catalog = _general_region_catalog(store, client)
            selected, target, next_cursor = _next_general_regions(store, catalog)
            selected_codes = [row["code"] for row in selected]
            existing = {item.video_id: item for item in db.scalars(select(MarketVideo)).all()}
            for region_data in selected:
                region = region_data["code"]
                source_run = MarketSourceRun(
                    source_lane=GENERAL_CHART_LANE,
                    region=region,
                    cohort_key="all-categories-most-popular",
                    started_at=datetime.now(UTC),
                    details={"region_name": region_data["name"], "catalog_source": "youtube_i18nRegions"},
                )
                db.add(source_run)
                db.flush()
                try:
                    response = client.get(MARKET_CHART_URL, params={
                        "key": settings.youtube_data_api_key,
                        "part": "snippet,statistics,contentDetails",
                        "chart": "mostPopular",
                        "regionCode": region,
                        # Omit videoCategoryId: YouTube returns the complete
                        # regional chart by default. The row's categoryId is
                        # still retained for later category filters.
                        "maxResults": settings.market_general_chart_max_results,
                    })
                    response.raise_for_status()
                    candidates = response.json().get("items", [])
                    run_created = run_duplicates = run_non_shorts = 0
                    for rank, item in enumerate(candidates, start=1):
                        statistics = item.get("statistics", {})
                        video, is_new = _upsert_chart_video(db, existing, item, now=now, source_lane=GENERAL_CHART_LANE)
                        if is_new:
                            created += 1
                            run_created += 1
                        else:
                            updated += 1
                            run_duplicates += 1
                        if video.shorts_status == "REJECTED_NOT_SHORTS":
                            run_non_shorts += 1
                        db.add(MarketVideoObservation(
                            market_video_id=video.id,
                            observed_at=now,
                            source_lane=GENERAL_CHART_LANE,
                            region=region,
                            category_id=video.category_id,
                            view_count=_int(statistics.get("viewCount")) or 0,
                            like_count=_int(statistics.get("likeCount")),
                            comment_count=_int(statistics.get("commentCount")),
                            source_rank=rank,
                            raw_payload={"etag": item.get("etag"), "chart_scope": "all_categories"},
                        ))
                        observations += 1
                    source_run.status = "OK"
                    source_run.completed_at = datetime.now(UTC)
                    source_run.candidates_seen = len(candidates)
                    source_run.unique_shorts = run_created
                    source_run.duplicate_shorts = run_duplicates
                    source_run.rejected_not_shorts = run_non_shorts
                    source_run.details = {**(source_run.details or {}), "chart_scope": "all_categories", "result_count": len(candidates)}
                    db.commit()
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    # An unavailable chart in one country must not destroy a
                    # whole 110-region cycle. Keep the failure visible.
                    errors += 1
                    source_run.status = "ERROR"
                    source_run.error_type = type(exc).__name__
                    source_run.completed_at = datetime.now(UTC)
                    source_run.details = {**(source_run.details or {}), "error": str(exc)[:280]}
                    db.commit()
            cycle_minutes = max(1, (target + max(1, settings.market_general_chart_regions_per_run) - 1) // max(1, settings.market_general_chart_regions_per_run)) * settings.market_general_chart_interval_minutes
        state = "partial_errors" if errors else "ok"
        store.set_status(
            general_video_chart_state=state,
            general_video_chart_last_scan_at=now.isoformat(),
            general_video_chart_catalog_regions=len(catalog),
            general_video_chart_target_regions=target,
            general_video_chart_regions_this_run=",".join(selected_codes),
            general_video_chart_next_cursor=next_cursor,
            general_video_chart_estimated_cycle_minutes=cycle_minutes,
            general_video_chart_created=created,
            general_video_chart_updated=updated,
            general_video_chart_observations=observations,
            general_video_chart_errors=errors,
        )
        return {"status": state, "target_regions": target, "regions": selected_codes, "created": created, "updated": updated, "observations": observations, "errors": errors}
    except httpx.HTTPError as exc:
        store.set_status(general_video_chart_state="catalog_error", general_video_chart_last_error=type(exc).__name__)
        raise self.retry(exc=exc, countdown=300, max_retries=1)
    finally:
        store.client.delete(GENERAL_CHART_LOCK)


@celery_app.task(bind=True, name="app.tasks.market_trends_tasks.collect_market_chart")
def collect_market_chart(self: Task) -> dict[str, int | str]:
    """Collect broad public chart evidence; it is never fed into seed discovery."""
    store = SeedStore()
    if not settings.market_trends_enabled:
        return {"status": "disabled"}
    if not settings.youtube_data_api_key:
        store.set_status(market_trends_state="youtube_data_api_key_not_configured")
        return {"status": "youtube_data_api_key_not_configured"}
    if not store.client.set(MARKET_LOCK, "1", nx=True, ex=540):
        return {"status": "skipped_locked"}
    created = updated = observations = 0
    now = datetime.now(UTC)
    regions = [item.strip().upper() for item in settings.market_trends_regions.split(",") if len(item.strip()) == 2]
    categories = [item.strip() for item in settings.market_trends_chart_categories.split(",") if item.strip()]
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.youtube_http_timeout_seconds)) as client, SessionLocal() as db:
            existing = {item.video_id: item for item in db.scalars(select(MarketVideo)).all()}
            for region in regions:
                for category in categories:
                    response = client.get(MARKET_CHART_URL, params={
                        "key": settings.youtube_data_api_key, "part": "snippet,statistics,contentDetails",
                        "chart": "mostPopular", "regionCode": region, "videoCategoryId": category,
                        "maxResults": settings.market_trends_max_results,
                    })
                    response.raise_for_status()
                    for rank, item in enumerate(response.json().get("items", []), start=1):
                        snippet, statistics, content = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
                        video_id = item["id"]
                        video = existing.get(video_id)
                        if not video:
                            video = MarketVideo(video_id=video_id, video_url=f"https://www.youtube.com/watch?v={video_id}")
                            db.add(video); db.flush(); existing[video_id] = video; created += 1
                        else:
                            updated += 1
                        video.channel_id = snippet.get("channelId")
                        video.channel_title = snippet.get("channelTitle")
                        video.title = snippet.get("title")
                        video.description = snippet.get("description")
                        video.thumbnail_url = (snippet.get("thumbnails", {}).get("high") or snippet.get("thumbnails", {}).get("default") or {}).get("url")
                        video.published_at = _parse_time(snippet.get("publishedAt"))
                        video.category_id = snippet.get("categoryId")
                        video.duration_iso8601 = content.get("duration")
                        # Store ALL chart videos for trending-topic analysis.
                        # Long videos are marked REJECTED_NOT_SHORTS so they
                        # stay in DB for topic grouping but skip Shorts
                        # verification. Do not erase an already audited decision.
                        if video.shorts_status not in {"VERIFIED_SHORTS", "REJECTED_NOT_SHORTS"}:
                            if _duration_seconds(content.get("duration")) > 180:
                                video.shorts_status = "REJECTED_NOT_SHORTS"
                            else:
                                video.shorts_status = "SHORT_DURATION_CANDIDATE"
                        video.last_seen_at = now
                        video.source_provenance = {"first_lane": "official_chart", "official": True}
                        db.add(MarketVideoObservation(market_video_id=video.id, observed_at=now, source_lane="official_chart", region=region, category_id=category, view_count=_int(statistics.get("viewCount")) or 0, like_count=_int(statistics.get("likeCount")), comment_count=_int(statistics.get("commentCount")), source_rank=rank, raw_payload={"etag": item.get("etag")}))
                        observations += 1
            db.commit()
        store.set_status(market_trends_state="ok", market_trends_last_scan_at=now.isoformat(), market_trends_created=created, market_trends_updated=updated, market_trends_observations=observations)
        return {"created": created, "updated": updated, "observations": observations}
    except httpx.HTTPError as exc:
        store.set_status(market_trends_state="source_error", market_trends_last_error=type(exc).__name__)
        raise self.retry(exc=exc, countdown=120, max_retries=2)
    finally:
        store.client.delete(MARKET_LOCK)

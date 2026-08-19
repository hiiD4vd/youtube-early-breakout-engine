"""Isolated general-video InnerTube collector.

This lane is intentionally separate from the Shorts seed/velocity pipeline.
It collects broad trending / search evidence and stores it in the existing
market evidence tables without changing the Shorts-specific logic.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketSourceRun, MarketVideo, MarketVideoObservation
from app.services.seed_store import SeedStore
from app.services.youtube_general_client import YoutubeGeneralDiscoveryError, YoutubeGeneralInnertubeClient
from app.tasks.celery_app import celery_app

INNERTUBE_GENERAL_LOCK = "ycgc:youtube:lock:innertube-general-trends"
INNERTUBE_GENERAL_REGION_CATALOG_KEY = "ycgc:youtube:innertube-general:region-catalog"
INNERTUBE_GENERAL_REGION_CURSOR_KEY = "ycgc:youtube:innertube-general:region-cursor"
INNERTUBE_GENERAL_BROWSE_LANE = "innertube_general_browse"
INNERTUBE_GENERAL_SEARCH_LANE = "innertube_general_search"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _int(value) -> int | None:
    return int(value) if value not in (None, "") else None


def _duration_seconds(value: str | None) -> int:
    if not value:
        return 0
    match = __import__("re").fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not match:
        return 0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _general_region_catalog(store: SeedStore, client: httpx.Client) -> list[dict[str, str]]:
    cached = store.client.get(INNERTUBE_GENERAL_REGION_CATALOG_KEY)
    if cached:
        try:
            rows = json.loads(cached)
            if isinstance(rows, list) and rows:
                return rows
        except json.JSONDecodeError:
            pass
    response = client.get("https://www.googleapis.com/youtube/v3/i18nRegions", params={
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
    rows = sorted({row["code"]: row for row in rows}.values(), key=lambda row: row["code"])
    if not rows:
        raise RuntimeError("youtube_i18n_regions_empty")
    store.client.set(INNERTUBE_GENERAL_REGION_CATALOG_KEY, json.dumps(rows), ex=settings.youtube_general_innertube_target_regions * 24 * 60 * 60)
    return rows


def _next_general_regions(store: SeedStore, catalog: list[dict[str, str]]) -> tuple[list[dict[str, str]], int, int]:
    target = min(max(1, settings.youtube_general_innertube_target_regions), len(catalog))
    day_key = datetime.now(UTC).date().isoformat()
    day_offset = datetime.now(UTC).date().toordinal() % len(catalog)
    pool = [catalog[(day_offset + offset) % len(catalog)] for offset in range(target)]
    per_run = min(max(1, settings.youtube_general_innertube_regions_per_run), len(pool))
    stored = store.client.get(INNERTUBE_GENERAL_REGION_CURSOR_KEY) or ""
    stored_day, _, stored_cursor = stored.partition(":")
    cursor = int(stored_cursor or 0) % len(pool) if stored_day == day_key else 0
    selected = [pool[(cursor + offset) % len(pool)] for offset in range(per_run)]
    next_cursor = (cursor + per_run) % len(pool)
    store.client.set(INNERTUBE_GENERAL_REGION_CURSOR_KEY, f"{day_key}:{next_cursor}")
    return selected, target, next_cursor


def _upsert_general_video(
    db,
    existing: dict[str, MarketVideo],
    item: dict[str, object],
    *,
    now: datetime,
    source_lane: str,
) -> tuple[MarketVideo, bool]:
    video_id = str(item.get("video_id") or "")
    if not video_id:
        raise KeyError("video_id")
    video = existing.get(video_id)
    created = video is None
    if video is None:
        video = MarketVideo(video_id=video_id, video_url=str(item.get("video_url") or f"https://www.youtube.com/watch?v={video_id}"))
        db.add(video)
        db.flush()
        existing[video_id] = video
    video.channel_id = item.get("channel_id") or None
    video.channel_title = item.get("channel_title") or None
    video.title = item.get("title") or None
    video.description = item.get("description") or None
    video.thumbnail_url = item.get("thumbnail_url") or None
    video.published_at = item.get("published_at") if isinstance(item.get("published_at"), datetime) else None
    video.category_id = item.get("category_id") or None
    video.duration_iso8601 = item.get("duration_label") or None
    # Route by the discovery surface, not by duration alone. This lane reads
    # YouTube's general browse/search surfaces, so every result remains useful
    # general-video evidence (including short landscape clips). A video is
    # moved to the Shorts projection only when a dedicated Shorts source has
    # positively verified it. Keep the legacy database value for compatibility;
    # product/API language presents it as GENERAL_VIDEO rather than "rejected".
    if video.shorts_status != "VERIFIED_SHORTS":
        video.shorts_status = "REJECTED_NOT_SHORTS"
    video.last_seen_at = now
    provenance = dict(video.source_provenance or {})
    provenance.setdefault("first_lane", source_lane)
    provenance.update({
        "innertube_general": True,
        source_lane: True,
        "source_surface": item.get("source_surface"),
        "format_route": "general",
        "format_route_reason": "general_innertube_surface",
    })
    if item.get("query"):
        provenance["query"] = item.get("query")
    video.source_provenance = provenance
    return video, created


def _collect_surface(
    db,
    *,
    now: datetime,
    client: YoutubeGeneralInnertubeClient,
    existing: dict[str, MarketVideo],
    source_lane: str,
    region: str | None,
    cohort_key: str,
    items: list[dict[str, object]],
    details: dict[str, object],
) -> tuple[int, int, int, int]:
    source_run = MarketSourceRun(
        source_lane=source_lane,
        region=region,
        cohort_key=cohort_key,
        started_at=now,
        details=details,
    )
    db.add(source_run)
    db.flush()
    created = updated = observations = routed_general = routed_shorts = 0
    try:
        for rank, item in enumerate(items, start=1):
            video, is_new = _upsert_general_video(db, existing, item, now=now, source_lane=source_lane)
            if is_new:
                created += 1
            else:
                updated += 1
            if video.shorts_status == "VERIFIED_SHORTS":
                routed_shorts += 1
            else:
                routed_general += 1
            db.add(MarketVideoObservation(
                market_video_id=video.id,
                observed_at=now,
                source_lane=source_lane,
                region=region,
                category_id=video.category_id,
                view_count=_int(item.get("view_count")) or 0,
                like_count=_int(item.get("like_count")),
                comment_count=_int(item.get("comment_count")),
                source_rank=rank,
                raw_payload={
                    "source_surface": item.get("source_surface"),
                    "query": item.get("query"),
                    "duration_seconds": item.get("duration_seconds"),
                },
            ))
            observations += 1
        source_run.status = "OK"
        source_run.completed_at = now
        source_run.candidates_seen = len(items)
        source_run.accepted_shorts = routed_shorts
        source_run.unique_shorts = created
        source_run.duplicate_shorts = updated
        source_run.rejected_not_shorts = routed_general
        source_run.details = {
            **(source_run.details or {}),
            "result_count": len(items),
            "source_surface": details.get("source_surface"),
            "routed_general": routed_general,
            "routed_shorts": routed_shorts,
            "routing_rule": "surface_first",
        }
        db.commit()
        return created, updated, observations, routed_general
    except Exception as exc:
        source_run.status = "ERROR"
        source_run.error_type = type(exc).__name__
        source_run.completed_at = now
        source_run.details = {**(source_run.details or {}), "error": str(exc)[:280]}
        db.commit()
        raise


@celery_app.task(bind=True, name="app.tasks.youtube_general_tasks.collect_youtube_general_innertube_trends")
def collect_youtube_general_innertube_trends(self: Task) -> dict[str, int | str | list[str]]:
    store = SeedStore()
    if not settings.youtube_general_innertube_enabled:
        return {"status": "disabled"}
    if not store.client.set(INNERTUBE_GENERAL_LOCK, "1", nx=True, ex=570):
        return {"status": "skipped_locked"}

    now = datetime.now(UTC)
    browse_created = browse_updated = browse_observations = browse_rejected = 0
    search_created = search_updated = search_observations = search_rejected = 0
    browse_errors = search_errors = 0
    last_error_type = last_error = ""
    selected_codes: list[str] = []
    target = 0
    search_terms = [term.strip() for term in settings.youtube_general_innertube_search_terms.split(",") if term.strip()]
    try:
        with httpx.Client(timeout=httpx.Timeout(settings.youtube_http_timeout_seconds)) as client, SessionLocal() as db:
            catalog = _general_region_catalog(store, client)
            selected, target, next_cursor = _next_general_regions(store, catalog)
            selected_codes = [row["code"] for row in selected]
            existing = {item.video_id: item for item in db.scalars(select(MarketVideo)).all()}
            for region_data in selected:
                region = region_data["code"]
                discovery_client = YoutubeGeneralInnertubeClient(client=client, region=region)
                try:
                    browse_items = discovery_client.browse_trending(max_results=settings.youtube_general_innertube_max_results)
                    c, u, o, r = _collect_surface(
                        db,
                        now=now,
                        client=discovery_client,
                        existing=existing,
                        source_lane=INNERTUBE_GENERAL_BROWSE_LANE,
                        region=region,
                        cohort_key="browse:trending",
                        items=browse_items,
                        details={"source_surface": "browse", "region_name": region_data["name"], "catalog_source": "youtube_i18nRegions"},
                    )
                    browse_created += c
                    browse_updated += u
                    browse_observations += o
                    browse_rejected += r
                except (httpx.HTTPError, YoutubeGeneralDiscoveryError, KeyError, ValueError) as exc:
                    browse_errors += 1
                    last_error_type, last_error = type(exc).__name__, str(exc)[:280]
                    run = MarketSourceRun(
                        source_lane=INNERTUBE_GENERAL_BROWSE_LANE,
                        region=region,
                        cohort_key="browse:trending",
                        started_at=now,
                        status="ERROR",
                        error_type=type(exc).__name__,
                        completed_at=now,
                        details={"source_surface": "browse", "region_name": region_data["name"], "error": str(exc)[:280]},
                    )
                    db.add(run)
                    db.commit()
                if settings.youtube_general_innertube_search_enabled and search_terms:
                    for term in search_terms:
                        try:
                            search_items = discovery_client.search_videos(term, max_results=settings.youtube_general_innertube_max_results)
                            c, u, o, r = _collect_surface(
                                db,
                                now=now,
                                client=discovery_client,
                                existing=existing,
                                source_lane=INNERTUBE_GENERAL_SEARCH_LANE,
                                region=region,
                                cohort_key=f"search:{term}",
                                items=search_items,
                                details={"source_surface": "search", "query": term, "region_name": region_data["name"], "catalog_source": "youtube_i18nRegions"},
                            )
                            search_created += c
                            search_updated += u
                            search_observations += o
                            search_rejected += r
                        except (httpx.HTTPError, YoutubeGeneralDiscoveryError, KeyError, ValueError) as exc:
                            search_errors += 1
                            last_error_type, last_error = type(exc).__name__, str(exc)[:280]
                            run = MarketSourceRun(
                                source_lane=INNERTUBE_GENERAL_SEARCH_LANE,
                                region=region,
                                cohort_key=f"search:{term}",
                                started_at=now,
                                status="ERROR",
                                error_type=type(exc).__name__,
                                completed_at=now,
                                details={"source_surface": "search", "query": term, "region_name": region_data["name"], "error": str(exc)[:280]},
                            )
                            db.add(run)
                            db.commit()
            cycle_minutes = max(1, (target + max(1, settings.youtube_general_innertube_regions_per_run) - 1) // max(1, settings.youtube_general_innertube_regions_per_run)) * settings.youtube_general_innertube_interval_minutes
        # A run is not healthy when all browse surfaces failed or when no
        # evidence was returned. Previously an unconditional "ok" concealed
        # the invalid/retired FEtrending response from the health dashboard.
        if selected_codes and browse_errors >= len(selected_codes) and browse_observations == 0:
            state = "source_error"
        elif browse_observations + search_observations == 0:
            state = "empty"
        elif browse_errors or search_errors:
            state = "partial"
        else:
            state = "ok"
        store.set_status(
            youtube_general_innertube_state=state,
            youtube_general_innertube_last_scan_at=now.isoformat(),
            youtube_general_innertube_catalog_regions=len(catalog),
            youtube_general_innertube_target_regions=target,
            youtube_general_innertube_regions_this_run=",".join(selected_codes),
            youtube_general_innertube_next_cursor=next_cursor,
            youtube_general_innertube_estimated_cycle_minutes=cycle_minutes,
            youtube_general_innertube_browse_created=browse_created,
            youtube_general_innertube_browse_updated=browse_updated,
            youtube_general_innertube_browse_observations=browse_observations,
            youtube_general_innertube_browse_rejected_not_shorts=browse_rejected,
            youtube_general_innertube_browse_errors=browse_errors,
            youtube_general_innertube_search_created=search_created,
            youtube_general_innertube_search_updated=search_updated,
            youtube_general_innertube_search_observations=search_observations,
            youtube_general_innertube_search_rejected_not_shorts=search_rejected,
            youtube_general_innertube_search_errors=search_errors,
            youtube_general_innertube_last_error_type=last_error_type,
            youtube_general_innertube_last_error=last_error,
            **({"youtube_general_innertube_last_success_at": now.isoformat()} if state in {"ok", "partial"} else {}),
        )
        return {
            "status": state,
            "target_regions": target,
            "regions": selected_codes,
            "browse_created": browse_created,
            "browse_updated": browse_updated,
            "browse_observations": browse_observations,
            "browse_errors": browse_errors,
            "search_created": search_created,
            "search_updated": search_updated,
            "search_observations": search_observations,
            "search_errors": search_errors,
        }
    except httpx.HTTPError as exc:
        store.set_status(
            youtube_general_innertube_state="source_error",
            youtube_general_innertube_last_error_type=type(exc).__name__,
            youtube_general_innertube_last_error=str(exc)[:280],
        )
        raise self.retry(exc=exc, countdown=60 if isinstance(exc, httpx.ConnectError) else 300, max_retries=1)
    finally:
        store.client.delete(INNERTUBE_GENERAL_LOCK)

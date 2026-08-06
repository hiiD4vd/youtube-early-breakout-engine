"""Format verification for Shorts-first Market Trends intake."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.market_trends import MarketVideo
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

VERIFY_LOCK = "ycgc:youtube:lock:market-shorts-verify"
VERIFYABLE = ("UNVERIFIED", "SHORT_DURATION_CANDIDATE", "VERIFY_FAILED")


def _dimensions(info: dict) -> tuple[int, int]:
    width, height = int(info.get("width") or 0), int(info.get("height") or 0)
    if width and height:
        return width, height
    formats = [item for item in info.get("formats", []) if item.get("width") and item.get("height")]
    if not formats:
        return 0, 0
    best = max(formats, key=lambda item: int(item["width"]) * int(item["height"]))
    return int(best["width"]), int(best["height"])


def _verify(video_id: str) -> tuple[str, dict]:
    result = subprocess.run(
        ["yt-dlp", "--skip-download", "--dump-single-json", "--no-warnings", f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, timeout=45,
    )
    if result.returncode != 0 or not result.stdout:
        return "VERIFY_FAILED", {"reason": "metadata_unavailable"}
    info = json.loads(result.stdout)
    width, height = _dimensions(info)
    duration = float(info.get("duration") or 0)
    evidence = {"width": width, "height": height, "duration_seconds": duration, "verified_at": datetime.now(UTC).isoformat()}
    if 0 < duration <= 180 and width > 0 and height >= width:
        return "VERIFIED_SHORTS", evidence
    return "REJECTED_NOT_SHORTS", evidence


@celery_app.task(bind=True, name="app.tasks.market_shorts_tasks.verify_market_shorts", soft_time_limit=330, time_limit=360)
def verify_market_shorts(self: Task) -> dict[str, int | str]:
    """Verify public-chart candidates before they can appear as Shorts evidence."""
    store = SeedStore()
    if not store.client.set(VERIFY_LOCK, "1", nx=True, ex=340):
        return {"status": "skipped_locked"}
    verified = rejected = failed = 0
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(MarketVideo).where(MarketVideo.shorts_status.in_(VERIFYABLE)).order_by(MarketVideo.last_seen_at.desc()).limit(settings.market_shorts_verify_batch_size)).all()
            for video in rows:
                status, evidence = _verify(video.video_id)
                video.shorts_status = status
                provenance = dict(video.source_provenance or {})
                provenance["shorts_verification"] = evidence
                video.source_provenance = provenance
                if status == "VERIFIED_SHORTS": verified += 1
                elif status == "REJECTED_NOT_SHORTS": rejected += 1
                else: failed += 1
            db.commit()
        store.set_status(market_shorts_last_verify_at=datetime.now(UTC).isoformat(), market_shorts_verified=verified, market_shorts_rejected=rejected, market_shorts_failed=failed)
        return {"verified": verified, "rejected": rejected, "failed": failed, "processed": len(rows)}
    finally:
        store.client.delete(VERIFY_LOCK)

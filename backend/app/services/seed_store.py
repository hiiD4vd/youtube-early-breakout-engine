from datetime import UTC, datetime

import redis

from app.config import settings
from app.core.redis_keys import (
    SEED_DISCOVERY_LOCK_KEY,
    SEED_INDEX_KEY,
    PIPELINE_STATUS_KEY,
    OBSERVATION_REPORT_KEY,
    VELOCITY_CHECK_LOCK_KEY,
    breakout_lock_key,
    coverage_key,
    pending_breakout_key,
    seed_key,
    signal_state_key,
    snapshot_key,
    velocity_samples_key,
)
from app.schemas.youtube import YoutubeSeed


class SeedStore:
    """Redis repository for the bounded, ephemeral discovery pool."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self.client = client or redis.from_url(settings.redis_url, decode_responses=True)

    def acquire_discovery_lock(self, timeout_seconds: int = 1_500) -> bool:
        return bool(self.client.set(SEED_DISCOVERY_LOCK_KEY, "1", nx=True, ex=timeout_seconds))

    def release_discovery_lock(self) -> None:
        self.client.delete(SEED_DISCOVERY_LOCK_KEY)

    def save(self, seed: YoutubeSeed) -> bool:
        """Store a newly found seed once; never reset its VTR baseline on a duplicate."""
        key = seed_key(seed.video_id)
        if self.client.exists(key):
            return False
        with self.client.pipeline(transaction=True) as pipe:
            pipe.set(key, seed.model_dump_json(), ex=settings.youtube_seed_ttl_seconds)
            pipe.zadd(SEED_INDEX_KEY, {seed.video_id: seed.seeded_at.timestamp()})
            pipe.expire(SEED_INDEX_KEY, settings.youtube_seed_ttl_seconds + 3_600)
            result = pipe.execute()
        return bool(result[0])

    def record_coverage(self, profile: str, *, seen: int, fresh: int, old: int, duplicates: int, sessions: int, target_shortfall: int) -> None:
        """Keep a bounded 24-hour operational counter per neutral profile."""
        key = coverage_key(profile)
        values = {"seen": seen, "fresh": fresh, "old": old, "duplicates": duplicates, "sessions": sessions, "target_shortfall": target_shortfall}
        with self.client.pipeline(transaction=True) as pipe:
            for field, value in values.items():
                pipe.hincrby(key, field, value)
            pipe.expire(key, 86_400)
            pipe.execute()

    def coverage(self, profile: str) -> dict[str, int]:
        raw = self.client.hgetall(coverage_key(profile))
        return {field: int(value) for field, value in raw.items()}

    def list_ids(self, limit: int | None = None) -> list[str]:
        """Return live IDs and remove index members whose individual TTL elapsed."""
        cutoff = datetime.now(UTC).timestamp() - settings.youtube_seed_ttl_seconds
        self.client.zremrangebyscore(SEED_INDEX_KEY, "-inf", cutoff)
        live: list[str] = []
        stale: list[str] = []
        for video_id in self.client.zrange(SEED_INDEX_KEY, 0, -1):
            if self.client.exists(seed_key(video_id)):
                live.append(video_id)
                if limit is not None and len(live) >= limit:
                    break
            else:
                stale.append(video_id)
        if stale:
            self.client.zrem(SEED_INDEX_KEY, *stale)
        return live

    def get(self, video_id: str) -> YoutubeSeed | None:
        raw = self.client.get(seed_key(video_id))
        return YoutubeSeed.model_validate_json(raw) if raw else None

    def remove(self, video_id: str) -> None:
        """Discard a seed that is no longer inside the configured freshness window."""
        self.client.delete(seed_key(video_id), snapshot_key(video_id), signal_state_key(video_id))
        self.client.zrem(SEED_INDEX_KEY, video_id)

    def append_snapshot(self, video_id: str, observed_at: datetime, view_count: int) -> list[dict]:
        key = snapshot_key(video_id)
        current = __import__("json").loads(self.client.get(key) or "[]")
        current.append({"observed_at": observed_at.isoformat(), "view_count": view_count})
        current = current[-12:]
        self.client.set(key, __import__("json").dumps(current), ex=settings.youtube_seed_ttl_seconds)
        return current

    def snapshots(self, video_id: str) -> list[dict]:
        return __import__("json").loads(self.client.get(snapshot_key(video_id)) or "[]")

    def record_report(self, **values: int) -> None:
        with self.client.pipeline(transaction=True) as pipe:
            for field, value in values.items():
                if value:
                    pipe.hincrby(OBSERVATION_REPORT_KEY, field, value)
            pipe.expire(OBSERVATION_REPORT_KEY, 86_400)
            pipe.execute()

    def observation_report(self) -> dict[str, int]:
        return {field: int(value) for field, value in self.client.hgetall(OBSERVATION_REPORT_KEY).items()}

    def record_tier_transition(self, video_id: str, tier: str) -> str | None:
        key = signal_state_key(video_id)
        previous = self.client.get(key)
        self.client.set(key, tier, ex=settings.youtube_seed_ttl_seconds)
        if previous != tier:
            self.record_report(**{f"transition_{previous or 'NEW'}_to_{tier}": 1, f"state_{tier}": 1})
        return previous

    def add_velocity_sample(self, bucket: str, observed_at: datetime, velocity: float) -> None:
        """Retain a bounded local population for later same-age percentile scoring."""
        key = velocity_samples_key(bucket)
        now = observed_at.timestamp()
        cutoff = now - 86_400
        samples = __import__("json").loads(self.client.get(key) or "[]")
        samples = [item for item in samples if float(item.get("observed_at", 0)) >= cutoff]
        samples.append({"observed_at": now, "velocity": velocity})
        self.client.set(key, __import__("json").dumps(samples[-1_000:]), ex=86_400)

    def velocity_percentile(self, bucket: str, velocity: float, minimum_samples: int) -> float | None:
        now = datetime.now(UTC).timestamp()
        samples = [item for item in __import__("json").loads(self.client.get(velocity_samples_key(bucket)) or "[]") if float(item.get("observed_at", 0)) >= now - 86_400]
        if len(samples) < minimum_samples:
            return None
        values = sorted(float(item["velocity"]) for item in samples)
        return round(100 * sum(item <= velocity for item in values) / len(values), 2)

    def velocity_sample_count(self, bucket: str) -> int:
        now = datetime.now(UTC).timestamp()
        return sum(1 for item in __import__("json").loads(self.client.get(velocity_samples_key(bucket)) or "[]") if float(item.get("observed_at", 0)) >= now - 86_400)

    def acquire_velocity_lock(self, timeout_seconds: int) -> bool:
        return bool(self.client.set(VELOCITY_CHECK_LOCK_KEY, "1", nx=True, ex=timeout_seconds))

    def release_velocity_lock(self) -> None:
        self.client.delete(VELOCITY_CHECK_LOCK_KEY)

    def acquire_breakout_lock(self, video_id: str) -> bool:
        return bool(self.client.set(breakout_lock_key(video_id), "1", nx=True, ex=settings.youtube_breakout_lock_seconds))

    def save_pending_breakout(self, payload: dict) -> None:
        video_id = payload["video_id"]
        self.client.set(pending_breakout_key(video_id), __import__("json").dumps(payload), ex=settings.youtube_seed_ttl_seconds)

    def get_pending_breakout(self, video_id: str) -> dict | None:
        raw = self.client.get(pending_breakout_key(video_id))
        return __import__("json").loads(raw) if raw else None

    def set_status(self, **values: str | int | float) -> None:
        from datetime import UTC, datetime
        values["updated_at"] = datetime.now(UTC).isoformat()
        self.client.hset(PIPELINE_STATUS_KEY, mapping={key: str(value) for key, value in values.items()})

    def status(self) -> dict[str, str]:
        return self.client.hgetall(PIPELINE_STATUS_KEY)

    def pending_count(self) -> int:
        return sum(1 for _ in self.client.scan_iter(match="ycgc:youtube:pending-breakout:*"))

    def pending_state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in self.client.scan_iter(match="ycgc:youtube:pending-breakout:*"):
            raw = self.client.get(key)
            if not raw:
                continue
            state = __import__("json").loads(raw).get("media_state", "unknown")
            counts[state] = counts.get(state, 0) + 1
        return counts

    def pending_breakouts(self, limit: int = 8) -> list[dict]:
        """Return a small, read-only operational view of delayed enrichment."""
        records: list[dict] = []
        for key in self.client.scan_iter(match="ycgc:youtube:pending-breakout:*"):
            raw = self.client.get(key)
            if not raw:
                continue
            payload = __import__("json").loads(raw)
            records.append({
                "video_id": payload.get("video_id"),
                "media_state": payload.get("media_state", "unknown"),
                "last_media_error": payload.get("last_media_error"),
                "media_attempts": int(payload.get("media_attempt", payload.get("media_attempts", 0))),
                "next_retry_at": payload.get("next_retry_at"),
                "enrichment_state": payload.get("enrichment_state", "pending"),
                "transcript_state": payload.get("transcript_state", "pending"),
                "stages": payload.get("stages", {}),
                "last_enrichment_error": payload.get("last_enrichment_error"),
            })
        return sorted(records, key=lambda item: item["media_attempts"], reverse=True)[:limit]

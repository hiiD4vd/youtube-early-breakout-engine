"""One canonical Redis key policy for the YouTube pipeline."""

YOUTUBE_NAMESPACE = "ycgc:youtube"


def seed_key(video_id: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:seed:{video_id}"


def snapshot_key(video_id: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:seed-snapshots:{video_id}"


SEED_INDEX_KEY = f"{YOUTUBE_NAMESPACE}:seed_ids"
SEED_DISCOVERY_LOCK_KEY = f"{YOUTUBE_NAMESPACE}:lock:seed-discovery"
VELOCITY_CHECK_LOCK_KEY = f"{YOUTUBE_NAMESPACE}:lock:velocity-check"
PIPELINE_STATUS_KEY = f"{YOUTUBE_NAMESPACE}:pipeline-status"
OBSERVATION_REPORT_KEY = f"{YOUTUBE_NAMESPACE}:observation-report-24h"


def breakout_lock_key(video_id: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:breakout:{video_id}"


def pending_breakout_key(video_id: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:pending-breakout:{video_id}"


def coverage_key(profile: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:coverage-24h:{profile.lower()}"


def signal_state_key(video_id: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:signal-state:{video_id}"


def velocity_samples_key(bucket: str) -> str:
    return f"{YOUTUBE_NAMESPACE}:velocity-samples:{bucket}"

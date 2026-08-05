import logging

import httpx
from celery import Task

from app.config import settings
from app.services.seed_store import SeedStore
from app.services.youtube_client import YoutubeAnonymousClient, YoutubeDiscoveryError
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.youtube_seed_tasks.discover_youtube_shorts_seeds",
    autoretry_for=(YoutubeDiscoveryError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    soft_time_limit=240,
    time_limit=300,
)
def discover_youtube_shorts_seeds(self: Task) -> dict[str, int | str | None]:
    """Discover anonymous Shorts, keeping only fresh complete records in Redis."""
    store = SeedStore()
    if not store.acquire_discovery_lock():
        logger.info("YouTube seed discovery skipped: an earlier run still holds the lock")
        return {"status": "skipped_locked"}

    try:
        seen = written = old = incomplete = duplicates = 0
        profile_metrics: dict[str, int] = {}
        for region, language in settings.youtube_profile_list:
            profile_seen = profile_fresh = profile_old = profile_duplicates = 0
            minimum_sessions = min(max(settings.youtube_sessions_per_profile, 1), settings.youtube_max_sessions_per_profile)
            sessions = 0
            while sessions < settings.youtube_max_sessions_per_profile and (sessions < minimum_sessions or profile_seen < settings.youtube_seed_target_candidates_per_profile):
                sessions += 1
                session_number = sessions
                client = YoutubeAnonymousClient(region=region, language=language)
                try:
                    seeds, result = client.discover_seeds(
                        max_pages=settings.youtube_seed_pages_per_session,
                        max_accepted=settings.youtube_seed_limit_per_session,
                    )
                    for seed in seeds:
                        seed.source = f"anonymous_shorts_feed:{region}/{language}:session-{session_number}"
                        if store.save(seed):
                            profile_fresh += 1
                        else:
                            profile_duplicates += 1
                    profile_seen += result.candidates_seen
                    profile_old += result.seeds_rejected_age
                    incomplete += result.seeds_rejected_incomplete
                finally:
                    client.close()
            profile_shortfall = max(0, settings.youtube_seed_target_candidates_per_profile - profile_seen)
            profile_name = f"{region}_{language}"
            store.record_coverage(profile_name, seen=profile_seen, fresh=profile_fresh, old=profile_old, duplicates=profile_duplicates, sessions=sessions, target_shortfall=profile_shortfall)
            seen += profile_seen; written += profile_fresh; old += profile_old; duplicates += profile_duplicates
            profile_metrics[f"profile_{region}_{language}_seen"] = profile_seen
            profile_metrics[f"profile_{region}_{language}_fresh"] = profile_fresh
            profile_metrics[f"profile_{region}_{language}_duplicates"] = profile_duplicates
            profile_metrics[f"profile_{region}_{language}_sessions"] = sessions
            profile_metrics[f"profile_{region}_{language}_target_shortfall"] = profile_shortfall
        store.set_status(last_seed_scan_at=__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(), last_seed_seen=seen, last_seed_written=written, last_seed_old=old, last_seed_duplicates=duplicates, **profile_metrics)
        logger.info(
            "YouTube seed discovery completed: profiles=%s target/profile=%s seen=%s stored=%s old=%s duplicates=%s incomplete=%s",
            len(settings.youtube_profile_list), settings.youtube_seed_target_candidates_per_profile, seen, written, old, duplicates, incomplete,
        )
        return {"profiles": len(settings.youtube_profile_list), "candidates_seen": seen, "seeds_written": written, "seeds_rejected_age": old, "duplicates": duplicates}
    except (YoutubeDiscoveryError, httpx.HTTPError) as exc:
        store.set_status(last_seed_error=str(exc)[:300])
        logger.warning("Anonymous YouTube seed discovery failed: %s", exc)
        raise YoutubeDiscoveryError(str(exc)) from exc
    finally:
        store.release_discovery_lock()

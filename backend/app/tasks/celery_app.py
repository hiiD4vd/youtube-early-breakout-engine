from celery import Celery

from app.config import settings

celery_app = Celery(
    "ycgc_v4",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.youtube_seed_tasks", "app.tasks.youtube_velocity_tasks", "app.tasks.youtube_enrichment_tasks"],
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_default_queue="default",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "discover-anonymous-youtube-shorts-seeds": {
            "task": "app.tasks.youtube_seed_tasks.discover_youtube_shorts_seeds",
            "schedule": settings.youtube_seed_interval_minutes * 60,
        }
        ,"check-youtube-seed-velocity": {
            "task": "app.tasks.youtube_velocity_tasks.check_youtube_seed_velocity",
            "schedule": settings.youtube_velocity_interval_minutes * 60,
        }
    },
)

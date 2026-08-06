from celery import Celery

from app.config import settings

celery_app = Celery(
    "ycgc_v4",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.youtube_seed_tasks", "app.tasks.youtube_velocity_tasks", "app.tasks.youtube_enrichment_tasks", "app.tasks.youtube_channel_tasks", "app.tasks.youtube_retry_tasks", "app.tasks.youtube_trend_tasks"],
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
        ,"retry-pending-youtube-enrichment": {
            "task": "app.tasks.youtube_retry_tasks.retry_pending_enrichment",
            "schedule": settings.youtube_enrichment_retry_interval_minutes * 60,
        }
        ,"build-youtube-trend-features": {
            "task": "app.tasks.youtube_trend_tasks.build_trend_signal_features",
            "schedule": settings.topic_feature_interval_minutes * 60,
        }
        ,"cluster-youtube-signal-candidates": {
            "task": "app.tasks.youtube_trend_tasks.cluster_recent_signals",
            "schedule": settings.topic_trends_interval_minutes * 60,
        }
        ,"score-youtube-topic-trends": {
            "task": "app.tasks.youtube_trend_tasks.score_topic_trends",
            "schedule": settings.topic_trends_snapshot_interval_minutes * 60,
        }
    },
)

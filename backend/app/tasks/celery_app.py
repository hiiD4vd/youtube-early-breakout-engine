import logging

from celery import Celery

from app.config import settings

# httpx logs full request URLs at INFO level. Several upstream APIs carry an
# API key in the query string, so worker logs must never print those URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)

celery_app = Celery(
    "ycgc_v4",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.youtube_seed_tasks", "app.tasks.youtube_velocity_tasks", "app.tasks.youtube_enrichment_tasks", "app.tasks.youtube_channel_tasks", "app.tasks.youtube_retry_tasks", "app.tasks.youtube_trend_tasks", "app.tasks.market_trends_tasks", "app.tasks.market_latest_tasks", "app.tasks.market_shorts_tasks", "app.tasks.market_feed_tasks", "app.tasks.market_feature_tasks", "app.tasks.market_gemini_tasks", "app.tasks.market_cluster_tasks", "app.tasks.market_topic_scoring_tasks", "app.tasks.market_fallback_topics_tasks", "app.tasks.market_metadata_tasks", "app.tasks.market_semantic_topic_tasks", "app.tasks.market_trending_topics_tasks", "app.tasks.market_content_truth_tasks", "app.tasks.market_apify_tasks", "app.tasks.external_benchmark_tasks"],
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_default_queue="default",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_routes={
        # Discovery and verification must keep moving even when the semantic
        # provider is slow. These tasks run in a separate one-at-a-time AI
        # lane configured in docker-compose.
        "app.tasks.market_gemini_tasks.enrich_market_topics": {"queue": "intelligence"},
        "app.tasks.market_semantic_topic_tasks.name_metadata_clusters": {"queue": "intelligence"},
        "app.tasks.market_trending_topics_tasks.build_trending_topics": {"queue": "intelligence"},
        "app.tasks.market_content_truth_tasks.audit_market_content_truth": {"queue": "intelligence"},
        "app.tasks.external_benchmark_tasks.match_external_benchmarks": {"queue": "intelligence"},
    },
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
        ,"collect-youtube-market-chart": {
            "task": "app.tasks.market_trends_tasks.collect_market_chart",
            "schedule": settings.market_trends_interval_minutes * 60,
        }
        ,"collect-general-youtube-video-chart": {
            "task": "app.tasks.market_trends_tasks.collect_general_video_chart",
            "schedule": settings.market_general_chart_interval_minutes * 60,
        }
        ,"collect-youtube-market-latest": {
            "task": "app.tasks.market_latest_tasks.collect_market_latest",
            "schedule": settings.market_latest_interval_minutes * 60,
        }
        ,"verify-youtube-market-shorts": {
            "task": "app.tasks.market_shorts_tasks.verify_market_shorts",
            "schedule": settings.market_shorts_verify_interval_minutes * 60,
        }
        ,"collect-market-shorts-feed": {
            "task": "app.tasks.market_feed_tasks.collect_market_shorts_feed",
            "schedule": settings.market_feed_interval_minutes * 60,
        }
        ,"seed-fresh-early-from-market-reel": {
            "task": "app.tasks.market_feed_tasks.seed_fresh_early_from_market_reel",
            "schedule": 10 * 60,
        }
        ,"collect-market-apify-shorts": {"task":"app.tasks.market_apify_tasks.collect_apify_shorts","schedule":settings.apify_interval_minutes * 60}
        ,"build-market-shorts-features": {"task":"app.tasks.market_feature_tasks.build_market_video_features","schedule":300}
        ,"enrich-market-short-semantics": {"task":"app.tasks.market_gemini_tasks.enrich_market_topics","schedule":settings.market_gemini_interval_minutes * 60}
        # Per-video semantic enrichment is bounded and routed to the separate
        # AI queue. It enriches evidence only; cross-channel rules still decide
        # whether a candidate becomes a public topic.
        ,"cluster-market-shorts-topics": {"task":"app.tasks.market_cluster_tasks.cluster_market_topics","schedule":600}
        ,"score-market-shorts-topics": {"task":"app.tasks.market_topic_scoring_tasks.score_market_topics","schedule":600}
        ,"build-market-title-overlap-candidates": {"task":"app.tasks.market_fallback_topics_tasks.build_title_overlap_candidates","schedule":600}
        ,"detect-market-metadata-bursts": {"task":"app.tasks.market_metadata_tasks.detect_market_metadata_bursts","schedule":900}
        ,"name-market-metadata-clusters": {"task":"app.tasks.market_semantic_topic_tasks.name_metadata_clusters","schedule":900}
        # A bounded GPT review validates retained clusters after independent
        # cross-channel evidence exists; it is never a polling loop per video.
        ,"build-market-trending-topics": {"task":"app.tasks.market_trending_topics_tasks.build_trending_topics","schedule":600}
        # Title-to-content verification runs after candidate construction and
        # before an event may remain on the public leaderboard.
        ,"audit-market-content-truth": {"task":"app.tasks.market_content_truth_tasks.audit_market_content_truth","schedule":settings.market_content_truth_interval_minutes * 60}
        ,"match-external-trend-benchmarks": {"task":"app.tasks.external_benchmark_tasks.match_external_benchmarks","schedule":settings.external_benchmark_interval_minutes * 60}
    },
)

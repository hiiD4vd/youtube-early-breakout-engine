"""Match analyst-entered external trend references to local ranked topics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models.market_trends import ExternalTrendBenchmark, MarketRankedTopic
from app.services.market_semantic_client import MarketSemanticClient
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:external-benchmark-match"


@celery_app.task(name="app.tasks.external_benchmark_tasks.match_external_benchmarks")
def match_external_benchmarks() -> dict[str, int | str]:
    """Evaluate small manual benchmark set; never compares platform metrics."""
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=900):
        return {"status": "locked"}
    matched = unmatched = skipped = 0
    try:
        with SessionLocal() as db:
            cutoff = datetime.now(UTC) - timedelta(days=2)
            rows = db.scalars(
                select(ExternalTrendBenchmark)
                .where(ExternalTrendBenchmark.match_status == "PENDING", ExternalTrendBenchmark.observed_on >= cutoff)
                .order_by(ExternalTrendBenchmark.created_at)
                .limit(12)
            ).all()
            topics = db.scalars(
                select(MarketRankedTopic)
                .where(MarketRankedTopic.status.in_(("EMERGING", "ACCELERATING", "CONFIRMED")))
                .order_by(MarketRankedTopic.trend_score.desc())
                .limit(30)
            ).all()
            if not rows:
                return {"status": "ok", "matched": 0, "unmatched": 0}
            if not topics:
                for row in rows:
                    row.match_status = "NO_LOCAL_TOPICS"
                db.commit()
                return {"status": "no_local_topics", "matched": 0, "unmatched": len(rows)}
            client = MarketSemanticClient()
            for row in rows:
                best: tuple[MarketRankedTopic | None, float] = (None, 0.0)
                for topic in topics:
                    same, confidence = client.same_topic(row.label, topic.label)
                    if same and confidence > best[1]:
                        best = (topic, confidence)
                if best[0] is None:
                    row.match_status = "NOT_CAPTURED"
                    row.match_confidence = 0.0
                    unmatched += 1
                else:
                    row.match_status = "CAPTURED"
                    row.match_confidence = best[1]
                    row.matched_ranked_topic_id = best[0].id
                    matched += 1
            db.commit()
        return {"status": "ok", "matched": matched, "unmatched": unmatched, "skipped": skipped}
    except Exception as exc:
        return {"status": "provider_error", "error": type(exc).__name__, "matched": matched, "unmatched": unmatched}
    finally:
        store.client.delete(LOCK)

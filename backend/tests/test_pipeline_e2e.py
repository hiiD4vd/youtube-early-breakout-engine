"""Safe end-to-end enrichment test: isolated PostgreSQL DB, no external calls."""

from datetime import UTC, datetime
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.youtube_snipe import YoutubeSnipe
from app.services.gemini_client import GeminiFacts
from app.tasks.youtube_enrichment_tasks import enrich_youtube_breakout

TEST_URL = "postgresql+psycopg://ycgc:ycgc_local_password@postgres:5432/ycgc_test"


class _PendingStore:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def get_pending_breakout(self, video_id: str) -> dict | None:
        return self.payload if video_id == self.payload["video_id"] else None


class PipelineE2ETest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(TEST_URL)
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        Base.metadata.drop_all(cls.engine)
        cls.engine.dispose()

    def test_pending_breakout_is_enriched_and_upserted_once(self) -> None:
        now = datetime.now(UTC).isoformat()
        pending = {
            "video_id": "testvideo01",
            "seed": {"video_id": "testvideo01", "channel_id": "UCtestchannel", "channel_title": "Test Channel", "title": "Test Short", "seed_view_count": 100, "published_at": now, "seeded_at": now, "video_url": "https://example.invalid/shorts/testvideo01", "thumbnail_url": None},
            "current_view_count": 5_000,
            "velocity_per_hour": 2_450.0,
            "peak_timestamp_seconds": 12.5,
            "peak_frame_path": "/tmp/test-frame.jpg",
        }
        facts = GeminiFacts(niche="Comedy", visual_facts=["Red text is visible"], transcript_summary="A test transcript.", confidence=0.9)
        with patch("app.tasks.youtube_enrichment_tasks.SeedStore", return_value=_PendingStore(pending)), patch("app.tasks.youtube_enrichment_tasks.fetch_transcript", return_value="Transcript"), patch("app.tasks.youtube_enrichment_tasks.GeminiClient") as gemini, patch("app.tasks.youtube_enrichment_tasks.SessionLocal", self.Session):
            gemini.return_value.analyze.return_value = facts
            self.assertEqual(enrich_youtube_breakout.run("testvideo01")["status"], "saved")
            pending["current_view_count"] = 6_000
            self.assertEqual(enrich_youtube_breakout.run("testvideo01")["status"], "saved")
        with self.Session() as db:
            rows = db.scalars(select(YoutubeSnipe).where(YoutubeSnipe.video_id == "testvideo01")).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].current_view_count, 6_000)
            self.assertEqual(rows[0].niche, "Comedy")
            self.assertEqual(rows[0].visual_facts["facts"], ["Red text is visible"])

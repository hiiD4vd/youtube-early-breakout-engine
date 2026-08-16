"""Automatic, versioned feature generation for verified Market Shorts."""
import re
from collections import Counter
from hashlib import sha256
from datetime import UTC, datetime
from sqlalchemy import select
from app.database import SessionLocal
from app.models.market_trends import MarketVideo, MarketVideoFeature
from app.services.seed_store import SeedStore
from app.tasks.celery_app import celery_app

LOCK = "ycgc:youtube:lock:market-features"
STOP = {"the","and","for","with","yang","dan","ini","itu","dari","untuk","shorts","short","youtube","www","http","https","com","use","fair","copyright","rights","reserved","all","video","videos","subscribe","channel","follow","original","content","music","audio","credit","credits","thanks","thank","link","links","more","new","official"}
FINGERPRINT_VERSION = "market-lexical-v2"

def _payload(video: MarketVideo):
    # Titles carry the useful semantic signal. Descriptions are included once
    # after URL removal because public-chart descriptions often contain long
    # copyright/credit templates that would otherwise form false topics.
    title = (video.title or "").casefold()
    description = re.sub(r"(?:https?://|www\.)\S+", " ", video.description or "").casefold()
    text = " ".join([title, title, title, description, (video.channel_title or "").casefold()])
    tokens = [token for token in re.findall(r"[\w']+", text) if len(token) > 2 and token not in STOP]
    counts = Counter(tokens)
    norm = sum(value * value for value in counts.values()) ** .5 or 1
    vector = {key: round(value / norm, 5) for key, value in counts.most_common(80)}
    hint = " · ".join(key.title() for key, _ in counts.most_common(3)) or "Unlabeled Shorts pattern"
    return text[:12000], vector, hint, sha256(text.encode()).hexdigest()

@celery_app.task(name="app.tasks.market_feature_tasks.build_market_video_features")
def build_market_video_features():
    store = SeedStore()
    if not store.client.set(LOCK, "1", nx=True, ex=280): return {"status":"skipped_locked"}
    created=updated=0
    try:
        with SessionLocal() as db:
            videos=db.scalars(select(MarketVideo).where(MarketVideo.shorts_status=="VERIFIED_SHORTS")).all()
            existing={item.market_video_id:item for item in db.scalars(select(MarketVideoFeature)).all()}
            for video in videos:
                text, vector, hint, digest=_payload(video); feature=existing.get(video.id)
                provenance={"content_hash":digest,"source":"verified_shorts","fingerprint_version":FINGERPRINT_VERSION,"generated_at":datetime.now(UTC).isoformat()}
                if feature and (feature.provenance or {}).get("content_hash")==digest and (feature.provenance or {}).get("fingerprint_version")==FINGERPRINT_VERSION: continue
                if not feature:
                    feature=MarketVideoFeature(market_video_id=video.id,feature_model="market-lexical-v1",normalized_text=text,sparse_vector=vector,topic_hint=hint,confidence=.25,provenance=provenance); db.add(feature); created+=1
                else:
                    feature.normalized_text,feature.sparse_vector,feature.provenance=text,vector,provenance
                    if feature.feature_model != "market-gemini-v1":
                        feature.feature_model, feature.topic_hint = "market-lexical-v2", hint
                    updated+=1
            db.commit()
        store.set_status(market_features_last_run_at=datetime.now(UTC).isoformat(),market_features_created=created,market_features_updated=updated)
        return {"created":created,"updated":updated}
    finally: store.client.delete(LOCK)

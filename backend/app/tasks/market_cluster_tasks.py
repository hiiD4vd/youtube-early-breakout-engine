from sqlalchemy import select
from app.database import SessionLocal
from app.models.market_trends import MarketVideo,MarketVideoFeature,MarketTopic,MarketTopicMembership
from app.tasks.celery_app import celery_app
def sim(a,b):
 return sum(float(v)*float(b.get(k,0)) for k,v in (a or {}).items())
@celery_app.task(name="app.tasks.market_cluster_tasks.cluster_market_topics")
def cluster_market_topics():
 created=assigned=0
 with SessionLocal() as db:
  # Public topics are semantic claims, so clustering waits for Gemini's
  # versioned label. Lexical fingerprints remain useful as private intake,
  # but words from a title alone must not become a published trend.
  feats=[feature for feature in db.scalars(select(MarketVideoFeature).where(MarketVideoFeature.feature_model=="market-gemini-v1")).all() if (feature.provenance or {}).get("market_gemini_version")=="market-gemini-v2"]; vids={v.id:v for v in db.scalars(select(MarketVideo)).all()}; member=set(db.scalars(select(MarketTopicMembership.market_video_id)).all()); topics=db.scalars(select(MarketTopic)).all(); vectors={t.id:{} for t in topics}
  for t in topics:
   for m,f in db.execute(select(MarketTopicMembership,MarketVideoFeature).join(MarketVideoFeature,MarketVideoFeature.market_video_id==MarketTopicMembership.market_video_id).where(MarketTopicMembership.market_topic_id==t.id)).all():
    for k,v in (f.sparse_vector or {}).items(): vectors[t.id][k]=vectors[t.id].get(k,0)+float(v)
  for f in feats:
   if f.market_video_id in member: continue
   best=max(topics,key=lambda t:sim(f.sparse_vector,vectors[t.id]),default=None); score=sim(f.sparse_vector,vectors[best.id]) if best else 0
   if not best or score<.42:
    best=MarketTopic(label=f.topic_hint or "Unlabeled topic");db.add(best);db.flush();topics.append(best);vectors[best.id]=dict(f.sparse_vector or {});created+=1;score=1
   db.add(MarketTopicMembership(market_topic_id=best.id,market_video_id=f.market_video_id,similarity_score=score));assigned+=1
  db.flush()
  for topic in topics:
   members=db.execute(select(MarketVideo).join(MarketTopicMembership,MarketTopicMembership.market_video_id==MarketVideo.id).where(MarketTopicMembership.market_topic_id==topic.id)).scalars().all()
   topic.member_count=len(members);topic.channel_count=len({v.channel_id for v in members if v.channel_id});topic.status="EMERGING" if topic.member_count>=2 and topic.channel_count>=2 else "PRIVATE_CANDIDATE"
  db.commit()
 return {"created":created,"assigned":assigned}

# Prompt: Fix Topic Clustering — 1 Topic = 1 Video

## Masalah

Setiap topik cuma punya **1 video**. Ini bukan clustering — cuma per-video labeling.

### Root Cause

Di `backend/app/tasks/youtube_trend_tasks.py`, function `cluster_recent_signals()`:

```python
# Baris 110-111
if best is None or best_score < settings.topic_lexical_similarity_threshold:
    best = TrendCluster(
        public_slug=f"candidate-{signal.video_id.lower()}",
        label=provisional_label(feature.sparse_vector),
        ...
    )
```

Setiap video yang nggak match cluster existing, **langsung bikin cluster baru**. Karena `provisional_label()` cuma ambil top-3 token dari video itu sendiri, video yang mirip secara tematik tapi beda kata nggak akan pernah gabung.

Contoh:
- "kopi latte art" vs "espresso manual brew" → cosine similarity = 0.0 → 2 cluster terpisah
- Padahal keduanya tentang kopi!

---

## Solusi: LLM-Based Grouping (seperti ViralEngine)

### Referensi: `chart_topic_labeler.py`

File ini sudah ada blueprint-nya. Cara kerjanya:
1. Ambil SEMUA video yang belum di-cluster
2. Kirim ke Gemini: "Ini list video, group yang mirip, beri label"
3. Gemini return JSON: `[{topic: "Kopi", video_indices: [0, 3, 5]}, {topic: "Gaming", video_indices: [1, 2, 4]}]`
4. Buat cluster dari hasil Gemini

### Yang Perlu Diubah

#### 1. Di `backend/app/tasks/youtube_trend_tasks.py`

**Ganti function `cluster_recent_signals()`:**

```python
@celery_app.task(bind=True, name="app.tasks.youtube_trend_tasks.cluster_recent_signals")
def cluster_recent_signals(self: Task) -> dict:
    store = SeedStore()
    if not store.client.set(CLUSTER_LOCK, "1", nx=True, ex=280):
        return {"status": "skipped_locked"}
    
    try:
        with SessionLocal() as db:
            # 1. Kumpulkan SEMUA sinyal aktif yang belum punya cluster
            signals = db.scalars(
                select(YoutubeSnipe)
                .where(YoutubeSnipe.signal_tier.in_(ACTIVE_TIERS))
                .order_by(YoutubeSnipe.detected_at)
            ).all()
            
            member_signal_ids = set(
                db.scalars(select(TrendMembership.youtube_snipe_id)).all()
            )
            
            # Filter: hanya sinyal yang BELUM ada di cluster manapun
            unassigned = [s for s in signals if s.id not in member_signal_ids]
            
            if len(unassigned) < 2:
                return {"status": "not_enough_signals", "unassigned": len(unassigned)}
            
            # 2. Kirim ke Gemini untuk grouping
            from app.services.gemini_client import GeminiClient
            
            # Build evidence text dari unassigned signals
            evidence_lines = []
            for i, signal in enumerate(unassigned[:50]):  # max 50 per batch
                title = signal.title or "Untitled"
                channel = signal.channel_title or "Unknown"
                niche = signal.niche or ""
                evidence_lines.append(f"[{i}] {title} | Channel: {channel} | Niche: {niche}")
            
            evidence = "\n".join(evidence_lines)
            
            # 3. Panggil Gemini untuk grouping
            client = GeminiClient()
            clusters = client.analyze_topic_cluster(evidence)
            # clusters = list of {topic_title, topic_type, video_indices, confidence}
            
            # 4. Buat cluster dari hasil Gemini
            created = 0
            for cluster_data in clusters:
                if cluster_data.get("confidence", 0) < 0.70:
                    continue
                
                cluster = TrendCluster(
                    public_slug=f"llm-{cluster_data['topic_title'][:50].lower().replace(' ', '-')}",
                    label=cluster_data["topic_title"],
                    label_confidence=cluster_data.get("confidence", 0.8),
                    niche=cluster_data.get("topic_type", ""),
                    status="PRIVATE_CANDIDATE",
                    cluster_reason=cluster_data.get("summary", "LLM-grouped from unassigned signals"),
                    model_metadata={"clustering_version": "llm-v1", "source": "gemini"},
                )
                db.add(cluster)
                db.flush()
                created += 1
                
                # Assign sinyal ke cluster ini
                for idx in cluster_data.get("video_indices", []):
                    if idx < len(unassigned):
                        signal = unassigned[idx]
                        db.add(TrendMembership(
                            cluster_id=cluster.id,
                            youtube_snipe_id=signal.id,
                            similarity_score=1.0,
                            membership_state="LLM_GROUPED",
                            weight=1.0,
                            feature_evidence={"source": "gemini_clustering"},
                        ))
                        cluster.member_count += 1
                        cluster.channel_count = len(set(
                            s.channel_id for s in [unassigned[i] for i in cluster_data.get("video_indices", []) if i < len(unassigned)]
                        ))
            
            db.commit()
            return {"created": created, "assigned": len(unassigned)}
    finally:
        store.client.delete(CLUSTER_LOCK)
```

#### 2. Di `backend/app/services/gemini_client.py`

**Pastikan method `analyze_topic_cluster` sudah ada dan berfungsi.** Jika belum, tambahkan:

```python
def analyze_topic_cluster(self, evidence: str) -> list[dict]:
    """
    Group unassigned YouTube Shorts into semantic topic clusters.
    
    Args:
        evidence: Newline-separated list of "[idx] Title | Channel: X | Niche: Y"
    
    Returns:
        List of {topic_title, topic_type, summary, entities, confidence, video_indices}
    """
    prompt = f"""You are grouping YouTube Shorts into topic clusters. Groups must have at least 2 videos sharing a specific, named topic or event.

Return a JSON array of clusters. Each cluster:
- topic_title: short, specific label (2-6 words). NOT generic like "funny", "viral", "entertainment". Must name the actual topic.
- topic_type: one of [sports, gaming, music, comedy, dance, food, tech, news, lifestyle, other]
- summary: one sentence explaining why these videos belong together
- confidence: 0.0-1.0 how confident you are this is a real cluster
- video_indices: array of integer indices that belong to this cluster

IMPORTANT:
- Only create clusters with 2+ videos
- Videos that don't fit any cluster should be left out (don't force-group)
- Don't create clusters based on broad categories like "Shorts compilation" or "Random clips"
- If videos share a specific person, event, trend, or clear topic → group them
- If you're unsure, set confidence < 0.5

Videos:
{evidence}

Return ONLY the JSON array, no other text."""
    
    # ... existing Gemini call logic ...
```

#### 3. Di `config.py`

Tambahkan:

```python
# LLM-powered topic clustering
topic_llm_clustering_enabled: bool = True
topic_llm_clustering_batch_size: int = 50
topic_llm_clustering_min_confidence: float = 0.70
```

---

## Verifikasi

Setelah implementasi, jalankan:

```bash
cd backend
python -c "
from app.tasks.youtube_trend_tasks import cluster_recent_signals
result = cluster_recent_signals()
print(result)
"
```

Cek database:
```sql
SELECT label, member_count, channel_count, status 
FROM trend_clusters 
WHERE status != 'PRIVATE_CANDIDATE' 
ORDER BY member_count DESC;
```

Hasil yang diharapkan:
- Cluster dengan 3-10 video (bukan 1)
- Cross-channel (minimal 2 channel berbeda)
- Label spesifik seperti "Indonesia vs Argentina Friendly" bukan "Indonesia · Argentina · Friendly"

---

## Catatan Penting

- ❌ Jangan hapus function `_cluster_vector` atau `_merge_private_clusters` — masih dipakai di `score_topic_trends`
- ❌ Jangan ubah `provisional_label()` — masih dipakai sebagai fallback
- ✅ Tambah LLM clustering sebagai **primary path**, lexical clustering sebagai **fallback**
- ✅ Gemini call sudah ada rate limiting di `gemini_client.py`
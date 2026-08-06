# Y-CGC V4 — Topic Trends Intelligence Plan

## 1. Product decision

Y-CGC remains an anonymous, zero-bias YouTube Shorts discovery system. Its
primary product view changes from a list of individual breakout videos to a
ranked list of **emerging topic clusters**.

An individual Short is still the raw evidence. A Topic Trend is a bounded,
time-aware cluster of semantically similar signal videos from independent
channels. The system must never use a topic, keyword, hashtag, or creator list
as an input to discovery. Titles, transcripts, frames, and Gemini output may
be used only **after** a video was independently discovered and passed a
velocity tier.

### Explicit scope boundaries

- Fresh discovery eligibility stays **under 24 hours from upload**.
- Existing validated signal history stays visible after 24 hours as research
  evidence; it must not be deleted or hidden simply because it aged.
- Whale, Proven Winner, and Gold labels are context fields only. They never
  select, exclude, or seed a video or cluster.
- A cluster is not a claim about global YouTube popularity. All dashboard
  values are labelled `observed` and are calculated only from Y-CGC members.
- A one-video event is a signal, not a public Topic Trend.

## 2. Target experience

### `/youtube/trends` — default product view

The page follows the information hierarchy of a topic-trends board while
keeping Y-CGC's existing dark UI:

| Field | Meaning |
| --- | --- |
| Rank | Rank by current cluster trend score, not raw lifetime views. |
| Topic | Neutral AI-generated cluster label, with `unlabelled` fallback. |
| Status | `EMERGING`, `ACCELERATING`, `CONFIRMED`, or `COOLING`. |
| Observed views | Sum of the latest observed view counts of member videos. |
| Trend | Small velocity / observed-view sparkline from cluster snapshots. |
| Evidence | Distinct videos, distinct channels, regions, and whale mix. |
| Related Shorts | Up to five member thumbnails; these are evidence, not search results. |

Filters: region/profile, niche, cluster status, channel context, and observed
time range. Tabs: `Topics` (default), `Posts` (existing individual signal
feed), and `Creators` (context-only breakdown).

### `/youtube/trends/[clusterId]` — cluster detail

1. Header: rank, generated topic label, status, first/last observed time,
   regions, member/video/channel counts, and confidence.
2. `Observed views` time series and `cluster velocity` time series, with 1h,
   6h, and 24h ranges. Members joining after cluster creation are marked.
3. All member cards: video, source channel, upload age at discovery, current
   views, VTR, tier, channel context, and direct YouTube link.
4. Evidence panel: why the videos were grouped, semantic confidence, shared
   factual terms, member/channel diversity, and duplicate/reupload warnings.
5. Enrichment panel: topic summary, niche, visual/transcript facts, and a
   clear `text-only` label when a frame was unavailable.
6. Audit panel: every cluster state transition and snapshot timestamp.

## 3. Data model

The existing `YoutubeSnipe` remains the permanent individual-signal record.
Add the following PostgreSQL entities through an Alembic migration:

### `trend_clusters`

- `id`, stable UUID/public slug, generated label, label confidence, niche;
- `status`, `trend_score`, `semantic_cohesion`, and `first_detected_at`;
- `last_observed_at`, `last_member_at`, `cooling_at`;
- `observed_views`, `observed_velocity_per_hour`, `acceleration`;
- `member_count`, `channel_count`, `region_mix`, `channel_context_mix`;
- `evidence_summary`, `cluster_reason`, and `model_metadata` JSON fields.

### `trend_memberships`

- `cluster_id`, `youtube_snipe_id`, unique pair;
- `joined_at`, `similarity_score`, feature evidence, and membership state;
- `is_reupload_suspect`, `is_same_channel_duplicate`, and `weight`.

### `trend_snapshots`

- `cluster_id`, observation timestamp;
- observed views, total/median velocity, acceleration;
- video/channel counts, new member/channel counts, and trend score;
- immutable snapshot reason/version for thesis auditability.

Indexes: `(status, trend_score desc)`, `(cluster_id, observed_at desc)`, and
unique membership pair. Raw feature payloads are bounded; large media remains
in the existing media store, not duplicated in cluster rows.

## 4. Evidence and clustering pipeline

### Stage A — eligible inputs

Only `EARLY`, `RISING`, and `BREAKOUT` records are candidates. `WATCH` and
`COOLED` are never used to start a public cluster. Existing historical signals
may be grouped for archive research, but only recent observations can affect
the live ranking.

### Stage B — feature collection after discovery

Feature priority, with source and confidence recorded:

1. Gemini niche, transcript summary, factual visual facts, and peak-frame
   analysis when present;
2. transcript/caption text; 
3. normalized title and hashtags, only as a weak fallback;
4. visual/text-only mode, language, region, upload/discovery timing, and
   velocity features.

No creator name, subscriber count, or channel status is used as a similarity
feature. Channel information is used only later for diversity and context.

### Stage C — semantic representations

Create an embedding only after a signal exists. Store the embedding and its
model/version separately from the signal's raw fields. PostgreSQL `pgvector`
is the planned similarity index. If enrichment is incomplete, use a lower
confidence lexical fingerprint and mark the membership provisional; do not
invent semantic certainty.

### Stage D — bounded assignment

Every clustering run:

1. process unassigned/recent signal candidates;
2. retrieve only recent candidate clusters by vector similarity;
3. compare semantic similarity, niche compatibility, temporal overlap, and
   factual-term overlap;
4. assign to the best cluster only when evidence exceeds a calibrated
   threshold; otherwise create a private one-video candidate;
5. merge clusters only with a recorded reason and reversible audit event;
6. recalculate aggregate values and write one immutable snapshot.

Single-video candidates expire from the live Topic view unless another
independent member validates them. They remain associated with their individual
signal and can be reconsidered during the bounded observation window.

## 5. Anti-bias and anti-duplication rules

To avoid calling a creator's own repeated uploads a trend:

- public `EMERGING` requires at least 2 signal videos from at least 2 channels;
- `ACCELERATING` and `CONFIRMED` require additional independent channel/video
  evidence, calibrated after the first 24-hour observation report;
- same-channel members receive capped weight; they remain visible but cannot
  dominate trend score;
- near-identical title, audio, frame, or reupload patterns are retained with a
  duplicate warning and reduced weight;
- cluster score rewards channel diversity and new-channel arrival, not merely
  raw views;
- Whale membership is displayed as context. A Whale cannot suppress a trend,
  nor can it make a single-channel trend public by itself.

## 6. Trend score and lifecycle

The score is versioned and stored with every snapshot. It is based on:

- recency and number of new members;
- distinct channels and regions;
- median/member-weighted velocity and acceleration;
- same-age relative performance of member videos;
- semantic cohesion and label confidence;
- penalties for duplicate concentration, one-channel dominance, stale members,
  and missing evidence.

Lifecycle:

```text
PRIVATE_CANDIDATE
  → EMERGING
  → ACCELERATING
  → CONFIRMED
  → COOLING
  → ARCHIVED
```

All thresholds begin as configuration values and are not silently hard-coded.
They will be calibrated only after enough 24-hour observations exist by age
bucket. Status changes record the reason and the input snapshot.

## 7. Celery tasks and schedules

| Task | Cadence | Responsibility |
| --- | --- | --- |
| `build_signal_features` | after EARLY/RISING/BREAKOUT | Collect permitted feature text and enqueue embedding. |
| `embed_signal_for_trends` | after features available | Generate/version semantic representation. |
| `cluster_recent_signals` | every 15 minutes | Assign, merge, update aggregates, and snapshot clusters. |
| `score_topic_trends` | every 15 minutes after clustering | Apply versioned trend score/lifecycle rules. |
| `cool_stale_topic_trends` | hourly | Transition stale clusters without deleting history. |
| `retry_pending_enrichment` | every 10 minutes | Retry media/transcript/Gemini independently of seed TTL. |

All tasks are idempotent, acquire scoped Redis locks, preserve retry metadata,
and never block anonymous discovery/velocity tasks.

## 8. API contract

- `GET /api/v1/youtube/trends`: ranked clusters, filters, related-short
  previews, and snapshot sparkline points.
- `GET /api/v1/youtube/trends/{cluster_id}`: header, snapshots, evidence,
  members, channel composition, and enrichment state.
- `GET /api/v1/youtube/trends/{cluster_id}/timeline`: compact chart payload.
- `GET /api/v1/youtube/posts`: existing individual signal feed.
- `GET /api/v1/youtube/observation-report`: add cluster counts, lifecycle
  transitions, diversity distribution, unclustered rate, and retry outcomes.

No endpoint implies global YouTube totals. Field names use `observed_*`.

## 9. Implementation phases

### Phase T0 — foundations

- Add configuration, SQLAlchemy models, Alembic migration, pgvector support,
  and cluster test fixtures.
- Add explicit retry scheduler for the currently pending enrichment records.
- Success: migration is reversible; no discovery or individual dashboard
  behavior regresses.

### Phase T1 — features and candidate clustering

- Build feature provenance and embedding pipeline.
- Create private candidates, memberships, duplicate flags, and audit events.
- Success: known fixture videos form correct clusters; unrelated fixtures do
  not merge.

### Phase T2 — scoring and historical snapshots

- Add snapshots, lifecycle transitions, trend ranking, and cooling.
- Success: synthetic time-series tests prove each state change and score is
  deterministic for a given input.

### Phase T3 — Topics dashboard

- Build Topics list, Posts/Creators tabs, filters, sparkline, and related
  Shorts evidence strip.
- Success: every card links to a detail route and reports only observed data.

**Implemented (2026-08-06):** `/youtube/trends` lists only public
cross-channel clusters (`EMERGING`, `ACCELERATING`, `CONFIRMED`) and makes the
number of private candidates explicit instead of publishing them as trends.
`/youtube/trends/{clusterId}` shows the stored observed-velocity line,
aggregate metrics, and the member Shorts that support the cluster. The
previous individual-signal dashboard remains available as **Signal posts**.
The Topics page can export the public ranking plus its member evidence as
`/api/v1/youtube/trends/export.csv`; a report never silently includes a private
one-video candidate.

### Phase T4 — Cluster detail and research report

- Build velocity chart, member evidence, channel composition, audit timeline,
  and CSV export.
- Success: an examiner can trace any trend number back to member snapshots.

### Phase T5 — live calibration

- Run at least 24 hours without modifying discovery inputs.
- Review false merges, one-channel clusters, delayed enrichment, and topic
  labels; tune configuration with a recorded scoring version.
- Only then activate any stricter public-rank threshold.

## 10. Test and acceptance matrix

1. Unit: similarity thresholds, duplicate penalties, state transitions, score
   monotonicity, and no-channel-bias invariants.
2. Integration: isolated PostgreSQL/Redis, idempotent memberships, retry task,
   snapshots, and API filters.
3. E2E fixtures: visual+text, text-only, heatmap-unavailable, transcript-
   unavailable, multi-channel cluster, and same-channel duplicate cases.
4. Live audit: manually inspect a small sample of clusters before describing
   them as thesis findings.

Release is accepted only when:

- discovery still has no keyword or creator-list input;
- every public trend has auditable member/channel evidence;
- cluster detail velocity is derived from stored snapshots;
- an unavailable heatmap never creates fabricated visual facts;
- all raw and derived state can be explained in the 24-hour report.

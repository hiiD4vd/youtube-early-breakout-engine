# Market Trends Engine — Broad YouTube Topic Coverage

## Product boundary

`Market Trends` answers: **what topics are visibly gaining momentum across the
observed YouTube market?** It is separate from `Early Breakouts`, which answers:
**which individual fresh videos are accelerating unusually early?**

The system will report an **observed market index**, never claim that it sees
every YouTube upload or has access to YouTube's private global ranking.

## Near-real-time definition

- Source ingestion target: every 5–10 minutes, subject to source limits.
- Statistics refresh target: every 10–15 minutes for active topic members.
- Topic score/snapshot: every 15 minutes.
- UI refresh: 30 seconds.
- All timestamps show the last successful source observation.

This is near-real-time polling. YouTube does not provide a public global upload
firehose or real-time topic-trend API.

## Separate source lanes

1. **Anonymous Shorts feed** — wide, logged-out, multi-region samples; no
   keywords, accounts, or watch history.
2. **Public market/chart lane** — official public video charts and category /
   region samples. This admits news and large creators intentionally.
3. **Broad public discovery lane** — rotating category, region, and language
   coverage. It is explicitly tagged with source/provenance and never treated
   as an unbiased random sample.

Each observation stores source lane, region, language, fetch time, and source
response version. The old Early Breakout pipeline remains untouched.

## Bias controls (not a false claim of zero bias)

No public external system can be bias-free. The defensible controls are:

1. No logged-in/personalized account for anonymous feed sampling.
2. Balanced rotating region/language source budget; no single source lane can
   dominate solely due to its volume.
3. Per-channel contribution cap in **topic ranking only**. Whale/news videos
   remain visible as evidence but cannot manufacture a multi-channel trend.
4. Re-upload/duplicate detection and transparent weights.
5. Source mix, whale/news/underdog mix, coverage, and confidence are visible
   on every topic.
6. Review feedback calibrates clustering only; it never changes discovery input.

## Topic pipeline

1. Ingest observed market candidates from each lane into a permanent
   `market_video_observations` store.
2. Refresh statistics and retain time-series snapshots.
3. Enrich representative candidates with transcript/frame + Gemini 3.5
   Flash-Lite. Cheap lexical features remain the fallback.
4. Cluster by semantic topic, entities, visual format, and language-aware text.
5. Merge/split online clusters; preserve every membership/audit event.
6. Score member/channel diversity, weighted observed velocity, acceleration,
   cross-source spread, freshness, duplicate risk, and whale concentration.
7. Publish `EMERGING → ACCELERATING → CONFIRMED → COOLING` market topics.

## Phased delivery

### M0 — data contract and isolation

- Create Market Trend models/migration and source provenance fields.
- Keep existing `YoutubeSnipe` and Trend Cluster tables unchanged.
- Success: one cannot accidentally mix Market Trends and Early Breakouts.

### M1 — broad public collection

- Add scheduled source lanes with quota/rate-limit budget and coverage report.
- Store observations and deduplicate globally.
- Success: dashboard reports exact source/region coverage, not a vague count.

### M2 — semantic topic grouping

- Use Gemini output when available plus lexical fallback.
- Add cross-lane clustering, topic labels, merge/split, and confidence.
- Success: fixtures prove same-topic cross-channel videos merge and unrelated
  videos do not.

### M3 — ranking and Market Trends UI

- Create TikTok-Studio-style Topics list and detail page.
- Show observed momentum, source mix, channel context mix, evidence videos,
  and last observed timestamps.
- Success: user can explain every rank from stored evidence.

### M4 — calibration and 24/7 operation

- Review queue targets uncertain market clusters.
- Evaluate false merges and source dominance after 7 days.
- Deploy worker to always-on infrastructure before claiming continuous coverage.

## Acceptance criteria

- `Market Trends` is visibly labeled as near-real-time observed coverage.
- Whale/news videos are included but channel concentration is measurable and
  capped only for topic ranking.
- No keyword or creator list feeds the anonymous Early Breakout discovery lane.
- Every market topic exposes source and channel composition.
- System operates safely if a source lane is unavailable; it degrades coverage
  rather than fabricating a trend.

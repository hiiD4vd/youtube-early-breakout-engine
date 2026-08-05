# Reliability Plan — Real-World Pipeline

This plan follows `AI-BLUEPRINT.md`. It preserves zero-bias discovery: no
keyword search and no creator whitelist are introduced as a fallback.

## Evidence that drives this work

The first live seed passed the configured velocity threshold. `yt-dlp` could
only expose storyboard formats for that video, so no stream was available for
ffmpeg. The system correctly withheld it from PostgreSQL, but the reason was
visible only in worker logs. Discovery batches also often contain only videos
older than 72 hours, which the filter correctly rejects.

## Workstream A — Media extraction reliability

### State contract

A velocity pass creates a pending breakout record in Redis before media work.
It has a state: `pending_media`, `retry_scheduled`, `media_unavailable`, or
`ready_for_enrichment`. The state records a reason code, attempt count, next
retry time, and last error summary. No state is written to PostgreSQL until
Gemini succeeds.

### Extraction sequence

1. Read heatmap metadata with current yt-dlp default clients.
2. Retry only recoverable cases with a bounded client fallback sequence.
3. If a playable stream exists, download one temporary source, extract one
   peak frame via ffmpeg, and delete the temporary source.
4. If only a storyboard or no heatmap exists, retain the candidate in Redis
   with a concrete reason; do not invent a frame or mark it final.
5. Retry at bounded intervals while the seed/pending record is live; then mark
   terminally unavailable and retain the reason for operations visibility.

### Acceptance criteria

- One failed candidate never blocks another candidate.
- A retry has a visible attempt count and reason.
- A non-playable video cannot reach Gemini/PostgreSQL.
- Peak-frame artifacts use a persistent, bounded storage location.

## Workstream B — Fresh seed yield

### Safe variation strategy

Each anonymous session uses only neutral session dimensions: a rotating
region/language profile and a fresh logged-out visitor context. Every response
is still obtained from the Shorts distribution surface. No topic, search query,
channel list, or follower criterion is used.

### Quality controls

- Keep per-profile metrics: seen, fresh accepted, old rejected, malformed.
- Use a short run lock per profile; do not overlap requests.
- Deduplicate globally by `video_id` in Redis.
- Keep the 72-hour rejection as a hard rule.
- Rotate profiles only after the current profile completes; rate-limit every
  request and retain HTTP errors without retry storms.

### Acceptance criteria

- The dashboard reports fresh-yield by profile.
- All accepted seeds have a valid channel ID, view count, and timestamp.
- No code path accepts keyword-derived or curated-creator candidates.

## Workstream C — Operations visibility

The live dashboard gains a compact operations strip showing: active seeds,
fresh yield, VTR passes, pending-media count, retry count, terminal-media
failures, final breakouts, last scan, and next expected scan in WIB. A detail
panel lists the latest failure reason but never leaks secrets.

## Validation sequence

1. Unit-test state transitions, VTR boundary conditions, and extractor reason
   classification.
2. Integration-test media retry state using mocked yt-dlp/ffmpeg.
3. Run a real anonymous scan and inspect only Redis operational state.
4. Observe a real candidate through media and Gemini before calling the system
   ready for deployment.

## Deployment gate

Before 24/7 deployment: pin an upgrade policy for yt-dlp, configure persistent
media storage, alert on sustained zero fresh-yield or media failure rate, and
document migration/restart/recovery procedures.

# Y-CGC V4 Implementation Plan

This plan operationalizes `AI-BLUEPRINT.md`. If this document and the
blueprint ever conflict, the blueprint wins.

## Non-negotiable product rules

1. No keyword search is a discovery source.
2. No creator whitelist, follower-count ranking, or top-creator source is used.
3. A candidate becomes permanent only after it passes velocity validation and
   Gemini completes factual enrichment.
4. Redis holds ephemeral discovery data only; PostgreSQL holds validated
   breakouts only.
5. Each external request must have timeouts, structured logs, and bounded retry.

## Phase 0 — Foundation and data contract

**Scope:** project infrastructure, FastAPI, Celery, Redis, PostgreSQL, Alembic,
Next.js, and the permanent breakout schema.

**Delivered:** root Docker Compose stack, backend image with ffmpeg,
configuration, SQLAlchemy `YoutubeSnipe`, initial Alembic migration, Celery
application, FastAPI health endpoint, and Next.js 14 starter.

**Final verification before Phase 1:**

1. Run `docker compose up --build`.
2. Run `docker compose exec backend alembic upgrade head`.
3. Verify `GET /health` returns 200.
4. Verify table `youtube_snipes` exists and has the unique `video_id` constraint.

## Phase 1 — Unbiased seed discovery

**Goal:** populate a rolling Redis pool from the logged-out Shorts feed.

**Components:**

- `services/youtube_client.py`: a narrowly scoped HTTP client for YouTube
  internal endpoints. It owns headers, request timeout, response parsing, and
  sanitized error reporting.
- `services/seed_store.py`: Redis repository, not raw Redis calls scattered
  throughout tasks.
- `tasks/youtube_seed_tasks.py`: Beat-triggered discovery task every 30 minutes.
- `core/redis_keys.py`: one canonical key naming policy.

**Redis contract:**

- `ycgc:youtube:seed:{video_id}`: JSON seed object with 24-hour TTL.
- `ycgc:youtube:seed_ids`: sorted set / index of current seed IDs, cleaned when
  individual keys expire.
- `ycgc:youtube:lock:seed-discovery`: short-lived distributed lock to prevent
  overlapping Beat runs.
- `ycgc:youtube:breakout:{video_id}`: idempotency lock to stop reprocessing a
  video while its downstream task is active.

**Seed payload minimum:** `video_id`, `channel_id`, `channel_title`, `title`,
`seed_view_count`, `published_at`, `seeded_at`, `video_url`, `thumbnail_url`,
and a response-version marker. A seed older than 72 hours is discarded before
writing Redis.

**Acceptance criteria:** valid source responses can be parsed; duplicate IDs
are idempotently updated; all seed keys receive 24h TTL; no PostgreSQL write is
made; malformed source payloads are logged without terminating the task.

## Phase 2 — Velocity signal and peak frame

**Goal:** turn eligible seeds into provisional breakouts with a reproducible
numeric signal and exactly one peak frame.

**Components:**

- `tasks/youtube_velocity_tasks.py`: scans current seed IDs every two hours
  (configurable 1–3h) and refetches present view counts.
- `services/velocity.py`: pure functions for elapsed time, delta views,
  VTR, and threshold decision. Unit-tested independently.
- `services/heatmap.py`: invokes yt-dlp only after a VTR pass and reads heatmap
  metadata defensively.
- `services/frame_extractor.py`: invokes ffmpeg with a validated timestamp and
  saves one JPEG in a managed local media directory.

**Initial measurement:** `velocity_per_hour = (current_view_count -
seed_view_count) / max(elapsed_hours, minimum_elapsed_hours)`. Threshold,
minimum view delta, and minimum observation window are environment settings,
not hard-coded business logic. We record the full input values so each score is
auditable.

**Idempotency and failure policy:** a per-video Redis lock prevents concurrent
heatmap work. A failed yt-dlp/ffmpeg job is retried with exponential backoff;
it never creates a PostgreSQL row. If heatmap is unavailable, use a documented
fallback timestamp only if the configuration explicitly permits it.

**Acceptance criteria:** below-threshold seeds do no media work; passing videos
produce one valid image; repeated Beat runs do not duplicate jobs; a task cannot
run indefinitely; frame artifacts have an owner video ID and cleanup policy.

## Phase 3 — AI factual funnel and permanent persistence

**Goal:** enrich only validated provisional breakouts, then persist them once.

**Components:**

- `services/transcript.py`: transcript acquisition with an explicit unavailable
  state; it must not fabricate transcript text.
- `services/gemini_client.py`: timeout, response validation, model configuration,
  retry classification, and cost-safe input sizing.
- `tasks/youtube_enrichment_tasks.py`: downstream Celery task scheduled only by
  a successful Phase 2 result.
- `schemas/youtube.py`: typed Gemini response and API response contracts.

**Gemini output schema:** `niche`, `visual_facts[]`, `transcript_summary`, and
`confidence`. The prompt limits Gemini to categorization and observable facts;
it must not make recommendations or invent facts. Raw model output is retained
in `ai_analysis` for audit, while the normalized facts go in `visual_facts`.

**Database invariants:** `video_id` is unique; save uses upsert semantics; data
written includes seed/current views, VTR, score inputs, peak timestamp, model
metadata, and timestamps. A retry after a partial failure updates the same row,
not a second row.

**Acceptance criteria:** Gemini is never called for a non-breakout; invalid AI
JSON is rejected/retried; one complete valid result becomes one `YoutubeSnipe`
row; secrets never appear in logs or API responses.

## Phase 4 — API and dashboard

**Goal:** display final rows as a clear, real-time breakout leaderboard.

**Backend:** `GET /api/v1/youtube/breakouts` with pagination, validated niche
filter, deterministic sort (most recent / highest VTR), and a lightweight
response DTO that never exposes internal raw credentials or huge blobs.

**Frontend:** `/youtube` route, SWR polling, loading/empty/error states, niche
filter, cards/table containing title, thumbnail/peak frame, channel, age,
views, VTR, niche, and Gemini factual observations. The dashboard reads only
PostgreSQL-backed API data; it does not contact YouTube, Redis, or Gemini.

**Acceptance criteria:** responsive usable page; filter changes URL/query state;
new valid data appears through polling; empty state works on a fresh database;
API failures have a visible retryable state.

## Hardening and handoff

1. Unit tests for parsers, VTR boundaries, Redis store, and Gemini schema.
2. Integration tests with mocked YouTube/Gemini and disposable PostgreSQL/Redis.
3. Structured logging with video ID/task ID; health/readiness endpoints;
   Celery failure visibility.
4. Rate limits, request backoff, configurable task concurrency, and a daily
   Gemini safety cap.
5. Artifact retention/cleanup so peak frames do not grow unbounded.
6. `.env.example`, deployment instructions, migration runbook, and thesis demo
   procedure.
7. Manual end-to-end run with a recorded sample before deployment.

## Phase gates

We stop after each phase, run its acceptance checks, summarize evidence, and
obtain confirmation before continuing. This prevents a change in an unverified
source API or data contract from contaminating later stages.

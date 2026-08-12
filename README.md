# Y-CGC V4

Standalone MVP for YouTube Content Gap Creator. Its FastAPI, Celery, Redis,
PostgreSQL, and Next.js layout mirrors ViralEngine, while keeping this project
independent for thesis development.

## Local start

1. Copy `.env.example` to `.env` and change `POSTGRES_PASSWORD` and the matching
   `DATABASE_URL` password.
2. Recommended one-shot start from the repo root:

   `.\run-all.ps1`

3. Manual fallback if you want to run things step by step:

   - `docker compose up --build -d`
   - `docker compose exec -T backend alembic upgrade head`
   - `cd frontend`
   - `npm run dev:clean`

4. Open `http://localhost:3010`; the API health endpoint is `http://localhost:8010/health`.

## Database migration

After the containers are running, create the schema with:

`docker compose exec backend alembic upgrade head`

The initial migration creates `youtube_snipes`. Future schema changes should be
generated with `docker compose exec backend alembic revision --autogenerate -m "description"`.

## Phase 1 seed discovery

Celery Beat schedules anonymous Shorts discovery every 30 minutes. It uses the
logged-out Shorts distribution feed and anonymous Innertube requests only; it
does not submit a search query or use a creator list. Redis keeps each complete,
fresh seed under `ycgc:youtube:seed:{video_id}` for 24 hours. Candidates older
than 72 hours are discarded before Redis writes.

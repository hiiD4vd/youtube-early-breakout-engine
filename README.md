# Y-CGC V4

Standalone MVP for YouTube Content Gap Creator. Its FastAPI, Celery, Redis,
PostgreSQL, and Next.js layout mirrors ViralEngine, while keeping this project
independent for thesis development.

## Local start

1. Copy `.env.example` to `.env` and change `POSTGRES_PASSWORD` and the matching
   `DATABASE_URL` password.
2. Start infrastructure and backend: `docker compose up --build`.
3. In a separate terminal, run `cd frontend`, `npm install`, then `npm run dev`.
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

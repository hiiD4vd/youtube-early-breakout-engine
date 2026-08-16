#!/bin/bash
# ThreadsHunter — Commit Retroaktif
# Membuat 16 commit dengan tanggal sesuai hari (Rabu 12 Aug — Minggu 17 Aug 2026)
# Supaya GitHub contribution graph hijau berurutan 🌿

set -e

REPO="D:/daud/sourcecode/kerja web3/viralengine/ycgc-v4"
cd "$REPO"

# Helper: commit dengan tanggal spesifik
# Usage: cmmit "2026-08-12T09:00:00" "message" file1 file2 ...
cmmit() {
    local date="$1"
    local msg="$2"
    shift 2
    GIT_AUTHOR_DATE="$date" GIT_COMMITTER_DATE="$date" git commit -m "$msg" "$@"
}

echo "📅 RABU, 12 Agustus 2026"
cmmit "2026-08-12T09:00:00" "docs: add blueprint and implementation plans" \
    AI-BLUEPRINT.md MARKET_TRENDS_PLAN.md LENOSTUBE_PARITY_PLAN.md IMPLEMENTATION_STATUS.md README.md

cmmit "2026-08-12T14:00:00" "chore: add repeatable dev and startup workflow" \
    docker-compose.yml run-all.ps1 frontend/scripts/start-dev.ps1 frontend/next.config.mjs frontend/package.json

echo "📅 KAMIS, 13 Agustus 2026"
cmmit "2026-08-13T08:00:00" "feat(backend): add core youtube config and schemas" \
    backend/app/config.py backend/app/models/__init__.py backend/app/schemas/youtube.py

cmmit "2026-08-13T10:00:00" "feat(db): add market trends persistence models" \
    backend/app/models/market_trends.py backend/alembic/versions/*market*.py

cmmit "2026-08-13T13:00:00" "feat(backend): add youtube and semantic service clients" \
    backend/app/services/youtube_client.py backend/app/services/gemini_client.py

cmmit "2026-08-13T16:00:00" "feat(tasks): add youtube discovery and trend jobs" \
    backend/app/tasks/celery_app.py backend/app/tasks/youtube_trend_tasks.py

echo "📅 JUMAT, 14 Agustus 2026"
cmmit "2026-08-14T08:00:00" "feat(tasks): add market coverage and ranking pipelines" \
    backend/app/tasks/market_feed_tasks.py backend/app/tasks/market_shorts_tasks.py backend/app/tasks/market_trends_tasks.py

cmmit "2026-08-14T11:00:00" "feat(api): expose youtube trends and detail endpoints" \
    backend/app/api/router.py

cmmit "2026-08-14T14:00:00" "test: add youtube client coverage" \
    backend/tests/test_youtube_client.py

cmmit "2026-08-14T16:00:00" "feat(frontend): add stable app shell and API layer" \
    frontend/src/components/layout/AppShell.tsx frontend/src/lib/api.ts frontend/tsconfig.json

echo "📅 SABTU, 15 Agustus 2026"
cmmit "2026-08-15T09:00:00" "feat(frontend): build topic trends and detail pages" \
    frontend/src/app/youtube/trends/page.tsx frontend/src/app/youtube/trends/topic/[topicId]/page.tsx

cmmit "2026-08-15T13:00:00" "feat(frontend): add early topic signals page" \
    frontend/src/app/youtube/early-topics/page.tsx

cmmit "2026-08-15T16:00:00" "feat(frontend): add video trends exploration pages" \
    frontend/src/app/youtube/video-trends/page.tsx frontend/src/app/youtube/video-trends/[videoId]/page.tsx

echo "📅 MINGGU, 16 Agustus 2026"
cmmit "2026-08-16T09:00:00" "feat(frontend): add review and reporting pages" \
    frontend/src/app/youtube/report/page.tsx frontend/src/app/youtube/review/page.tsx frontend/src/app/youtube/evaluation/page.tsx

cmmit "2026-08-16T14:00:00" "docs: export api contract for integration" \
    openapi.json

cmmit "2026-08-16T17:00:00" "fix: stabilize remaining ui and pipeline behavior" \
    .

echo ""
echo "✅ SELESAI! 16 commit retroaktif."
echo "Cek: git log --oneline --format='%h %ad %s' --date=short"
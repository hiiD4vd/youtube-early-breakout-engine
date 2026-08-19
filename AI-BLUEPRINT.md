# Y-CGC V4.0 - MASTER AI BLUEPRINT
**CRITICAL INSTRUCTION FOR AI ASSISTANT:** Read this entire document before generating any code. This document serves as the absolute source of truth for the architecture, data pipeline, and tech stack of this project. Do NOT deviate from this architecture or introduce alternative logic (e.g., keyword searching or top-creator tracking).

## 1. Project Overview
*   **Project Name:** Y-CGC V4.0 (YouTube Early Breakout Engine)
*   **Goal:** Build an autonomous, 24/7 background service that discovers "Meme Coins" (videos from random, 0-follower accounts that are going viral from 1k to 1M views) on YouTube Shorts.
*   **Architecture Pattern:** Fullstack Headless Service (FastAPI Backend + Next.js Frontend).
*   **Core Philosophy (No Keyword / No Creator List):** We DO NOT search YouTube using keywords. We DO NOT track a list of Top Creators. We sample the anonymous Shorts distribution surface.
    *   **Honesty note (supersedes earlier "Zero Bias" wording):** the anonymous Shorts feed is itself curated by YouTube's recommendation system. Our sample therefore represents *videos granted early exposure by the algorithm*, not the population of all uploads, and not proof of purely organic virality. True zero-bias sampling is impossible because YouTube exposes no list of all uploads. We mitigate by (a) sampling multiple neutral region/language profiles, and (b) classifying every detected signal by channel context (UNDERDOG / ESTABLISHED / WHALE) so the audience size behind each signal is measured, not assumed.

## 2. Tech Stack
*   **Backend:** Python 3.10+, FastAPI (REST APIs).
*   **Background Workers:** Celery (with Redis as the message broker).
*   **Caching & Noise Filtering:** Redis (TTL-based storage).
*   **Permanent Database:** PostgreSQL (using SQLAlchemy ORM and Alembic).
*   **AI/ML:** Gemini 2.5 Flash Lite API.
*   **Data Extraction:** `yt-dlp` (for Heatmap parsing) and `ffmpeg` (for frame extraction).
*   **Frontend:** Next.js 14, TypeScript, Tailwind CSS, Axios/SWR.

## 3. The 4-Stage Data Pipeline (The Funnel)

### Stage 1: The Seed (Incognito Shorts Feed Scraper)
*   **Trigger:** Celery Beat task running periodically (e.g., every 30 minutes).
*   **Action:** Send raw HTTP requests to YouTube's internal API (`/youtubei/v1/reel/reel_item_watch`) simulating a logged-out user scrolling the Shorts feed. Do NOT use heavy browsers like Playwright/Selenium.
*   **Extraction:** Grab `Video_ID`, `Channel_ID`, `View_Count`, and `PublishedAt`.
*   **Storage:** Store this raw metadata in **Redis** with a 24-hour Time-To-Live (TTL).
*   **Filter:** Drop any video where `PublishedAt` is older than 24 hours. We only want fresh content early enough to be useful as a breakout signal.

### Stage 2: The Signal (Velocity Check & Frame Extraction)
*   **Trigger:** Celery Beat task running every 1 to 3 hours.
*   **Action:** Retrieve the seed videos from Redis. Refetch their current `View_Count` via the YouTube API.
*   **Calculation:** Calculate the Velocity-to-Time Ratio (VTR) -> `(Current Views - Seed Views) / Time Elapsed`.
*   **Filter:** If the VTR is below our breakout threshold, do nothing. If it exceeds the threshold, it is flagged as an "Early Breakout".
*   **Visual Extraction:** For breakouts only, invoke `yt-dlp` to extract the "Most Replayed" (Heatmap) JSON metadata to find the peak retention timestamp (e.g., 0:45). Then, invoke `ffmpeg` to extract exactly 1 still frame (.jpg/.png) at that exact timestamp.

### Stage 3: AI Categorization & Factual Extraction
*   **Trigger:** Triggered automatically after Stage 2 success.
*   **Action:** Send the 1 extracted peak frame + the video's transcript to the **Gemini 2.5 Flash Lite API**.
*   **Prompt Constraints:** 
    1.  Categorize the video into a Niche (e.g., Gaming, Finance, Comedy, Random).
    2.  Extract factual visual anomalies from the frame (e.g., "The lighting is dark", "There is red text on screen saying 'Wait for it'"). Do NOT provide strategic advice.
*   **Storage:** Save the final validated video, its VTR, Niche, and Gemini facts to **PostgreSQL** (the `YoutubeSnipe` table).

### Stage 4: Next.js Dashboard
*   **Action:** Build a clean, modern Next.js 14 dashboard.
*   **Features:** A real-time leaderboard fetching data from the FastAPI `/api/v1/youtube/breakouts` endpoint. Users can filter breakouts by Niche (as categorized by Gemini in Stage 3).

## 4. Execution Phases (For the AI Assistant)
When the user asks you to start, please execute in this exact order. Await user confirmation before proceeding to the next phase.

*   **Phase 0:** Initialize Project & Database Setup. (Initialize Next.js and FastAPI folders. Write SQLAlchemy models and setup Alembic).
*   **Phase 1:** Implement Stage 1 (Incognito Scraper -> Redis).
*   **Phase 2:** Implement Stage 2 (Celery Velocity Tasks + yt-dlp/ffmpeg).
*   **Phase 3:** Implement Stage 3 (Gemini API Integration).
*   **Phase 4:** Implement Stage 4 (Next.js Dashboard UI).

*(End of Blueprint)*

## Active project amendments

The following user-approved decisions supersede older wording in this document:

1. Fresh discovery rejects videos at or above **24 hours** old; the earlier
   72-hour wording is obsolete.
2. Validated signal history remains in PostgreSQL after the fresh discovery
   window as auditable research evidence.
3. Topic Trends is a post-signal clustering layer. It never supplies keywords,
   topics, or creator lists to anonymous discovery. Its complete implementation
   plan is `TOPIC_TRENDS_PLAN.md`.
4. Seed view counts must be exact integers from the Innertube `player`
   response. Candidates whose view count survives only as compact feed text
   ("1.2K views") are rejected (`imprecise_view_count`), because rounding
   noise (~100 views) exceeds our lowest velocity tier (250/h).
5. Every signal is classified by channel context (UNDERDOG / ESTABLISHED /
   PROVEN_WINNER / WHALE) via subscriber count + recent-view history. The
   "0-follower meme coin" claim is only valid for rows classified UNDERDOG.
6. The active semantic service is an **OpenAI-compatible configurable
   provider**, not a hard-coded Gemini client. Provider base URL, key, and
   model come from environment variables. A provider outage must pause AI
   enrichment without stopping discovery or deleting stored evidence.
7. Broad video intelligence has two independently labelled sources:
   **Official YouTube regional charts** (durable fallback) and the optional
   **InnerTube General Browse** lane (additional discovery coverage). If the
   latter has a network/source failure, its endpoint may show official-chart
   fallback data only when `data_mode` clearly says so; it must never pretend
   that fallback rows came from InnerTube.
8. Apify is disabled unless explicitly re-enabled by the user. Scheduled
   collection currently relies on YouTube/InnerTube lanes.
9. A topic is never a single video. Public/exploratory topic rows require at
   least two videos from two independent channels. Semantic clustering uses a
   normalized centroid plus matching theme, recognizable format, or entity +
   topic type. Generic lexical overlap alone may not merge evidence.
10. Local frontend development uses exactly one host `next dev` process.
    Normal startup must not run `next build`, and Docker's frontend service is
    opt-in so it cannot compete with the host for port 3010 or the Next cache.

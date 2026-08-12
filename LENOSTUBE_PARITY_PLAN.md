# LenosTube Functional-Parity Plan

## Product boundary

Build **YouTube Video Trends** as a general-video product first. It is a
separate sibling of Y-CGC's Shorts Intelligence, never a fallback source for
Early Topic Signals and never an input that changes anonymous Shorts discovery.

The target is functional parity with the public LenosTube experience:

1. ranked general YouTube videos;
2. selectable market and time window (today, week, month);
3. video metadata: title, channel, thumbnail, duration, age, views,
   engagement, observed rank change;
4. market breadth and persistence: regions and a streak/history graph;
5. filters and drill-down;
6. transparent scope and source health.

"Parity" means equivalent user-facing capabilities, not copying its data,
branding, HTML, or claiming access to its private collection system.

## Current foundation

- The official YouTube `videos.list(chart=mostPopular)` collector already
  stores every chart row, including non-Shorts, with source rank, region,
  statistics and timestamp.
- `MarketVideoObservation` is append-only. This enables rank movement,
  observed-country breadth and history without mixing rows into Shorts.
- `/youtube/video-trends` is the first dedicated UI/API. It uses only
  `official_chart` rows whose status is not `VERIFIED_SHORTS`.

## Implementation sequence

### Stage A — exact chart ledger

- Make chart scan identity deterministic by region/category/scan time.
- Persist best rank per market per video per scan; retain category memberships.
- Add rolling summaries for 1, 7 and 30 days, including entry/exit and rank
  movement.
- Display clearly: **observed by this system**, never YouTube-wide totals.

### Stage B — parity dashboard

- Add Today / 7 days / 30 days controls.
- Add region, category, duration and upload-age filters.
- Add total views, likes, comments, observed days, tracked regions and rank
  movement.
- Add a detail screen with rank/history chart, daily observations and all
  tracked regions.

### Stage C — collection coverage

- Expand the official chart region list independently: each added region gets
  its own request budget; existing regions are never divided.
- Show data freshness, quota failures and source runs in a dedicated source
  health panel.
- Only add an external licensed source if it has permission and a stable API;
  the external source remains labelled separately from official chart data.

#### 110-region implementation

The general Video Trends lane reads YouTube's supported-country catalog from
`i18nRegions`, caches it for seven days, and then rotates a fixed fair slice
through up to 110 supported regions. It asks for the complete regional
`mostPopular` chart, not a keyword search or a Shorts feed. Four countries are
collected every ten minutes by default, so an initial 110-country sweep takes
about four hours and thirty-five minutes. A chart failure is attached to the
individual country health record and never cancels the rest of the cycle.

### Stage D — later Y-CGC adaptation

Only after Stage A–C are stable, decide which general-market topics can become
context for a new product view. They must not become Early Signals merely
because they are popular.

## Anti-regression: white/un-styled screen

Next.js dev mode writes mutable artefacts to `frontend/.next`. Running a
second `next dev` process while an old process/cache remains produces HTML
whose referenced CSS/JS belongs to another build. The browser then renders
plain HTML.

Use **one** startup command only:

```powershell
cd "D:\daud\sourcecode\kerja web3\viralengine\ycgc-v4\frontend"
npm run dev:clean
```

`dev:clean` stops only the process listening on port 3010, clears only the
rebuildable `.next` cache, and launches one clean Next dev server. It does not
touch PostgreSQL, Redis, Docker data, source files, or Git history.

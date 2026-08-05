# Early Signal and Anonymous Coverage Plan

## Goal

Find more fresh Shorts from the anonymous distribution feed and surface a
probabilistic early warning before a video becomes an obvious breakout. This
does not use keyword search, channel lists, follower counts, or creator history.

## Product change: signal tiers

The system will retain `signal_detected` as the outward result and attach one
of three evidence-based tiers:

| Tier | Purpose | Required evidence |
| --- | --- | --- |
| `EARLY` | Watchlist while a Short is still small | Fresh seed, minimum sample window, age-normalized VTR above peer baseline |
| `RISING` | Stronger candidate | Two positive snapshots and positive acceleration |
| `BREAKOUT` | High-confidence distribution spike | Three snapshots, sustained acceleration, and top percentile among same-age seeds |

The dashboard must show the tier as a probability signal, never as a claim that
the video certainly will go viral.

## Scoring design

### Snapshot model

Redis retains a compact series for each seed: `(observed_at, view_count)`. A
snapshot is collected every 15–30 minutes for fresh candidates; the wider pool
continues at the normal interval to control cost and source load.

### Features

1. **Age-normalized velocity**: compare views/hour only against seeds in a
   similar upload-age bucket, e.g. 0–2h, 2–6h, 6–12h, 12–24h.
2. **Acceleration**: current VTR minus previous VTR. Positive acceleration is
   more informative than one large cumulative delta.
3. **Repeat confirmation**: require two or three increasing snapshots before
   assigning RISING/BREAKOUT.
4. **Relative percentile**: calculate percentile within same-age observations
   from the anonymous pool, not an absolute view threshold alone.
5. **Minimum evidence**: protect against tiny denominators with minimum age,
   minimum view delta, and minimum snapshot count.

All raw inputs, score version, and thresholds are retained with the signal for
auditing in the thesis.

## Increasing anonymous coverage safely

### Coverage means depth, not just more profiles

Adding profiles alone is insufficient when every profile observes only a few
items. Coverage is measured as **unique raw candidates seen per profile per
run**, before the 24-hour freshness filter. A profile can return few visible
items for a particular run; it must therefore traverse several independent
reel steps and, later, several fresh anonymous sessions.

The current baseline is 3 neutral profiles (`ID/id`, `US/en`, `GB/en`), a
maximum of 20 reel steps per profile per run, a maximum of 200 accepted seeds
per profile, and a 30-minute interval. The 200 number is a ceiling, not a
guarantee: each reel step can expose a variable number of videos and many will
be rejected as older than 24 hours. The dashboard records the actual seen and
fresh yield for every profile.

### Depth expansion plan

1. **Baseline measurement (today):** keep the 3 existing profiles and current
   20-step cap unchanged. Collect at least 12 hours, preferably 24, of
   per-profile `seen`, `fresh`, duplicate, error, and request-duration data.
   This becomes the reference point; without it we cannot tell whether a later
   change truly improves fresh yield.
2. **Deeper traversal:** if error rate remains low, raise the reel-step cap in
   small increments (20 -> 30 -> 40), keeping the accepted-seed cap and
   30-minute schedule. Stop an individual run at its request/time budget even
   if its nominal cap has not been reached.
3. **Independent anonymous sessions:** add a configurable
   `sessions_per_profile` value (initially 2, later at most 3). Each session
   obtains a new logged-out Shorts bootstrap and then performs a shallow,
   bounded traversal. This increases distinct distribution samples, rather
   than repeatedly following one feed sequence.
4. **Efficient hydration:** reject an item from the light reel response as
   soon as its displayed age is clearly over 24 hours; request full player
   metadata only for candidates that may be fresh or whose age is incomplete.
   This turns the request budget into more raw observations instead of spending
   it on obviously old videos.
5. **Global deduplication and reporting:** deduplicate all sessions and
   profiles by `video_id` before Redis. Report raw seen, unique seen, fresh
   accepted, old rejected, and duplicates so a higher number cannot be
   mistaken for genuinely broader coverage.

### Safety and bias guardrails

- The profiles remain fixed neutral region/language viewpoints; no keywords,
  subscribed accounts, channel lists, or creator metrics are introduced.
- Session order is staggered and each profile has its own lock. Requests are
  bounded, sequential within a session, and back off/cool down on 429/5xx.
- A configuration increase is accepted only when fresh-yield improvement is
  greater than the added request cost and source errors remain below the
  documented threshold.
- This reduces sampling bias; it does not claim that an algorithmic feed can
  be perfectly bias-free.

### Profile pool

Use a configured, documented list of neutral logged-out profiles, each only
containing `region` and `language`, e.g. `ID/id`, `US/en`, `GB/en`. Profiles
are distribution viewpoints, not topics. The study configuration chooses a
fixed list before data collection and records it in the run metadata.

### Discovery execution

1. A scheduler dispatches one shallow anonymous reel run per profile.
2. Runs are staggered, not burst concurrently; each has a per-profile lock.
3. Each run starts from its own fresh logged-out `/shorts` bootstrap context.
4. Reels are followed only up to a strict request/page cap.
5. All resulting `video_id`s deduplicate into the existing global Redis pool.
6. The 24-hour filter remains mandatory before accepting a seed.

### Initial safety limits

- 3 profiles maximum in development.
- 1 request stream per profile; no browser automation.
- 20 reel steps maximum per profile/run (the currently deployed baseline).
- 30-minute discovery interval initially.
- Exponential backoff on HTTP 429/5xx and a cooldown after repeated failures.

These are configuration values, not hard-coded. We increase them only after
measuring fresh-yield and error rate for 24 hours.

## Operations metrics

Dashboard/API status will add:

- fresh accepted / seen / rejected-old per profile;
- active early, rising, and breakout counts;
- median seed age and age-bucket distribution;
- extraction/enrichment success rate;
- source error and cooldown state;
- last and next scan in WIB.

## Validation gates

1. Unit tests for age buckets, percentile, acceleration, and tier transitions.
2. Integration test with a synthetic snapshot sequence: EARLY → RISING →
   BREAKOUT, plus a decaying case that never promotes.
3. A 24-hour local observation report records per-profile yield, duplicates,
   request errors, and tier counts.
4. Promote thresholds only after reviewing the report; do not tune from a
   single viral example.

## Implementation order

1. Add snapshot storage and pure scoring/tier functions.
2. Add candidate-specific faster polling and tier fields to PostgreSQL/API.
3. Add dashboard status pills and tier explanations.
4. Add neutral profile pool, per-profile locks, and yield metrics.
5. Run the 24-hour observation and tune documented configuration values.

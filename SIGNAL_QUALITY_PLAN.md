# Signal Quality Plan — Early Breakout Detection

## 1. Objective

Surface a Short only when its observed growth is meaningfully unusual for its
upload age. The dashboard is an early-warning surface, not a list of all
recent videos that gained views. A video may stay in Redis for measurement
without becoming visible to the user.

The active freshness boundary is **under 24 hours**. It is enforced both when
discovering a seed and before every later velocity check.

## 2. Diagnosis of the current false positive

The current scorer promotes a video to `EARLY` whenever one observation window
has VTR >= 250 views/hour. It does not yet require a second measurement,
does not apply the video age bucket, and labels a formula score as a percent.

Consequently, a 23-hour-old Short with 13K views can enter the dashboard after
gaining roughly 1K views in an observation window. That is a measurement, not
evidence of an impending breakout.

## 3. State machine and visibility rules

| State | Stored where | Minimum evidence | Visible on main dashboard? |
| --- | --- | --- | --- |
| `WATCH` | Redis only | Seed or one snapshot | No |
| `EARLY` | PostgreSQL + Redis | Two snapshots, positive growth, passes its age-bucket threshold | Yes, in Early section |
| `RISING` | PostgreSQL + Redis | Three snapshots, positive acceleration, high peer-relative velocity | Yes, main leaderboard |
| `BREAKOUT` | PostgreSQL + Redis | Three or more snapshots, sustained acceleration, top peer percentile | Yes, prioritised; media enrichment eligible |
| `COOLED` | Redis only / audit log | Growth stalls or declines | No |

There will be no percentage label until calibration data supports a real
probability model. The UI will show evidence instead: `2/2 observations`,
`accelerating`, or `top 5% for age`.

## 4. Measurement design

### 4.0 Adaptive polling cadence (implemented)

The scheduler wakes every 30 minutes. A seed under 12 hours old is eligible
for a new observation every 30 minutes; a seed aged 12–24 hours is eligible
every 60 minutes. This produces two comparable observations early enough to
be useful, while avoiding unnecessary refetches near the 24-hour cutoff. A
Redis lock prevents overlapping velocity runs.

### 4.1 Initial and follow-up observations

1. Discovery stores the initial view count and upload time in Redis.
2. The first recheck after the minimum observation interval is recorded as
   snapshot 1 and remains `WATCH`.
3. The second recheck provides the first comparable interval. It may promote
   to `EARLY` only if both intervals are positive and the newest interval does
   not slow materially.
4. A third recheck is required for acceleration and promotion to `RISING` or
   `BREAKOUT`.
5. A candidate that loses momentum is retained only for short audit purposes,
   then marked `COOLED` and removed from the public list.

### 4.2 Age buckets

Only the following buckets are scored; anything >= 24 hours is rejected:

| Upload age | Interpretation | Promotion policy |
| --- | --- | --- |
| 0–2 hours | Earliest, noisier distribution test | Permit lower absolute VTR, require two positive observations |
| 2–6 hours | Primary early-detection window | Require strong recent VTR and non-negative acceleration |
| 6–12 hours | Confirmation window | Require higher VTR than 2–6h and positive acceleration |
| 12–24 hours | Late early-warning window | Require high relative percentile and clear acceleration; steady growth alone is rejected |

Initial thresholds are intentionally treated as configuration, not facts. They
will be selected from measured baseline distributions, not guessed from a
single example.

### 4.3 Peer-relative score

For each bucket, Redis maintains a rolling population of observed interval
velocities. A candidate is compared only to fresh anonymous-feed videos from
the same age bucket:

- `EARLY`: at or above the configured initial percentile and two positive intervals.
- `RISING`: high percentile plus positive acceleration across two intervals.
- `BREAKOUT`: top percentile plus sustained acceleration across at least three
  intervals.

The score record contains raw VTR, interval VTRs, acceleration, age bucket,
percentile, snapshot count, and scoring version. This makes the thesis result
auditable and enables threshold tuning later.

## 5. Supplementary guardrails

These are secondary evidence; none uses keyword search or a top-creator list.

1. **Engagement support:** when public like/comment data is available, retain
   the rate and use it as a down-weighting signal for exceptionally weak
   engagement. Do not make it a hard gate because metrics can lag.
2. **Minimum absolute movement:** require enough new views to avoid a large
   rate calculated from a tiny time denominator.
3. **Outlier protection:** reject malformed counts, clock anomalies, and
   one-off count corrections before scoring.
4. **Media separation:** heatmap/frame/Gemini remains optional enrichment for
   `BREAKOUT`; it never determines whether the underlying velocity signal is
   stored.

## 6. Implementation phases

### Phase A — Correctness first

- Move single-snapshot candidates to `WATCH` only.
- Enforce 24-hour expiry at discovery, velocity check, and API query.
- Replace percentage labels with explicit evidence labels.
- Persist snapshot count, interval velocities, age bucket, acceleration, and
  scorer version.
- Add unit tests for age cutoff, snapshot transitions, and a declining case.

### Phase B — Relative scoring

- Add Redis rolling distributions per age bucket.
- Compute percentile from anonymous-feed observations only.
- Introduce configurable bucket thresholds and a `COOLED` transition.
- Update the dashboard with evidence badges and a separate watch count, not
  public watch cards.

### Phase C — Calibration and validation

- Run a 24-hour baseline without tuning thresholds mid-run.
- Produce a report: seeds seen, fresh accepted, snapshot completion, watch to
  early/rising/breakout conversion, cooldowns, source concentration, and
  source errors.
- Manually inspect a small sample of promoted and cooled candidates.
- Set documented thresholds using the report, then run a second 24-hour
  comparison.

### Phase D — Optional engagement enrichment

- Evaluate whether reliably available public engagement data improves precision.
- Add it only after recording coverage and delay characteristics.
- Compare precision/recall proxy results against the velocity-only baseline.

## 7. Acceptance criteria

The change is accepted only if all conditions hold:

1. A candidate with one snapshot never appears as a public `EARLY` signal.
2. A 12–24-hour video with steady but unexceptional growth is cooled or kept
   private, not displayed as a signal.
3. A synthetic sequence with increasing interval growth transitions
   `WATCH -> EARLY -> RISING -> BREAKOUT` predictably.
4. A decaying sequence never promotes beyond `WATCH` or is cooled.
5. No video >= 24 hours appears in discovery, processing, or dashboard API.
6. Dashboard text never claims an uncalibrated percentage probability.
7. Existing unit and integration tests pass, and no optional media failure
   prevents a validated velocity signal from being recorded.

## 8. Decision order

Implement Phase A first. It immediately removes the misleading one-snapshot
cards and makes the system honest. Then collect enough comparable observations
to implement Phase B thresholds based on evidence rather than intuition.

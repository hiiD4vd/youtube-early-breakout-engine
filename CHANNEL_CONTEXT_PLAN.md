# Channel Context and Gold Signal Plan

## Principle

Discovery, seed acceptance, velocity scoring, and public raw signals remain
zero-bias. No keyword, channel list, subscriber threshold, or whale exclusion
is used before a video becomes an `EARLY` signal.

Channel context is an **after-the-fact explanation layer**. It never removes a
raw signal. A strong video from a whale remains visible; a strong video from an
underdog receives the additional `GOLD CANDIDATE` label.

## Evidence collected after EARLY

- publicly exposed subscriber count, when available;
- a bounded sample of recent public channel uploads;
- median recent views and sample size;
- source and confidence for the context result.

## Classification

| Context | Meaning |
| --- | --- |
| `WHALE` | Public channel evidence shows a large audience or consistently high recent views. |
| `ESTABLISHED` | Channel has meaningful, but not whale-level, evidence. |
| `UNDERDOG` | Enough public evidence indicates neither a large audience nor consistently high history. |
| `UNKNOWN` | Public evidence is incomplete; no conclusion is forced. |

`GOLD CANDIDATE` means a raw signal is already valid and context is
`UNDERDOG`. It is a research label, not a guarantee of future virality.

## Data source and safeguards

The default implementation uses public yt-dlp metadata and a bounded channel
history sample. It runs only after EARLY, is cached on the signal record, and
records its outcome in the 24-hour report. An optional official YouTube Data
API key can be added later if more stable channel statistics are needed.

## Validation

1. Unit-test classification thresholds and insufficient-data fallback.
2. Ensure a whale-context signal still appears in the raw leaderboard.
3. Ensure `UNKNOWN` never becomes `UNDERDOG` without enough evidence.
4. Manually audit a small sample before using Gold labels in thesis results.

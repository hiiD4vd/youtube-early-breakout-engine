# Active Learning Plan — Topic Trends

## Goal

Improve cluster quality from human review without feeding keywords, creators, or
reviewer preferences back into anonymous discovery.

## Guardrails

1. Discovery remains anonymous and unconditioned by feedback.
2. A reviewer label never deletes raw evidence or rewrites a historical score.
3. Calibration is report-first: it recommends a threshold change; it does not
   mutate production settings automatically.
4. Every review records reviewer, decision, note, timestamp, and the feature
   model/version that produced the candidate.

## Review decisions

- `CONFIRM_CLUSTER`: videos genuinely represent one emerging topic.
- `REJECT_CLUSTER`: cluster is an incorrect merge/no real common topic.
- `SPLIT_NEEDED`: more than one topic is mixed together.
- `INSUFFICIENT_EVIDENCE`: defer; neither positive nor negative training label.

## Active-learning loop

1. Rank review queue by uncertainty: near-threshold similarity, low cohesion,
   rapidly changing membership, and cross-channel candidates close to public
   visibility.
2. Reviewer examines the evidence posts and submits a decision.
3. Store immutable review feedback.
4. After a minimum labelled sample, calculate precision/recall by similarity
   band and produce a versioned calibration recommendation.
5. A human accepts a recommendation in a future explicit configuration change.

## Initial delivery

- `topic_cluster_feedback` table and Alembic migration.
- Review queue API and feedback endpoint.
- Dashboard review page with transparent reviewer decisions.
- No discovery input, threshold, or cluster state is automatically changed.

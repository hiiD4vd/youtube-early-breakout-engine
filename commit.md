chore: stop tracking generated Next.js dashboard artifacts
fix(dev): enforce a single stable Next.js development server
fix(docker): isolate frontend profile and configure container DNS
docs: document current architecture and operational constraints
docs: add InnerTube and clustering implementation specifications
feat(config): configure general discovery and semantic processing
feat(innertube): add general YouTube discovery client
feat(tasks): collect general videos across rotating regions
feat(celery): schedule InnerTube collection and disable unused Apify jobs
feat(api): expose general and Shorts video trend endpoints
feat(api): add source health and semantic provider diagnostics
feat(semantic): add provider-backed batch topic grouping
feat(semantic): recover rejected videos with compatible semantic themes
feat(clustering): normalize centroids and prevent unrelated topic merges
test(clustering): cover centroid normalization and semantic compatibility
feat(signals): group early signals into multi-video topics
feat(ranking): improve topic scoring, deduplication, and human summaries
feat(topic-pool): add media scope, time period, diagnostics, and pagination
feat(frontend): add human-readable video, Shorts, and topic exploration
fix(frontend): add shared loading states, API handling, navigation, and details
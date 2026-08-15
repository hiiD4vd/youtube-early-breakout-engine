"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Video = {
  video_id: string;
  title: string | null;
  channel_title: string | null;
  video_url: string;
  thumbnail_url: string | null;
  published_at: string | null;
  duration: string | null;
  view_count: number;
  views_gained: number;
  velocity_per_day: number;
  velocity_per_hour: number;
  observation_span_hours: number;
  observation_count: number;
  rank: number | null;
  rank_change: number | null;
  tracked_regions: string[];
  region_count: number;
  observed_days: number;
};

type RegionHealth = {
  region: string;
  state: "active" | "stale" | "failed";
  last_run_at: string | null;
  runs_7d: number;
  error_runs_7d: number;
};

type Pagination = {
  total: number;
  limit: number;
  offset: number;
  returned: number;
  has_more: boolean;
};

type Coverage = {
  target_regions: number;
  catalog_regions: number;
  estimated_cycle_minutes: number;
  last_scan_at: string | null;
  state: string;
};

type Response = {
  items: Video[];
  tracked_regions: string[];
  region_health: RegionHealth[];
  pagination: Pagination;
  period_days: number;
  coverage: Coverage;
  methodology: string;
};

type SortOption =
  | "rank"
  | "rank_gain"
  | "views"
  | "velocity"
  | "engagement"
  | "region_breadth"
  | "streak"
  | "new_entries";

type CategoryOption = { value: string; label: string };

const compact = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function Change({ value }: { value: number | null }) {
  if (value == null)
    return (
      <span className="text-xs text-text-tertiary">Collecting history</span>
    );
  if (value > 0)
    return <span className="text-xs font-medium text-neon">+{value} rank</span>;
  if (value < 0)
    return (
      <span className="text-xs font-medium text-warning">
        -{Math.abs(value)} rank
      </span>
    );
  return <span className="text-xs text-text-tertiary">No change</span>;
}

function Cycle({ minutes }: { minutes: number }) {
  if (!minutes) return <>Preparing</>;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return (
    <>
      {hours ? `${hours}h ` : ""}
      {remainder}m
    </>
  );
}

const categoryOptions: CategoryOption[] = [
  { value: "", label: "All categories" },
  { value: "10", label: "Music" },
  { value: "17", label: "Sports" },
  { value: "24", label: "Entertainment" },
];

const sortOptions: Array<{ value: SortOption; label: string }> = [
  { value: "rank", label: "Rank" },
  { value: "rank_gain", label: "Rank gain" },
  { value: "velocity", label: "Growth velocity" },
  { value: "engagement", label: "Engagement" },
  { value: "region_breadth", label: "Market breadth" },
  { value: "streak", label: "Streak" },
  { value: "new_entries", label: "New entries" },
];

// Skeleton row component for loading state
function SkeletonRow() {
  return (
    <div className="grid gap-4 border-b border-line px-5 py-5 lg:grid-cols-[52px_minmax(260px,1.6fr)_130px_135px_minmax(190px,1fr)] lg:items-center lg:px-6">
      <div className="h-8 w-8 rounded-lg bg-line/30 animate-pulse" />
      <div className="flex gap-3">
        <div className="h-16 w-28 shrink-0 rounded-md bg-line/30 animate-pulse" />
        <div className="min-w-0 space-y-2 flex-1">
          <div className="h-4 w-3/4 rounded bg-line/30 animate-pulse" />
          <div className="h-3 w-1/2 rounded bg-line/30 animate-pulse" />
        </div>
      </div>
      <div className="space-y-2">
        <div className="h-4 w-16 rounded bg-line/30 animate-pulse" />
        <div className="h-3 w-20 rounded bg-line/30 animate-pulse" />
      </div>
      <div className="space-y-2">
        <div className="h-4 w-12 rounded bg-line/30 animate-pulse" />
        <div className="h-3 w-16 rounded bg-line/30 animate-pulse" />
      </div>
      <div className="space-y-2">
        <div className="h-4 w-24 rounded bg-line/30 animate-pulse" />
        <div className="h-3 w-32 rounded bg-line/30 animate-pulse" />
      </div>
    </div>
  );
}

export default function GeneralVideoTrendsPage() {
  const [region, setRegion] = useState("All regions");
  const [periodDays, setPeriodDays] = useState(7);
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState<SortOption>("rank");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [minDuration, setMinDuration] = useState<number | null>(null);
  const [maxDuration, setMaxDuration] = useState<number | null>(null);
  const [minAge, setMinAge] = useState<number | null>(null);
  const [maxAge, setMaxAge] = useState<number | null>(null);
  const [minViews, setMinViews] = useState(0);
  const [minEngagement, setMinEngagement] = useState(0);
  const [offset, setOffset] = useState(0);

  const pageSize = 100;
  const params = new URLSearchParams({
    period_days: String(periodDays),
    sort,
    limit: String(pageSize),
    offset: String(offset),
  });
  if (region !== "All regions") params.set("region", region);
  if (category) params.set("category", category);
  if (minDuration !== null && minDuration > 0)
    params.set("min_duration_seconds", String(minDuration * 60));
  if (maxDuration !== null && maxDuration > 0)
    params.set("max_duration_seconds", String(maxDuration * 60));
  if (minAge !== null && minAge > 0)
    params.set("min_age_hours", String(minAge));
  if (maxAge !== null && maxAge > 0)
    params.set("max_age_hours", String(maxAge));
  if (minViews > 0) params.set("min_views", String(minViews));
  if (minEngagement > 0) params.set("min_engagement", String(minEngagement));

  const { data, error, isLoading } = useSWR<Response>(
    `/api/v1/youtube/video-trends?${params}`,
    fetcher,
    { refreshInterval: 60_000, keepPreviousData: true },
  );

  // Any filter change invalidates the current page window.
  function applyFilter(change: () => void) {
    setOffset(0);
    change();
  }

  if (error)
    return (
      <p className="text-red-400">
        Chart video belum dapat dimuat. Sistem akan mencoba lagi otomatis.
      </p>
    );

  // Show a default empty structure with loading state
  const items = data?.items || [];
  const coverage = data?.coverage;
  const pagination = data?.pagination;
  const regionSummary = data?.region_health?.length
    ? {
        active: data.region_health.filter((item) => item.state === "active")
          .length,
        stale: data.region_health.filter((item) => item.state === "stale")
          .length,
        failed: data.region_health.filter((item) => item.state === "failed")
          .length,
      }
    : null;

  return (
    <div className="mx-auto max-w-[1500px]">
      <section className="rounded-2xl border border-line bg-[radial-gradient(circle_at_85%_0%,rgba(255,184,0,.13),transparent_35%),rgb(var(--surface))] px-6 py-7 md:px-8">
        <p className="text-xs font-semibold uppercase tracking-[.2em] text-warning">
          YouTube video trends
        </p>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="text-3xl font-bold tracking-tight md:text-4xl">
              What&apos;s charting on YouTube.
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">
              Chart video umum dari region resmi YouTube: musik, trailer, video
              panjang, dan live. Jalur ini sepenuhnya terpisah dari Shorts
              Intelligence.
            </p>
          </div>
          <div className="flex gap-3">
            <div className="rounded-xl border border-line bg-bg-primary/40 px-4 py-3">
              <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">
                Target coverage
              </p>
              <p className="mt-1 font-mono text-2xl text-warning">
                {coverage ? coverage.target_regions : "-"}
              </p>
              <p className="text-[11px] text-text-tertiary">YouTube regions</p>
            </div>
            <div className="rounded-xl border border-line bg-bg-primary/40 px-4 py-3">
              <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">
                Full sweep
              </p>
              <p className="mt-1 font-mono text-2xl text-warning">
                {coverage ? (
                  <Cycle minutes={coverage.estimated_cycle_minutes} />
                ) : (
                  "-"
                )}
              </p>
              <p className="text-[11px] text-text-tertiary">rotating fairly</p>
            </div>
          </div>
        </div>
      </section>

      <section className="mt-5 space-y-4 border-b border-line pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex rounded-lg border border-line bg-surface p-1">
            {[
              { label: "Today", days: 1 },
              { label: "7 days", days: 7 },
              { label: "30 days", days: 30 },
            ].map((option) => (
              <button
                key={option.days}
                onClick={() => applyFilter(() => setPeriodDays(option.days))}
                className={`rounded-md px-3 py-1.5 text-sm transition ${periodDays === option.days ? "bg-warning text-bg-primary" : "text-text-secondary hover:text-text-primary"}`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="text-xs font-medium text-neon hover:text-warning transition"
          >
            {showAdvanced ? "Hide filters" : "Show filters"} →
          </button>
        </div>

        <div className="flex flex-wrap gap-3">
          <select
            aria-label="Category"
            value={category}
            onChange={(event) =>
              applyFilter(() => setCategory(event.target.value))
            }
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
          >
            {categoryOptions.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <select
            aria-label="Region"
            value={region}
            onChange={(event) =>
              applyFilter(() => setRegion(event.target.value))
            }
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
          >
            <option>All regions</option>
            {(data?.region_health ?? []).map((item) => (
              <option key={item.region} value={item.region}>
                {item.region}
                {item.state === "active" ? "" : ` (${item.state})`}
              </option>
            ))}
          </select>
          <select
            aria-label="Sort"
            value={sort}
            onChange={(event) =>
              applyFilter(() => setSort(event.target.value as SortOption))
            }
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
          >
            {sortOptions.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        {showAdvanced && (
          <div className="rounded-lg border border-line/50 bg-bg-primary/40 p-4 grid gap-3 sm:grid-cols-2 md:grid-cols-3">
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-1">
                Min duration (min)
              </label>
              <input
                type="number"
                min="0"
                value={minDuration ?? ""}
                onChange={(e) =>
                  setMinDuration(e.target.value ? Number(e.target.value) : null)
                }
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
                placeholder="minutes"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-1">
                Max duration
              </label>
              <input
                type="number"
                min="0"
                value={maxDuration ?? ""}
                onChange={(e) =>
                  setMaxDuration(e.target.value ? Number(e.target.value) : null)
                }
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
                placeholder="minutes"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-1">
                Min age (hours)
              </label>
              <input
                type="number"
                min="0"
                value={minAge ?? ""}
                onChange={(e) =>
                  setMinAge(e.target.value ? Number(e.target.value) : null)
                }
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
                placeholder="hours"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-1">
                Max age
              </label>
              <input
                type="number"
                min="0"
                value={maxAge ?? ""}
                onChange={(e) =>
                  setMaxAge(e.target.value ? Number(e.target.value) : null)
                }
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
                placeholder="hours"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-1">
                Min views
              </label>
              <input
                type="number"
                min="0"
                value={minViews}
                onChange={(e) => setMinViews(Number(e.target.value) || 0)}
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
                placeholder="views"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-text-secondary block mb-1">
                Min engagement
              </label>
              <input
                type="number"
                min="0"
                value={minEngagement}
                onChange={(e) => setMinEngagement(Number(e.target.value) || 0)}
                className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
                placeholder="likes + comments"
              />
            </div>
          </div>
        )}
      </section>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-secondary">
        <span>Source: official YouTube regional chart</span>
        <span>
          {coverage?.catalog_regions ?? "..."} regions confirmed by YouTube
          catalog
        </span>
        <span>
          {coverage?.state === "ok"
            ? "Collector healthy"
            : `Collector: ${coverage?.state ?? "loading"}`}
        </span>
        {regionSummary && (
          <span>
            {regionSummary.active} region active
            {regionSummary.stale ? ` · ${regionSummary.stale} stale` : ""}
            {regionSummary.failed ? ` · ${regionSummary.failed} failed` : ""}
          </span>
        )}
      </div>

      <section className="mt-4 overflow-hidden rounded-xl border border-line bg-surface relative">
        <div className="hidden grid-cols-[52px_minmax(260px,1.6fr)_130px_135px_minmax(190px,1fr)] gap-4 border-b border-line bg-white/[.02] px-6 py-3 text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary lg:grid">
          <span>Rank</span>
          <span>Video</span>
          <span>Growth ({periodDays}d)</span>
          <span>Movement</span>
          <span>Market evidence</span>
        </div>

        {/* Loading overlay with transparency */}
        {isLoading && (
          <div className="absolute inset-0 bg-black/20 backdrop-blur-sm z-10" />
        )}

        {/* Content with opacity transition during loading */}
        <div
          className={`transition-opacity duration-200 ${isLoading ? "opacity-50" : "opacity-100"}`}
        >
          {items.map((video) => (
            <div
              key={video.video_id}
              className="group grid gap-4 border-b border-line px-5 py-5 transition hover:bg-white/[.025] last:border-0 lg:grid-cols-[52px_minmax(260px,1.6fr)_130px_135px_minmax(190px,1fr)] lg:items-center lg:px-6"
            >
              <div>
                <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-warning/50 bg-warning/20 font-mono text-sm text-warning">
                  {video.rank ?? "-"}
                </span>
              </div>
              <a
                href={`/youtube/video-trends/${video.video_id}`}
                className="flex min-w-0 gap-3 cursor-pointer"
              >
                <img
                  src={
                    video.thumbnail_url ||
                    `https://i.ytimg.com/vi/${video.video_id}/hqdefault.jpg`
                  }
                  alt=""
                  className="h-16 w-28 shrink-0 rounded-md border border-line object-cover group-hover:border-warning/50 transition"
                />
                <div className="min-w-0">
                  <h2 className="line-clamp-2 text-sm font-semibold group-hover:text-neon transition">
                    {video.title || video.video_id}
                  </h2>
                  <p className="mt-1 truncate text-xs text-text-secondary">
                    {video.channel_title || "Unknown channel"}
                  </p>
                  <p className="mt-1 text-[11px] text-text-tertiary">
                    {video.duration || "Duration unavailable"} ·
                    <a
                      href={video.video_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-1 text-neon hover:text-warning transition"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Watch
                    </a>
                  </p>
                </div>
              </a>
              <div>
                <p className="font-mono text-sm">
                  +{compact.format(video.velocity_per_day)}/day
                </p>
                <p className="mt-1 text-[11px] text-text-tertiary">
                  ({compact.format(video.views_gained)} total ·{" "}
                  {video.observation_count > 1
                    ? `${video.observation_span_hours}h window`
                    : "single scan"}
                  )
                </p>
              </div>
              <div>
                <Change value={video.rank_change} />
                <p className="mt-1 text-[11px] text-text-tertiary">
                  same-country comparison
                </p>
              </div>
              <div>
                <p className="text-sm text-text-primary">
                  {video.region_count} tracked region
                  {video.region_count === 1 ? "" : "s"}
                </p>
                <p className="mt-1 text-xs text-text-secondary">
                  {video.tracked_regions.join(" · ") || "Region pending"}
                </p>
                <p className="mt-1 text-[11px] text-text-tertiary">
                  {video.observed_days} observed day
                  {video.observed_days === 1 ? "" : "s"}
                </p>
              </div>
            </div>
          ))}
        </div>

        {/* Skeleton loading rows */}
        {isLoading && (
          <div className="absolute inset-0 pointer-events-none">
            {[...Array(8)].map((_, i) => (
              <SkeletonRow key={i} />
            ))}
          </div>
        )}

        {!items.length && !isLoading && (
          <div className="px-6 py-12 text-center">
            <p className="font-medium">
              Belum ada video umum yang telah lolos pemisahan format.
            </p>
            <p className="mt-2 text-sm text-text-secondary">
              Collector region resmi sedang berjalan. Video tidak akan masuk
              halaman Shorts kecuali lulus verifikasi Shorts yang ketat.
            </p>
          </div>
        )}
      </section>
      {pagination && pagination.total > 0 && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-text-secondary">
            Showing {pagination.offset + 1}-
            {pagination.offset + pagination.returned} of {pagination.total}{" "}
            videos
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - pageSize))}
              disabled={offset === 0 || isLoading}
              className="rounded-lg border border-line px-3 py-1.5 text-sm text-text-secondary transition hover:text-text-primary disabled:opacity-40 disabled:hover:text-text-secondary"
            >
              Previous
            </button>
            <button
              onClick={() => setOffset(offset + pageSize)}
              disabled={!pagination.has_more || isLoading}
              className="rounded-lg border border-line px-3 py-1.5 text-sm text-text-secondary transition hover:text-text-primary disabled:opacity-40 disabled:hover:text-text-secondary"
            >
              Next
            </button>
          </div>
        </div>
      )}

      <p className="mt-4 max-w-5xl text-xs leading-5 text-text-tertiary">
        {data?.methodology || "Mengambil metodologi chart..."}
      </p>
    </div>
  );
}

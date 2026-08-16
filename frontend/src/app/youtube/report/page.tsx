"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Coverage = { seen?: number; fresh?: number; duplicates?: number };
type Report = { raw_candidates_seen: number; fresh_accepted: number; fresh_rate_percent: number; duplicates: number; age_buckets: Record<string, number>; tier_states: Record<string, number>; velocity_samples_by_age: Record<string, number>; relative_scoring: { enabled: boolean; minimum_samples: number }; profiles: { profile: string; coverage_24h: Coverage }[] };
type MarketCoverage = {
  verified_unique_shorts: number;
  fresh_age_buckets: Record<string, number>;
  source_lanes: { lane: string; unique_shorts: number; fresh_0_72h: number; observations: number; repeat_observations: number }[];
  regions: { region: string; unique_shorts: number; fresh_0_72h: number }[];
  content_truth: Record<string, number>;
  format_verification: Record<string, number>;
  format_by_source: { lane: string; statuses: Record<string, number> }[];
  format_by_region: { region: string; statuses: Record<string, number> }[];
  last_verification_batch: { verified_last_batch: number; rejected_last_batch: number; failed_last_batch: number };
  apify_health: { state: string; invalid: number; failed_batches: number };
  source_run_health?: { lane: string; region: string; runs: number; ok_runs: number; error_runs: number; candidates_seen: number; accepted_shorts: number; unique_shorts: number; duplicate_shorts: number; fresh_0_24h: number; fresh_24_72h: number; rejected_not_shorts: number }[];
  semantic_backlog?: number;
  methodology: string;
};

const labels: Record<string, string> = { "0-2h": "0–2 jam", "2-6h": "2–6 jam", "6-12h": "6–12 jam", "12-24h": "12–24 jam", "0_24h": "0–24 jam", "24_72h": "24–72 jam", "72_168h": "72–168 jam", "older_or_unknown": "lebih lama/tidak diketahui" };

function MetricBlock({ title, rows }: { title: string; rows: [string, string | number][] }) {
  return <div className="rounded-lg border border-line bg-bg-primary/30 p-4"><p className="text-xs font-semibold uppercase tracking-[.12em] text-text-tertiary">{title}</p><div className="mt-3 space-y-2">{rows.length ? rows.map(([label, value]) => <div key={label} className="flex items-center justify-between gap-3 text-xs"><span className="truncate text-text-secondary">{labels[label] || label}</span><span className="font-mono text-neon">{value}</span></div>) : <p className="text-xs text-text-tertiary">Belum ada data.</p>}</div></div>;
}

function CohortHealth({ runs }: { runs: NonNullable<MarketCoverage["source_run_health"]> }) {
  if (!runs.length) return <p className="mt-5 rounded-lg border border-dashed border-line p-3 text-xs text-text-tertiary">Cohort telemetry mulai muncul pada scan berikutnya. Data historis tidak direka ulang.</p>;
  return <div className="mt-5 overflow-x-auto"><p className="mb-3 text-xs font-semibold uppercase tracking-[.14em] text-text-tertiary">24-hour cohort proof</p><table className="min-w-full text-left text-xs"><thead className="border-b border-line text-text-tertiary"><tr><th className="px-2 py-2">Source / region</th><th className="px-2 py-2">Runs</th><th className="px-2 py-2">Seen</th><th className="px-2 py-2">Unique</th><th className="px-2 py-2">Repeat</th><th className="px-2 py-2">Fresh 0-24h</th><th className="px-2 py-2">Fresh 24-72h</th><th className="px-2 py-2">Rejected</th></tr></thead><tbody>{runs.map((run) => <tr key={`${run.lane}-${run.region}`} className="border-b border-line/60"><td className="px-2 py-2 font-mono">{run.lane} / {run.region}</td><td className="px-2 py-2">{run.ok_runs}/{run.runs}{run.error_runs ? ` (${run.error_runs} error)` : ""}</td><td className="px-2 py-2">{run.candidates_seen}</td><td className="px-2 py-2 text-neon">{run.unique_shorts}</td><td className="px-2 py-2">{run.duplicate_shorts}</td><td className="px-2 py-2">{run.fresh_0_24h}</td><td className="px-2 py-2">{run.fresh_24_72h}</td><td className="px-2 py-2">{run.rejected_not_shorts}</td></tr>)}</tbody></table></div>;
}

export default function ObservationReportPage() {
  const { data, error } = useSWR<Report>("/api/v1/youtube/observation-report", fetcher, { refreshInterval: 30_000 });
  const { data: market } = useSWR<MarketCoverage>("/api/v1/youtube/market/coverage", fetcher, { refreshInterval: 30_000 });
  if (error) return <p className="text-red-400">Laporan belum bisa dimuat. Sistem akan mencoba lagi otomatis.</p>;
  if (!data) return <p className="text-text-secondary">Memuat laporan observasi...</p>;
  const cards = [["Raw candidates", data.raw_candidates_seen], ["Fresh accepted", data.fresh_accepted], ["Fresh rate", `${data.fresh_rate_percent}%`], ["Duplicates", data.duplicates]];
  return <main className="mx-auto max-w-7xl"><p className="text-sm font-semibold uppercase tracking-[.2em] text-neon">Y-CGC V4</p><h1 className="mt-2 text-4xl font-bold">24-hour observation report</h1><p className="mt-2 text-text-secondary">Bukti rolling untuk evaluasi skripsi dan kesehatan pipeline.</p>
    <section className="mt-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{cards.map(([label, value]) => <div key={String(label)} className="rounded-xl border border-line bg-surface p-4 shadow-card"><p className="text-[10px] font-semibold uppercase tracking-wider text-text-tertiary">{label}</p><p className="mt-2 font-mono text-2xl">{value}</p></div>)}</section>
    <section className="mt-6 grid gap-4 lg:grid-cols-2"><MetricBlock title="Fresh age distribution" rows={Object.entries(data.age_buckets)} /><MetricBlock title="Signal states observed" rows={Object.entries(data.tier_states)} /></section>
    <section className="mt-4 grid gap-4 lg:grid-cols-2"><MetricBlock title="Profile coverage" rows={data.profiles.map(({ profile, coverage_24h }) => [profile, `${coverage_24h.seen ?? 0} raw · ${coverage_24h.fresh ?? 0} fresh · ${coverage_24h.duplicates ?? 0} dup`])} /><div className="rounded-xl border border-line bg-surface p-5"><p className="text-xs font-semibold uppercase tracking-[.14em] text-text-tertiary">Relative scoring baseline</p><p className="mt-3 text-sm text-text-secondary">{data.relative_scoring.enabled ? "Active" : "Collecting only"}; needs {data.relative_scoring.minimum_samples} samples per age bucket.</p><div className="mt-4 space-y-3">{Object.entries(data.velocity_samples_by_age).map(([bucket, count]) => <div key={bucket} className="flex justify-between border-b border-line pb-2 text-sm"><span>{labels[bucket] || bucket}</span><span className="font-mono">{count} samples</span></div>)}</div></div></section>
    {market && <section className="mt-6 rounded-xl border border-line bg-surface p-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-[.14em] text-neon">Market source health</p><h2 className="mt-2 text-xl font-semibold">Apakah coverage benar-benar bertambah?</h2><p className="mt-1 text-sm text-text-secondary">Unique = Shorts berbeda. Repeat = scan ulang video yang sama dan tidak menaikkan coverage.</p></div><div className="rounded-lg border border-line bg-bg-primary/40 px-4 py-3"><p className="text-[10px] uppercase tracking-[.12em] text-text-tertiary">Verified unique Shorts</p><p className="mt-1 font-mono text-2xl text-neon">{market.verified_unique_shorts}</p></div></div><div className="mt-5 grid gap-4 lg:grid-cols-4"><MetricBlock title="Fresh market Shorts" rows={Object.entries(market.fresh_age_buckets)} /><MetricBlock title="Per source lane" rows={market.source_lanes.map((item) => [item.lane, `${item.unique_shorts} unique · ${item.fresh_0_72h} fresh · ${item.repeat_observations} repeat`])} /><MetricBlock title="Shorts format gate" rows={Object.entries(market.format_verification)} /><MetricBlock title="Content verification" rows={Object.entries(market.content_truth)} /></div><div className="mt-4 grid gap-4 lg:grid-cols-2"><MetricBlock title="Format result per source" rows={market.format_by_source.map((item) => [item.lane, Object.entries(item.statuses).map(([status, count]) => `${status}:${count}`).join(" · ")])} /><MetricBlock title="Format result per region" rows={market.format_by_region.map((item) => [item.region, Object.entries(item.statuses).map(([status, count]) => `${status}:${count}`).join(" · ")])} /></div><div className="mt-4 grid gap-4 lg:grid-cols-2"><MetricBlock title="Latest format verification batch" rows={[["Verified", market.last_verification_batch.verified_last_batch], ["Rejected non-Shorts", market.last_verification_batch.rejected_last_batch], ["Verification failed", market.last_verification_batch.failed_last_batch]]} /><MetricBlock title="Apify health" rows={[["State", market.apify_health.state], ["Invalid rows", market.apify_health.invalid], ["Failed batches", market.apify_health.failed_batches]]} /></div><p className="mt-4 text-xs text-text-tertiary">{market.methodology}</p></section>}
    {market && <section className="mt-5 rounded-xl border border-line bg-surface p-5"><div className="flex items-end justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[.14em] text-neon">Cohort proof</p><h2 className="mt-2 text-xl font-semibold">Scan yang benar-benar menambah coverage</h2></div><p className="font-mono text-sm text-text-secondary">semantic backlog: <span className="text-neon">{market.semantic_backlog ?? 0}</span></p></div><CohortHealth runs={market.source_run_health ?? []} /></section>}
  </main>;
}

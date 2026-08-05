"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Breakout = { video_id: string; title: string | null; channel_title: string | null; video_url: string; thumbnail_url: string | null; current_view_count: number; velocity_per_hour: number; signal_tier: string; snapshot_count: number; age_bucket: string | null; acceleration: number | null; niche: string | null; visual_facts: string[] };
type Response = { items: Breakout[] };
type WatchCandidate = { video_id: string; title: string | null; channel_title: string | null; video_url: string; thumbnail_url: string | null; latest_view_count: number; observations: number; required_observations: number; age_minutes: number; freshness_lane: string; next_observation_at: string; profile: string };
type WatchlistResponse = { items: WatchCandidate[] };
type Coverage = { seen?: number; fresh?: number; old?: number; duplicates?: number; sessions?: number };
type Profile = { region: string; language: string; latest: Coverage; coverage_24h: Coverage };
type Pending = { video_id: string | null; media_state: string; media_attempts: number };
type PipelineStatus = { seed_active: number; pending_breakouts: number; signal_count: number; last_seed_seen: number; last_velocity_eligible: number; last_media_failures: number; last_seed_scan_at: string | null; last_velocity_scan_at: string | null; profiles: Profile[]; pending_items: Pending[] };

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const wib = (value: string | null | undefined) => value ? `${new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" }).format(new Date(value))} WIB` : "Belum ada";
const ageLabel = (minutes: number) => `${Math.floor(minutes / 60)}j ${minutes % 60}m`;

export default function YoutubeDashboard() {
  const { data, error, isLoading } = useSWR<Response>("/api/v1/youtube/breakouts", fetcher, { refreshInterval: 30_000 });
  const { data: status } = useSWR<PipelineStatus>("/api/v1/youtube/status", fetcher, { refreshInterval: 30_000 });
  const { data: watchlist } = useSWR<WatchlistResponse>("/api/v1/youtube/watchlist", fetcher, { refreshInterval: 30_000 });
  const [niche, setNiche] = useState("all");
  const items = useMemo(() => (data?.items ?? []).filter((item) => niche === "all" || item.niche === niche), [data, niche]);
  const niches = useMemo(() => Array.from(new Set((data?.items ?? []).map((item) => item.niche).filter(Boolean))) as string[], [data]);

  return <main className="mx-auto max-w-7xl">
    <div className="flex flex-wrap items-end justify-between gap-5">
      <div><p className="text-sm font-semibold uppercase tracking-[.2em] text-neon">Y-CGC V4</p><h1 className="mt-2 text-4xl font-bold">YouTube Early Breakouts</h1><p className="mt-2 text-text-secondary">Early probability signals; enrichment follows when available.</p></div>
      <select value={niche} onChange={(event) => setNiche(event.target.value)} className="rounded-lg border border-line bg-surface px-3 py-2"><option value="all">All niches</option>{niches.map((value) => <option key={value} value={value}>{value}</option>)}</select>
    </div>

    <section className="mt-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {[["Active seeds", status?.seed_active ?? 0], ["VTR passes", status?.last_velocity_eligible ?? 0], ["Media retry", status?.last_media_failures ?? 0], ["Signals", status?.signal_count ?? 0]].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-line bg-surface p-4 shadow-card"><p className="text-[10px] font-semibold uppercase tracking-wider text-text-tertiary">{label}</p><p className="mt-2 font-mono text-2xl text-text-primary">{value}</p></div>)}
    </section>

    <section className="mt-7 grid gap-4 lg:grid-cols-[1.5fr_1fr]">
      <div className="rounded-xl border border-line bg-surface p-5 shadow-card"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-xs font-semibold uppercase tracking-[.14em] text-text-tertiary">Feed coverage</p><p className="mt-1 text-sm text-text-secondary">Pemindaian terakhir: {wib(status?.last_seed_scan_at)}</p></div><span className="rounded-full bg-neon-dim px-2.5 py-1 text-xs font-medium text-neon">{status?.last_seed_seen ?? 0} dilihat pada scan ini</span></div><div className="mt-4 grid gap-2 sm:grid-cols-3">{(status?.profiles ?? []).map((profile) => <div key={`${profile.region}-${profile.language}`} className="rounded-lg border border-line bg-black/10 p-3"><p className="font-mono text-sm text-text-primary">{profile.region} / {profile.language}</p><p className="mt-2 text-xs text-text-secondary">Scan ini: {profile.latest.seen ?? 0} dilihat · <span className="text-neon">{profile.latest.fresh ?? 0} fresh</span></p><p className="mt-1 text-xs text-text-tertiary">24 jam: {profile.coverage_24h.seen ?? 0} dilihat · {profile.coverage_24h.fresh ?? 0} fresh · {profile.coverage_24h.duplicates ?? 0} duplikat</p></div>)}</div></div>
      <div className="rounded-xl border border-line bg-surface p-5 shadow-card"><p className="text-xs font-semibold uppercase tracking-[.14em] text-text-tertiary">Pipeline status</p><p className="mt-2 text-sm text-text-secondary">VTR terakhir: {wib(status?.last_velocity_scan_at)}</p><p className="mt-1 text-sm text-text-secondary">{status?.pending_breakouts ?? 0} kandidat menunggu pengayaan media.</p></div>
    </section>

    <section className="mt-4 rounded-xl border border-line bg-surface p-5 shadow-card"><div className="flex flex-wrap items-baseline justify-between gap-2"><div><p className="text-xs font-semibold uppercase tracking-[.14em] text-warning">Fresh candidates under observation</p><p className="mt-1 text-sm text-text-secondary">Video nyata dari feed anonim. Belum merupakan sinyal atau prediksi viral.</p></div><span className="font-mono text-sm text-text-secondary">{watchlist?.items.length ?? 0} aktif</span></div>{(watchlist?.items.length ?? 0) === 0 ? <p className="mt-4 text-sm text-text-tertiary">Belum ada video fresh di pool saat ini.</p> : <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{watchlist?.items.map((item) => <article key={item.video_id} className="overflow-hidden rounded-lg border border-line bg-black/10"><img className="aspect-video w-full object-cover" src={item.thumbnail_url || `https://i.ytimg.com/vi/${item.video_id}/hqdefault.jpg`} alt=""/><div className="p-3"><div className="flex items-center justify-between gap-2"><span className="rounded bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning">WATCH · {item.observations}/{item.required_observations}</span><span className={item.freshness_lane === "ULTRA FRESH" ? "text-[10px] font-semibold text-neon" : "text-[10px] text-text-tertiary"}>{item.freshness_lane} · {ageLabel(item.age_minutes)}</span></div><a className="mt-2 block truncate text-sm font-medium hover:text-neon" href={item.video_url} target="_blank">{item.title || item.video_id}</a><p className="mt-1 truncate text-xs text-text-secondary">{item.channel_title || "Unknown channel"} · {item.profile}</p><p className="mt-3 text-xs text-text-secondary">{compact.format(item.latest_view_count)} views · next check {wib(item.next_observation_at)}</p></div></article>)}</div>}</section>

    {(status?.pending_items?.length ?? 0) > 0 && <section className="mt-4 rounded-xl border border-line bg-surface p-5 shadow-card"><p className="text-xs font-semibold uppercase tracking-[.14em] text-text-tertiary">Enrichment queue</p><div className="mt-3 space-y-2">{status?.pending_items.map((pending) => <div key={pending.video_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-sm"><span className="font-mono text-text-secondary">{pending.video_id}</span><span className="text-text-secondary">{pending.media_state.replaceAll("_", " ")} · percobaan {pending.media_attempts}</span></div>)}</div></section>}

    {isLoading && <p className="mt-10 text-text-secondary">Loading signals...</p>}
    {error && <p className="mt-10 text-red-400">Cannot reach the API. The dashboard will retry automatically.</p>}
    {!isLoading && !error && items.length === 0 && <div className="mt-7 rounded-xl border border-dashed border-line-strong bg-surface p-8 text-text-secondary">No early signal yet. Pipeline is active. <a className="ml-2 text-neon underline" href="/youtube/demo">Preview final card layout</a>.</div>}
    <section className="mt-7 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">{items.map((item) => <article key={item.video_id} className="overflow-hidden rounded-xl border border-line bg-surface shadow-card"><img className="aspect-video w-full bg-black/20 object-cover" src={item.thumbnail_url || `https://i.ytimg.com/vi/${item.video_id}/hqdefault.jpg`} alt=""/><div className="p-4"><div className="flex items-center justify-between"><p className="text-xs font-medium text-neon">{item.signal_tier} · {item.snapshot_count} observations</p><p className="text-[10px] text-text-tertiary">{item.age_bucket || "AI pending"}</p></div><a className="mt-1 block font-semibold hover:text-neon" href={item.video_url} target="_blank">{item.title || item.video_id}</a><p className="mt-1 text-sm text-text-secondary">{item.channel_title || "Unknown channel"}</p><div className="mt-4 flex gap-4 text-sm"><span>{compact.format(item.current_view_count)} views</span><span className="text-neon">{compact.format(item.velocity_per_hour)}/hr</span></div><p className="mt-3 text-xs text-text-secondary">{(item.acceleration ?? 0) > 0 ? "Acceleration observed" : "Growth confirmed"}</p>{item.visual_facts.length > 0 && <p className="mt-4 border-t border-line pt-3 text-sm text-text-secondary">{item.visual_facts[0]}</p>}</div></article>)}</section>
  </main>;
}

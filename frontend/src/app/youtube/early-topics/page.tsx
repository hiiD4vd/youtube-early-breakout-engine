"use client";

import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Evidence = { video_id: string; thumbnail_url: string | null };
type Snapshot = { observed_views: number };
type Topic = {
  id: string; label: string; observed_views: number; observed_velocity_per_hour: number;
  early_member_count: number; early_channel_count: number; cluster_reason: string | null;
  evidence_summary: { early_phase?: string; lifecycle_age_hours?: number; lifecycle_window_hours?: number };
  members: Evidence[]; snapshots: Snapshot[];
};
type Response = { items: Topic[]; diagnostics?: { active_seeds: number; clusters_observed: number; cross_channel_candidates: number; named_candidates: number; public_topics: number } };
const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

function Sparkline({ snapshots }: { snapshots: Snapshot[] }) {
  if (snapshots.length < 3) return <span className="text-xs text-text-tertiary">building history</span>;
  const values = snapshots.map((x) => x.observed_views);
  const min = Math.min(...values); const range = Math.max(1, Math.max(...values) - min);
  const points = values.map((x, i) => `${(i / (values.length - 1)) * 90},${26 - ((x - min) / range) * 20}`).join(" ");
  return <svg className="h-8 w-24" viewBox="0 0 90 30"><polyline points={points} fill="none" stroke="rgb(var(--neon))" strokeWidth="2" strokeLinecap="round" /></svg>;
}

export default function EarlyTopicsPage() {
  const { data, error } = useSWR<Response>("/api/v1/youtube/early-topics", fetcher, { refreshInterval: 60_000 });
  if (error) return <p className="text-red-400">Early Topic Signals belum dapat dimuat.</p>;
  if (!data) return <p className="text-text-secondary">Mencari pola baru dari sinyal organik...</p>;

  return <div className="mx-auto max-w-[1500px]">
    <section className="rounded-2xl border border-line bg-[radial-gradient(circle_at_85%_0%,rgba(55,125,255,.18),transparent_35%),rgb(var(--surface))] px-6 py-7 md:px-8">
      <p className="text-xs font-semibold uppercase tracking-[.2em] text-neon">Early intelligence · 72-hour lifecycle</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
        <div><h1 className="text-3xl font-bold tracking-tight md:text-4xl">Topics that may break next.</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">Shorts harus tertangkap pada 24 jam awal; topiknya kemudian diuji selama 72 jam melalui fase Fresh, Rising, dan Validating.</p></div>
        <div className="rounded-xl border border-line bg-bg-primary/40 px-4 py-3"><p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">Early topics</p><p className="mt-1 font-mono text-2xl text-neon">{data.items.length}</p></div>
      </div>
    </section>
    <section className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{[["Seeds dipantau", data.diagnostics?.active_seeds ?? 0], ["Cluster diamati", data.diagnostics?.clusters_observed ?? 0], ["Lintas channel", data.diagnostics?.cross_channel_candidates ?? 0], ["Menunggu/siap nama", data.diagnostics?.named_candidates ?? 0], ["Layak tampil", data.diagnostics?.public_topics ?? data.items.length]].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-line bg-surface px-4 py-3"><p className="text-[10px] font-semibold uppercase tracking-[.13em] text-text-tertiary">{label}</p><p className="mt-1 font-mono text-xl text-neon">{value}</p></div>)}</section>
    <section className="mt-5 overflow-hidden rounded-xl border border-line bg-surface">
      <div className="hidden grid-cols-[52px_minmax(260px,1.4fr)_130px_125px_minmax(300px,1fr)] gap-4 border-b border-line bg-white/[.02] px-6 py-3 text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary lg:grid"><span>Rank</span><span>Early topic</span><span>Early views</span><span>Movement</span><span>Evidence</span></div>
      {data.items.map((topic, index) => {
        const phase = topic.evidence_summary.early_phase || "FRESH";
        const age = topic.evidence_summary.lifecycle_age_hours ?? 0;
        return <Link key={topic.id} href={`/youtube/trends/${topic.id}`} className="grid gap-4 border-b border-line px-5 py-5 transition hover:bg-white/[.025] last:border-0 lg:grid-cols-[52px_minmax(260px,1.4fr)_130px_125px_minmax(300px,1fr)] lg:items-center lg:px-6">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-neon/40 bg-neon-dim font-mono text-sm text-neon">{index + 1}</span>
          <div><div className="flex items-center gap-2"><h2 className="font-semibold">{topic.label}</h2><span className="rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-semibold text-warning">{phase}</span></div><p className="mt-1 text-xs text-text-secondary">{topic.early_member_count} early Shorts · {topic.early_channel_count} creators · {age}h / {topic.evidence_summary.lifecycle_window_hours ?? 72}h</p><p className="mt-1 line-clamp-1 text-[11px] text-text-tertiary">{topic.cluster_reason || "Semantic context is being verified."}</p></div>
          <div><p className="font-mono text-sm">{compact.format(topic.observed_views)}</p><p className="mt-1 text-[11px] text-text-tertiary">early observed views</p></div>
          <div><Sparkline snapshots={topic.snapshots} /><p className="font-mono text-xs text-neon">{compact.format(topic.observed_velocity_per_hour)}/hr</p></div>
          <div className="flex gap-2 overflow-hidden">{topic.members.slice(0, 5).map((item) => item.thumbnail_url ? <img key={item.video_id} src={item.thumbnail_url} alt="" className="h-16 w-12 shrink-0 rounded-md border border-line object-cover" /> : <span key={item.video_id} className="h-16 w-12 shrink-0 rounded-md border border-line bg-bg-secondary" />)}</div>
        </Link>;
      })}
      {!data.items.length && <div className="px-6 py-12 text-center"><p className="font-medium">Belum ada topik awal yang cukup kuat.</p><p className="mt-2 text-sm text-text-secondary">Satu video tidak cukup; sistem menunggu pola serupa dari minimal dua channel saat videonya masih baru dan kecil.</p><p className="mt-3 text-xs text-text-tertiary">Saat ini {data.diagnostics?.active_seeds ?? 0} seed dipantau; {data.diagnostics?.cross_channel_candidates ?? 0} sudah memiliki bukti lintas-channel.</p></div>}
    </section>
    <p className="mt-4 text-xs leading-5 text-text-tertiary">Sesudah 72 jam, bukti tetap tersimpan untuk active learning tetapi tidak lagi tampil sebagai Early Signal.</p>
  </div>;
}

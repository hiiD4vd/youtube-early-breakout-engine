"use client";

import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Member = { video_id: string; title: string | null; channel_title: string | null; thumbnail_url: string | null; velocity_per_hour: number };
type Snapshot = { observed_at: string; trend_score: number; observed_velocity_per_hour: number };
type Trend = { id: string; label: string; niche: string | null; status: string; trend_score: number; observed_views: number; observed_velocity_per_hour: number; acceleration: number | null; member_count: number; channel_count: number; members: Member[]; snapshots: Snapshot[] };
type TrendResponse = { items: Trend[]; private_candidate_count: number; methodology: string };

const format = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const velocity = (value: number) => `${format.format(value)}/hr`;

function Sparkline({ snapshots }: { snapshots: Snapshot[] }) {
  if (snapshots.length < 2) return <span className="text-xs text-text-tertiary">Collecting observations</span>;
  const values = snapshots.map((item) => item.trend_score);
  const min = Math.min(...values); const max = Math.max(...values); const range = max - min || 1;
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 120},${34 - ((value - min) / range) * 26}`).join(" ");
  return <svg viewBox="0 0 120 40" className="h-10 w-28 overflow-visible" aria-label="Observed trend score"><polyline points={points} fill="none" stroke="rgb(var(--neon))" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /><path d={`M 0 38 L ${points.replaceAll(",", " ").split(" ").filter((_, i) => i % 2 === 0).join(" 38 L ")} 38`} fill="none" /></svg>;
}

function Status({ status }: { status: string }) {
  const color = status === "CONFIRMED" ? "text-neon" : status === "ACCELERATING" ? "text-warning" : "text-sky-300";
  return <span className={`text-xs font-semibold ${color}`}>{status.replace("_", " ")}</span>;
}

export default function TopicTrendsPage() {
  const { data, error } = useSWR<TrendResponse>("/api/v1/youtube/trends", fetcher, { refreshInterval: 30_000 });
  if (error) return <p className="text-red-400">Topic trends belum bisa dimuat. Sistem akan mencoba kembali otomatis.</p>;
  if (!data) return <p className="text-text-secondary">Memuat observed topic trends...</p>;
  return <div className="mx-auto max-w-7xl">
    <p className="text-sm font-semibold uppercase tracking-[.2em] text-neon">Y-CGC V4 · Topic intelligence</p>
    <div className="mt-2 flex flex-wrap items-end justify-between gap-4"><div><h1 className="text-4xl font-bold">Trending topics</h1><p className="mt-2 max-w-2xl text-text-secondary">Pola yang muncul lintas video dan lintas channel—bukan daftar video viral satu per satu.</p></div><div className="flex items-center gap-3"><a href="/api/v1/youtube/trends/export.csv" className="rounded-lg border border-line px-3 py-2 text-xs font-medium text-text-secondary hover:border-line-strong hover:text-text-primary">Export CSV</a><div className="rounded-lg border border-line bg-surface px-4 py-3 text-sm"><span className="font-mono text-neon">{data.items.length}</span> public clusters</div></div></div>
    <div className="mt-7 overflow-hidden rounded-xl border border-line bg-surface"><div className="grid grid-cols-[minmax(210px,1.5fr)_110px_120px_90px] gap-4 border-b border-line px-5 py-3 text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary"><span>Topic</span><span>Observed views</span><span>Momentum</span><span>Evidence</span></div>
      {data.items.map((trend, index) => <Link key={trend.id} href={`/youtube/trends/${trend.id}`} className="grid grid-cols-1 gap-3 border-b border-line px-5 py-5 transition-colors last:border-0 hover:bg-white/[.03] md:grid-cols-[minmax(210px,1.5fr)_110px_120px_90px] md:items-center md:gap-4"><div className="flex min-w-0 gap-3"><span className="mt-0.5 font-mono text-sm text-text-tertiary">{String(index + 1).padStart(2, "0")}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="truncate text-base font-semibold">{trend.label}</h2><Status status={trend.status} /></div><p className="mt-1 text-xs text-text-secondary">{trend.niche || "Uncategorized"} · {trend.channel_count} independent channels · {trend.member_count} evidence posts</p><div className="mt-3 flex -space-x-2">{trend.members.slice(0, 5).map((member) => member.thumbnail_url ? <img key={member.video_id} src={member.thumbnail_url} alt="" className="h-9 w-7 rounded border border-bg-primary object-cover" /> : <span key={member.video_id} className="h-9 w-7 rounded border border-bg-primary bg-bg-secondary" />)}</div></div></div><div className="font-mono text-sm">{format.format(trend.observed_views)}</div><div><Sparkline snapshots={trend.snapshots} /><p className="font-mono text-xs text-neon">{velocity(trend.observed_velocity_per_hour)}</p></div><div className="text-sm"><span className="font-mono">{Math.round(trend.trend_score)}</span><span className="block text-xs text-text-tertiary">score</span></div></Link>)}
    </div>
    {!data.items.length && <section className="mt-6 rounded-xl border border-dashed border-line-strong bg-surface/40 p-6"><p className="font-medium">Belum ada topik publik yang dikonfirmasi.</p><p className="mt-2 max-w-3xl text-sm text-text-secondary">Saat ini ada {data.private_candidate_count} kandidat privat. Kandidat satu video tidak ditampilkan sebagai trend; ia baru menjadi topik ketika pola serupa muncul dari channel independen.</p></section>}
    <p className="mt-5 text-xs leading-5 text-text-tertiary">{data.methodology}</p>
  </div>;
}

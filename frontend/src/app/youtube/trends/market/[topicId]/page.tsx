"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { PageState } from "@/components/page-state";

type Snapshot = { observed_at: string; observed_views: number; trend_score: number; observed_velocity_per_hour: number };
type Member = { video_id: string; title: string | null; channel_title: string | null; video_url: string; thumbnail_url: string | null; view_count: number };
type Topic = { label: string; status: string; trend_score: number; observed_views: number; observed_velocity_per_hour: number; acceleration: number | null; member_count: number; channel_count: number; last_observed_at: string | null; source_mix: Record<string, number>; region_mix: Record<string, number>; snapshots: Snapshot[]; members: Member[]; methodology: string; scope?: string; period?: string };

const number = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

function MomentumChart({ snapshots }: { snapshots: Snapshot[] }) {
  if (snapshots.length < 2) return <p className="text-sm text-text-secondary">Mengumpulkan observasi berikutnya untuk grafik momentum.</p>;
  const values = snapshots.map((item) => item.observed_velocity_per_hour);
  const maximum = Math.max(...values, 1);
  const points = values.map((value, index) => `${(index / (values.length - 1)) * 460},${150 - (value / maximum) * 125}`).join(" ");
  return <svg viewBox="0 0 460 160" className="mt-4 h-44 w-full" aria-label="Observed velocity over time"><line x1="0" y1="151" x2="460" y2="151" stroke="rgb(var(--line))" /><polyline points={points} fill="none" stroke="rgb(var(--neon))" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

export default function MarketTopicDetailPage() {
  const params = useParams<{ topicId: string }>();
  const searchParams = useSearchParams();
  const scope = searchParams.get("scope") || "combined";
  const period = searchParams.get("period") || "7d";
  const { data, error } = useSWR<Topic>(`/api/v1/youtube/market/topics/${params.topicId}?scope=${scope}&period=${period}`, fetcher, { refreshInterval: 60_000 });
  if (error) return <PageState title="Topic belum tersedia" message="Topic detail belum memiliki bukti publik yang bisa dibaca saat ini." tone="error" actionHref="/youtube/topic-pool" actionLabel="Back to topic pool" />;
  if (!data) return <PageState title="Memuat topic evidence" message="Sistem sedang menunggu bukti publik untuk topik ini." tone="loading" actionHref="/youtube/topic-pool" actionLabel="Back to topic pool" />;
  return <div className="mx-auto max-w-7xl"><Link href="/youtube/topic-pool" className="text-sm text-neon">← Kembali ke Topic Pool</Link><section className="mt-5 rounded-xl border border-line bg-surface p-6"><p className="text-xs font-semibold uppercase tracking-[.16em] text-neon">{data.status} · {scope === "shorts" ? "Shorts saja" : scope === "videos" ? "Video biasa" : "Gabungan"} · {period === "today" ? "Hari ini" : period === "30d" ? "30 hari" : "7 hari"}</p><h1 className="mt-3 text-4xl font-bold">{data.label}</h1><div className="mt-6 grid gap-4 sm:grid-cols-4"><div><p className="text-xs text-text-tertiary">Views terpantau</p><p className="mt-1 font-mono text-xl">{number.format(data.observed_views)}</p></div><div><p className="text-xs text-text-tertiary">Kecepatan pertumbuhan</p><p className="mt-1 font-mono text-xl text-neon">{number.format(data.observed_velocity_per_hour)}/jam</p></div><div><p className="text-xs text-text-tertiary">Kreator berbeda</p><p className="mt-1 font-mono text-xl">{data.channel_count}</p></div><div><p className="text-xs text-text-tertiary">Skor topik tersimpan</p><p className="mt-1 font-mono text-xl">{Math.round(data.trend_score)}</p></div></div><MomentumChart snapshots={data.snapshots} /><p className="mt-2 text-xs text-text-tertiary">Terakhir terlihat: {data.last_observed_at ? new Intl.DateTimeFormat("id-ID", { dateStyle: "medium", timeStyle: "short" }).format(new Date(data.last_observed_at)) : "menunggu sumber"}</p></section><section className="mt-5 grid gap-4 md:grid-cols-2"><div className="rounded-xl border border-line bg-surface p-5"><h2 className="font-semibold">Sumber data</h2><p className="mt-1 text-sm text-text-secondary">Tempat sistem menemukan video bukti.</p><div className="mt-4 flex flex-wrap gap-2">{Object.entries(data.source_mix).map(([source, count]) => <span key={source} className="rounded-full border border-line px-3 py-1 font-mono text-xs">{source}: {count}</span>)}</div></div><div className="rounded-xl border border-line bg-surface p-5"><h2 className="font-semibold">Wilayah terpantau</h2><p className="mt-1 text-sm text-text-secondary">Cakupan sistem, bukan total global YouTube.</p><div className="mt-4 flex flex-wrap gap-2">{Object.entries(data.region_mix).map(([region, count]) => <span key={region} className="rounded-full border border-line px-3 py-1 font-mono text-xs">{region}: {count}</span>)}</div></div></section><section className="mt-7"><h2 className="text-xl font-semibold">Video pembentuk topik</h2><p className="mt-1 text-sm text-text-secondary">Semua kartu di bawah sesuai format dan periode yang dipilih dari Topic Pool.</p><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{data.members.map((member) => <a key={member.video_id} href={member.video_url} target="_blank" rel="noreferrer" className="overflow-hidden rounded-xl border border-line bg-surface hover:bg-white/[.03]"><div className="aspect-video bg-bg-secondary">{member.thumbnail_url && <img src={member.thumbnail_url} alt="" className="h-full w-full object-cover" />}</div><div className="p-3"><p className="line-clamp-2 text-sm font-medium">{member.title || member.video_id}</p><p className="mt-1 truncate text-xs text-text-secondary">{member.channel_title || "Kreator tidak diketahui"}</p><p className="mt-3 font-mono text-xs text-neon">{number.format(member.view_count)} views terpantau</p></div></a>)}</div></section><p className="mt-6 text-xs leading-5 text-text-tertiary">{data.methodology}</p></div>;
}

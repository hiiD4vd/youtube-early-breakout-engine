"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { PageState } from "@/components/page-state";
import { fetcher } from "@/lib/api";

type Member = {
  video_id: string; title: string | null; channel_title: string | null; video_url: string;
  thumbnail_url: string | null; current_view_count: number; velocity_per_hour: number;
  signal_tier: string; similarity_score: number | null; is_reupload_suspect: boolean;
  is_same_channel_duplicate: boolean; published_at?: string | null;
};

type Snapshot = {
  observed_at: string; observed_views: number; observed_velocity_per_hour: number;
  trend_score: number; member_count: number; channel_count: number;
};

type HumanSummary = {
  status_explanation?: string; fresh_24h_count?: number; fresh_72h_count?: number;
  newest_evidence_age_hours?: number | null; oldest_evidence_age_hours?: number | null;
  duplicate_count?: number; reupload_suspect_count?: number; history_points?: number;
  movement?: string; velocity_change_percent?: number | null; region_count?: number;
  region_data_available?: boolean;
};

type Trend = {
  label: string; label_confidence: number | null; semantic_cohesion: number | null;
  niche: string | null; status: string; trend_score: number; observed_views: number;
  observed_velocity_per_hour: number; acceleration: number | null; member_count: number;
  channel_count: number; cluster_reason: string | null; members: Member[];
  snapshots: Snapshot[]; human_summary?: HumanSummary;
};

const compact = new Intl.NumberFormat("id-ID", { notation: "compact", maximumFractionDigits: 1 });
function clean(value?: string | null) { return (value || "Belum tersedia").replaceAll("Â·", "·"); }
function percent(value: number | null) { return value == null ? "Belum dinilai" : `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`; }
function age(hours?: number | null) { if (hours == null) return "belum diketahui"; return hours < 24 ? `${Math.round(hours)} jam` : `${Math.round(hours / 24)} hari`; }
const topicStage: Record<string, string> = {
  PRIVATE_CANDIDATE: "Masih mengumpulkan bukti",
  EMERGING: "Mulai terlihat",
  ACCELERATING: "Pertumbuhannya meningkat",
  CONFIRMED: "Bukti sudah kuat",
  COOLING: "Tidak lagi bertambah cepat",
  ARCHIVED: "Riwayat topik",
};

function Metric({ label, value, note, neon = false }: { label: string; value: string | number; note: string; neon?: boolean }) {
  return <div className="rounded-xl border border-line bg-surface p-4"><p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">{label}</p><p className={`mt-2 font-mono text-xl ${neon ? "text-neon" : ""}`}>{value}</p><p className="mt-1 text-xs leading-5 text-text-secondary">{note}</p></div>;
}

function LineChart({ snapshots }: { snapshots: Snapshot[] }) {
  if (snapshots.length < 2) return <div className="flex h-48 items-center justify-center text-sm text-text-tertiary">Grafik muncul setelah minimal dua pengamatan.</div>;
  const pointsData = snapshots.slice(-20);
  const values = pointsData.map(item => item.observed_velocity_per_hour);
  const max = Math.max(...values, 1), min = Math.min(...values), range = Math.max(max-min, 1), width = 680, height = 180;
  const points = values.map((value,index) => `${(index/(values.length-1))*width},${height-18-((value-min)/range)*(height-36)}`).join(" ");
  return <div><svg viewBox={`0 0 ${width} ${height}`} className="h-52 w-full" preserveAspectRatio="none"><line x1="0" y1={height-18} x2={width} y2={height-18} stroke="rgba(255,255,255,.14)"/><polyline points={points} fill="none" stroke="rgb(var(--neon))" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/></svg><div className="flex justify-between text-xs text-text-tertiary"><span>{new Date(pointsData[0].observed_at).toLocaleString("id-ID")}</span><span>{new Date(pointsData.at(-1)!.observed_at).toLocaleString("id-ID")}</span></div></div>;
}

export default function TopicDetailPage() {
  const params = useParams<{ clusterId: string }>();
  const { data, error } = useSWR<Trend>(params.clusterId ? `/api/v1/youtube/trends/${params.clusterId}` : null, fetcher, { refreshInterval: 30_000 });
  if (error) return <PageState title="Topik belum dapat dibaca" message="Detail topik tidak ditemukan atau backend belum mengirim datanya." note="Data tetap aman." tone="error" actionHref="/youtube/topic-pool" actionLabel="Kembali ke daftar topik" />;
  if (!data) return <PageState title="Memuat detail topik" message="Mengambil momentum, kualitas data, dan video pembentuk topik." tone="loading" actionHref="/youtube/topic-pool" actionLabel="Kembali ke daftar topik" />;

  const summary = data.human_summary || {};
  const warnings = (summary.duplicate_count || 0) + (summary.reupload_suspect_count || 0);
  return <div className="mx-auto max-w-7xl">
    <Link href="/youtube/topic-pool" className="text-sm text-neon">← Kembali ke daftar topik</Link>

    <section className="mt-5 rounded-xl border border-line bg-surface p-6"><div className="grid gap-6 lg:grid-cols-[1.3fr_.7fr]"><div><p className="text-xs font-semibold uppercase tracking-[.16em] text-neon">{topicStage[data.status] || "Sedang dipantau"} · detail topik</p><h1 className="mt-3 text-3xl font-bold">{clean(data.label)}</h1><p className="mt-2 text-text-secondary">{data.member_count} video bukti dari {data.channel_count} kreator berbeda</p><p className="mt-4 text-sm leading-6 text-text-secondary">{summary.status_explanation || data.cluster_reason || "Sistem masih mengumpulkan bukti untuk topik ini."}</p></div>
      <div className="border-l border-line pl-0 lg:pl-6"><p className="text-xs uppercase tracking-[.14em] text-text-tertiary">Views seluruh bukti</p><p className="mt-1 font-mono text-3xl">{compact.format(data.observed_views)}</p><p className="mt-4 text-xs uppercase tracking-[.14em] text-text-tertiary">Momentum teramati</p><p className="mt-1 font-mono text-lg text-neon">{compact.format(data.observed_velocity_per_hour)}/jam</p><p className="mt-3 text-xs text-text-tertiary">Skor sistem {Math.round(data.trend_score)} · akselerasi {data.acceleration == null ? "sedang dikumpulkan" : data.acceleration.toFixed(2)}</p></div></div></section>

    <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="Keyakinan nama" value={percent(data.label_confidence)} note="Seberapa yakin AI terhadap nama topik" />
      <Metric label="Kekompakan isi" value={percent(data.semantic_cohesion)} note="Kemiripan isi antarvideo dalam klaster" />
      <Metric label="Bukti baru" value={summary.fresh_24h_count || 0} note={`Dalam 24 jam · bukti terbaru ${age(summary.newest_evidence_age_hours)}`} neon />
      <Metric label="Kualitas bukti" value={warnings || "Bersih"} note={warnings ? "Video perlu diperiksa karena duplikat/reupload" : "Tidak ada peringatan duplikat/reupload"} />
    </section>

    <section className="mt-5 rounded-xl border border-line bg-surface p-6"><div className="flex flex-wrap items-baseline justify-between gap-2"><div><h2 className="font-semibold">Perkembangan momentum</h2><p className="mt-1 text-sm text-text-secondary">Perubahan kecepatan views dari video bukti yang sama sepanjang waktu.</p></div><span className="text-xs text-text-tertiary">Bukan angka total seluruh YouTube</span></div><div className="mt-5"><LineChart snapshots={data.snapshots}/></div></section>

    <section className="mt-5"><div className="flex items-baseline justify-between"><div><h2 className="text-xl font-semibold">Video pembentuk topik</h2><p className="mt-1 text-sm text-text-secondary">Periksa isi video untuk memastikan semuanya benar-benar mendukung topik yang sama.</p></div><span className="text-sm text-text-tertiary">{data.members.length} video</span></div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{data.members.map(member => <a key={member.video_id} href={member.video_url} target="_blank" rel="noreferrer" className="overflow-hidden rounded-xl border border-line bg-surface transition hover:border-line-strong hover:bg-white/[.03]"><div className="aspect-video bg-bg-secondary">{member.thumbnail_url && <img src={member.thumbnail_url} alt="" className="h-full w-full object-cover"/>}</div><div className="p-4"><p className="text-xs font-semibold text-neon">{member.signal_tier} · {member.similarity_score == null ? "bukti" : `${Math.round(member.similarity_score*100)}% kemiripan`}</p><h3 className="mt-2 line-clamp-2 font-semibold">{clean(member.title || member.video_id)}</h3><p className="mt-1 truncate text-sm text-text-secondary">{member.channel_title || "Channel tidak diketahui"}</p><div className="mt-4 flex gap-4 font-mono text-sm"><span>{compact.format(member.current_view_count)} views</span><span className="text-neon">{compact.format(member.velocity_per_hour)}/jam</span></div>{(member.is_reupload_suspect || member.is_same_channel_duplicate) && <p className="mt-3 text-xs text-warning">{member.is_reupload_suspect ? "Diduga reupload" : "Duplikat dari channel yang sama"}</p>}<p className="mt-4 text-xs font-medium text-neon">Tonton di YouTube →</p></div></a>)}</div>
    </section>
  </div>;
}

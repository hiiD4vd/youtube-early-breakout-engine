"use client";

import { useState } from "react";
import useSWR from "swr";
import { API_BASE, fetcher } from "@/lib/api";
import { PageState } from "@/components/page-state";

type Member = { video_id: string; title: string | null; channel_title: string | null; video_url: string; thumbnail_url: string | null };
type Candidate = { id: number; label: string; status: string; is_v2: boolean; member_count: number; channel_count: number; summary: string; instruction: string; members: Member[] };
type Queue = { items: Candidate[]; total_pending: number; backfill: { legacy_clusters: number; v2_promoted: number; reviewed: number }; decisions: string[]; methodology: string };
type TruthMember = { video_id: string; title: string | null; thumbnail_url: string | null; video_url: string; audit_status: string; content_summary: string | null; mismatch_reason: string | null };
type TruthItem = { id: number; label: string; status: string; truth: { status?: string; audited_count?: number; aligned_count?: number; mismatch_count?: number; required_audits?: number }; members: TruthMember[] };
type TruthQueue = { items: TruthItem[]; methodology: string };

const actions = [
  { key: "VALID_TOPIC", title: "Ya, ini satu topik", note: "Video-video ini jelas membahas hal yang sama.", tone: "bg-neon text-black" },
  { key: "WRONG_MERGE", title: "Bukan satu topik", note: "Video terlihat tercampur hanya karena kata/nama yang mirip.", tone: "border border-red-400/40 text-red-300 hover:bg-red-400/10" },
  { key: "TOO_GENERIC", title: "Hanya kategori, belum tren", note: "Contoh: semua tentang sepak bola, tetapi bukan satu pertandingan atau peristiwa.", tone: "border border-warning/40 text-warning hover:bg-warning/10" },
  { key: "NEEDS_MORE_EVIDENCE", title: "Butuh bukti lagi", note: "Arahnya masuk akal, tetapi contoh videonya belum cukup.", tone: "border border-line text-text-secondary hover:bg-white/[.05]" },
];

export default function TopicReviewPage() {
  const { data, error, mutate } = useSWR<Queue>("/api/v1/youtube/market/ranked-review-queue", fetcher, { refreshInterval: 60_000 });
  const { data: truthData } = useSWR<TruthQueue>("/api/v1/youtube/market/content-truth-review", fetcher, { refreshInterval: 60_000 });
  const [index, setIndex] = useState(0);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const candidate = data?.items[index];

  async function decide(decision: string) {
    if (!candidate) return;
    setSaving(true); setMessage("");
    try {
      const response = await fetch(`${API_BASE}/youtube/market/ranked-topics/${candidate.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) });
      if (!response.ok) throw new Error("Keputusan belum tersimpan. Coba lagi.");
      setMessage("Tersimpan. Lanjut ke kandidat berikutnya.");
      await mutate(); setIndex(0);
    } catch (err) { setMessage(err instanceof Error ? err.message : "Keputusan belum tersimpan."); } finally { setSaving(false); }
  }

  if (error) return <PageState title="Halaman review belum dapat dimuat" message="Queue review tidak bisa diambil dari backend saat ini." note="Ini tidak menghapus kandidat; hanya tampilan review yang belum bisa dibaca." tone="error" />;
  if (!data) return <PageState title="Memuat queue review" message="Sedang menunggu kandidat masuk ke antrean review." tone="loading" />;

  return <div className="mx-auto max-w-5xl">
    <p className="text-xs font-semibold uppercase tracking-[.2em] text-neon">Quality check</p>
    <h1 className="mt-2 text-3xl font-bold tracking-tight md:text-4xl">Apakah ini benar-benar satu topik?</h1>
    <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">Tidak perlu memahami angka atau istilah teknis. Lihat beberapa video di bawah, lalu pilih jawaban yang paling sesuai. Keputusan Anda membantu AI tidak mengulang kesalahan yang sama.</p>

    <section className="mt-6 grid gap-3 sm:grid-cols-3">
      <Stat label="Cluster V1 tersisa" value={String(data.backfill.legacy_clusters)} hint="sedang divalidasi ulang" />
      <Stat label="Topik V2 lolos" value={String(data.backfill.v2_promoted)} hint="sudah layak tampil" />
      <Stat label="Review Anda" value={String(data.backfill.reviewed)} hint="membantu kalibrasi AI" />
    </section>

    {truthData?.items.length ? <section className="mt-6 rounded-2xl border border-warning/30 bg-warning/5 p-5 md:p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-[.16em] text-warning">Pemeriksaan kecocokan isi</p><h2 className="mt-2 text-xl font-semibold">Topik ditahan karena judul belum terbukti sesuai isi video</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">Ini bukan tuduhan bot atau manipulasi. Sistem hanya memastikan topik tidak dibuat dari judul yang tidak cocok dengan video.</p></div><span className="rounded-full bg-warning/10 px-3 py-1 text-xs font-semibold text-warning">{truthData.items.length} ditahan</span></div><div className="mt-5 grid gap-3">{truthData.items.slice(0, 3).map((item) => <div key={item.id} className="rounded-xl border border-line bg-bg-primary/40 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-semibold">{item.label}</p><span className={`rounded-full px-2 py-1 text-[10px] font-semibold ${item.status === "QUARANTINED_METADATA_MISMATCH" ? "bg-red-500/15 text-red-300" : "bg-warning/10 text-warning"}`}>{item.status === "QUARANTINED_METADATA_MISMATCH" ? "Ditahan: isi tidak cocok" : "Menunggu bukti isi"}</span></div><p className="mt-2 text-xs text-text-secondary">{item.truth.aligned_count ?? 0} cocok · {item.truth.mismatch_count ?? 0} tidak cocok · {item.truth.audited_count ?? 0}/{item.truth.required_audits ?? 2} diperiksa</p><div className="mt-3 grid gap-2 sm:grid-cols-2">{item.members.slice(0, 4).map((member) => <a key={member.video_id} href={member.video_url} target="_blank" rel="noreferrer" className="flex gap-3 rounded-lg border border-line p-2 hover:bg-white/[.03]"><div className="h-12 w-9 shrink-0 overflow-hidden rounded bg-bg-secondary">{member.thumbnail_url && <img src={member.thumbnail_url} alt="" className="h-full w-full object-cover" />}</div><span className="min-w-0"><span className="block truncate text-xs font-medium">{member.title || member.video_id}</span><span className={`mt-1 block text-[11px] ${member.audit_status === "MISMATCH" ? "text-red-300" : member.audit_status === "ALIGNED" ? "text-neon" : "text-warning"}`}>{member.audit_status === "MISMATCH" ? "Isi tidak mendukung judul" : member.audit_status === "ALIGNED" ? "Isi mendukung judul" : "Bukti belum cukup"}</span>{member.mismatch_reason && <span className="mt-1 block line-clamp-2 text-[11px] text-text-tertiary">{member.mismatch_reason}</span>}</span></a>)}</div></div>)}</div><p className="mt-4 text-xs text-text-tertiary">{truthData.methodology}</p></section> : null}

    {message && <p className="mt-5 rounded-lg border border-neon/30 bg-neon-dim px-4 py-3 text-sm text-neon">{message}</p>}
    {candidate ? <section className="mt-6 rounded-2xl border border-line bg-surface p-5 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-[.16em] text-neon">Langkah {index + 1} dari {data.items.length}</p><p className="mt-2 text-xs font-semibold uppercase tracking-[.12em] text-warning">{candidate.is_v2 ? "Usulan topik V2" : "Label lama V1 — belum dianggap benar"}</p><h2 className="mt-1 text-2xl font-semibold">{candidate.label}</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">{candidate.summary}</p></div><span className={`rounded-full px-3 py-1 text-xs font-semibold ${candidate.is_v2 ? "bg-neon-dim text-neon" : "bg-warning/10 text-warning"}`}>{candidate.is_v2 ? "V2 candidate" : "Needs your check"}</span></div>
      <div className="mt-5 rounded-xl border border-line bg-bg-primary/40 p-4"><p className="text-sm font-medium">Yang perlu Anda cek</p><p className="mt-1 text-sm text-text-secondary">{candidate.instruction}</p><p className="mt-2 font-mono text-xs text-text-tertiary">{candidate.member_count} Shorts · {candidate.channel_count} channel</p></div>
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{candidate.members.map((member) => <a key={member.video_id} href={member.video_url} target="_blank" rel="noreferrer" className="group flex gap-3 rounded-xl border border-line bg-bg-primary/30 p-3 transition hover:border-line-strong hover:bg-white/[.03]"><div className="h-20 w-14 shrink-0 overflow-hidden rounded-md bg-bg-secondary">{member.thumbnail_url && <img src={member.thumbnail_url} alt="" className="h-full w-full object-cover" />}</div><span className="min-w-0"><span className="line-clamp-2 text-sm font-medium group-hover:text-neon">{member.title || member.video_id}</span><span className="mt-2 block truncate text-xs text-text-secondary">{member.channel_title || "Unknown channel"}</span><span className="mt-1 block text-[11px] text-neon">Buka video ↗</span></span></a>)}</div>
      <div className="mt-6"><p className="text-sm font-medium">Pilih satu jawaban</p><div className="mt-3 grid gap-2 sm:grid-cols-2">{actions.map((action) => <button key={action.key} type="button" disabled={saving} onClick={() => decide(action.key)} className={`rounded-xl px-4 py-3 text-left transition disabled:opacity-50 ${action.tone}`}><span className="block text-sm font-semibold">{saving ? "Menyimpan..." : action.title}</span><span className="mt-1 block text-xs opacity-80">{action.note}</span></button>)}</div><button type="button" onClick={() => setIndex((index + 1) % data.items.length)} className="mt-4 text-sm text-text-secondary hover:text-text-primary">Lewati kandidat ini →</button></div>
    </section> : <section className="mt-6 rounded-2xl border border-dashed border-line-strong p-8 text-center"><p className="font-medium">Belum ada kandidat yang perlu Anda review.</p><p className="mt-2 text-sm text-text-secondary">Pipeline tetap melakukan backfill V1 ke V2 secara otomatis.</p></section>}
    <p className="mt-6 text-xs leading-5 text-text-tertiary">{data.methodology}</p>
  </div>;
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) { return <div className="rounded-xl border border-line bg-surface p-4"><p className="text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary">{label}</p><p className="mt-2 font-mono text-2xl text-neon">{value}</p><p className="mt-1 text-xs text-text-secondary">{hint}</p></div>; }

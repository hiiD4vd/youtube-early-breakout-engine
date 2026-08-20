"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { PageState } from "@/components/page-state";
import { fetcher } from "@/lib/api";
import { Pagination } from "@/components/pagination";

type Evidence = {
  video_id: string;
  thumbnail_url: string | null;
  current_view_count?: number | null;
};

type Snapshot = {
  observed_velocity_per_hour: number;
};

type Topic = {
  id: string;
  detail_href?: string;
  label: string;
  niche: string | null;
  status: string;
  observed_views: number;
  observed_velocity_per_hour: number;
  member_count: number;
  channel_count: number;
  members: Evidence[];
  snapshots?: Snapshot[];
  human_summary?: { movement?: string };
  ranking_score?: number;
  ranking_reason?: string;
  period_growth_views?: number;
  organic_velocity_per_hour?: number;
  media_mix?: { shorts?: number; videos?: number };
};

type Scope = "shorts" | "videos" | "combined";
type Period = "today" | "7d" | "30d";
type Diagnostics = { stored_scope_videos: number; featured_scope_videos: number; semantic_scope_videos: number; clustered_scope_videos: number; pending_semantic_videos: number };
type Response = { items: Topic[]; total_items: number; offset: number; limit: number; has_more: boolean; scope: Scope; period: Period; diagnostics?: Diagnostics };

const compact = new Intl.NumberFormat("id-ID", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function clean(value: string | null | undefined) {
  const readable = (value || "Topik belum diberi nama").replaceAll("Â·", "·");
  if (/ranking moments compilations/i.test(readable)) return "Video ranking dan hitung mundur";
  return readable;
}

function MiniTrend({ topic }: { topic: Topic }) {
  const values = (topic.snapshots || []).map((item) => item.observed_velocity_per_hour).slice(-8);
  if (values.length >= 2) {
    const max = Math.max(...values, 1);
    const min = Math.min(...values);
    const range = Math.max(max - min, 1);
    const points = values.map((value, index) => `${(index / (values.length - 1)) * 64},${24 - ((value - min) / range) * 18}`).join(" ");
    return <svg viewBox="0 0 64 28" className="h-8 w-16"><polyline points={points} fill="none" stroke="rgb(var(--neon))" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }

  const movement = topic.human_summary?.movement;
  const points = movement === "RISING" ? "2,23 18,17 32,19 48,8 62,5" : movement === "FALLING" ? "2,5 18,8 32,7 48,18 62,22" : "2,15 18,14 32,15 48,13 62,14";
  return <svg viewBox="0 0 64 28" className="h-8 w-16"><polyline points={points} fill="none" stroke="rgb(var(--neon))" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function EvidenceThumbs({ members }: { members: Evidence[] }) {
  return <div className="flex gap-2 overflow-hidden">
    {members.slice(0, 5).map((item) => <div key={item.video_id} className="relative h-[76px] w-[54px] shrink-0 overflow-hidden rounded-lg border border-line bg-bg-secondary">
      {item.thumbnail_url && <img src={item.thumbnail_url} alt="" className="h-full w-full object-cover" />}
      {!!item.current_view_count && <span className="absolute inset-x-0 bottom-0 bg-black/75 px-1 py-0.5 text-center text-[9px] text-white">▶ {compact.format(item.current_view_count)}</span>}
    </div>)}
  </div>;
}

export default function TopicPoolPage() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [scope, setScope] = useState<Scope>("combined");
  const [period, setPeriod] = useState<Period>("7d");
  const offset = (page - 1) * pageSize;
  const { data, error } = useSWR<Response>(`/api/v1/youtube/topic-pool?limit=${pageSize}&offset=${offset}&scope=${scope}&period=${period}${debouncedQuery ? `&q=${encodeURIComponent(debouncedQuery)}` : ""}`, fetcher, { refreshInterval: 60_000 });

  useEffect(() => setPage(1), [scope, period]);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, 350);
    return () => clearTimeout(timer);
  }, [query]);

  const items = data?.items || [];

  if (error) return <PageState title="Topic pool belum dapat dimuat" message="Frontend gagal membaca data calon topik." note="Data yang sudah tersimpan tidak dihapus." tone="error" actionHref="/youtube/report" actionLabel="Cek laporan kesehatan" />;
  if (!data) return <PageState title="Memuat calon topik" message="Mengambil daftar topik dan video terkait." tone="loading" />;

  return <div className="mx-auto max-w-[1500px]">
    <section className="flex flex-wrap items-end justify-between gap-5 border-b border-line pb-5">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[.18em] text-neon">Eksplorasi topik</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">Topik yang sedang dipantau</h1>
        <p className="mt-2 text-sm text-text-secondary">Fitur mandiri untuk menjelajahi kelompok video yang membahas hal sama. Pilih format dan periode; sistem menghitung ulang peringkatnya otomatis.</p>
      </div>
      <div className="rounded-xl border border-line bg-surface px-4 py-3 text-right"><p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">Topik ditemukan</p><p className="mt-1 font-mono text-2xl text-neon">{data.total_items}</p><p className="text-[10px] text-text-tertiary">menampilkan {offset + 1}–{Math.min(offset + data.limit, data.total_items)}</p></div>
    </section>

    <section className="mt-5 rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-2 text-xs font-semibold uppercase tracking-[.12em] text-text-tertiary">Jenis video</span>
        {([['combined', 'Gabungan'], ['shorts', 'Shorts saja'], ['videos', 'Video biasa']] as [Scope, string][]).map(([value, label]) => <button key={value} type="button" onClick={() => setScope(value)} className={`rounded-lg px-3 py-2 text-sm transition ${scope === value ? 'bg-neon text-black' : 'border border-line bg-bg-primary text-text-secondary hover:text-white'}`}>{label}</button>)}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="mr-2 text-xs font-semibold uppercase tracking-[.12em] text-text-tertiary">Periode</span>
        {([['today', 'Hari ini'], ['7d', '7 hari'], ['30d', '30 hari']] as [Period, string][]).map(([value, label]) => <button key={value} type="button" onClick={() => setPeriod(value)} className={`rounded-lg px-3 py-2 text-sm transition ${period === value ? 'bg-neon text-black' : 'border border-line bg-bg-primary text-text-secondary hover:text-white'}`}>{label}</button>)}
      </div>
      <p className="mt-3 text-xs leading-5 text-text-secondary">Peringkat: 45% kenaikan views nyata, 20% percepatan, 15% kreator baru, 10% sebaran wilayah, dan 10% kebaruan bukti. Total views hanya informasi pendukung.</p>
    </section>

    <section className="mt-4 flex flex-wrap items-center gap-3">
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Cari topik…" className="min-w-[240px] flex-1 rounded-lg border border-line bg-surface px-3 py-2.5 text-sm outline-none focus:border-neon/50" />
      <p className="rounded-lg border border-line bg-surface px-3 py-2.5 text-xs text-text-secondary">Urutan server—bukan hasil sortir sementara di browser.</p>
    </section>

    <section className="mt-4 overflow-hidden rounded-xl border border-line bg-surface">
      <div className="hidden grid-cols-[64px_minmax(260px,1.4fr)_150px_130px_minmax(290px,1fr)] gap-4 border-b border-line bg-white/[.02] px-6 py-3 text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary lg:grid">
        <span>Peringkat</span><span>Topik</span><span>Kenaikan</span><span>Momentum</span><span>Video terkait</span>
      </div>

      {items.map((topic, index) => <Link key={topic.id} href={topic.detail_href || `/youtube/trends/${topic.id}?from=topic-pool`} className="grid gap-4 border-b border-line px-5 py-5 transition last:border-0 hover:bg-white/[.025] lg:grid-cols-[64px_minmax(260px,1.4fr)_150px_130px_minmax(290px,1fr)] lg:items-center lg:px-6">
        <div><span className={`inline-flex h-9 w-9 items-center justify-center rounded-lg border font-mono ${offset + index < 3 ? "border-warning/50 bg-warning/10 text-warning" : "border-line bg-bg-primary text-text-secondary"}`}>{offset + index + 1}</span></div>
        <div className="min-w-0"><h2 className="text-base font-semibold leading-6">{clean(topic.label)}</h2><p className="mt-1 text-xs text-text-secondary">{topic.member_count} video · {topic.channel_count} kreator · {topic.media_mix?.shorts || 0} Shorts · {topic.media_mix?.videos || 0} video biasa</p><p className="mt-2 line-clamp-1 text-[11px] text-text-tertiary">{topic.ranking_reason || "Peringkat dihitung dari bukti pertumbuhan yang teramati."}</p></div>
        <div><p className="font-mono text-base text-neon">+{compact.format(topic.period_growth_views || 0)}</p><p className="mt-1 text-[11px] text-text-tertiary">views dalam periode</p><p className="mt-1 text-[10px] text-text-tertiary">{compact.format(topic.observed_views)} views terpantau</p></div>
        <div className="flex items-center gap-3 lg:block"><MiniTrend topic={topic}/><p className="font-mono text-[11px] text-neon">+{compact.format(topic.organic_velocity_per_hour ?? topic.observed_velocity_per_hour)}/jam</p><p className="text-[10px] text-text-tertiary">skor {Math.round(topic.ranking_score || 0)}/100</p></div>
        <div className="flex items-center justify-between gap-4"><EvidenceThumbs members={topic.members}/><span className="shrink-0 text-sm font-medium text-neon">Buka →</span></div>
      </Link>)}

      {!items.length && <div className="px-6 py-14 text-center">
        <p className="font-medium">{query ? "Tidak ada topik yang cocok." : scope === "videos" ? "Video biasa belum selesai dibentuk menjadi topik." : "Belum ada topik untuk pilihan ini."}</p>
        {query ? <p className="mt-2 text-sm text-text-secondary">Hapus kata pencarian untuk menampilkan seluruh topik yang tersedia.</p> : <>
          <p className="mt-2 text-sm text-text-secondary">Video tersimpan tidak hilang. Sistem memproses fitur, memahami isi metadata, lalu mengelompokkannya secara bertahap.</p>
          {data.diagnostics && <p className="mt-3 font-mono text-xs text-neon">{compact.format(data.diagnostics.stored_scope_videos)} tersimpan · {compact.format(data.diagnostics.featured_scope_videos)} siap dianalisis · {compact.format(data.diagnostics.semantic_scope_videos)} dipahami AI · {compact.format(data.diagnostics.clustered_scope_videos)} masuk cluster</p>}
        </>}
      </div>}
    </section>
    {data.total_items > pageSize && <Pagination page={page} pageSize={pageSize} total={data.total_items} onPageChange={setPage} onPageSizeChange={(size) => { setPageSize(size); setPage(1); }} />}
  </div>;
}

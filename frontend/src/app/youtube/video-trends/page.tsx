"use client";

import Link from "next/link";
import { useState } from "react";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { PageState } from "@/components/page-state";
import { Pagination } from "@/components/pagination";

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
  like_count: number | null;
  comment_count: number | null;
  engagement: number;
  duration_seconds: number;
  age_hours: number;
  rank: number | null;
  regional_rank?: number | null;
  global_internal_rank?: number | null;
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
  data_mode?: "official_chart_fallback";
  inner_tube_coverage?: {
    state: string;
    last_scan_at: string | null;
    last_success_at?: string | null;
    last_error_type?: string | null;
    last_error?: string | null;
  };
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
const regionNames = new Intl.DisplayNames(["id"], { type: "region" });

function regionLabel(value: string) {
  if (value === "Combined tracked regions") return value;
  return regionNames.of(value) ?? value;
}

function humanAge(hours: number | null | undefined) {
  if (hours == null || Number.isNaN(hours)) return "Age n/a";
  const total = Math.max(0, Math.round(hours));
  const days = Math.floor(total / 24);
  const remainder = total % 24;
  if (days && remainder) return `${days}d ${remainder}h`;
  if (days) return `${days}d`;
  return `${remainder}h`;
}

function youtubeWatchUrl(videoId: string, videoUrl?: string | null) {
  return videoUrl || `https://www.youtube.com/watch?v=${videoId}`;
}

function engagementRate(video: Video) {
  if (!video.view_count) return null;
  const score = (video.engagement || 0) / video.view_count;
  if (!score) return null;
  return `${(score * 100).toFixed(score >= 0.1 ? 1 : 2)}%`;
}

function Change({ value }: { value: number | null }) {
  if (value == null) return <span className="text-xs text-text-tertiary">Riwayat belum cukup</span>;
  if (value > 0) return <span className="text-xs font-medium text-neon">Naik {value} posisi</span>;
  if (value < 0) return <span className="text-xs font-medium text-warning">Turun {Math.abs(value)} posisi</span>;
  return <span className="text-xs text-text-tertiary">Posisi tetap</span>;
}

function Cycle({ minutes }: { minutes: number }) {
  if (!minutes) return <>Preparing</>;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return <>{hours ? `${hours}h ` : ""}{remainder}m</>;
}

const categoryOptions: CategoryOption[] = [
  { value: "", label: "All categories" },
  { value: "10", label: "Music" },
  { value: "17", label: "Sports" },
  { value: "24", label: "Entertainment" },
];

const regionOptions = [
  { value: "Combined tracked regions", label: "Combined tracked regions" },
  { value: "ID", label: "Indonesia" },
  { value: "US", label: "Amerika Serikat" },
  { value: "GB", label: "Britania Raya" },
  { value: "JP", label: "Jepang" },
  { value: "BR", label: "Brasil" },
  { value: "IN", label: "India" },
  { value: "MX", label: "Meksiko" },
];

const sortOptions: Array<{ value: SortOption; label: string }> = [
  { value: "rank", label: "Peringkat saat ini" },
  { value: "rank_gain", label: "Kenaikan peringkat" },
  { value: "velocity", label: "Pertumbuhan views" },
  { value: "engagement", label: "Interaksi penonton" },
  { value: "region_breadth", label: "Jangkauan negara" },
  { value: "streak", label: "Lama bertahan di chart" },
  { value: "new_entries", label: "Video baru masuk" },
];

function FilterChip({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm transition ${active ? "bg-warning text-bg-primary" : "bg-surface text-text-secondary hover:text-text-primary"}`}
    >
      {children}
    </button>
  );
}

function LoadingRow() {
  return (
    <div className="grid gap-4 border-b border-line px-5 py-5 last:border-0 lg:grid-cols-[52px_minmax(280px,1.55fr)_145px_145px_minmax(210px,1fr)] lg:items-center lg:px-6">
      <div className="h-8 w-8 animate-pulse rounded-lg bg-line/30" />
      <div className="flex gap-3">
        <div className="h-16 w-28 shrink-0 animate-pulse rounded-md bg-line/30" />
        <div className="flex-1 space-y-2"><div className="h-4 w-3/4 animate-pulse rounded bg-line/30" /><div className="h-3 w-1/2 animate-pulse rounded bg-line/30" /></div>
      </div>
      <div className="h-9 animate-pulse rounded bg-line/30" />
      <div className="h-9 animate-pulse rounded bg-line/30" />
      <div className="h-9 animate-pulse rounded bg-line/30" />
    </div>
  );
}

function TrendRow({ video, shortsOnly }: { video: Video; shortsOnly: boolean }) {
  const detailHref = shortsOnly ? `/youtube/shorts-trends/${video.video_id}` : `/youtube/video-trends/${video.video_id}`;
  const watchHref = youtubeWatchUrl(video.video_id, video.video_url);
  const rate = engagementRate(video);
  const rankValue = video.global_internal_rank ?? video.rank ?? "-";
  const regionalValue = video.regional_rank ?? video.rank ?? "-";
  const ageLabel = humanAge(video.age_hours);

  return (
    <article className="group grid gap-4 border-b border-line px-5 py-5 transition last:border-0 hover:bg-white/[.025] lg:grid-cols-[52px_minmax(280px,1.55fr)_145px_145px_minmax(210px,1fr)] lg:items-center lg:px-6">
      <div><span className="inline-flex h-8 min-w-8 items-center justify-center rounded-lg border border-warning/50 bg-warning/20 px-2 font-mono text-sm text-warning">{rankValue}</span></div>
      <div className="flex min-w-0 gap-3">
        <Link href={detailHref} className="relative shrink-0">
          <img src={video.thumbnail_url || `https://i.ytimg.com/vi/${video.video_id}/hqdefault.jpg`} alt="" className={`h-16 rounded-md border border-line object-cover transition group-hover:border-warning/50 ${shortsOnly ? "w-12" : "w-28"}`} />
        </Link>
        <div className="min-w-0">
          <Link href={detailHref}><h2 className="line-clamp-2 text-sm font-semibold transition group-hover:text-neon">{video.title || video.video_id}</h2></Link>
          <p className="mt-1 truncate text-xs text-text-secondary">{video.channel_title || "Channel belum diketahui"}</p>
          <p className="mt-1 text-[11px] text-text-tertiary">{video.duration || "Durasi belum tersedia"} · usia {ageLabel} · <a href={watchHref} target="_blank" rel="noreferrer" className="text-neon transition hover:text-warning">Tonton di YouTube</a></p>
        </div>
      </div>
      <div>
        <p className="font-mono text-sm text-text-primary">{compact.format(video.view_count)} views</p>
        <p className="mt-1 text-[11px] text-neon">bertambah {compact.format(video.views_gained)}</p>
        <p className="mt-1 text-[11px] text-text-tertiary">{video.observation_count > 1 ? `selama ${video.observation_span_hours} jam` : "baru satu kali dipantau"}</p>
      </div>
      <div>
        <p className="font-mono text-sm text-warning">+{compact.format(video.velocity_per_day)} views/hari</p>
        <p className="mt-1 text-[11px] text-text-secondary">{rate ? `${rate} interaksi` : "rasio interaksi belum tersedia"}</p>
        <div className="mt-1"><Change value={video.rank_change} /></div>
      </div>
      <div>
        <p className="text-sm text-text-primary">Terpantau di {video.region_count} negara</p>
        <p className="mt-1 line-clamp-2 text-xs text-text-secondary">{video.tracked_regions.map(regionLabel).join(" · ") || "Negara belum tercatat"}</p>
        <p className="mt-1 text-[11px] text-text-tertiary">Peringkat regional #{regionalValue} · masuk chart {video.observed_days} hari</p>
      </div>
    </article>
  );
}

export default function GeneralVideoTrendsPage() {
  const pathname = usePathname();
  const shortsOnly = pathname?.startsWith("/youtube/shorts-trends");
  const generalOnly = pathname?.startsWith("/youtube/general-trends");
  const [region, setRegion] = useState("Combined tracked regions");
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
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const offset = (page - 1) * pageSize;
  const params = new URLSearchParams({
    period_days: String(periodDays),
    sort,
    limit: String(pageSize),
    offset: String(offset),
  });
  if (region !== "Combined tracked regions") params.set("region", region);
  if (category) params.set("category", category);
  if (minDuration !== null && minDuration > 0) params.set("min_duration_seconds", String(minDuration * 60));
  if (maxDuration !== null && maxDuration > 0) params.set("max_duration_seconds", String(maxDuration * 60));
  if (minAge !== null && minAge > 0) params.set("min_age_hours", String(minAge));
  if (maxAge !== null && maxAge > 0) params.set("max_age_hours", String(maxAge));
  if (minViews > 0) params.set("min_views", String(minViews));
  if (minEngagement > 0) params.set("min_engagement", String(minEngagement));

  const endpoint = generalOnly
    ? "/api/v1/youtube/general-trends"
    : shortsOnly
      ? "/api/v1/youtube/shorts-trends"
      : "/api/v1/youtube/video-trends";
  const { data, error, isLoading } = useSWR<Response>(`${endpoint}?${params}`, fetcher, {
    refreshInterval: 60_000,
    keepPreviousData: true,
  });

  function applyFilter(change: () => void) {
    setPage(1);
    change();
  }

  if (error) {
    return <PageState title="Chart video belum dapat dimuat" message="Collector sedang tidak mengirim data, backend belum sempat menjawab, atau proxy API sedang restart. Halaman ini akan mencoba lagi otomatis tanpa merusak data yang sudah ada." note="Kalau baru saja kamu menjalankan Docker atau restart worker, tunggu sebentar lalu refresh. Kalau tetap kosong, kemungkinan lane datanya memang belum terisi." tone="error" actionHref="/youtube/report" actionLabel="Lihat laporan kesehatan" />;
  }

  const items = data?.items || [];
  const coverage = data?.coverage;
  const pagination = data?.pagination;
  const regionSummary = data?.region_health?.length
    ? {
        active: data.region_health.filter((item) => item.state === "active").length,
        stale: data.region_health.filter((item) => item.state === "stale").length,
        failed: data.region_health.filter((item) => item.state === "failed").length,
      }
    : null;
  const pageLabel = generalOnly ? "YouTube general trends" : shortsOnly ? "YouTube shorts trends" : "YouTube video trends";
  const pageHeadline = generalOnly ? "Combined tracked regions" : shortsOnly ? "Top trending Shorts" : "Top trending videos";
  const pageDescription = generalOnly
    ? "Video umum dari penelusuran YouTube. Video landscape tetap dipakai di sini; Shorts dipindahkan ke halaman Shorts setelah formatnya terverifikasi."
    : shortsOnly
      ? "Hanya video yang benar-benar terverifikasi sebagai YouTube Shorts. Angka di bawah menjelaskan popularitas, pertumbuhan, dan jangkauan dengan bahasa sederhana."
      : "Video populer dari chart regional resmi YouTube. Semua angka adalah hasil pemantauan sistem, bukan klaim ranking global YouTube.";
  const coverageLabel = generalOnly ? "Negara yang ditargetkan" : shortsOnly ? "Target negara Shorts" : "Target negara";
  const sweepLabel = generalOnly ? "Waktu satu putaran" : shortsOnly ? "Waktu satu putaran" : "Waktu satu putaran";
  const sweepNote = "perkiraan seluruh negara selesai dipindai";
  const emptyTitle = generalOnly
    ? "Belum ada video umum yang lolos pemisahan format."
    : shortsOnly
      ? "Belum ada Shorts verified yang lolos pemisahan format."
      : "Belum ada video umum yang telah lolos pemisahan format.";
  const emptyDescription = generalOnly
    ? "Collector tetap menyimpan hasilnya. Video berdurasi pendek akan diperiksa formatnya dahulu, lalu diarahkan ke video umum atau Shorts."
    : shortsOnly
      ? "Collector Shorts sedang berjalan. Video tidak akan masuk halaman ini kecuali lolos verifikasi Shorts yang ketat."
      : "Collector region resmi sedang berjalan. Video tidak akan masuk halaman video kecuali lulus verifikasi format yang ketat.";

  return (
    <div className="mx-auto max-w-[1500px]">
      <section className="rounded-2xl border border-line bg-[radial-gradient(circle_at_85%_0%,rgba(255,184,0,.13),transparent_35%),rgb(var(--surface))] px-6 py-7 md:px-8">
        <p className="text-xs font-semibold uppercase tracking-[.2em] text-warning">{pageLabel}</p>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="text-3xl font-bold tracking-tight md:text-4xl">{pageHeadline}</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-text-secondary">{pageDescription}</p>
          </div>
          <div className="flex gap-3">
            <div className="rounded-xl border border-line bg-bg-primary/40 px-4 py-3">
              <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">{coverageLabel}</p>
              <p className="mt-1 font-mono text-2xl text-warning">{coverage ? coverage.target_regions : "-"}</p>
              <p className="text-[11px] text-text-tertiary">tracked regions</p>
            </div>
            <div className="rounded-xl border border-line bg-bg-primary/40 px-4 py-3">
              <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">{sweepLabel}</p>
              <p className="mt-1 font-mono text-2xl text-warning">{coverage ? <Cycle minutes={coverage.estimated_cycle_minutes} /> : "-"}</p>
              <p className="text-[11px] text-text-tertiary">{sweepNote}</p>
            </div>
          </div>
        </div>
      </section>

      {generalOnly && data?.data_mode === "official_chart_fallback" ? (
        <section className="mt-4 rounded-xl border border-warning/35 bg-warning/10 px-5 py-4">
          <p className="text-sm font-semibold text-warning">Menampilkan cadangan chart resmi YouTube</p>
          <p className="mt-1 text-xs leading-5 text-text-secondary">
            Koneksi InnerTube sedang terganggu, jadi halaman tetap menampilkan data resmi yang sudah tersimpan. Data ini tidak diklaim sebagai hasil InnerTube dan akan berganti otomatis setelah kolektor pulih.
          </p>
          {data.inner_tube_coverage?.last_error_type ? (
            <p className="mt-2 font-mono text-[11px] text-text-tertiary">
              Diagnosis terakhir: {data.inner_tube_coverage.last_error_type}
              {data.inner_tube_coverage.last_error?.includes("name resolution") ? " — DNS container tidak dapat menemukan alamat server." : ""}
            </p>
          ) : null}
        </section>
      ) : null}

      <section className="mt-5 space-y-4 border-b border-line pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex rounded-lg border border-line bg-surface p-1">
            {[
              { label: "Hari ini", days: 1 },
              { label: "7 hari", days: 7 },
              { label: "30 hari", days: 30 },
            ].map((option) => (
              <FilterChip
                key={option.days}
                active={periodDays === option.days}
                onClick={() => applyFilter(() => setPeriodDays(option.days))}
              >
                {option.label}
              </FilterChip>
            ))}
          </div>
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="text-xs font-medium text-neon transition hover:text-warning"
          >
            {showAdvanced ? "Sembunyikan filter" : "Tampilkan filter lengkap"} →
          </button>
        </div>

        <div className="flex flex-wrap gap-3">
          <select
            aria-label="Category"
            value={category}
            onChange={(event) => applyFilter(() => setCategory(event.target.value))}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
          >
            {categoryOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
          <select
            aria-label="Region"
            value={region}
            onChange={(event) => applyFilter(() => setRegion(event.target.value))}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
          >
            {regionOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
            {(data?.region_health ?? [])
              .filter((item) => item.region !== "Combined tracked regions")
              .map((item) => (
                <option key={item.region} value={item.region}>
                  {regionLabel(item.region)}{item.state === "active" ? "" : ` (${item.state})`}
                </option>
              ))}
          </select>
          <select
            aria-label="Sort"
            value={sort}
            onChange={(event) => applyFilter(() => setSort(event.target.value as SortOption))}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
          >
            {sortOptions.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </div>

        {showAdvanced && (
          <div className="grid gap-3 rounded-lg border border-line/50 bg-bg-primary/40 p-4 sm:grid-cols-2 md:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Durasi minimum (menit)</label>
              <input type="number" min="0" value={minDuration ?? ""} onChange={(e) => setMinDuration(e.target.value ? Number(e.target.value) : null)} className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none" placeholder="minutes" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Durasi maksimum (menit)</label>
              <input type="number" min="0" value={maxDuration ?? ""} onChange={(e) => setMaxDuration(e.target.value ? Number(e.target.value) : null)} className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none" placeholder="minutes" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Usia video minimum (jam)</label>
              <input type="number" min="0" value={minAge ?? ""} onChange={(e) => setMinAge(e.target.value ? Number(e.target.value) : null)} className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none" placeholder="hours" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Usia video maksimum (jam)</label>
              <input type="number" min="0" value={maxAge ?? ""} onChange={(e) => setMaxAge(e.target.value ? Number(e.target.value) : null)} className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none" placeholder="hours" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Views minimum</label>
              <input type="number" min="0" value={minViews} onChange={(e) => setMinViews(Number(e.target.value) || 0)} className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none" placeholder="views" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-text-secondary">Jumlah interaksi minimum</label>
              <input type="number" min="0" value={minEngagement} onChange={(e) => setMinEngagement(Number(e.target.value) || 0)} className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none" placeholder="likes + comments" />
            </div>
          </div>
        )}
      </section>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-secondary">
        <span>Sumber: {generalOnly ? "penelusuran publik YouTube (InnerTube)" : shortsOnly ? "video YouTube yang lolos verifikasi Shorts" : "chart regional resmi YouTube"}</span>
        <span>{coverage?.catalog_regions ?? "..."} negara tersedia dalam katalog YouTube</span>
        <span>{coverage?.state === "ok" ? "Pengambilan data berjalan normal" : `Status pengambilan: ${coverage?.state ?? "memuat"}`}</span>
        {regionSummary && (
          <span>
            {regionSummary.active} negara aktif
            {regionSummary.stale ? ` · ${regionSummary.stale} belum diperbarui` : ""}
            {regionSummary.failed ? ` · ${regionSummary.failed} gagal dipindai` : ""}
          </span>
        )}
      </div>

      <section className="mt-4 overflow-hidden rounded-xl border border-line bg-surface">
        <div className="hidden grid-cols-[52px_minmax(280px,1.55fr)_145px_145px_minmax(210px,1fr)] gap-4 border-b border-line bg-white/[.02] px-6 py-3 text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary lg:grid">
          <span>Posisi</span>
          <span>{shortsOnly ? "Shorts" : "Video"}</span>
          <span>Views dipantau</span>
          <span>Pertumbuhan</span>
          <span>Jangkauan & chart</span>
        </div>

        <div>
          {isLoading && [...Array(6)].map((_, i) => <LoadingRow key={i} />)}
          {!isLoading && items.map((video) => <TrendRow key={video.video_id} video={video} shortsOnly={shortsOnly} />)}
          {!items.length && !isLoading && (
            <div className="px-6 py-12 text-center">
              <p className="font-medium">{emptyTitle}</p>
              <p className="mt-2 text-sm text-text-secondary">{emptyDescription}</p>
            </div>
          )}
        </div>
      </section>

      {pagination && pagination.total > 0 && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={pagination.total}
          loading={isLoading}
          onPageChange={setPage}
          onPageSizeChange={(size) => { setPageSize(size); setPage(1); }}
        />
      )}

      <p className="mt-4 max-w-5xl text-xs leading-5 text-text-tertiary">{data?.methodology || "Mengambil metodologi chart..."}</p>
    </div>
  );
}

"use client";

import { useParams } from "next/navigation";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Observation = {
  observed_at: string;
  region: string | null;
  source_lane: string | null;
  source_rank: number | null;
  view_count: number | null;
  like_count: number | null;
  comment_count: number | null;
};

type Video = {
  video_id: string;
  title: string | null;
  thumbnail_url: string | null;
  channel_title: string | null;
  video_url: string | null;
  duration: string | null;
  published_at: string | null;
  view_count: number | null;
  rank: number | null;
  rank_change: number | null;
  velocity_per_hour: number | null;
  velocity_per_day: number | null;
  tracked_regions: string[];
  region_count: number | null;
  observed_days: number | null;
  last_observed_at: string | null;
};

type Response = {
  video: Video | null;
  observations: Observation[];
};

const compact = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export default function VideoTrendDetail() {
  const params = useParams<{ videoId: string }>();
  const videoId = params?.videoId;
  const { data, error } = useSWR<Response>(
    videoId ? `/api/v1/youtube/video-trends/${videoId}/history` : null,
    fetcher,
  );

  if (error) {
    return <p className="text-red-400">Gagal memuat riwayat video.</p>;
  }
  if (!data) {
    return <p className="text-text-secondary">Memuat riwayat...</p>;
  }

  const video = data.video;
  const observations = data.observations || [];
  const latest = observations[0];
  const regions = Array.from(
    new Set(
      observations
        .map((item) => item.region)
        .filter((region): region is string => Boolean(region)),
    ),
  );
  const chartPoints = observations
    .map((item) => ({
      t: item.observed_at ? new Date(item.observed_at) : null,
      v: Number(item.view_count || 0),
    }))
    .filter((item): item is { t: Date; v: number } => Boolean(item.t))
    .sort((a, b) => a.t.getTime() - b.t.getTime());

  return (
    <div className="mx-auto max-w-6xl">
      <a href="/youtube/video-trends" className="inline-flex text-sm text-text-secondary hover:text-neon">
        Back to video trends
      </a>

      <section className="mt-5 rounded-2xl border border-line bg-[radial-gradient(circle_at_85%_0%,rgba(255,184,0,.13),transparent_35%),rgb(var(--surface))] p-6 md:p-8">
        <div className="grid gap-6 lg:grid-cols-[1.1fr_.9fr]">
          <div className="space-y-4">
            <div className="flex items-center gap-3 text-xs font-semibold uppercase tracking-[.16em] text-warning">
              <span>Video trend detail</span>
              <span className="rounded-full border border-line bg-bg-primary/40 px-2 py-1 text-[10px] tracking-[.12em] text-text-secondary">
                {video?.rank ?? "-"} rank
              </span>
            </div>
            <h1 className="text-3xl font-bold tracking-tight md:text-4xl">
              {video?.title || videoId}
            </h1>
            <p className="max-w-2xl text-sm leading-6 text-text-secondary">
              {video?.channel_title || "Unknown channel"}
            </p>
            <p className="max-w-3xl text-sm leading-6 text-text-secondary">
              Halaman ini menunjukkan gerak satu video di chart publik YouTube dari waktu ke waktu. Ini bukan total resmi YouTube, tetapi observasi sistem pada chart regional yang kita kumpulkan sendiri.
            </p>
            <div className="flex flex-wrap gap-2 text-xs text-text-secondary">
              {video?.duration && <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1">{video.duration}</span>}
              {video?.published_at && <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1">Published {new Date(video.published_at).toLocaleDateString()}</span>}
              {video?.last_observed_at && <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1">Last seen {new Date(video.last_observed_at).toLocaleString()}</span>}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Metric label="Current views" value={compact.format(video?.view_count ?? 0)} />
            <Metric label="Velocity / day" value={`${compact.format(video?.velocity_per_day ?? 0)}/day`} />
            <Metric label="Velocity / hour" value={`${compact.format(video?.velocity_per_hour ?? 0)}/hr`} />
            <Metric label="Regions tracked" value={String(video?.region_count ?? regions.length)} />
            <Metric label="Observed days" value={String(video?.observed_days ?? observations.length)} />
            <Metric label="Rank change" value={video?.rank_change == null ? "n/a" : `${video.rank_change > 0 ? "+" : ""}${video.rank_change}`} />
          </div>
        </div>
      </section>

      <section className="mt-5 grid gap-4 lg:grid-cols-[1.25fr_.75fr]">
        <div className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">View timeline</h2>
          <p className="mt-1 text-sm text-text-secondary">
            Perubahan view dari observasi yang sama, diurutkan dari yang paling lama ke terbaru.
          </p>
          <div className="mt-4 h-44 w-full">
            <Sparkline points={chartPoints} />
          </div>
        </div>

        <div className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">Market footprint</h2>
          <p className="mt-1 text-sm text-text-secondary">
            {regions.length} region terekam di data ini.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {regions.length ? regions.map((region) => (
              <span key={region} className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1 text-xs text-text-secondary">
                {region}
              </span>
            )) : <span className="text-sm text-text-tertiary">Belum ada region.</span>}
          </div>
          <div className="mt-5 rounded-lg border border-line bg-bg-primary/40 p-4">
            <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">Latest observation</p>
            <p className="mt-1 text-sm text-text-secondary">
              {latest?.observed_at ? new Date(latest.observed_at).toLocaleString() : "Belum ada"}
            </p>
            <p className="mt-2 text-sm text-text-secondary">
              {latest?.region || "-"} | {latest?.source_lane || "unknown lane"}
            </p>
            <p className="mt-1 text-sm text-text-secondary">
              Rank {latest?.source_rank ?? "-"} | Views {compact.format(latest?.view_count ?? 0)}
            </p>
          </div>
        </div>
      </section>

      <section className="mt-5 rounded-xl border border-line bg-surface p-5">
        <h2 className="text-lg font-semibold">Observations</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Riwayat scan yang membentuk grafik ini.
        </p>
        <div className="mt-4 space-y-3">
          {observations.length === 0 && (
            <p className="text-text-secondary">Belum ada observasi.</p>
          )}
          {observations.map((obs) => (
            <div
              key={`${obs.observed_at}-${obs.region}-${obs.source_lane}`}
              className="grid gap-3 rounded-lg border border-line p-4 md:grid-cols-[1.2fr_.8fr_.7fr] md:items-center"
            >
              <div className="text-sm">
                <div className="font-medium">
                  {new Date(obs.observed_at).toLocaleString()}
                </div>
                <div className="text-xs text-text-secondary">
                  {obs.region || "-"} | {obs.source_lane}
                </div>
              </div>
              <div className="text-sm text-text-secondary">
                Views {compact.format(obs.view_count ?? 0)} | Likes {compact.format(obs.like_count ?? 0)} | Comments {compact.format(obs.comment_count ?? 0)}
              </div>
              <div className="text-right">
                <div className="font-mono text-lg">{obs.source_rank ?? "-"}</div>
                <div className="text-xs text-text-secondary">rank</div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-line bg-bg-primary/40 p-4">
      <p className="text-[10px] uppercase tracking-[.12em] text-text-tertiary">
        {label}
      </p>
      <p className="mt-1 font-mono text-lg text-neon">{value}</p>
    </div>
  );
}

function Sparkline({ points }: { points: { t: Date; v: number }[] }) {
  if (!points || points.length === 0) {
    return (
      <div className="flex h-full items-center justify-center rounded-md border border-dashed border-line text-sm text-text-tertiary">
        Tidak cukup data untuk grafik.
      </div>
    );
  }

  const xs = points.map((point) => point.t.getTime());
  const ys = points.map((point) => point.v);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = 8;
  const width = 640;
  const height = 170;

  const scaleX = (x: number) =>
    pad + ((x - minX) / (maxX - minX || 1)) * (width - pad * 2);
  const scaleY = (y: number) =>
    height - pad - ((y - minY) / (maxY - minY || 1)) * (height - pad * 2);

  const linePoints = points
    .map((point) => `${scaleX(point.t.getTime()).toFixed(2)},${scaleY(point.v).toFixed(2)}`)
    .join(" ");
  const path = `M ${linePoints.replaceAll(" ", " L ")}`;
  const area = `${path} L ${scaleX(points[points.length - 1].t.getTime()).toFixed(2)} ${height - pad} L ${scaleX(points[0].t.getTime()).toFixed(2)} ${height - pad} Z`;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      className="h-full w-full rounded-md bg-bg-primary/40"
    >
      <defs>
        <linearGradient id="trend-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#10b981" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#10b981" stopOpacity="0.03" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#trend-fill)" stroke="none" />
      <path
        d={path}
        fill="none"
        stroke="#10b981"
        strokeWidth={3}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {points.map((point, index) => (
        <circle
          key={index}
          cx={scaleX(point.t.getTime())}
          cy={scaleY(point.v)}
          r={2.5}
          fill="#052e16"
        />
      ))}
    </svg>
  );
}

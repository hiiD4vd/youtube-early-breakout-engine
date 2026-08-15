"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

type Snapshot = {
  observed_at: string;
  trend_score: number;
  observed_velocity_per_hour: number;
  organic_velocity_per_hour: number;
  organic_measurement_ready: boolean;
  observed_views: number;
};

type Evidence = {
  video_id: string;
  title: string | null;
  thumbnail_url: string | null;
  channel_title: string | null;
  video_url: string;
};

type Topic = {
  id: number;
  label: string;
  topic_type: string;
  status: string;
  observed_views: number;
  observed_velocity_per_hour: number;
  organic_velocity_per_hour: number;
  member_count: number;
  channel_count: number;
  region_count: number;
  semantic_summary: string | null;
  history_ready: boolean;
  why_moving: { new_member_count?: number; source_count?: number; entity_verified?: boolean };
  freshness: { buckets?: Record<string, number>; newest_hours?: number | null };
  content_truth?: { status?: string; aligned?: number; mismatched?: number; pending?: number };
  evidence: Evidence[];
  snapshots: Snapshot[];
};

const format = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

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

function History({
  snapshots,
  ready,
}: {
  snapshots: Snapshot[];
  ready: boolean;
}) {
  const organic = snapshots.filter((item) => item.organic_measurement_ready);
  if (!ready || organic.length < 3) {
    return (
      <div className="flex h-36 items-center justify-center rounded-md border border-dashed border-line text-sm text-text-tertiary">
        Grafik pertumbuhan organik muncul setelah tiga pengamatan yang sebanding.
      </div>
    );
  }

  const values = organic.map((item) => item.organic_velocity_per_hour);
  const min = Math.min(...values);
  const range = Math.max(1, Math.max(...values) - min);
  const points = values
    .map(
      (value, index) =>
        `${(index / (values.length - 1)) * 640},${150 - ((value - min) / range) * 118}`,
    )
    .join(" ");

  return (
    <svg viewBox="0 0 640 170" className="h-44 w-full" aria-label="Organic growth history">
      <polyline
        points={`0,150 ${points} 640,150`}
        fill="rgba(var(--neon),.12)"
        stroke="none"
      />
      <polyline
        points={points}
        fill="none"
        stroke="rgb(var(--neon))"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function TopicDetailPage() {
  const params = useParams<{ topicId: string }>();
  const { data, error } = useSWR<Topic>(
    `/api/v1/youtube/market/ranked-topics/${params.topicId}`,
    fetcher,
    { refreshInterval: 60_000 },
  );

  if (error) {
    return <p className="text-red-400">Topik tidak ditemukan atau belum tersedia.</p>;
  }
  if (!data) {
    return <p className="text-text-secondary">Memuat detail topik...</p>;
  }

  const isTheme = data.status === "THEME" || data.topic_type.endsWith("_theme");
  const buckets = data.freshness?.buckets || {};
  const truth = data.content_truth || {};
  const verificationStatus = truth.status || "AWAITING_CONTENT_VALIDATION";

  return (
    <div className="mx-auto max-w-[1500px]">
      <Link href="/youtube/trends" className="inline-flex text-sm text-text-secondary hover:text-neon">
        Back to trending topics
      </Link>

      <section className="mt-5 rounded-2xl border border-line bg-[radial-gradient(circle_at_90%_10%,rgba(55,125,255,.18),transparent_32%),rgb(var(--surface))] p-6 md:p-8">
        <div className="grid gap-8 lg:grid-cols-[1.25fr_.75fr]">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[.18em] text-neon">
              {isTheme ? "Topic" : "Event"}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <h1 className="text-3xl font-bold tracking-tight md:text-4xl">
                {data.label}
              </h1>
              <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[.14em] text-text-secondary">
                {data.status.toLowerCase()}
              </span>
            </div>
            <p className="mt-3 max-w-xl text-sm leading-6 text-text-secondary">
              {data.semantic_summary || "A cross-channel conversation observed in YouTube Shorts."}
            </p>
            <div className="mt-4 flex flex-wrap gap-2 text-xs text-text-secondary">
              <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1">
                {data.member_count} verified Shorts
              </span>
              <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1">
                {data.channel_count} creators
              </span>
              <span className="rounded-full border border-line bg-bg-primary/40 px-2.5 py-1">
                {data.region_count} regions
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2">
            <Metric label="Fresh coverage views" value={format.format(data.observed_views)} />
            <Metric label="Organic growth" value={`${format.format(data.organic_velocity_per_hour)}/hr`} />
            <Metric label="Creators" value={String(data.channel_count)} />
            <Metric label="Fresh Shorts" value={String(data.member_count)} />
          </div>
        </div>
      </section>

      <section className="mt-5 grid gap-4 lg:grid-cols-3">
        <div className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">Why this topic is moving</h2>
          <p className="mt-2 text-sm text-text-secondary">
            {data.why_moving?.new_member_count ?? 0} bukti baru ditambahkan pada perbandingan terakhir dari {data.channel_count} kreator independen.
          </p>
          <p className="mt-3 text-xs text-neon">
            Bukti baru memperluas cakupan; tidak menambah pertumbuhan view organik.
          </p>
          <p className="mt-4 text-xs text-text-tertiary">
            Entity verified: {data.why_moving?.entity_verified ? "yes" : "pending"}
          </p>
        </div>

        <div className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">Freshness of evidence</h2>
          <div className="mt-3 grid grid-cols-4 gap-2 text-center">
            <Box value={buckets["0_24h"] ?? 0} label="0-24h" />
            <Box value={buckets["24_72h"] ?? 0} label="24-72h" />
            <Box value={buckets["72_120h"] ?? 0} label="72-120h" />
            <Box value={buckets["120_168h"] ?? 0} label="120-168h" />
          </div>
          <p className="mt-3 text-xs text-text-tertiary">
            Bukti terbaru: {data.freshness?.newest_hours == null ? "sedang dihitung" : `${data.freshness.newest_hours}h lalu`}.
          </p>
        </div>

        <div className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-lg font-semibold">Content verification</h2>
          <p className="mt-2 text-sm text-text-secondary">
            {verificationStatus === "VALIDATED"
              ? "Isi Shorts sudah selaras dengan topik."
              : verificationStatus === "QUARANTINED_METADATA_MISMATCH"
                ? "Topik ditahan: judul tidak sesuai dengan isi Shorts."
                : "Audit isi sedang mengumpulkan bukti transcript dan visual."}
          </p>
          <div className="mt-3 flex gap-3 text-xs">
            <span className="text-neon">{truth.aligned ?? 0} sesuai</span>
            <span className="text-amber-300">{truth.pending ?? 0} menunggu</span>
            <span className="text-red-400">{truth.mismatched ?? 0} tidak sesuai</span>
          </div>
          <p className="mt-4 text-xs text-text-tertiary">
            Class: {isTheme ? "broad topic" : "event candidate"}
          </p>
        </div>
      </section>

      <section className="mt-5 rounded-xl border border-line bg-surface p-5">
        <h2 className="text-lg font-semibold">Organic view growth</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Hanya perubahan view dari Shorts yang sudah menjadi anggota topik pada scan sebelumnya.
        </p>
        <History snapshots={data.snapshots} ready={data.history_ready} />
      </section>

      <section className="mt-5 rounded-xl border border-line bg-surface p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Related Shorts</h2>
            <p className="mt-1 text-sm text-text-secondary">
              Bukti Shorts yang aktif dan masih relevan untuk topik ini.
            </p>
          </div>
          <div className="rounded-lg border border-line bg-bg-primary/40 px-4 py-2 text-xs text-text-secondary">
            {data.evidence.length} evidence items
          </div>
        </div>
        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {data.evidence.map((item) => (
            <a
              key={item.video_id}
              href={item.video_url}
              target="_blank"
              rel="noreferrer"
              className="overflow-hidden rounded-xl border border-line bg-bg-primary/30 transition hover:bg-white/[.04]"
            >
              <div className="aspect-[9/12] bg-bg-secondary">
                {item.thumbnail_url && (
                  <img
                    src={item.thumbnail_url}
                    alt=""
                    className="h-full w-full object-cover"
                  />
                )}
              </div>
              <div className="p-3">
                <p className="line-clamp-2 text-sm font-medium">
                  {item.title || item.video_id}
                </p>
                <p className="mt-1 truncate text-xs text-text-secondary">
                  {item.channel_title || "Unknown creator"}
                </p>
              </div>
            </a>
          ))}
        </div>
      </section>

      <p className="mt-6 text-xs text-text-tertiary">
        Metrik adalah observasi sistem ini, bukan total YouTube. Shorts lama dipertahankan hanya sebagai riwayat audit.
      </p>
    </div>
  );
}

function Box({ value, label }: { value: number; label: string }) {
  return (
    <div className="rounded-lg bg-bg-primary/50 p-2">
      <p className="font-mono text-lg text-neon">{value}</p>
      <p className="text-[10px] text-text-tertiary">{label}</p>
    </div>
  );
}

"use client";

import Link from "next/link";
import { useState } from "react";
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
  why_moving: { new_member_count: number };
  freshness: { buckets?: Record<string, number>; newest_hours?: number | null };
  evidence: Evidence[];
  snapshots: Snapshot[];
};

type TopicResponse = { items: Topic[]; methodology: string };

const categories = [
  { label: "All categories", value: "" },
  { label: "Entertainment", value: "entertainment" },
  { label: "Music", value: "music" },
  { label: "Sports", value: "sports" },
  { label: "News", value: "news" },
];
const regions = ["All regions", "ID", "US", "GB", "JP", "BR", "IN", "MX"];
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const format = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function TrendLine({ snapshots }: { snapshots: Snapshot[] }) {
  const organic = snapshots.filter((item) => item.organic_measurement_ready);
  if (organic.length < 3) {
    return <span className="text-xs text-text-tertiary">Building organic history</span>;
  }

  const values = organic.map((item) => item.organic_velocity_per_hour);
  const min = Math.min(...values);
  const range = Math.max(1, Math.max(...values) - min);
  const points = values
    .map(
      (value, index) =>
        `${(index / (values.length - 1)) * 92},${26 - ((value - min) / range) * 20}`,
    )
    .join(" ");

  return (
    <svg className="h-8 w-24" viewBox="0 0 92 30" aria-label="Organic view growth">
      <polyline
        points={points}
        fill="none"
        stroke="rgb(var(--neon))"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Rank({ value }: { value: number }) {
  const color =
    value === 1
      ? "border-warning/50 bg-warning/20 text-warning"
      : value === 2
        ? "border-slate-400/40 bg-slate-400/10 text-slate-200"
        : value === 3
          ? "border-orange-400/40 bg-orange-400/10 text-orange-300"
          : "border-line bg-bg-secondary text-text-tertiary";

  return (
    <span
      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border font-mono text-sm ${color}`}
    >
      {value}
    </span>
  );
}

function TopicBadge({ topic }: { topic: Topic }) {
  const kind = topic.topic_type.toLowerCase();
  const label = kind.includes("event")
    ? "EVENT"
    : kind.includes("theme")
      ? "TOPIC"
      : "SIGNAL";
  const style =
    label === "EVENT"
      ? "bg-neon-dim text-neon"
      : label === "TOPIC"
        ? "bg-sky-400/10 text-sky-300"
        : "bg-warning/10 text-warning";

  return (
    <>
      <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${style}`}>
        {label}
      </span>
      <span className="rounded-full bg-white/[.05] px-2 py-0.5 text-[10px] font-semibold text-text-secondary">
        {topic.status.toLowerCase()}
      </span>
    </>
  );
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">{label}</p>
      <p className="mt-1 font-mono text-2xl text-neon">{value}</p>
      <p className="text-[11px] text-text-tertiary">{note}</p>
    </div>
  );
}

export default function TrendingTopicsPage() {
  const [region, setRegion] = useState("All regions");
  const [category, setCategory] = useState("");
  const query = `/api/v1/youtube/market/ranked-topics?${new URLSearchParams({
    ...(region === "All regions" ? {} : { region }),
    ...(category ? { category } : {}),
  })}`;
  const { data, error } = useSWR<TopicResponse>(query, fetcher, {
    refreshInterval: 60_000,
  });

  if (error) {
    return <p className="text-red-400">Topik belum dapat dimuat. Sistem akan mencoba lagi otomatis.</p>;
  }
  if (!data) {
    return <p className="text-text-secondary">Memuat topik yang sedang bergerak...</p>;
  }

  const topTopic = data.items[0];
  const activeRegions = new Set(data.items.map((topic) => topic.region_count).filter((count) => count > 0));

  return (
    <div className="mx-auto max-w-[1500px]">
      <section className="mb-7 rounded-2xl border border-line bg-[radial-gradient(circle_at_85%_0%,rgba(55,125,255,.18),transparent_35%),rgb(var(--surface))] px-6 py-7 md:px-8">
        <p className="text-xs font-semibold uppercase tracking-[.2em] text-neon">
          YouTube Shorts intelligence
        </p>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="text-3xl font-bold tracking-tight md:text-4xl">
              What&apos;s moving on YouTube Shorts.
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
              Topik lintas channel dalam jendela 7 hari. Bukti baru diberi bobot paling besar; klaim event harus lolos pemeriksaan isi video.
            </p>
          </div>
          <div className="rounded-xl border border-line bg-bg-primary/40 px-4 py-3">
            <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">
              Topics found - 7d
            </p>
            <p className="mt-1 font-mono text-2xl text-neon">{data.items.length}</p>
          </div>
        </div>
      </section>

      <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Top topics"
          value={format.format(data.items.length)}
          note="rolling 7-day leaderboard"
        />
        <Stat
          label="Active regions"
          value={format.format(activeRegions.size)}
          note="topics with market breadth"
        />
        <Stat
          label="Current filter"
          value={region === "All regions" ? "Global" : region}
          note={category || "All categories"}
        />
        <Stat
          label="Method"
          value="cross-channel"
          note="Shorts only, no keyword ranking"
        />
      </section>

      {topTopic && (
        <section className="mb-5 rounded-xl border border-line bg-surface p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[.14em] text-neon">
                Current leader
              </p>
              <h2 className="mt-2 text-xl font-semibold">{topTopic.label}</h2>
              <p className="mt-1 text-sm text-text-secondary">
                {topTopic.member_count} verified Shorts - {topTopic.channel_count} creators - {topTopic.region_count} regions
              </p>
            </div>
            <div className="rounded-lg border border-line bg-bg-primary/40 px-4 py-3 text-right">
              <p className="text-[10px] uppercase tracking-[.14em] text-text-tertiary">
                Organic velocity
              </p>
              <p className="mt-1 font-mono text-xl text-neon">
                {format.format(topTopic.organic_velocity_per_hour)}/hr
              </p>
            </div>
          </div>
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div className="flex flex-wrap gap-2">
          {categories.map((item) => (
            <button
              key={item.value}
              type="button"
              onClick={() => setCategory(item.value)}
              className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
                category === item.value
                  ? "bg-neon text-black"
                  : "bg-surface text-text-secondary hover:bg-white/[.06] hover:text-text-primary"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <select
          aria-label="Region"
          value={region}
          onChange={(event) => setRegion(event.target.value)}
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm text-text-primary outline-none"
        >
          {regions.map((item) => (
            <option key={item} value={item}>
              {item}
              {item === "All regions" ? "" : ` - ${regionNames.of(item) ?? item}`}
            </option>
          ))}
        </select>
      </div>

      <section className="mt-5 overflow-hidden rounded-xl border border-line bg-surface">
        <div className="hidden grid-cols-[52px_minmax(260px,1.4fr)_130px_125px_minmax(300px,1fr)] gap-4 border-b border-line bg-white/[.02] px-6 py-3 text-[10px] font-semibold uppercase tracking-[.14em] text-text-tertiary lg:grid">
          <span>Rank</span>
          <span>Topic</span>
          <span>Views</span>
          <span>Trend</span>
          <span>Related Shorts</span>
        </div>

        {data.items.map((topic, index) => (
          <Link
            key={topic.id}
            href={`/youtube/trends/topic/${topic.id}`}
            className="grid gap-4 border-b border-line px-5 py-5 transition hover:bg-white/[.025] last:border-0 lg:grid-cols-[52px_minmax(260px,1.4fr)_130px_125px_minmax(300px,1fr)] lg:items-center lg:px-6"
          >
            <div>
              <Rank value={index + 1} />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">{topic.label}</h2>
                <TopicBadge topic={topic} />
              </div>
              <p className="mt-1 text-xs text-text-secondary">
                {topic.member_count} verified Shorts - {topic.channel_count} creators - {topic.region_count} regions
              </p>
              <p className="mt-1 text-[11px] text-neon">
                {topic.freshness.buckets?.["0_24h"] ?? 0} new today - {topic.freshness.buckets?.["24_72h"] ?? 0} in 3 days - {topic.why_moving.new_member_count} new evidence
              </p>
              <p className="mt-1 line-clamp-1 text-[11px] text-text-tertiary">
                {topic.semantic_summary || "Evidence is being summarized."}
              </p>
            </div>
            <div>
              <p className="font-mono text-sm">{format.format(topic.observed_views)}</p>
              <p className="mt-1 text-[11px] text-text-tertiary">observed - 7-day window</p>
            </div>
            <div>
              <TrendLine snapshots={topic.snapshots} />
              <p className="font-mono text-xs text-neon">
                {topic.history_ready
                  ? `${format.format(topic.organic_velocity_per_hour)}/hr`
                  : "building history"}
                <span className="ml-1 text-text-tertiary">organic</span>
              </p>
            </div>
            <div className="flex gap-2 overflow-hidden">
              {topic.evidence.slice(0, 5).map((item) =>
                item.thumbnail_url ? (
                  <img
                    key={item.video_id}
                    src={item.thumbnail_url}
                    alt=""
                    className="h-16 w-12 shrink-0 rounded-md border border-line object-cover"
                  />
                ) : (
                  <span
                    key={item.video_id}
                    className="h-16 w-12 shrink-0 rounded-md border border-line bg-bg-secondary"
                  />
                ),
              )}
            </div>
          </Link>
        ))}

        {!data.items.length && (
          <div className="px-6 py-12 text-center">
            <p className="font-medium">Belum ada topik yang cukup kuat untuk ditampilkan.</p>
            <p className="mt-2 text-sm text-text-secondary">
              Sistem mengumpulkan bukti lintas channel dan akan memperbarui halaman ini otomatis.
            </p>
          </div>
        )}
      </section>

      <p className="mt-4 text-xs leading-5 text-text-tertiary">
        Lifecycle: emerging -&gt; accelerating -&gt; confirmed -&gt; cooling -&gt; archived.
        Views dan grafik adalah observasi sistem, bukan angka total YouTube. Video berusia lebih dari 7 hari tidak dipakai untuk ranking; metadata yang tidak sesuai isi dikarantina.
      </p>
    </div>
  );
}

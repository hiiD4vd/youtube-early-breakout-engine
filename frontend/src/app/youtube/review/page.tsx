"use client";

import { useState } from "react";
import useSWR from "swr";
import { API_BASE, fetcher } from "@/lib/api";

type Member = { video_id: string; title: string | null; channel_title: string | null; video_url: string; thumbnail_url: string | null; velocity_per_hour: number };
type Item = { id: string; label: string; status: string; member_count: number; channel_count: number; semantic_cohesion: number | null; review_uncertainty: number; review_reason: string; members: Member[] };
type Queue = { items: Item[]; decisions: string[]; methodology: string };
const decisionLabels: Record<string, string> = { CONFIRM_CLUSTER: "Confirm cluster", REJECT_CLUSTER: "Wrong merge", SPLIT_NEEDED: "Split needed", INSUFFICIENT_EVIDENCE: "Need more evidence" };

export default function TopicReviewPage() {
  const { data, error, mutate } = useSWR<Queue>("/api/v1/youtube/trends/review-queue", fetcher, { refreshInterval: 30_000 });
  const [saving, setSaving] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  async function submit(clusterId: string, decision: string) {
    setSaving(clusterId); setMessage("");
    try {
      const response = await fetch(`${API_BASE}/api/v1/youtube/trends/${clusterId}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) });
      if (!response.ok) throw new Error("Tidak dapat menyimpan review");
      setMessage("Review tersimpan. Ia akan dipakai untuk laporan kalibrasi, bukan langsung mengubah discovery.");
      await mutate();
    } catch (err) { setMessage(err instanceof Error ? err.message : "Tidak dapat menyimpan review"); } finally { setSaving(null); }
  }
  if (error) return <p className="text-red-400">Review queue belum dapat dimuat.</p>;
  if (!data) return <p className="text-text-secondary">Memuat cluster yang perlu ditinjau...</p>;
  return <div className="mx-auto max-w-6xl"><p className="text-sm font-semibold uppercase tracking-[.2em] text-neon">Active learning · human review</p><h1 className="mt-2 text-4xl font-bold">Review ambiguous clusters</h1><p className="mt-2 max-w-3xl text-text-secondary">Beri label hanya ketika bukti cukup. Sistem memprioritaskan cluster yang paling informatif untuk memperbaiki kualitas merge dan split.</p>{message && <p className="mt-5 rounded-lg border border-line bg-surface p-3 text-sm text-neon">{message}</p>}<div className="mt-7 space-y-4">{data.items.map((item) => <section key={item.id} className="rounded-xl border border-line bg-surface p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-semibold text-warning">UNCERTAINTY {Math.round(item.review_uncertainty * 100)}%</p><h2 className="mt-1 text-xl font-semibold">{item.label}</h2><p className="mt-1 text-sm text-text-secondary">{item.status} · {item.member_count} posts · {item.channel_count} channels · cohesion {item.semantic_cohesion?.toFixed(2) ?? "collecting"}</p></div><p className="max-w-sm text-right text-xs text-text-tertiary">{item.review_reason}</p></div><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{item.members.map((member) => <a key={member.video_id} href={member.video_url} target="_blank" rel="noreferrer" className="flex gap-3 rounded-lg border border-line p-2 hover:bg-white/[.03]">{member.thumbnail_url && <img src={member.thumbnail_url} alt="" className="h-14 w-10 rounded object-cover" />}<span className="min-w-0"><span className="block truncate text-sm font-medium">{member.title || member.video_id}</span><span className="mt-1 block truncate text-xs text-text-secondary">{member.channel_title || "Unknown"}</span><span className="mt-1 block font-mono text-xs text-neon">{Math.round(member.velocity_per_hour)}/hr</span></span></a>)}</div><div className="mt-5 flex flex-wrap gap-2">{data.decisions.map((decision) => <button key={decision} type="button" disabled={saving === item.id} onClick={() => submit(item.id, decision)} className="rounded-lg border border-line px-3 py-2 text-xs font-medium text-text-secondary transition-colors hover:border-line-strong hover:text-text-primary disabled:opacity-50">{saving === item.id ? "Saving..." : decisionLabels[decision]}</button>)}</div></section>)}</div>{!data.items.length && <section className="mt-6 rounded-xl border border-dashed border-line-strong p-6 text-sm text-text-secondary">Belum ada cluster ambigu yang belum direview. Queue akan terisi otomatis saat evidence baru masuk.</section>}<p className="mt-6 text-xs leading-5 text-text-tertiary">{data.methodology}</p></div>;
}

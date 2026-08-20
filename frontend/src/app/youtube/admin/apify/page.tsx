"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { fetcher, API_BASE } from "@/lib/api";
import { PageState } from "@/components/page-state";

export default function ApifyAdminPage() {
  const [loading, setLoading] = useState(true);
  const [actor, setActor] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    fetcher<any>("/admin/apify")
      .then((data) => {
        setActor(data.actor_id || "");
        setEnabled(Boolean(data.enabled));
      })
      .catch(() => {
        setMessage("Gagal memuat konfigurasi Apify.");
      })
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      const res = await fetch(`${API_BASE}/admin/apify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor_id: actor, enabled }),
      });
      if (!res.ok) throw new Error("save failed");
      const body = await res.json();
      setMessage("Disimpan.");
    } catch (err) {
      setMessage("Gagal menyimpan konfigurasi.");
    } finally {
      setSaving(false);
    }
  }

  async function triggerRun() {
    setSaving(true);
    setMessage(null);
    try {
      const res = await fetch(`${API_BASE}/admin/apify/trigger`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("trigger failed");
      const body = await res.json();
      setMessage(`Tugas enqueued: ${body.task_id}`);
    } catch (err) {
      setMessage("Gagal men-trigger koleksi Apify.");
    } finally {
      setSaving(false);
    }
  }

  const { data: costData } = useSWR<any>("/admin/apify/costs", fetcher, {
    refreshInterval: 60_000,
  });

  if (loading) {
    return (
      <PageState
        title="Memuat konfigurasi Apify"
        message="Sistem sedang membaca setting actor dan status lane Apify."
        tone="loading"
      />
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-semibold">Admin Apify</h1>
      <p className="text-sm text-text-secondary mt-1">
        Lihat atau ubah actor Apify yang digunakan untuk lane Apify.
      </p>
      <div className="mt-4 space-y-4">
        <label className="block">
          <div className="text-sm font-medium">Actor ID</div>
          <input
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            className="mt-1 w-full rounded-md border px-3 py-2"
          />
        </label>
        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          <span className="text-sm">Apify enabled</span>
        </label>
        <div>
          <button
            onClick={save}
            disabled={saving}
            className="rounded bg-warning px-4 py-2 text-sm text-bg-primary"
          >
            {saving ? "Menyimpan..." : "Simpan"}
          </button>
          <button
            onClick={triggerRun}
            disabled={saving}
            className="ml-3 rounded bg-accent px-4 py-2 text-sm text-bg-primary"
          >
            {saving ? "..." : "Trigger run"}
          </button>
        </div>
        {message && <p className="text-sm text-text-secondary">{message}</p>}
        {costData && (
          <div className="mt-6 rounded-lg border border-line bg-bg-primary/30 p-4">
            <p className="text-xs font-semibold uppercase tracking-[.12em] text-text-tertiary">
              Apify spend (summary)
            </p>
            {costData.available ? (
              <div className="mt-3 text-sm">
                <div className="font-mono">
                  Total (last {costData.days} days): ${costData.total_usd}
                </div>
                <div className="mt-2 overflow-x-auto">
                  <table className="min-w-full text-left text-xs">
                    <thead className="border-b border-line text-text-tertiary">
                      <tr>
                        <th className="px-2 py-2">Day</th>
                        <th className="px-2 py-2">Actor</th>
                        <th className="px-2 py-2">USD</th>
                      </tr>
                    </thead>
                    <tbody>
                      {costData.rows.map((r: any) => (
                        <tr
                          key={`${r.day}-${r.actor}`}
                          className="border-b border-line/60"
                        >
                          <td className="px-2 py-2">
                            {r.day?.split("T")?.[0]}
                          </td>
                          <td className="px-2 py-2 font-mono">
                            {r.actor_id || "(none)"}
                          </td>
                          <td className="px-2 py-2">${r.amount_usd}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <p className="text-sm text-text-secondary">
                Biaya tidak tersedia: {costData.error || costData.message}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

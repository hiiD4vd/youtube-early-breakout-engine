"use client";

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

function pageList(current: number, total: number): (number | "gap")[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const out: (number | "gap")[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) out.push("gap");
  for (let page = start; page <= end; page += 1) out.push(page);
  if (end < total - 1) out.push("gap");
  out.push(total);
  return out;
}

type Props = {
  page: number;
  pageSize: number;
  total: number;
  loading?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
};

export function Pagination({
  page,
  pageSize,
  total,
  loading = false,
  onPageChange,
  onPageSizeChange,
}: Props) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, page), totalPages);
  const start = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const end = Math.min(current * pageSize, total);

  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-text-secondary">
        <span>
          Menampilkan <span className="text-text-primary">{start}–{end}</span> dari{" "}
          <span className="text-text-primary">{total}</span>
        </span>
        {onPageSizeChange && (
          <label className="inline-flex items-center gap-1.5">
            <span className="text-text-tertiary">per halaman</span>
            <select
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
              className="rounded-md border border-line bg-surface px-2 py-1 text-xs text-text-primary outline-none focus:border-neon/50"
            >
              {PAGE_SIZE_OPTIONS.map((size) => (
                <option key={size} value={size}>{size}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onPageChange(current - 1)}
          disabled={current <= 1 || loading}
          aria-label="Halaman sebelumnya"
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-text-secondary transition hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          ←
        </button>

        {pageList(current, totalPages).map((item, index) =>
          item === "gap" ? (
            <span key={`gap-${index}`} className="px-1.5 text-xs text-text-tertiary">…</span>
          ) : (
            <button
              key={item}
              type="button"
              onClick={() => onPageChange(item)}
              disabled={loading}
              className={
                item === current
                  ? "min-w-[2.25rem] rounded-lg border border-warning bg-warning px-2.5 py-1.5 text-sm font-semibold text-bg-primary"
                  : "min-w-[2.25rem] rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm text-text-secondary transition hover:text-text-primary"
              }
            >
              {item}
            </button>
          ),
        )}

        <button
          type="button"
          onClick={() => onPageChange(current + 1)}
          disabled={current >= totalPages || loading}
          aria-label="Halaman berikutnya"
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-text-secondary transition hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          →
        </button>
      </div>
    </div>
  );
}

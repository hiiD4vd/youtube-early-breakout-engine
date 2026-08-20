"use client";

import Link from "next/link";
import type { ReactNode } from "react";

type PageStateProps = {
  title: string;
  message: string;
  note?: string;
  actionHref?: string;
  actionLabel?: string;
  tone?: "error" | "loading" | "info";
  children?: ReactNode;
};

const toneClasses: Record<NonNullable<PageStateProps["tone"]>, string> = {
  error: "border-red-500/30 bg-red-500/8 text-red-200",
  loading: "border-line bg-surface text-text-secondary",
  info: "border-line bg-surface text-text-secondary",
};

export function PageState({
  title,
  message,
  note,
  actionHref,
  actionLabel,
  tone = "info",
  children,
}: PageStateProps) {
  return (
    <div className="mx-auto max-w-4xl">
      <div className={`rounded-2xl border px-6 py-7 shadow-card ${toneClasses[tone]}`}>
        <p className="text-xs font-semibold uppercase tracking-[.18em] text-neon">
          {tone === "error" ? "Status" : tone === "loading" ? "Memuat" : "Info"}
        </p>
        <h1 className="mt-3 text-2xl font-bold text-text-primary">{title}</h1>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">{message}</p>
        {note && <p className="mt-3 text-xs leading-5 text-text-tertiary">{note}</p>}
        <div className="mt-5 flex flex-wrap gap-3">
          {actionHref && actionLabel && (
            <Link
              href={actionHref}
              className="inline-flex items-center rounded-full border border-neon/40 bg-neon-dim px-4 py-2 text-sm font-semibold text-neon transition hover:bg-neon hover:text-black"
            >
              {actionLabel}
            </Link>
          )}
        </div>
        {children && <div className="mt-5">{children}</div>}
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const groups = [{ label: "Intelligence", links: [{ href: "/youtube/trends", label: "Trending topics" }, { href: "/youtube", label: "Signal posts" }, { href: "/youtube/report", label: "24-hour report" }] }, { label: "System", links: [{ href: "/youtube/demo", label: "Preview layout" }] }];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return <div className="flex min-h-screen bg-bg-primary"><aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-bg-secondary md:flex"><div className="px-5 pt-6"><div className="flex items-center gap-2"><i className="h-2 w-2 rounded-full bg-neon"/><span className="text-sm font-semibold">Y-CGC Signal</span></div><p className="mt-1 text-[10px] text-text-tertiary">Find breakouts before they signal</p></div><nav className="mt-7 space-y-5 px-3">{groups.map((group) => <div key={group.label}><p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[.12em] text-text-tertiary">{group.label}</p>{group.links.map((link) => <Link key={link.href} href={link.href} className={`block rounded-lg px-3 py-2.5 text-sm font-medium transition-colors ${pathname === link.href ? "bg-neon-dim text-neon" : "text-text-secondary hover:bg-white/[.04] hover:text-text-primary"}`}>{link.label}</Link>)}</div>)}</nav><div className="mt-auto border-t border-line px-5 py-4 text-xs text-text-secondary"><i className="mr-2 inline-block h-2 w-2 rounded-full bg-neon"/>Pipeline online</div></aside><div className="min-w-0 flex-1"><header className="flex h-16 items-center justify-between border-b border-line bg-bg-secondary px-5 md:px-7"><div className="text-sm font-medium text-text-secondary">YouTube Intelligence</div><div className="flex items-center gap-4 text-xs"><span className="text-text-tertiary">Autonomous scan</span><span className="font-mono text-neon">LIVE</span></div></header><main className="p-5 md:p-7">{children}</main></div></div>;
}

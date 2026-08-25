"use client";

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`skeleton rounded-md ${className}`} />;
}

function ThumbSkeleton() {
  return <Skeleton className="h-[76px] w-[54px]" />;
}

export function TopicRowSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="grid gap-4 border-b border-line px-5 py-5 last:border-0 lg:grid-cols-[64px_minmax(260px,1.4fr)_150px_130px_minmax(290px,1fr)] lg:items-center lg:px-6"
    >
      <div>
        <Skeleton className="h-9 w-9" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
        <Skeleton className="h-3 w-2/3" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-3 w-16" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-8 w-16" />
        <Skeleton className="h-3 w-14" />
      </div>
      <div className="flex items-center justify-between gap-4">
        <div className="flex gap-2 overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <ThumbSkeleton key={i} />
          ))}
        </div>
        <Skeleton className="h-4 w-12" />
      </div>
    </div>
  );
}

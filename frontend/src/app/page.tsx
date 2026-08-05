export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-5xl items-center px-6">
      <section>
        <p className="text-sm font-medium uppercase tracking-[0.25em] text-cyan-400">Y-CGC V4</p>
        <h1 className="mt-3 text-4xl font-bold tracking-tight">YouTube Breakout Intelligence</h1>
        <p className="mt-4 max-w-2xl text-zinc-400">
          <a className="text-cyan-400 underline" href="/youtube">Open the YouTube breakout dashboard.</a>
        </p>
      </section>
    </main>
  );
}

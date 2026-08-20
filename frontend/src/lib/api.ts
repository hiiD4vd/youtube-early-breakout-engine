// Default to backend dev URL if NEXT_PUBLIC_API_BASE_URL is not set in runtime.
// This helps when the frontend dev server is run in a container without the
// public env var wired correctly into the client bundle.
const defaultBase = "http://localhost:8010";
const configuredBase = (
  process.env.NEXT_PUBLIC_API_BASE_URL || defaultBase
).replace(/\/$/, "");

// Keep one canonical API base for fetcher() and the few mutation calls that
// use API_BASE directly. Environment files may provide either the host only
// or a URL that already ends in /api/v1.
export const API_BASE = /\/api\/v1$/i.test(configuredBase)
  ? configuredBase
  : `${configuredBase}/api/v1`;

export async function fetcher<T>(path: string): Promise<T> {
  // Callers may pass either "/youtube/..." or "/api/v1/youtube/...".
  const normalizedPath = path.startsWith("/api/v1/")
    ? path.slice("/api/v1".length)
    : path;
  const url = `${API_BASE}${normalizedPath}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  let response: globalThis.Response;
  try {
    response = await fetch(url, {
      credentials: "include",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) throw new Error(`API ${path} failed: ${response.status}`);
  return response.json() as Promise<T>;
}

// Default to backend dev URL if NEXT_PUBLIC_API_BASE_URL is not set in runtime.
// This helps when the frontend dev server is run in a container without the
// public env var wired correctly into the client bundle.
const defaultBase = "http://localhost:8010/api/v1";
export const API_BASE = (
  process.env.NEXT_PUBLIC_API_BASE_URL || defaultBase
).replace(/\/$/, "");

export async function fetcher<T>(path: string): Promise<T> {
  // Path values in the app are written like "/admin/apify" so joining
  // API_BASE + path yields e.g. http://localhost:8010/api/v1/admin/apify
  const url = `${API_BASE}${path}`;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
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

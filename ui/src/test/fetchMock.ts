import { vi } from "vitest";

type Route = { status?: number; body?: unknown; error?: Error };

/** Stub global fetch with a fixed response per URL path. Unknown paths return 404. */
export function mockFetch(routes: Record<string, Route>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const route = routes[url];
    if (!route) {
      return new Response("not found", { status: 404 });
    }
    if (route.error) {
      throw route.error;
    }
    return new Response(JSON.stringify(route.body ?? null), {
      status: route.status ?? 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

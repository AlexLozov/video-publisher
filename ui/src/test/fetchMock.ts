import { vi } from "vitest";

export type Route =
  | { status?: number; body?: unknown; error?: Error }
  | ((req: { method: string; url: string; body: unknown }) => {
      status?: number;
      body?: unknown;
    });

/**
 * Stub global fetch. Keys are either "METHOD /path" or just "/path" (any method).
 * Unknown routes return 404. Returns the mock so tests can inspect calls.
 */
export function mockFetch(routes: Record<string, Route>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const route = routes[`${method} ${url}`] ?? routes[url];
    if (!route) {
      return new Response(JSON.stringify({ code: "not_found", params: {} }), { status: 404 });
    }
    const body = typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined;
    const result = typeof route === "function" ? route({ method, url, body }) : route;
    if ("error" in result && result.error) {
      throw result.error;
    }
    const status = result.status ?? 200;
    if (status === 204) {
      return new Response(null, { status });
    }
    return new Response(JSON.stringify(result.body ?? null), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Requests the mock received, as {method, url, body}. */
export function calls(fn: ReturnType<typeof mockFetch>) {
  return fn.mock.calls.map(([input, init]) => ({
    method: (init?.method ?? "GET").toUpperCase(),
    url: typeof input === "string" ? input : input.toString(),
    body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
  }));
}

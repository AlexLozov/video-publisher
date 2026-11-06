import { describe, expect, it, vi } from "vitest";

import { ApiError, fetchHealth, fetchPlatforms } from "./api";
import { mockFetch } from "./test/fetchMock";

describe("api client", () => {
  it("returns parsed JSON on success", async () => {
    mockFetch({ "/api/health": { body: { status: "ok", version: "0.1.0" } } });
    await expect(fetchHealth()).resolves.toEqual({ status: "ok", version: "0.1.0" });
  });

  it("throws ApiError with the backend error code", async () => {
    mockFetch({ "/api/platforms": { status: 503, body: { code: "internal_error", params: {} } } });
    const err = await fetchPlatforms().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ path: "/api/platforms", status: 503, code: "internal_error" });
  });

  it("uses 'unknown' code when the error body has no code", async () => {
    mockFetch({ "/api/health": { status: 500, body: { detail: "x" } } });
    await expect(fetchHealth()).rejects.toMatchObject({ code: "unknown", status: 500 });
  });

  it("uses 'unknown' code when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502 })),
    );
    await expect(fetchHealth()).rejects.toMatchObject({ code: "unknown", status: 502 });
  });

  it("propagates network errors", async () => {
    mockFetch({ "/api/health": { error: new TypeError("Failed to fetch") } });
    await expect(fetchHealth()).rejects.toThrow("Failed to fetch");
  });
});

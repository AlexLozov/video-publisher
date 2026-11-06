import { describe, expect, it } from "vitest";

import { fetchHealth, fetchPlatforms } from "./api";
import { mockFetch } from "./test/fetchMock";

describe("api client", () => {
  it("returns parsed JSON on success", async () => {
    mockFetch({ "/api/health": { body: { status: "ok", version: "0.1.0" } } });
    await expect(fetchHealth()).resolves.toEqual({ status: "ok", version: "0.1.0" });
  });

  it("throws with path and status on HTTP error", async () => {
    mockFetch({ "/api/platforms": { status: 503, body: {} } });
    await expect(fetchPlatforms()).rejects.toThrow("/api/platforms: HTTP 503");
  });

  it("propagates network errors", async () => {
    mockFetch({ "/api/health": { error: new TypeError("Failed to fetch") } });
    await expect(fetchHealth()).rejects.toThrow("Failed to fetch");
  });
});

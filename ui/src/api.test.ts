import { describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteSecret,
  fetchHealth,
  fetchPlatforms,
  fetchSettings,
  patchSettings,
  putSecret,
} from "./api";
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

  it("keeps params from the error body and ignores a malformed params value", async () => {
    mockFetch({
      "/api/settings": { status: 422, body: { code: "validation_error", params: "nope" } },
    });
    const err = await fetchSettings().catch((e: unknown) => e);
    expect(err).toMatchObject({ code: "validation_error", params: {} });
  });

  it("returns undefined for 204 No Content", async () => {
    mockFetch({ "DELETE /api/secrets/telegram_bot_token": { status: 204 } });
    await expect(deleteSecret("telegram_bot_token")).resolves.toBeUndefined();
  });

  it("sends JSON bodies with the right method", async () => {
    const fetch = mockFetch({
      "PATCH /api/settings": { body: { ok: true } },
      "PUT /api/secrets/telegram_api_hash": { status: 204 },
    });
    await patchSettings({ language: "en" });
    await putSecret("telegram_api_hash", "abc");
    const [, patchInit] = fetch.mock.calls[0];
    expect(patchInit?.headers).toEqual({ "Content-Type": "application/json" });
    expect(fetch.mock.calls.map(([url, init]) => [init?.method, url, init?.body])).toEqual([
      ["PATCH", "/api/settings", '{"language":"en"}'],
      ["PUT", "/api/secrets/telegram_api_hash", '{"value":"abc"}'],
    ]);
  });

  it("propagates network errors", async () => {
    mockFetch({ "/api/health": { error: new TypeError("Failed to fetch") } });
    await expect(fetchHealth()).rejects.toThrow("Failed to fetch");
  });
});

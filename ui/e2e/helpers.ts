import { expect, type APIRequestContext } from "@playwright/test";

/** Put the backend back to default settings with no secrets. */
export async function resetBackend(request: APIRequestContext) {
  const resp = await request.patch("/api/settings", {
    data: { language: "ru", telegram: { mode: "bot", channel: null, api_id: null } },
  });
  expect(resp.ok()).toBe(true);
  for (const name of ["telegram_bot_token", "telegram_api_hash", "telegram_session"]) {
    expect((await request.delete(`/api/secrets/${name}`)).status()).toBe(204);
  }
}

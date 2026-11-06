import { mkdtempSync, openSync, closeSync, ftruncateSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, type APIRequestContext } from "@playwright/test";

const PORT = Number(process.env.VP_E2E_PORT ?? 8790);
export const FAKE_TELEGRAM_URL = `http://127.0.0.1:${PORT + 1}`;

export const VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ";

/** Put the backend back to default settings with no secrets, and clear the fake Telegram. */
export async function resetBackend(request: APIRequestContext) {
  const resp = await request.patch("/api/settings", {
    data: { language: "ru", telegram: { mode: "bot", channel: null, api_id: null } },
  });
  expect(resp.ok()).toBe(true);
  for (const name of ["telegram_bot_token", "telegram_api_hash", "telegram_session"]) {
    expect((await request.delete(`/api/secrets/${name}`)).status()).toBe(204);
  }
  expect((await request.post(`${FAKE_TELEGRAM_URL}/reset`)).ok()).toBe(true);
}

export async function configureBot(request: APIRequestContext, channel = "@my_test_channel") {
  expect(
    (await request.patch("/api/settings", { data: { telegram: { mode: "bot", channel } } })).ok(),
  ).toBe(true);
  expect(
    (await request.put("/api/secrets/telegram_bot_token", { data: { value: VALID_TOKEN } }))
      .status(),
  ).toBe(204);
}

const tmpDir = mkdtempSync(path.join(os.tmpdir(), "vp-e2e-videos-"));

/** Create a test video file of the given size (sparse, so large sizes cost no disk). */
export function makeVideo(name: string, sizeBytes = 1024): string {
  const file = path.join(tmpDir, name);
  if (sizeBytes <= 1024 * 1024) {
    writeFileSync(file, Buffer.alloc(sizeBytes));
  } else {
    const fd = openSync(file, "w");
    ftruncateSync(fd, sizeBytes);
    closeSync(fd);
  }
  return file;
}

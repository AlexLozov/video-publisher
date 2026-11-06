import type { Settings } from "../api";

export const VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ";

export function makeSettings(overrides: Partial<Settings> = {}): Settings {
  return {
    language: "ru",
    telegram: { mode: "bot", channel: null, api_id: null },
    secrets: { telegram_bot_token: false, telegram_api_hash: false, telegram_session: false },
    ...overrides,
  };
}

export const HEALTH_OK = { body: { status: "ok", version: "1.2.3" } };
export const PLATFORMS = {
  body: [
    { id: "youtube", available: false },
    { id: "facebook", available: false },
    { id: "telegram", available: true },
  ],
};

import type { SecretName, Settings } from "../api";

export const VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ";

export const NO_SECRETS: Record<SecretName, boolean> = {
  telegram_bot_token: false,
  telegram_api_hash: false,
  telegram_session: false,
  facebook_app_secret: false,
  facebook_page_token: false,
  youtube_client_secret: false,
  youtube_refresh_token: false,
};

type SettingsOverrides = Partial<Omit<Settings, "secrets">> & {
  secrets?: Partial<Record<SecretName, boolean>>;
};

/** Settings for tests; `secrets` only needs the ones that differ from "not set". */
export function makeSettings(overrides: SettingsOverrides = {}): Settings {
  const { secrets, ...rest } = overrides;
  return {
    language: "ru",
    telegram: { mode: "bot", channel: null, api_id: null },
    facebook: { app_id: null, page_id: null, page_name: null },
    youtube: {
      client_id: null,
      channel_id: null,
      channel_title: null,
      privacy: "public",
      made_for_kids: false,
      category_id: "22",
      tags: [],
    },
    ...rest,
    secrets: { ...NO_SECRETS, ...secrets },
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

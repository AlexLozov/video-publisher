import type { Settings } from "./api";

/** All networks in display order (working ones first). Names are translated via `platform.<id>`. */
export const ALL_PLATFORMS = ["telegram", "youtube", "instagram", "facebook", "tiktok"] as const;
export type PlatformId = (typeof ALL_PLATFORMS)[number];

/** Networks the app can already publish to. */
export const SUPPORTED_PLATFORMS: readonly PlatformId[] = ["telegram", "youtube", "facebook"];

/** Whether everything needed to publish to `platform` is filled in. */
export function isConfigured(settings: Settings | null, platform: PlatformId): boolean {
  if (!settings) return false;
  if (platform === "facebook") {
    return settings.facebook.page_id !== null && settings.secrets.facebook_page_token;
  }
  if (platform === "youtube") {
    const yt = settings.youtube;
    return (
      yt.client_id !== null &&
      yt.channel_id !== null &&
      settings.secrets.youtube_client_secret &&
      settings.secrets.youtube_refresh_token
    );
  }
  if (platform !== "telegram") return false;
  const tg = settings.telegram;
  const s = settings.secrets;
  if (!tg.channel) return false;
  return tg.mode === "account"
    ? tg.api_id !== null && s.telegram_api_hash && s.telegram_session
    : s.telegram_bot_token;
}

/** Whether `platform` (as set up now) can hold a scheduled post itself. */
export function canScheduleOn(settings: Settings | null, platform: PlatformId): boolean {
  if (!settings) return false;
  if (platform === "facebook" || platform === "youtube") return true;
  return platform === "telegram" && settings.telegram.mode === "account";
}

import type { Settings } from "./api";

/** All networks in display order (working ones first). Names are translated via `platform.<id>`. */
export const ALL_PLATFORMS = ["telegram", "youtube", "instagram", "facebook", "tiktok"] as const;
export type PlatformId = (typeof ALL_PLATFORMS)[number];

/** Networks the app can already publish to. */
export const SUPPORTED_PLATFORMS: readonly PlatformId[] = ["telegram"];

/** Whether everything needed to publish to `platform` is filled in. */
export function isConfigured(settings: Settings | null, platform: PlatformId): boolean {
  if (!settings) return false;
  if (platform !== "telegram") return false;
  const tg = settings.telegram;
  const s = settings.secrets;
  if (!tg.channel) return false;
  return tg.mode === "account"
    ? tg.api_id !== null && s.telegram_api_hash && s.telegram_session
    : s.telegram_bot_token;
}

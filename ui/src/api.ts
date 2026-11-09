import type { Language } from "./i18n";

export interface Health {
  status: string;
  version: string;
}

/** Platform names are translated on the client via the `platform.<id>` key. */
export interface PlatformInfo {
  id: string;
  available: boolean;
}

export type TelegramMode = "bot" | "account";

export interface TelegramSettings {
  mode: TelegramMode;
  channel: string | null;
  api_id: number | null;
}

export interface FacebookSettings {
  /** The user's own Meta app (the App Secret is a secret). */
  app_id: string | null;
  /** Filled in by logging in with Facebook. */
  page_id: string | null;
  page_name: string | null;
}

export type YouTubePrivacy = "public" | "unlisted" | "private";

export interface YouTubeSettings {
  /** The user's own Google Cloud OAuth client (the client secret is a secret). */
  client_id: string | null;
  /** Filled in by logging in with Google. */
  channel_id: string | null;
  channel_title: string | null;
  /** Defaults for every upload. */
  privacy: YouTubePrivacy;
  made_for_kids: boolean;
  category_id: string;
  tags: string[];
}

export type SecretName =
  | "telegram_bot_token"
  | "telegram_api_hash"
  | "telegram_session"
  | "facebook_app_secret"
  | "facebook_page_token"
  | "youtube_client_secret"
  | "youtube_refresh_token";
/** Sessions and tokens come from login flows, never typed in. */
export type WritableSecret = Exclude<
  SecretName,
  "telegram_session" | "facebook_page_token" | "youtube_refresh_token"
>;

export interface Settings {
  language: Language;
  telegram: TelegramSettings;
  facebook: FacebookSettings;
  youtube: YouTubeSettings;
  /** Only whether each secret is set; values never leave the backend. */
  secrets: Record<SecretName, boolean>;
}

export interface SettingsPatch {
  language?: Language;
  telegram?: Partial<TelegramSettings>;
  facebook?: { app_id?: string | null };
  youtube?: Partial<Omit<YouTubeSettings, "channel_id" | "channel_title">>;
}

/**
 * Backend errors carry a machine-readable code (translated via `errors.<code>`)
 * instead of user-facing text. Validation errors list field codes in `params.fields`.
 */
export class ApiError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
    readonly code: string,
    readonly params: Record<string, unknown> = {},
  ) {
    super(`${path}: HTTP ${status} (${code})`);
    this.name = "ApiError";
  }

  /** Field path (e.g. `telegram.channel`) → error code, without the `body.` prefix. */
  get fields(): Record<string, string> {
    const raw = this.params.fields;
    if (!raw || typeof raw !== "object") return {};
    return Object.fromEntries(
      Object.entries(raw as Record<string, string>).map(([k, v]) => [k.replace(/^body\./, ""), v]),
    );
  }
}

async function readError(resp: Response): Promise<{ code: string; params: Record<string, unknown> }> {
  try {
    const body: unknown = await resp.json();
    if (body && typeof body === "object" && "code" in body && typeof body.code === "string") {
      const params =
        "params" in body && body.params && typeof body.params === "object"
          ? (body.params as Record<string, unknown>)
          : {};
      return { code: body.code, params };
    }
  } catch {
    // not JSON
  }
  return { code: "unknown", params: {} };
}

async function request<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const resp = await fetch(path, {
    method: init?.method ?? "GET",
    ...(init?.body !== undefined
      ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(init.body) }
      : {}),
  });
  if (!resp.ok) {
    const { code, params } = await readError(resp);
    throw new ApiError(path, resp.status, code, params);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export type PublicationStatus = "draft" | "scheduled" | "publishing" | "published" | "failed";

export interface Publication {
  id: string;
  /** Shared by the networks of one upload. */
  batch_id: string;
  platform: string;
  method: string;
  target: string | null;
  video_path: string;
  title: string;
  status: PublicationStatus;
  /** ISO time (UTC) when the platform publishes a scheduled post. */
  scheduled_at: string | null;
  url: string | null;
  error_code: string | null;
  error_params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PublishRequest {
  video_path: string;
  platform: string;
  title: string;
  description: string;
  hashtags: string[];
  /** ISO time with offset; omitted to publish now. */
  scheduled_at?: string;
}

export const startPublication = (req: PublishRequest) =>
  request<Publication>("/api/publications", { method: "POST", body: req });
export interface BatchPublishRequest extends Omit<PublishRequest, "platform"> {
  platforms: string[];
}

/** One video to several networks; nothing starts unless every network accepts it. */
export const startBatch = (req: BatchPublishRequest) =>
  request<Publication[]>("/api/publications/batch", { method: "POST", body: req });
export const fetchPublications = (limit = 20) =>
  request<Publication[]>(`/api/publications?limit=${limit}`);
/** Removes one record; a scheduled post is withdrawn from the network first. */
export const deletePublication = (id: string) =>
  request<void>(`/api/publications/${id}`, { method: "DELETE" });
/** Removes published and failed records; scheduled and running ones stay. */
export const clearHistory = () =>
  request<{ deleted: number }>("/api/publications", { method: "DELETE" });

export const fetchHealth = () => request<Health>("/api/health");
export const fetchPlatforms = () => request<PlatformInfo[]>("/api/platforms");
export const fetchSettings = () => request<Settings>("/api/settings");
export const patchSettings = (patch: SettingsPatch) =>
  request<Settings>("/api/settings", { method: "PATCH", body: patch });
export const putSecret = (name: WritableSecret, value: string) =>
  request<void>(`/api/secrets/${name}`, { method: "PUT", body: { value } });
export const deleteSecret = (name: SecretName) =>
  request<void>(`/api/secrets/${name}`, { method: "DELETE" });

/** Sign-in to the user's own Telegram account (needed for "my account" publishing). */
export type TelegramLoginState = "logged_out" | "code_sent" | "password_needed" | "logged_in";

export interface TelegramLoginStatus {
  state: TelegramLoginState;
  /** Masked phone while sign-in is in progress, e.g. "+7••••••4567". */
  phone: string | null;
}

const LOGIN = "/api/telegram/account";
export const fetchTelegramLogin = () => request<TelegramLoginStatus>(LOGIN);
export const sendTelegramCode = (phone: string) =>
  request<TelegramLoginStatus>(`${LOGIN}/send-code`, { method: "POST", body: { phone } });
export const signInTelegram = (code: string) =>
  request<TelegramLoginStatus>(`${LOGIN}/sign-in`, { method: "POST", body: { code } });
export const sendTelegramPassword = (password: string) =>
  request<TelegramLoginStatus>(`${LOGIN}/password`, { method: "POST", body: { password } });
export const cancelTelegramLogin = () =>
  request<TelegramLoginStatus>(`${LOGIN}/cancel`, { method: "POST" });
export const logoutTelegram = () =>
  request<TelegramLoginStatus>(`${LOGIN}/logout`, { method: "POST" });

/** Logging in with Facebook: the browser opens, the user picks a page. */
export type FacebookLoginState = "logged_out" | "waiting" | "choose_page" | "logged_in";

export interface FacebookPage {
  id: string;
  name: string;
}

export interface FacebookLoginStatus {
  state: FacebookLoginState;
  page: FacebookPage | null;
  /** Pages to choose from (only in `choose_page`). */
  pages: FacebookPage[];
  /** Goes into "Valid OAuth Redirect URIs" of the Meta app. */
  redirect_uri: string;
  /** Why the last attempt failed (an `errors.<code>` key). */
  error: string | null;
}

const FB_LOGIN = "/api/facebook/account";
export const fetchFacebookLogin = () => request<FacebookLoginStatus>(FB_LOGIN);
export const startFacebookLogin = () =>
  request<FacebookLoginStatus>(`${FB_LOGIN}/login`, { method: "POST" });
export const selectFacebookPage = (page_id: string) =>
  request<FacebookLoginStatus>(`${FB_LOGIN}/select-page`, { method: "POST", body: { page_id } });
export const cancelFacebookLogin = () =>
  request<FacebookLoginStatus>(`${FB_LOGIN}/cancel`, { method: "POST" });
export const logoutFacebook = () =>
  request<FacebookLoginStatus>(`${FB_LOGIN}/logout`, { method: "POST" });

/** Logging in with Google for YouTube: the browser opens, Google sends the user back. */
export type YouTubeLoginState = "logged_out" | "waiting" | "logged_in";

export interface YouTubeLoginStatus {
  state: YouTubeLoginState;
  channel: { id: string; title: string } | null;
  /** Why the last attempt failed (an `errors.<code>` key). */
  error: string | null;
}

const YT_LOGIN = "/api/youtube/account";
export const fetchYouTubeLogin = () => request<YouTubeLoginStatus>(YT_LOGIN);
export const startYouTubeLogin = () =>
  request<YouTubeLoginStatus>(`${YT_LOGIN}/login`, { method: "POST" });
export const cancelYouTubeLogin = () =>
  request<YouTubeLoginStatus>(`${YT_LOGIN}/cancel`, { method: "POST" });
export const logoutYouTube = () =>
  request<YouTubeLoginStatus>(`${YT_LOGIN}/logout`, { method: "POST" });

/** Duration and frame size of a local file (read on this computer, nothing is uploaded). */
export interface VideoInfo {
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  /** Vertical or square and up to 3 minutes; null when unknown. */
  youtube_short: boolean | null;
}

export const fetchVideoInfo = (path: string) =>
  request<VideoInfo>(`/api/publications/video-info?path=${encodeURIComponent(path)}`);

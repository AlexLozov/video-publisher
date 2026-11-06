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

export type SecretName = "telegram_bot_token" | "telegram_api_hash" | "telegram_session";
export type WritableSecret = Exclude<SecretName, "telegram_session">;

export interface Settings {
  language: Language;
  telegram: TelegramSettings;
  /** Only whether each secret is set; values never leave the backend. */
  secrets: Record<SecretName, boolean>;
}

export interface SettingsPatch {
  language?: Language;
  telegram?: Partial<TelegramSettings>;
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

export const fetchHealth = () => request<Health>("/api/health");
export const fetchPlatforms = () => request<PlatformInfo[]>("/api/platforms");
export const fetchSettings = () => request<Settings>("/api/settings");
export const patchSettings = (patch: SettingsPatch) =>
  request<Settings>("/api/settings", { method: "PATCH", body: patch });
export const putSecret = (name: WritableSecret, value: string) =>
  request<void>(`/api/secrets/${name}`, { method: "PUT", body: { value } });
export const deleteSecret = (name: SecretName) =>
  request<void>(`/api/secrets/${name}`, { method: "DELETE" });

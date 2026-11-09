import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  cancelYouTubeLogin,
  fetchYouTubeLogin,
  verifyYouTubeLogin,
  logoutYouTube,
  patchSettings,
  startYouTubeLogin,
  type SecretName,
  type Settings,
  type YouTubeLoginStatus,
  type YouTubePrivacy,
} from "../api";
import { errorKey } from "../errors";
import { SecretField } from "./SecretField";

interface Props {
  settings: Settings;
  onSaved: (s: Settings) => void;
  onSecret: (name: SecretName, isSet: boolean) => void;
  /** How often to ask the backend whether the browser login finished. */
  pollMs?: number;
}

type ErrorState = { key: string; params: Record<string, unknown> } | null;

/** YouTube category ids offered in Settings; names are translated via `youtubeCategory.<id>`. */
export const YOUTUBE_CATEGORIES = [
  "1",
  "2",
  "10",
  "15",
  "17",
  "19",
  "20",
  "22",
  "23",
  "24",
  "25",
  "26",
  "27",
  "28",
  "29",
] as const;
const PRIVACY: YouTubePrivacy[] = ["public", "unlisted", "private"];

const input =
  "w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400";
const select = "w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm";
const primary = "rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40";
const secondary = "rounded-lg border border-slate-200 px-3 py-1.5 text-sm disabled:opacity-40";

/** "cats, funny cats" → ["cats", "funny cats"] */
export function parseTags(text: string): string[] {
  return text
    .split(",")
    .map((t) => t.trim().replace(/^#+/, "").trim())
    .filter((t, i, all) => t !== "" && all.indexOf(t) === i);
}

/** Settings → YouTube: the user's Google OAuth client, "Log in with Google", upload defaults. */
export function YouTubeSection({ settings, onSaved, onSecret, pollMs = 1500 }: Props) {
  const { t } = useTranslation();
  const ids = {
    clientId: useId(),
    privacy: useId(),
    category: useId(),
    tags: useId(),
  };
  const yt = settings.youtube;
  const [clientId, setClientId] = useState(yt.client_id ?? "");
  const [savingId, setSavingId] = useState(false);
  const [idSaved, setIdSaved] = useState(false);
  const [idError, setIdError] = useState<string | null>(null);

  const [privacy, setPrivacy] = useState<YouTubePrivacy>(yt.privacy);
  const [kids, setKids] = useState(yt.made_for_kids);
  const [category, setCategory] = useState(yt.category_id);
  const [tags, setTags] = useState(yt.tags.join(", "));
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [defaultsSaved, setDefaultsSaved] = useState(false);
  const [defaultsError, setDefaultsError] = useState<string | null>(null);

  const [status, setStatus] = useState<YouTubeLoginStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorState>(null);
  const loggedInRef = useRef(settings.secrets.youtube_refresh_token && yt.channel_id !== null);

  useEffect(() => setClientId(yt.client_id ?? ""), [yt.client_id]);

  /** Keep the parent's settings in step with the login (status badge, publish screen). */
  function apply(next: YouTubeLoginStatus) {
    setStatus(next);
    const loggedIn = next.state === "logged_in";
    if (loggedIn !== loggedInRef.current) {
      loggedInRef.current = loggedIn;
      onSaved({
        ...settings,
        youtube: {
          ...settings.youtube,
          channel_id: next.channel?.id ?? null,
          channel_title: next.channel?.title ?? null,
        },
        secrets: { ...settings.secrets, youtube_refresh_token: loggedIn },
      });
    }
  }

  useEffect(() => {
    let cancelled = false;
    // Opening the section checks the stored login with Google (cached on the backend).
    verifyYouTubeLogin()
      .then((s) => {
        if (!cancelled) apply(s);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError({ key: errorKey(err), params: {} });
      });
    return () => {
      cancelled = true;
    };
    // Load once; later changes come from the actions and polling below.
  }, []);

  // While the browser is open, ask the backend until the login finishes.
  const waiting = status?.state === "waiting";
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => {
      fetchYouTubeLogin()
        .then(apply)
        .catch(() => {
          // keep waiting; the next tick tries again
        });
    }, pollMs);
    return () => clearInterval(timer);
  }, [waiting, pollMs]);

  async function run(action: () => Promise<YouTubeLoginStatus>) {
    setBusy(true);
    setError(null);
    try {
      apply(await action());
    } catch (err) {
      setError({ key: errorKey(err), params: err instanceof ApiError ? err.params : {} });
    } finally {
      setBusy(false);
    }
  }

  function fieldMessage(err: unknown, field: string): string {
    const code = err instanceof ApiError ? err.fields[field] : undefined;
    return code
      ? t(`fieldErrors.${code}`, { defaultValue: t("fieldErrors.default") })
      : t(errorKey(err), { defaultValue: t("errors.unknown") });
  }

  async function saveClientId() {
    setSavingId(true);
    setIdSaved(false);
    setIdError(null);
    try {
      const value = clientId.trim();
      onSaved(await patchSettings({ youtube: { client_id: value === "" ? null : value } }));
      setIdSaved(true);
    } catch (err) {
      setIdError(fieldMessage(err, "youtube.client_id"));
    } finally {
      setSavingId(false);
    }
  }

  async function saveDefaults() {
    setSavingDefaults(true);
    setDefaultsSaved(false);
    setDefaultsError(null);
    try {
      const updated = await patchSettings({
        youtube: {
          privacy,
          made_for_kids: kids,
          category_id: category,
          tags: parseTags(tags),
        },
      });
      onSaved(updated);
      setTags(updated.youtube.tags.join(", "));
      setDefaultsSaved(true);
    } catch (err) {
      setDefaultsError(fieldMessage(err, "youtube.tags"));
    } finally {
      setSavingDefaults(false);
    }
  }

  const keysReady = yt.client_id !== null && settings.secrets.youtube_client_secret;
  const lastError = status?.error ? { key: `errors.${status.error}`, params: {} } : null;
  const shownError = error ?? lastError;

  return (
    <>
      <p className="mb-4 text-xs text-slate-500">{t("settings.youtube.intro")}</p>

      <div className="space-y-1.5">
        <label htmlFor={ids.clientId} className="text-sm font-medium">
          {t("settings.youtube.clientId")}
        </label>
        <div className="flex gap-2">
          <input
            id={ids.clientId}
            value={clientId}
            placeholder="123456789012-abc….apps.googleusercontent.com"
            onChange={(e) => setClientId(e.target.value)}
            aria-invalid={idError !== null}
            className={input}
          />
          <button
            type="button"
            disabled={savingId}
            onClick={() => void saveClientId()}
            className={primary}
          >
            {savingId ? t("common.saving") : t("common.save")}
          </button>
        </div>
        {idSaved && <p className="text-xs text-emerald-700">{t("common.saved")}</p>}
        {idError && (
          <p role="alert" className="text-xs text-rose-600">
            {idError}
          </p>
        )}
      </div>

      <div className="mt-4">
        <SecretField
          name="youtube_client_secret"
          label={t("settings.youtube.clientSecret")}
          hint={t("settings.youtube.clientSecretHint")}
          isSet={settings.secrets.youtube_client_secret}
          onChange={(isSet) => onSecret("youtube_client_secret", isSet)}
        />
      </div>

      <div className="mt-6 space-y-3 border-t border-slate-100 pt-6">
        <h4 className="text-sm font-medium">{t("settings.youtube.login.title")}</h4>

        {status === null && <p className="text-sm text-slate-400">{t("common.loading")}</p>}

        {status?.state === "logged_out" &&
          (keysReady ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(startYouTubeLogin)}
              className={primary}
            >
              {t("settings.youtube.login.start")}
            </button>
          ) : (
            <p className="text-sm text-slate-500">{t("settings.youtube.login.needKeys")}</p>
          ))}

        {status?.state === "waiting" && (
          <div className="space-y-2">
            <p className="text-sm text-slate-600">{t("settings.youtube.login.waiting")}</p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(cancelYouTubeLogin)}
              className={secondary}
            >
              {t("settings.youtube.login.cancel")}
            </button>
          </div>
        )}

        {status?.state === "logged_in" && (
          <div className="space-y-2">
            <p className="text-sm text-emerald-700">
              {t("settings.youtube.login.loggedIn", { channel: status.channel?.title ?? "" })}
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(logoutYouTube)}
              className={secondary}
            >
              {t("settings.youtube.login.logout")}
            </button>
            <p className="text-xs text-slate-400">{t("settings.youtube.login.stored")}</p>
          </div>
        )}

        {shownError && (
          <p role="alert" className="text-sm text-rose-600">
            {t(shownError.key, { ...shownError.params, defaultValue: t("errors.unknown") })}
          </p>
        )}
        <p className="text-xs text-amber-700">{t("settings.youtube.auditHint")}</p>
      </div>

      <div className="mt-6 space-y-4 border-t border-slate-100 pt-6">
        <h4 className="text-sm font-medium">{t("settings.youtube.defaults.title")}</h4>

        <div className="space-y-1.5">
          <label htmlFor={ids.privacy} className="text-sm">
            {t("settings.youtube.defaults.privacy")}
          </label>
          <select
            id={ids.privacy}
            value={privacy}
            onChange={(e) => setPrivacy(e.target.value as YouTubePrivacy)}
            className={select}
          >
            {PRIVACY.map((p) => (
              <option key={p} value={p}>
                {t(`settings.youtube.privacy.${p}`)}
              </option>
            ))}
          </select>
          <p className="text-xs text-slate-400">{t("settings.youtube.defaults.privacyHint")}</p>
        </div>

        <fieldset className="space-y-1">
          <legend className="text-sm">{t("settings.youtube.defaults.kids")}</legend>
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="youtube-kids"
                checked={!kids}
                onChange={() => setKids(false)}
              />
              {t("settings.youtube.defaults.kidsNo")}
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="youtube-kids"
                checked={kids}
                onChange={() => setKids(true)}
              />
              {t("settings.youtube.defaults.kidsYes")}
            </label>
          </div>
        </fieldset>

        <div className="space-y-1.5">
          <label htmlFor={ids.category} className="text-sm">
            {t("settings.youtube.defaults.category")}
          </label>
          <select
            id={ids.category}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className={select}
          >
            {YOUTUBE_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {t(`youtubeCategory.${c}`)}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1.5">
          <label htmlFor={ids.tags} className="text-sm">
            {t("settings.youtube.defaults.tags")}
          </label>
          <input
            id={ids.tags}
            value={tags}
            placeholder={t("settings.youtube.defaults.tagsPlaceholder")}
            onChange={(e) => setTags(e.target.value)}
            aria-invalid={defaultsError !== null}
            className={input}
          />
          <p className="text-xs text-slate-400">{t("settings.youtube.defaults.tagsHint")}</p>
        </div>

        <button
          type="button"
          disabled={savingDefaults}
          onClick={() => void saveDefaults()}
          className={primary}
        >
          {savingDefaults ? t("common.saving") : t("common.save")}
        </button>
        {defaultsSaved && <p className="text-xs text-emerald-700">{t("common.saved")}</p>}
        {defaultsError && (
          <p role="alert" className="text-xs text-rose-600">
            {defaultsError}
          </p>
        )}
      </div>
    </>
  );
}

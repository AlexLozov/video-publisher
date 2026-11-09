import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  cancelFacebookLogin,
  fetchFacebookLogin,
  logoutFacebook,
  patchSettings,
  selectFacebookPage,
  startFacebookLogin,
  type FacebookLoginStatus,
  type SecretName,
  type Settings,
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

const input =
  "w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400";
const primary = "rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40";
const secondary = "rounded-lg border border-slate-200 px-3 py-1.5 text-sm disabled:opacity-40";

/** Settings → Facebook Page: the user's Meta app keys, then "Log in with Facebook". */
export function FacebookSection({ settings, onSaved, onSecret, pollMs = 1500 }: Props) {
  const { t } = useTranslation();
  const appIdId = useId();
  const fb = settings.facebook;
  const [appId, setAppId] = useState(fb.app_id ?? "");
  const [savingId, setSavingId] = useState(false);
  const [idSaved, setIdSaved] = useState(false);
  const [idError, setIdError] = useState<string | null>(null);

  const [status, setStatus] = useState<FacebookLoginStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorState>(null);
  const loggedInRef = useRef(settings.secrets.facebook_page_token && fb.page_id !== null);

  useEffect(() => setAppId(fb.app_id ?? ""), [fb.app_id]);

  /** Keep the parent's settings in step with the login (status badge, publish screen). */
  function apply(next: FacebookLoginStatus) {
    setStatus(next);
    const loggedIn = next.state === "logged_in";
    if (loggedIn !== loggedInRef.current) {
      loggedInRef.current = loggedIn;
      onSaved({
        ...settings,
        facebook: {
          ...settings.facebook,
          page_id: next.page?.id ?? null,
          page_name: next.page?.name ?? null,
        },
        secrets: { ...settings.secrets, facebook_page_token: loggedIn },
      });
    }
  }

  useEffect(() => {
    let cancelled = false;
    fetchFacebookLogin()
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
      fetchFacebookLogin()
        .then(apply)
        .catch(() => {
          // keep waiting; the next tick tries again
        });
    }, pollMs);
    return () => clearInterval(timer);
  }, [waiting, pollMs]);

  async function run(action: () => Promise<FacebookLoginStatus>) {
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

  async function saveAppId() {
    setSavingId(true);
    setIdSaved(false);
    setIdError(null);
    try {
      const updated = await patchSettings({
        facebook: { app_id: appId.trim() === "" ? null : appId.trim() },
      });
      onSaved(updated);
      setIdSaved(true);
    } catch (err) {
      const code = err instanceof ApiError ? err.fields["facebook.app_id"] : undefined;
      setIdError(
        code
          ? t(`fieldErrors.${code}`, { defaultValue: t("fieldErrors.default") })
          : t(errorKey(err), { defaultValue: t("errors.unknown") }),
      );
    } finally {
      setSavingId(false);
    }
  }

  const keysReady = fb.app_id !== null && settings.secrets.facebook_app_secret;
  const lastError = status?.error ? { key: `errors.${status.error}`, params: {} } : null;
  const shownError = error ?? lastError;

  return (
    <>
      <p className="mb-4 text-xs text-slate-500">{t("settings.facebook.intro")}</p>

      <div className="space-y-1.5">
        <label htmlFor={appIdId} className="text-sm font-medium">
          {t("settings.facebook.appId")}
        </label>
        <div className="flex gap-2">
          <input
            id={appIdId}
            value={appId}
            inputMode="numeric"
            onChange={(e) => setAppId(e.target.value)}
            aria-invalid={idError !== null}
            className={input}
          />
          <button
            type="button"
            disabled={savingId}
            onClick={() => void saveAppId()}
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
          name="facebook_app_secret"
          label={t("settings.facebook.appSecret")}
          hint={t("settings.facebook.appSecretHint")}
          isSet={settings.secrets.facebook_app_secret}
          onChange={(isSet) => onSecret("facebook_app_secret", isSet)}
        />
      </div>

      {status && (
        <p className="mt-4 text-xs text-slate-500">
          {t("settings.facebook.redirectHint")}{" "}
          <code data-testid="fb-redirect" className="select-all rounded bg-slate-100 px-1">
            {status.redirect_uri}
          </code>
        </p>
      )}

      <div className="mt-6 space-y-3 border-t border-slate-100 pt-6">
        <h4 className="text-sm font-medium">{t("settings.facebook.login.title")}</h4>

        {status === null && <p className="text-sm text-slate-400">{t("common.loading")}</p>}

        {status?.state === "logged_out" &&
          (keysReady ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(startFacebookLogin)}
              className={primary}
            >
              {t("settings.facebook.login.start")}
            </button>
          ) : (
            <p className="text-sm text-slate-500">{t("settings.facebook.login.needKeys")}</p>
          ))}

        {status?.state === "waiting" && (
          <div className="space-y-2">
            <p className="text-sm text-slate-600">{t("settings.facebook.login.waiting")}</p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(cancelFacebookLogin)}
              className={secondary}
            >
              {t("settings.facebook.login.cancel")}
            </button>
          </div>
        )}

        {status?.state === "choose_page" && (
          <div className="space-y-2">
            <p className="text-sm text-slate-600">{t("settings.facebook.login.choosePage")}</p>
            <ul className="space-y-1">
              {status.pages.map((page) => (
                <li key={page.id}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void run(() => selectFacebookPage(page.id))}
                    className={secondary + " w-full text-left"}
                  >
                    {page.name}
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(cancelFacebookLogin)}
              className="text-sm text-slate-500 underline"
            >
              {t("settings.facebook.login.cancel")}
            </button>
          </div>
        )}

        {status?.state === "logged_in" && (
          <div className="space-y-2">
            <p className="text-sm text-emerald-700">
              {t("settings.facebook.login.loggedIn", { page: status.page?.name ?? "" })}
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(logoutFacebook)}
              className={secondary}
            >
              {t("settings.facebook.login.logout")}
            </button>
            <p className="text-xs text-slate-400">{t("settings.facebook.login.stored")}</p>
          </div>
        )}

        {shownError && (
          <p role="alert" className="text-sm text-rose-600">
            {t(shownError.key, { ...shownError.params, defaultValue: t("errors.unknown") })}
          </p>
        )}
      </div>
    </>
  );
}

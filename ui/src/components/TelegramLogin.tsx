import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  cancelTelegramLogin,
  fetchTelegramLogin,
  logoutTelegram,
  sendTelegramCode,
  sendTelegramPassword,
  signInTelegram,
  type TelegramLoginStatus,
} from "../api";
import { errorKey } from "../errors";

interface Props {
  /** api_id and api_hash are saved; sign-in is impossible without them. */
  keysReady: boolean;
  /** Whether the backend holds a session (from the settings response). */
  sessionSet: boolean;
  onSessionChange: (isSet: boolean) => void;
}

type ErrorState = { key: string; params: Record<string, unknown> } | null;

const input =
  "w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400";
const primary = "rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40";
const secondary = "rounded-lg border border-slate-200 px-3 py-1.5 text-sm disabled:opacity-40";

/** Phone → code from Telegram → (cloud password) → signed in; plus sign-out. */
export function TelegramLogin({ keysReady, sessionSet, onSessionChange }: Props) {
  const { t } = useTranslation();
  const ids = { phone: useId(), code: useId(), password: useId() };
  const [status, setStatus] = useState<TelegramLoginStatus | null>(null);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorState>(null);

  useEffect(() => {
    let cancelled = false;
    fetchTelegramLogin()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setStatus({ state: sessionSet ? "logged_in" : "logged_out", phone: null });
          setError({ key: errorKey(err), params: {} });
        }
      });
    return () => {
      cancelled = true;
    };
    // Load once; later changes come from the actions below.
  }, []);

  async function run(action: () => Promise<TelegramLoginStatus>) {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      setStatus(next);
      setCode("");
      setPassword("");
      const loggedIn = next.state === "logged_in";
      if (loggedIn !== sessionSet) onSessionChange(loggedIn);
    } catch (err) {
      setError({ key: errorKey(err), params: err instanceof ApiError ? err.params : {} });
      // Some errors (expired code...) end the sign-in on the backend: show the real step.
      try {
        setStatus(await fetchTelegramLogin());
      } catch {
        // keep the current step
      }
    } finally {
      setBusy(false);
    }
  }

  const state = status?.state;
  const loggedIn = state === "logged_in";

  return (
    <div className="space-y-3" data-testid="telegram-login">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">{t("settings.telegram.login.title")}</h3>
        {status && (
          <span
            data-testid="telegram-login-status"
            className={
              loggedIn
                ? "rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700"
                : "rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500"
            }
          >
            {loggedIn ? t("settings.telegram.login.loggedIn") : t("settings.telegram.login.loggedOut")}
          </span>
        )}
      </div>

      {status === null && <p className="text-sm text-slate-400">{t("common.loading")}</p>}

      {!keysReady && status !== null && !loggedIn && (
        <p className="text-xs text-amber-700">{t("settings.telegram.login.needKeys")}</p>
      )}

      {state === "logged_out" && (
        <div className="space-y-1.5">
          <label htmlFor={ids.phone} className="text-sm">
            {t("settings.telegram.login.phone")}
          </label>
          <div className="flex gap-2">
            <input
              id={ids.phone}
              type="tel"
              autoComplete="off"
              value={phone}
              placeholder="+79991234567"
              onChange={(e) => setPhone(e.target.value)}
              className={input}
            />
            <button
              type="button"
              disabled={busy || !keysReady || phone.trim() === ""}
              onClick={() => void run(() => sendTelegramCode(phone.trim()))}
              className={`${primary} shrink-0`}
            >
              {busy ? t("settings.telegram.login.busy") : t("settings.telegram.login.sendCode")}
            </button>
          </div>
          <p className="text-xs text-slate-400">{t("settings.telegram.login.phoneHint")}</p>
        </div>
      )}

      {state === "code_sent" && (
        <div className="space-y-1.5">
          <p className="text-sm text-slate-600">
            {t("settings.telegram.login.codeSentTo", { phone: status?.phone ?? "" })}
          </p>
          <label htmlFor={ids.code} className="text-sm">
            {t("settings.telegram.login.code")}
          </label>
          <div className="flex gap-2">
            <input
              id={ids.code}
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className={input}
            />
            <button
              type="button"
              disabled={busy || code.trim() === ""}
              onClick={() => void run(() => signInTelegram(code.trim()))}
              className={`${primary} shrink-0`}
            >
              {busy ? t("settings.telegram.login.busy") : t("settings.telegram.login.signIn")}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(cancelTelegramLogin)}
              className={`${secondary} shrink-0`}
            >
              {t("settings.telegram.login.cancel")}
            </button>
          </div>
        </div>
      )}

      {state === "password_needed" && (
        <div className="space-y-1.5">
          <p className="text-sm text-slate-600">{t("settings.telegram.login.passwordNeeded")}</p>
          <label htmlFor={ids.password} className="text-sm">
            {t("settings.telegram.login.password")}
          </label>
          <div className="flex gap-2">
            <input
              id={ids.password}
              type="password"
              autoComplete="off"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={input}
            />
            <button
              type="button"
              disabled={busy || password === ""}
              onClick={() => void run(() => sendTelegramPassword(password))}
              className={`${primary} shrink-0`}
            >
              {busy ? t("settings.telegram.login.busy") : t("settings.telegram.login.confirm")}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(cancelTelegramLogin)}
              className={`${secondary} shrink-0`}
            >
              {t("settings.telegram.login.cancel")}
            </button>
          </div>
        </div>
      )}

      {loggedIn && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void run(logoutTelegram)}
          className={`${secondary} text-rose-600`}
        >
          {t("settings.telegram.login.logout")}
        </button>
      )}

      {error && (
        <p role="alert" className="text-xs text-rose-600">
          {t(error.key, { ...error.params, defaultValue: t("errors.unknown") })}
        </p>
      )}
      <p className="text-xs text-slate-400">{t("settings.telegram.login.stored")}</p>
    </div>
  );
}

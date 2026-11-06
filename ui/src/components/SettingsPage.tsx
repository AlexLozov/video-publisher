import { useEffect, useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, patchSettings, type SecretName, type Settings, type TelegramMode } from "../api";
import { errorKey } from "../errors";
import { SUPPORTED_LANGUAGES, type Language } from "../i18n";
import { SecretField } from "./SecretField";
import { TelegramLogin } from "./TelegramLogin";

interface Props {
  settings: Settings | null;
  loadError: string | null;
  onSettingsChange: (settings: Settings) => void;
  onLanguageChange: (lng: Language) => void;
}

export function SettingsPage({ settings, loadError, onSettingsChange, onLanguageChange }: Props) {
  const { t, i18n } = useTranslation();
  const languageId = useId();

  if (loadError) {
    return (
      <Card>
        <p role="alert" className="text-sm text-rose-600">
          {t("settings.loadFailed")}: {t(loadError, { defaultValue: t("errors.unknown") })}
        </p>
      </Card>
    );
  }
  if (!settings) {
    return (
      <Card>
        <p className="text-sm text-slate-400">{t("common.loading")}</p>
      </Card>
    );
  }

  const setSecret = (name: SecretName, isSet: boolean) =>
    onSettingsChange({ ...settings, secrets: { ...settings.secrets, [name]: isSet } });

  return (
    <div className="space-y-6">
      <Card title={t("settings.general")}>
        <div className="flex items-center justify-between gap-4">
          <label htmlFor={languageId} className="text-sm font-medium">
            {t("language.label")}
          </label>
          <select
            id={languageId}
            value={i18n.resolvedLanguage}
            onChange={(e) => onLanguageChange(e.target.value as Language)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm"
          >
            {SUPPORTED_LANGUAGES.map((lng) => (
              <option key={lng} value={lng} lang={lng}>
                {t(`language.${lng}`)}
              </option>
            ))}
          </select>
        </div>
      </Card>

      <TelegramSection settings={settings} onSaved={onSettingsChange} onSecret={setSecret} />
    </div>
  );
}

function TelegramSection({
  settings,
  onSaved,
  onSecret,
}: {
  settings: Settings;
  onSaved: (s: Settings) => void;
  onSecret: (name: SecretName, isSet: boolean) => void;
}) {
  const { t } = useTranslation();
  const channelId = useId();
  const apiIdId = useId();
  const tg = settings.telegram;

  const [mode, setMode] = useState<TelegramMode>(tg.mode);
  const [channel, setChannel] = useState(tg.channel ?? "");
  const [apiId, setApiId] = useState(tg.api_id?.toString() ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    setMode(tg.mode);
    setChannel(tg.channel ?? "");
    setApiId(tg.api_id?.toString() ?? "");
  }, [tg.mode, tg.channel, tg.api_id]);

  async function save() {
    setBusy(true);
    setSaved(false);
    setError(null);
    setFieldErrors({});
    const trimmedId = apiId.trim();
    const parsedId = Number(trimmedId);
    try {
      const updated = await patchSettings({
        telegram: {
          mode,
          channel: channel.trim() === "" ? null : channel.trim(),
          // Send unparseable input as-is so the backend reports a proper field error.
          api_id:
            trimmedId === ""
              ? null
              : Number.isInteger(parsedId)
                ? parsedId
                : (trimmedId as unknown as number),
        },
      });
      onSaved(updated);
      setSaved(true);
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fields).length > 0) {
        setFieldErrors(err.fields);
      } else {
        setError(errorKey(err));
      }
    } finally {
      setBusy(false);
    }
  }

  const fieldError = (path: string) => {
    const code = fieldErrors[path];
    return code ? t(`fieldErrors.${code}`, { defaultValue: t("fieldErrors.default") }) : null;
  };

  return (
    <Card title={t("settings.telegram.title")}>
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">{t("settings.telegram.mode")}</legend>
        <ModeOption
          value="bot"
          current={mode}
          onSelect={setMode}
          label={t("settings.telegram.modeBot")}
          hint={t("settings.telegram.modeBotHint")}
        />
        <ModeOption
          value="account"
          current={mode}
          onSelect={setMode}
          label={t("settings.telegram.modeAccount")}
          hint={t("settings.telegram.modeAccountHint")}
        />
      </fieldset>

      <div className="mt-6 space-y-1.5">
        <label htmlFor={channelId} className="text-sm font-medium">
          {t("settings.telegram.channel")}
        </label>
        <input
          id={channelId}
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          placeholder="@my_channel"
          aria-invalid={fieldError("telegram.channel") !== null}
          className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400"
        />
        <FieldError message={fieldError("telegram.channel")} />
        <p className="text-xs text-slate-400">{t("settings.telegram.channelHint")}</p>
      </div>

      {mode === "account" && (
        <div className="mt-6 space-y-1.5">
          <label htmlFor={apiIdId} className="text-sm font-medium">
            {t("settings.telegram.apiId")}
          </label>
          <input
            id={apiIdId}
            value={apiId}
            inputMode="numeric"
            onChange={(e) => setApiId(e.target.value)}
            aria-invalid={fieldError("telegram.api_id") !== null}
            className="w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400"
          />
          <FieldError message={fieldError("telegram.api_id")} />
          <p className="text-xs text-slate-400">{t("settings.telegram.apiHint")}</p>
        </div>
      )}

      <div className="mt-6 flex items-center gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => void save()}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
        >
          {busy ? t("common.saving") : t("common.save")}
        </button>
        {saved && <span className="text-sm text-emerald-700">{t("common.saved")}</span>}
        {error && (
          <span role="alert" className="text-sm text-rose-600">
            {t(error, { defaultValue: t("errors.unknown") })}
          </span>
        )}
      </div>

      <div className="mt-6 border-t border-slate-100 pt-6">
        {mode === "bot" ? (
          <SecretField
            name="telegram_bot_token"
            label={t("settings.telegram.botToken")}
            hint={t("settings.telegram.botTokenHint")}
            isSet={settings.secrets.telegram_bot_token}
            onChange={(isSet) => onSecret("telegram_bot_token", isSet)}
          />
        ) : (
          <div className="space-y-4">
            <SecretField
              name="telegram_api_hash"
              label={t("settings.telegram.apiHash")}
              isSet={settings.secrets.telegram_api_hash}
              onChange={(isSet) => onSecret("telegram_api_hash", isSet)}
            />
            <div className="border-t border-slate-100 pt-4">
              <TelegramLogin
                keysReady={tg.api_id !== null && settings.secrets.telegram_api_hash}
                sessionSet={settings.secrets.telegram_session}
                onSessionChange={(isSet) => onSecret("telegram_session", isSet)}
              />
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function ModeOption({
  value,
  current,
  onSelect,
  label,
  hint,
}: {
  value: TelegramMode;
  current: TelegramMode;
  onSelect: (m: TelegramMode) => void;
  label: string;
  hint: string;
}) {
  const checked = value === current;
  return (
    <label
      className={
        "flex cursor-pointer gap-3 rounded-xl border p-3 " +
        (checked ? "border-slate-900 bg-slate-50" : "border-slate-200")
      }
    >
      <input
        type="radio"
        name="telegram-mode"
        value={value}
        checked={checked}
        onChange={() => onSelect(value)}
        className="mt-1"
      />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        <span className="block text-xs text-slate-500">{hint}</span>
      </span>
    </label>
  );
}

function FieldError({ message }: { message: string | null }) {
  return message ? (
    <p role="alert" className="text-xs text-rose-600">
      {message}
    </p>
  ) : null;
}

function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      {title && <h2 className="mb-4 text-lg font-medium">{title}</h2>}
      {children}
    </section>
  );
}

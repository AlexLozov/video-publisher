import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { deleteSecret, putSecret, type WritableSecret } from "../api";
import { errorKey } from "../errors";

interface Props {
  name: WritableSecret;
  label: string;
  hint?: string;
  isSet: boolean;
  onChange: (isSet: boolean) => void;
}

/** Write-only input: the stored value is never shown, only whether it is set. */
export function SecretField({ name, label, hint, isSet, onChange }: Props) {
  const { t } = useTranslation();
  const id = useId();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function run(action: () => Promise<void>, nowSet: boolean) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await action();
      setValue("");
      setSaved(nowSet);
      onChange(nowSet);
    } catch (err) {
      setError(errorKey(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <span
          data-testid={`${name}-status`}
          className={
            isSet
              ? "rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700"
              : "rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500"
          }
        >
          {isSet ? t("secret.set") : t("secret.notSet")}
        </span>
      </div>
      <div className="flex gap-2">
        <input
          id={id}
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={value}
          placeholder={isSet ? t("secret.placeholderReplace") : t("secret.placeholderNew")}
          onChange={(e) => setValue(e.target.value)}
          aria-invalid={error !== null}
          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400"
        />
        <button
          type="button"
          disabled={busy || value.trim() === ""}
          aria-label={`${t("common.save")}: ${label}`}
          onClick={() => void run(() => putSecret(name, value), true)}
          className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
        >
          {t("common.save")}
        </button>
        {isSet && (
          <button
            type="button"
            disabled={busy}
            aria-label={`${t("common.delete")}: ${label}`}
            onClick={() => void run(() => deleteSecret(name), false)}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-rose-600 disabled:opacity-40"
          >
            {t("common.delete")}
          </button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-xs text-rose-600">
          {t(error, { defaultValue: t("errors.unknown") })}
        </p>
      )}
      {saved && <p className="text-xs text-emerald-700">{t("common.saved")}</p>}
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
      <p className="text-xs text-slate-400">{t("secret.storedSafely")}</p>
    </div>
  );
}

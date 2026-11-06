import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGUAGES, type Language } from "../i18n";

export function LanguageSwitcher({ onChange }: { onChange: (lng: Language) => void }) {
  const { t, i18n } = useTranslation();
  const current = i18n.resolvedLanguage as Language;

  return (
    <div
      role="group"
      aria-label={t("language.label")}
      className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 text-sm shadow-sm"
    >
      {SUPPORTED_LANGUAGES.map((lng) => {
        const active = lng === current;
        return (
          <button
            key={lng}
            type="button"
            lang={lng}
            aria-pressed={active}
            title={t(`language.${lng}`)}
            onClick={() => onChange(lng)}
            className={
              active
                ? "rounded-md bg-slate-900 px-2.5 py-1 font-medium text-white"
                : "rounded-md px-2.5 py-1 text-slate-500 hover:text-slate-800"
            }
          >
            {lng.toUpperCase()}
          </button>
        );
      })}
    </div>
  );
}
